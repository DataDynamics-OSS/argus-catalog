# SPDX-License-Identifier: Apache-2.0
"""내장 동기화 DDL·유일성 회귀 테스트 (자기완결형, in-memory SQLite).

IT 05-datasources SRC-T10·T15 에서 발견:
- 내장 sync 가 DDL 을 datasource_properties 에만 넣고 Dataset.ddl 컬럼을 비워 두어
  데이터셋 상세의 DDL 탭이 안 보이고 데이터 소스 ERD 의 관계가 0건이었다.
- PostgreSQL DDL 의 FK 절에 참조 컬럼이 빠져(`REFERENCES shop.customers`) ERD 파서가
  관계를 인식하지 못했다.
- 복합 PK 의 구성 컬럼을 "고유"로 표시해 ERD 카디널리티가 1:1 로 오판됐다.
- (SRC-T24) MySQL 1044(Access denied … to database 'x' — 없는 DB/권한 없음)를 "인증 실패"로 안내했다.
- (SRC-T20) 조직 없이 시스템을 만들면 `org=%d` 에 None 이 들어가 `--- Logging error ---` 가 남았다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_datasource_sync_ddl.py
"""

from __future__ import annotations

import asyncio
import logging

import app.catalog.models as cm
from app.catalog import service, topology_service
from app.catalog import sync as catalog_sync
from app.catalog.erd import build_datasource_erd, parse_ddl_keys
from app.catalog.schemas import DatasourceCreate, SystemCreate
from tests.test_datasource_stage2 import _make_session


def _test_pg_fk_ddl() -> None:
    clause = catalog_sync._pg_fk_ddl({
        "conname": "orders_customer_id_fkey", "contype": "f", "columns": ["customer_id"],
        "ref_table": "shop.customers", "ref_columns": ["customer_id"],
    })
    assert clause.endswith('REFERENCES shop.customers ("customer_id")'), clause
    fks = parse_ddl_keys(f"CREATE TABLE t (\n  {clause}\n);")["fks"]
    assert fks == [{
        "constraint_name": "orders_customer_id_fkey", "columns": ["customer_id"],
        "ref_schema": "shop", "ref_table": "customers", "ref_columns": ["customer_id"],
    }], fks
    # 참조 컬럼을 못 읽은 경우에도 예전 형식으로 생성(예외 없음)
    assert catalog_sync._pg_fk_ddl({
        "conname": "x", "contype": "f", "columns": ["a"], "ref_table": "s.t", "ref_columns": None,
    }).endswith("REFERENCES s.t")


def _test_single_unique_columns() -> None:
    rows = [
        {"contype": "p", "columns": ["order_id", "line_no"]},   # 복합 PK
        {"contype": "u", "columns": ["code"]},                  # 단일 UNIQUE
        {"contype": "f", "columns": ["product_id"]},
        {"contype": "p", "columns": None},
    ]
    assert catalog_sync._single_unique_columns(rows) == {"code"}
    assert catalog_sync._single_unique_columns([{"contype": "p", "columns": ["id"]}]) == {"id"}


def _test_pg_view_ddl() -> None:
    ddl = catalog_sync._pg_view_ddl("shop", "v_customer_sales", " SELECT c.name\n   FROM shop.customers c;")
    assert ddl == 'CREATE OR REPLACE VIEW "shop"."v_customer_sales" AS\nSELECT c.name\n   FROM shop.customers c;', ddl
    assert "CREATE TABLE" not in ddl
    # 정의를 못 읽으면 None → CREATE TABLE 재구성으로 대체
    assert catalog_sync._pg_view_ddl("s", "v", None) is None
    assert catalog_sync._pg_view_ddl("s", "v", "  ") is None


