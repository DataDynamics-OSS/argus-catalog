# SPDX-License-Identifier: Apache-2.0
"""카탈로그 엔티티 2단계 (design/catalog-entity-design.md) — 자기완결형, in-memory SQLite.

- 데이터 소스 역할(CATALOG / DATABASE / ENGINE), 엔진은 자동 카탈로그 없음
- SYNC 연결 없는 엔진의 데이터셋 생성 409
- 연결 추가·해제 규칙(QUERY 가능 유형, SYNC 카탈로그·데이터 소스당 하나, 자동 SYNC 해제 금지)
- 카탈로그 수동 생성·수정·삭제 규칙
- 엔진 카탈로그 탐색(Trino 응답은 가짜로 대체)

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_catalog_entity_stage2
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException

import app.catalog.models as cm
from app.catalog import catalogs_router, catalogs_service, service
from app.catalog import router as catalog_router
from app.catalog.schemas import (
    CatalogConnectionCreate,
    CatalogCreate,
    CatalogUpdate,
    DatasetCreate,
    DatasourceCreate,
)
from tests.test_catalog_entity_stage1 import _count, _make_session


class _User:
    username = "tester"


async def _expect(coro, status: int) -> None:
    try:
        await coro
    except HTTPException as e:
        assert e.status_code == status, (e.status_code, e.detail)
        return
    raise AssertionError(f"expected HTTP {status}")


async def _setup(db):
    hms = await service.create_datasource(db, DatasourceCreate(name="hms", type="hive"))
    impala = await service.create_datasource(db, DatasourceCreate(name="impala", type="impala"))
    trino = await service.create_datasource(db, DatasourceCreate(name="trino", type="trino"))
    mysql = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
    hms_cat = (await catalogs_service.list_datasource_catalogs(db, hms.id))[0]
    return hms, impala, trino, mysql, hms_cat


async def _test_roles_and_engine_has_no_catalog() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        hms, impala, trino, mysql, _ = await _setup(db)
        assert (hms.role, impala.role, trino.role, mysql.role) == ("CATALOG", "ENGINE", "ENGINE", "DATABASE")
        assert await _count(db, cm.Catalog) == 2          # hms + mysql
        assert await catalogs_service.list_datasource_catalogs(db, impala.id) == []


async def _test_engine_dataset_requires_sync() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        _, _, trino, _, _ = await _setup(db)
        await _expect(catalog_router.create_dataset(
            DatasetCreate(name="t", datasource_id=trino.id), _User(), None, db), 409)

        # 엔진을 통해서만 닿는 카탈로그를 만들고 SYNC 로 연결하면 수집 가능
        pg_behind = await catalogs_router.create_catalog(None, CatalogCreate(name="pg via trino", kind="postgresql"), db)
        assert pg_behind.catalog_key.startswith("postgresql-") and not pg_behind.auto_created
        await catalogs_router.add_connection(None, pg_behind.id, CatalogConnectionCreate(
            datasource_id=trino.id, role="SYNC", remote_name="pg"), db)
        created = await service.create_dataset(db, DatasetCreate(name="t", datasource_id=trino.id))
        assert (await db.get(cm.Dataset, created.id)).catalog_id == pg_behind.id


async def _test_connection_rules() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        hms, impala, trino, mysql, hms_cat = await _setup(db)
        # QUERY: 엔진 OK, 일반 DB 는 409
        cat = await catalogs_router.add_connection(None, hms_cat.id, CatalogConnectionCreate(
            datasource_id=impala.id, role="QUERY"), db)
        assert {(c.datasource_id, c.role) for c in cat.connections} == {(hms.id, "SYNC"), (impala.id, "QUERY")}
        await _expect(catalogs_router.add_connection(None, hms_cat.id, CatalogConnectionCreate(
            datasource_id=mysql.id, role="QUERY"), db), 409)
        # 중복 409, 없는 카탈로그·데이터 소스 404
        await _expect(catalogs_router.add_connection(None, hms_cat.id, CatalogConnectionCreate(
            datasource_id=impala.id, role="QUERY"), db), 409)
        await _expect(catalogs_router.add_connection(None, 999, CatalogConnectionCreate(
            datasource_id=impala.id), db), 404)
        await _expect(catalogs_router.add_connection(None, hms_cat.id, CatalogConnectionCreate(
            datasource_id=999), db), 404)
        # SYNC: 이미 HMS 가 수집 중인 카탈로그에 Trino SYNC 는 409
        await _expect(catalogs_router.add_connection(None, hms_cat.id, CatalogConnectionCreate(
            datasource_id=trino.id, role="SYNC"), db), 409)
        # Trino 가 하나를 SYNC 하면 다른 카탈로그 SYNC 는 409 (데이터 소스당 하나)
        a = await catalogs_service.create_catalog(db, CatalogCreate(name="a"))
        b = await catalogs_service.create_catalog(db, CatalogCreate(name="b"))
        await catalogs_service.add_connection(db, a.id, CatalogConnectionCreate(datasource_id=trino.id, role="SYNC"))
        await _expect(catalogs_router.add_connection(None, b.id, CatalogConnectionCreate(
            datasource_id=trino.id, role="SYNC"), db), 409)
        # 해제: 자동 카탈로그의 SYNC 는 409, QUERY 는 OK, 없는 연결 404
        await _expect(catalogs_router.remove_connection(None, hms_cat.id, hms.id, db), 409)
        cat = await catalogs_router.remove_connection(None, hms_cat.id, impala.id, db)
        assert [c.datasource_id for c in cat.connections] == [hms.id]
        await _expect(catalogs_router.remove_connection(None, hms_cat.id, impala.id, db), 404)
        # 데이터 소스 기준 조회
        assert [c.id for c in await catalogs_router.list_datasource_catalogs(trino.id, None, db)] == [a.id]
        await _expect(catalogs_router.list_datasource_catalogs(999, None, db), 404)


async def _test_catalog_crud_rules() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        _, _, trino, _, hms_cat = await _setup(db)
        # 자동 카탈로그: 이름 변경·삭제 409, 큐레이션 필드는 OK
        await _expect(catalogs_router.update_catalog(None, hms_cat.id, CatalogUpdate(name="x"), db), 409)
        upd = await catalogs_router.update_catalog(None, hms_cat.id, CatalogUpdate(description="설명", owner="kim"), db)
        assert (upd.description, upd.owner, upd.name) == ("설명", "kim", "hms")
        await _expect(catalogs_router.delete_catalog(None, hms_cat.id, db), 409)
        # 수동 카탈로그: 데이터셋 있으면 409, 없으면 삭제
        manual = await catalogs_service.create_catalog(db, CatalogCreate(name="manual", kind="postgresql"))
        await catalogs_service.add_connection(db, manual.id, CatalogConnectionCreate(datasource_id=trino.id, role="SYNC"))
        await service.create_dataset(db, DatasetCreate(name="t", datasource_id=trino.id))
        await _expect(catalogs_router.delete_catalog(None, manual.id, db), 409)
        empty = await catalogs_service.create_catalog(db, CatalogCreate(name="empty"))
        await catalogs_router.delete_catalog(None, empty.id, db)
        assert await catalogs_service.get_catalog(db, empty.id) is None
        await _expect(catalogs_router.delete_catalog(None, empty.id, db), 404)


async def _test_engine_discovery() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        hms, impala, trino, _, hms_cat = await _setup(db)
        ice = await catalogs_service.create_catalog(db, CatalogCreate(name="lake", kind="iceberg-rest"))
        await catalogs_service.add_connection(db, hms_cat.id, CatalogConnectionCreate(
            datasource_id=trino.id, role="QUERY", remote_name="hive"))

        # Impala 는 자동 탐색 미지원
        res = await catalogs_router.discover_engine_catalogs(None, impala.id, db)
        assert res.supported is False and res.catalogs == []

        async def fake(_config):
            return [("hive", "hive"), ("lake", "iceberg"), ("system", "system"), ("pg", "postgresql")]

        orig = catalogs_service._trino_catalogs
        catalogs_service._trino_catalogs = fake
        try:
            res = await catalogs_router.discover_engine_catalogs(None, trino.id, db)
        finally:
            catalogs_service._trino_catalogs = orig
        by_name = {c.remote_name: c for c in res.catalogs}
        assert set(by_name) == {"hive", "lake", "pg"}                 # system 은 내부 카탈로그라 제외
        assert by_name["hive"].connected_catalog_id == hms_cat.id
        assert by_name["hive"].suggested_catalog_ids == [hms_cat.id]
        assert set(by_name["lake"].suggested_catalog_ids) == {ice.id, hms_cat.id}  # iceberg → REST·HMS
        assert by_name["pg"].suggested_catalog_ids == []              # 등록된 postgresql 카탈로그 없음

        # 접속 설정이 없으면 형식 오류 메시지로 응답(예외 아님)
        res = await catalogs_router.discover_engine_catalogs(None, trino.id, db)
        assert res.supported and res.catalogs == [] and res.message
        await _expect(catalogs_router.discover_engine_catalogs(None, 999, db), 404)


_CASES = (
    _test_roles_and_engine_has_no_catalog,
    _test_engine_dataset_requires_sync,
    _test_connection_rules,
    _test_catalog_crud_rules,
    _test_engine_discovery,
)


def test_catalog_entity_stage2() -> None:
    """pytest 진입점."""
    for case in _CASES:
        asyncio.run(case())


if __name__ == "__main__":
    for case in _CASES:
        asyncio.run(case())
    print("test_catalog_entity_stage2 OK")
