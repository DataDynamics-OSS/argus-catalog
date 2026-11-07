# SPDX-License-Identifier: Apache-2.0
"""데이터 소스 3단계 보강 단위 테스트 (자기완결형, in-memory SQLite).

- ERD: (스키마, 테이블) 기준 FK 해석, removed 제외, 스키마 지정 FK
- 내장 동기화: 접속 설정 database 를 MySQL 스캔 범위로 사용, 삭제 반영 범위
  (전체 스캔 시 사라진 DB 포함, 점이 든 DB 이름 오매칭 방지, 0개 결과 가드), SSL 옵션
- system_name 채우기, 토폴로지 데이터셋 수 removed 제외, 조직/시스템 수정 null 422,
  페더레이션 허용 목록 경고

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_datasource_stage3
"""

from __future__ import annotations

import asyncio
import ssl

from pydantic import ValidationError

import app.catalog.models as cm
from app.catalog import service, topology_service
from app.catalog import sync as catalog_sync
from app.catalog.erd import build_datasource_erd, build_erd
from app.catalog.schemas import DatasourceCreate, OrganizationUpdate, SystemUpdate
from app.core.config import settings
from tests.test_datasource_stage2 import _make_session


def _ds(datasource, name, ddl=None, status="active"):
    return cm.Dataset(urn=f"{datasource.datasource_id}.{name}.dataset", name=name,
                      datasource_id=datasource.id, origin="DEV",
                      qualified_name=f"{datasource.datasource_id}.{name}", status=status, ddl=ddl)


# ---------------------------------------------------------------------------
# ERD
# ---------------------------------------------------------------------------

async def _test_erd() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        src = await service.create_datasource(db, DatasourceCreate(name="pg", type="postgresql"))
        a_users = _ds(src, "a.users")
        b_users = _ds(src, "b.users")
        a_orders = _ds(src, "a.orders", ddl=(
            "CREATE TABLE orders (id int, user_id int, "
            "CONSTRAINT fk_u FOREIGN KEY (user_id) REFERENCES users (id))"))
        b_orders = _ds(src, "b.orders", ddl=(
            "CREATE TABLE orders (id int, user_id int, "
            "CONSTRAINT fk_u FOREIGN KEY (user_id) REFERENCES users (id))"))
        # 스키마를 명시한 교차 스키마 FK
        c_log = _ds(src, "c.log", ddl=(
            "CREATE TABLE log (id int, uid int, "
            "FOREIGN KEY (uid) REFERENCES \"b\".\"users\" (id))"))
        gone = _ds(src, "a.gone", status="removed", ddl=(
            "CREATE TABLE gone (x int, FOREIGN KEY (x) REFERENCES users (id))"))
        db.add_all([a_users, b_users, a_orders, b_orders, c_log, gone])
        await db.commit()

        erd = await build_datasource_erd(db, src.id)
        names = {t["full_name"] for t in erd["tables"]}
        assert "a.gone" not in names, names                 # removed 제외
        assert len(names) == 5
        rel = {(r["source_dataset_id"], r["target_dataset_id"]) for r in erd["relations"]}
        assert (a_orders.id, a_users.id) in rel               # 같은 스키마 우선
        assert (b_orders.id, b_users.id) in rel
        assert (a_orders.id, b_users.id) not in rel
        assert (c_log.id, b_users.id) in rel                  # 스키마 지정 FK
        assert len(rel) == 3

        # 중심 모드: removed 중심 데이터셋도 자신과 FK 이웃(같은 스키마 users)은 보인다
        center = await build_erd(db, gone.id)
        assert sorted(t["full_name"] for t in center["tables"]) == ["a.gone", "a.users"]


# ---------------------------------------------------------------------------
# 내장 동기화 범위·SSL
# ---------------------------------------------------------------------------

def _test_scope_and_ssl() -> None:
    f = catalog_sync._in_sync_scope
    assert f("x.t", None) and f("a.b.t", None)
    assert f("a.t", "a")
    assert not f("a.b.t", "a")          # 'a' 스캔이 'a.b' DB 테이블을 건드리지 않음
    assert not f("ab.t", "a")
    assert f("a.b.t", "a.b")

    assert service.connection_ssl({}, pg=True) is None
    assert service.connection_ssl({"ssl_enabled": "false"}, pg=False) is None
    assert service.connection_ssl({"ssl_enabled": True}, pg=True) == "require"
    ctx = service.connection_ssl({"ssl_enabled": "true"}, pg=False)
    assert isinstance(ctx, ssl.SSLContext)