async def _test_sync_fills_ddl_column() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        await service.save_datasource_configuration(db, ds.id, {"host": "h", "password": "pw", "database": "app"})
        dsid = ds.datasource_id

    col = catalog_sync.ColumnInfo
    parent_ddl = "CREATE TABLE `users` (`id` int NOT NULL, PRIMARY KEY (`id`))"
    child_ddl = ("CREATE TABLE `orders` (`id` int NOT NULL, `user_id` int, PRIMARY KEY (`id`), "
                 "CONSTRAINT `fk_u` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`))")
    tables = [
        catalog_sync.TableInfo(database="app", name="users", table_type="BASE TABLE",
                               columns=[col(name="id", data_type="NUMBER", native_type="int",
                                            nullable=False, ordinal=1, is_primary_key=True, is_unique=True)],
                               datasource_properties={"ddl": parent_ddl}),
        catalog_sync.TableInfo(database="app", name="orders", table_type="BASE TABLE",
                               columns=[col(name="id", data_type="NUMBER", native_type="int",
                                            nullable=False, ordinal=1, is_primary_key=True, is_unique=True),
                                        col(name="user_id", data_type="NUMBER", native_type="int",
                                            nullable=True, ordinal=2)],
                               datasource_properties={"ddl": child_ddl}),
    ]

    async def fake_read(host, port, user, password, database=None, ssl=None):
        return list(tables)

    async def no_sample(*a, **k):
        return None

    orig = catalog_sync._read_mysql_metadata, catalog_sync._fetch_sample_rows
    catalog_sync._read_mysql_metadata, catalog_sync._fetch_sample_rows = fake_read, no_sample
    try:
        async with make_db() as db:
            res = await catalog_sync.sync_datasource_metadata(db, dsid)
            assert not res.errors and res.tables_created == 2, res
        async with make_db() as db:
            erd = await build_datasource_erd(db, ds.id)
            ddls = {t["full_name"] for t in erd["tables"]}
            assert ddls == {"app.users", "app.orders"}
            assert [(r["source_columns"], r["target_columns"], r["cardinality"]) for r in erd["relations"]] \
                == [(["user_id"], ["id"], "N:1")], erd["relations"]

        # 재동기화(UPDATE)에서 DDL 이 없으면 기존 컬럼 값을 지우지 않는다
        tables[0].datasource_properties = None
        async with make_db() as db:
            res = await catalog_sync.sync_datasource_metadata(db, dsid)
            assert res.tables_updated == 2, res
        async with make_db() as db:
            from sqlalchemy import select
            rows = (await db.execute(select(cm.Dataset).where(cm.Dataset.datasource_id == ds.id))).scalars().all()
            assert {r.name: r.ddl for r in rows} == {"app.users": parent_ddl, "app.orders": child_ddl}
    finally:
        catalog_sync._read_mysql_metadata, catalog_sync._fetch_sample_rows = orig


async def _test_create_system_without_org_logs() -> None:
    messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())   # 포맷 오류면 TypeError

    handler = _Capture(level=logging.INFO)
    lg = logging.getLogger(topology_service.__name__)
    old_level = lg.level
    lg.addHandler(handler)
    lg.setLevel(logging.INFO)
    try:
        make_db = await _make_session()
        async with make_db() as db:
            res = await topology_service.create_system(db, SystemCreate(name="미분류 시스템"))
            assert res.org_id is None
    finally:
        lg.removeHandler(handler)
        lg.setLevel(old_level)
    assert any("org=None" in m for m in messages), messages


def _test_classify_db_access_denied() -> None:
    c = service._classify_connection_error
    msg = c(Exception("(1044, \"Access denied for user 'sakila'@'%' to database 'nope'\")"), 8)
    assert msg == "데이터베이스를 찾을 수 없거나 접근 권한이 없습니다.", msg
    assert c(Exception('permission denied for database "shop"'), 8) == msg
    # 계정·비밀번호 오류(1045)는 그대로 인증 실패
    assert "인증" in c(Exception("(1045, \"Access denied for user 'sakila'@'x' (using password: YES)\")"), 8)


def test_datasource_sync_ddl() -> None:
    _test_classify_db_access_denied()
    _test_pg_fk_ddl()
    _test_single_unique_columns()
    _test_pg_view_ddl()
    asyncio.run(_test_sync_fills_ddl_column())
    asyncio.run(_test_create_system_without_org_logs())


if __name__ == "__main__":
    test_datasource_sync_ddl()
    print("test_datasource_sync_ddl OK")
