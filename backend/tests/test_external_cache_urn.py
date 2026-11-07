# SPDX-License-Identifier: Apache-2.0
"""외부 API 캐시 키 회귀 테스트 (in-memory SQLite, 자기완결형).

- C17: 옛/별칭 URN 으로 두 번째 이후 호출(``urn_map`` 캐시 적중)에서도 정규 URN 을 돌려줘
  ``metadata:{정규 urn}`` 한 키만 쓰는지.
- C18: 캐시 적중 응답에 붙인 ``_cache`` 진단 키가 캐시 원본 dict 를 오염시키지 않는지.
- C16: ID 기준 캐시 무효화가 실제 적재 키(``metadata:{urn}``/``avro:{urn}``)를 지우는지.

실행:
    cd backend
    pytest tests/test_external_cache_urn.py
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
import app.external.cache as cache_mod
from app.external import router as ext_router
from app.external import service as ext_service

CANON = "pg-abc.shop.orders.dataset"
OLD = "pg-abc.shop.orders.DEV.dataset"
ALIAS = "legacy.shop.orders"


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        cm.Base.metadata.tables["catalog_datasources"],
        cm.Dataset.__table__,
        cm.DatasetUrnAlias.__table__,
    ]
    pending, seen = list(tables), set()
    while pending:
        t = pending.pop()
        if t.name in seen:
            continue
        seen.add(t.name)
        if t not in tables:
            tables.append(t)
        pending.extend(fk.column.table for fk in t.foreign_keys)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: cm.Base.metadata.create_all(c, tables=tables))
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as db:
        ds = cm.Datasource(datasource_id="ds-1", name="pg", type="postgresql")
        db.add(ds)
        await db.flush()
        d = cm.Dataset(urn=CANON, name="orders", datasource_id=ds.id)
        db.add(d)
        await db.flush()
        db.add(cm.DatasetUrnAlias(old_urn=ALIAS, dataset_id=d.id))
        await db.commit()
    yield f
    await engine.dispose()


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    """테스트마다 빈 전역 캐시 + DB 조립 대신 가벼운 가짜 메타데이터/Avro."""
    cache = cache_mod.MetadataCache(max_size=100, ttl_seconds=60)
    monkeypatch.setattr(cache_mod, "_cache", cache)

    async def build_metadata(_session, dataset_id):
        return {"dataset_id": dataset_id, "urn": CANON}

    async def build_avro(_session, dataset_id):
        return {"type": "record", "name": "orders", "fields": []}

    monkeypatch.setattr(ext_service, "_build_metadata", build_metadata)
    monkeypatch.setattr(ext_service, "_build_avro_schema", build_avro)
    return cache


@pytest.mark.parametrize("given", [OLD, ALIAS])
async def test_resolve_returns_canonical_urn_on_cache_hit(factory, fresh_cache, given):
    async with factory() as db:
        first = await ext_service._resolve_dataset_id(db, given)
        second = await ext_service._resolve_dataset_id(db, given)  # urn_map 캐시 적중
    assert first == second and first[1] == CANON

    async with factory() as db:
        for _ in range(2):
            await ext_service.get_dataset_metadata(db, given)
    keys = set(fresh_cache._cache)
    assert f"metadata:{CANON}" in keys
    assert f"metadata:{given}" not in keys


async def test_cache_hit_does_not_mutate_cached_dict(factory, fresh_cache):
    async with factory() as db:
        miss = await ext_service.get_dataset_metadata(db, CANON)
        hit = await ext_service.get_dataset_metadata(db, CANON)
    assert miss["_cache"]["hit"] is False and hit["_cache"]["hit"] is True
    hit["extra"] = 1
    stored = await fresh_cache.get(f"metadata:{CANON}")
    assert "_cache" not in stored and "extra" not in stored


async def test_invalidate_by_id_removes_urn_keys(factory, fresh_cache):
    async with factory() as db:
        await ext_service.get_dataset_metadata(db, CANON)
        await ext_service.get_dataset_avro_schema(db, CANON)
        dataset_id = (await ext_service._resolve_dataset_id(db, CANON))[0]
        r = await ext_router.invalidate_by_id(None, dataset_id, user=None, session=db)
        assert r == {"invalidated": 2, "dataset_id": dataset_id}
        assert await fresh_cache.get(f"metadata:{CANON}") is None
        # 없는 dataset 은 0
        r = await ext_router.invalidate_by_id(None, 9999, user=None, session=db)
        assert r["invalidated"] == 0
