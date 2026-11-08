# SPDX-License-Identifier: Apache-2.0
"""카탈로그 엔티티 1단계 (design/catalog-entity-design.md) — 자기완결형, in-memory SQLite.

- 데이터 소스 생성 → 자동 카탈로그(catalog_key = datasource_id) + SYNC 연결
- 데이터셋 생성 → catalog_id 가 데이터 소스의 SYNC 카탈로그로 채워짐
- 데이터 소스 이름 변경 → 자동 카탈로그 이름도 변경
- 데이터 소스 삭제 → 남은 데이터셋·다른 연결이 없는 자동 카탈로그만 정리
- 조회 API(목록·단건·404)

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_catalog_entity_stage1
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException
from sqlalchemy import func, select

import app.catalog.models as cm
from app.catalog import catalogs_router, catalogs_service, service
from app.catalog.schemas import DatasetCreate, DatasourceCreate, DatasourceUpdate
from tests.test_datasource_stage2 import _make_session as _make_base_session

# create_dataset 응답 조립이 읽는 테이블 (공용 헬퍼 목록에 없는 것만)
_EXTRA_TABLES = [
    "catalog_tags", "catalog_dataset_tags", "catalog_owners", "catalog_glossary_terms",
    "catalog_dataset_glossary_terms", "catalog_dataset_properties",
]


async def _make_session():
    make_db = await _make_base_session()
    async with make_db() as db:
        conn = await db.connection()
        tables = [cm.Base.metadata.tables[t] for t in _EXTRA_TABLES]
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(sc, tables=tables))
        await db.commit()
    return make_db


async def _count(db, model) -> int:
    return await db.scalar(select(func.count()).select_from(model))


async def _test_create_datasource_makes_catalog() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="hive-prod", type="hive"))
        catalogs = await catalogs_service.list_catalogs(db)
        assert len(catalogs) == 1
        c = catalogs[0]
        assert c.catalog_key == ds.datasource_id      # 카탈로그 기준 URN = 지금 URN
        assert (c.name, c.kind, c.source_type, c.auto_created) == ("hive-prod", "hive", "EXTERNAL", True)
        assert [(x.datasource_id, x.role) for x in c.connections] == [(ds.id, "SYNC")]

        # 멱등: 다시 호출해도 카탈로그·연결이 늘지 않는다
        row = await db.get(cm.Datasource, ds.id)
        again = await catalogs_service.ensure_datasource_catalog(db, row)
        await db.commit()
        assert again.id == c.id
        assert await _count(db, cm.Catalog) == 1
        assert await _count(db, cm.CatalogConnection) == 1


async def _test_dataset_gets_catalog_id() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        created = await service.create_dataset(db, DatasetCreate(name="t", datasource_id=ds.id))
        row = await db.get(cm.Dataset, created.id)
        cat = (await catalogs_service.list_catalogs(db))[0]
        assert row.catalog_id == cat.id
        assert row.urn == f"{cat.catalog_key}.t.dataset"
        assert (await catalogs_service.get_catalog(db, cat.id)).dataset_count == 1


async def _test_dataset_without_connection_keeps_null() -> None:
    """연결이 없는 데이터 소스(백필 전 데이터 등)에서도 데이터셋 생성은 실패하지 않는다."""
    make_db = await _make_session()
    async with make_db() as db:
        ds = cm.Datasource(datasource_id="mysql-legacy", name="legacy", type="mysql", origin="DEV")
        db.add(ds)
        await db.commit()
        assert await catalogs_service.sync_catalog_id(db, ds.id) is None
        created = await service.create_dataset(db, DatasetCreate(name="t", datasource_id=ds.id))
        assert (await db.get(cm.Dataset, created.id)).catalog_id is None


async def _test_rename_follows() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="old", type="mysql"))
        await service.update_datasource(db, ds.id, DatasourceUpdate(name="new"))
        assert (await catalogs_service.list_catalogs(db))[0].name == "new"


async def _test_delete_cleans_auto_catalog() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        await service.create_dataset(db, DatasetCreate(name="t", datasource_id=ds.id))
        assert await service.delete_datasource(db, ds.id, force=True)
        assert await _count(db, cm.Catalog) == 0
        assert await _count(db, cm.CatalogConnection) == 0


async def _test_delete_keeps_shared_catalog() -> None:
    """다른 데이터 소스가 연결된 카탈로그는 남기고, 지운 데이터 소스의 연결만 사라진다."""
    make_db = await _make_session()
    async with make_db() as db:
        owner = await service.create_datasource(db, DatasourceCreate(name="hms", type="hive"))
        engine = await service.create_datasource(db, DatasourceCreate(name="impala", type="impala"))
        hms_cat = (await catalogs_service.get_catalog(db, 1))
        assert hms_cat.connections[0].datasource_id == owner.id
        # 엔진을 HMS 카탈로그에 QUERY 로 연결 (2단계에서 API 로 제공할 동작)
        db.add(cm.CatalogConnection(catalog_id=hms_cat.id, datasource_id=engine.id, role="QUERY"))
        await db.commit()

        assert await service.delete_datasource(db, owner.id, force=True)
        kept = await catalogs_service.get_catalog(db, hms_cat.id)
        assert kept is not None
        assert [(x.datasource_id, x.role) for x in kept.connections] == [(engine.id, "QUERY")]
        # 엔진(impala)은 2단계부터 자체 카탈로그를 만들지 않는다 — 남은 건 HMS 카탈로그뿐
        assert await _count(db, cm.Catalog) == 1


async def _test_router() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        listed = await catalogs_router.list_catalogs(None, db)
        assert [c.catalog_key for c in listed] == [ds.datasource_id]
        one = await catalogs_router.get_catalog(listed[0].id, None, db)
        assert one.id == listed[0].id
        try:
            await catalogs_router.get_catalog(999, None, db)
            raise AssertionError("expected 404")
        except HTTPException as e:
            assert e.status_code == 404


_CASES = (
    _test_create_datasource_makes_catalog,
    _test_dataset_gets_catalog_id,
    _test_dataset_without_connection_keeps_null,
    _test_rename_follows,
    _test_delete_cleans_auto_catalog,
    _test_delete_keeps_shared_catalog,
    _test_router,
)


def test_catalog_entity_stage1() -> None:
    """pytest 진입점."""
    for case in _CASES:
        asyncio.run(case())


if __name__ == "__main__":
    for case in _CASES:
        asyncio.run(case())
    print("test_catalog_entity_stage1 OK")