async def _test_sync_scope() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        await service.save_datasource_configuration(
            db, ds.id, {"host": "h", "password": "pw", "ssl_enabled": "true"})
        dsid = ds.datasource_id
        keep = _ds(ds, "app.t1")
        vanished_table = _ds(ds, "app.t2")
        dotted = _ds(ds, "app.x.t")           # 'app.x' DB — 'app' 스캔 범위 밖
        db.add_all([keep, vanished_table, dotted])
        await db.commit()
        ids = {"keep": keep.id, "t2": vanished_table.id, "dotted": dotted.id}

    calls: list[dict] = []
    result_tables: list = [catalog_sync.TableInfo(database="app", name="t1", table_type="BASE TABLE",
                                                  table_comment="", columns=[])]

    async def fake_read(host, port, user, password, database=None, ssl=None):
        calls.append({"database": database, "ssl": ssl})
        return list(result_tables)

    async def no_sample(*a, **k):
        return None

    orig = catalog_sync._read_mysql_metadata, catalog_sync._fetch_sample_rows
    catalog_sync._read_mysql_metadata, catalog_sync._fetch_sample_rows = fake_read, no_sample
    try:
        # 1) 접속 설정 database 가 없으면 전체 스캔 → 범위는 데이터 소스 전체
        async with make_db() as db:
            res = await catalog_sync.sync_datasource_metadata(db, dsid)
            assert not res.errors, res.errors
            assert calls[-1]["database"] is None
            assert isinstance(calls[-1]["ssl"], ssl.SSLContext)   # ssl_enabled 반영
            st = {i: (await db.get(cm.Dataset, i)).status for i in ids.values()}
            assert st[ids["keep"]] == "active"
            assert st[ids["t2"]] == "removed"
            assert st[ids["dotted"]] == "removed"   # 전체 스캔에서 사라진 DB 도 제거 반영
            assert res.tables_removed == 2

        # 2) 접속 설정 database 지정 → 그 DB 만 스캔·제거 반영
        async with make_db() as db:
            for i in ids.values():
                (await db.get(cm.Dataset, i)).status = "active"
            await db.commit()
            await service.save_datasource_configuration(
                db, (await db.get(cm.Datasource, ds.id)).id, {"host": "h", "password": "", "database": "app"})
        async with make_db() as db:
            res = await catalog_sync.sync_datasource_metadata(db, dsid)
            assert calls[-1]["database"] == "app"
            assert calls[-1]["ssl"] is None
            assert (await db.get(cm.Dataset, ids["t2"])).status == "removed"
            assert (await db.get(cm.Dataset, ids["dotted"])).status == "active"   # 오매칭 없음

        # 3) 원본 테이블 0개 → 제거 반영 생략
        result_tables.clear()
        async with make_db() as db:
            (await db.get(cm.Dataset, ids["t2"])).status = "active"
            await db.commit()
        async with make_db() as db:
            res = await catalog_sync.sync_datasource_metadata(db, dsid)
            assert res.tables_removed == 0
            assert (await db.get(cm.Dataset, ids["keep"])).status == "active"
    finally:
        catalog_sync._read_mysql_metadata, catalog_sync._fetch_sample_rows = orig


# ---------------------------------------------------------------------------
# 응답·검증 보강
# ---------------------------------------------------------------------------

async def _test_misc() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        sys_ = cm.System(name="결제", code="pay")
        db.add(sys_)
        await db.flush()
        ds = await service.create_datasource(db, DatasourceCreate(name="fed-src", type="mysql"))
        row = await db.get(cm.Datasource, ds.id)
        row.system_id = sys_.id
        db.add_all([_ds(ds, "a.t"), _ds(ds, "a.r", status="removed")])
        await db.commit()

        listed = {d.id: d for d in await service.list_datasources(db)}
        assert listed[ds.id].system_name == "결제"
        assert (await service.get_datasource(db, ds.id)).system_name == "결제"

        topo = await topology_service.get_topology(db)
        counts = [d.dataset_count for o in [topo] for d in _walk_datasources(o)]
        assert counts == [1], counts       # removed 제외 — 상세 화면과 같은 기준

        old = settings.federation_export_datasource_allowlist
        settings.federation_export_datasource_allowlist = ["fed-src"]
        try:
            impact = await service.get_datasource_delete_impact(db, ds.id)
            assert impact["federation_allowlisted"] is True
        finally:
            settings.federation_export_datasource_allowlist = old

    for model, bad in [(OrganizationUpdate, {"name": None}), (OrganizationUpdate, {"sort_order": None}),
                       (SystemUpdate, {"status": None}), (SystemUpdate, {"name": "  "})]:
        try:
            model(**bad)
            raise AssertionError(f"expected ValidationError: {bad}")
        except ValidationError:
            pass
    assert OrganizationUpdate(description=None).description is None   # nullable 은 허용


def _walk_datasources(topo):
    data = topo.model_dump() if hasattr(topo, "model_dump") else topo
    out = []

    def walk(node):
        if isinstance(node, dict):
            if "dataset_count" in node and "type" in node:
                out.append(type("D", (), node))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(data)
    return out


async def _run() -> None:
    await _test_erd()
    _test_scope_and_ssl()
    await _test_sync_scope()
    await _test_misc()


def test_datasource_stage3() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_datasource_stage3 OK")
