# SPDX-License-Identifier: Apache-2.0
"""쿼리 리니지 자동 재해석 (TODO-033).

- 카탈로그 연결을 추가·해제하면 그 엔진 행의 해석을 비우고 다시 한다(연결이 바뀌면 카탈로그가 바뀐다).
- 수집 실행이 데이터셋을 만들고 끝나면 그 카탈로그로 미해석 행을 채운다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest tests/test_lineage_auto_reresolve.py
"""

from __future__ import annotations

from sqlalchemy import text

import app.catalog.models as cm
from app.catalog import catalogs_service, service, sync_runs_service
from app.catalog.schemas import CatalogConnectionCreate, DatasetCreate, DatasourceCreate
from tests.test_catalog_entity_stage1 import _make_session as _make_base


class _Db:
    """세션 팩토리 + 끝나면 엔진 정리(정리하지 않은 aiosqlite 엔진은 이후 테스트에 'Event loop is closed' 경고를 남긴다)."""

    async def __aenter__(self):
        self.make = await _make_base()
        self.db = self.make()
        return await self.db.__aenter__()

    async def __aexit__(self, *exc):
        await self.db.__aexit__(*exc)
        await self.make.kw["bind"].dispose()


async def _lineage(db, rid: int) -> tuple:
    row = (await db.execute(text(
        "SELECT source_dataset_id, target_dataset_id FROM argus_query_lineage WHERE id = :i"), {"i": rid})).one()
    return tuple(row)


async def _insert_lineage(db, rid: int, engine: str, source: str, target: str) -> None:
    await db.execute(text(
        "INSERT INTO argus_query_lineage (id, datasource_id, source_table, target_table) "
        "VALUES (:i, :k, :s, :t)"), {"i": rid, "k": engine, "s": source, "t": target})
    await db.commit()


async def _sync_catalog(db, ds_id: int) -> int:
    return await catalogs_service.sync_catalog_id(db, ds_id)


async def test_connection_add_and_remove_reresolve_engine_rows():
    async with _Db() as db:
        hms1 = await service.create_datasource(db, DatasourceCreate(name="hms1", type="hive"))
        hms2 = await service.create_datasource(db, DatasourceCreate(name="hms2", type="hive"))
        impala = await service.create_datasource(db, DatasourceCreate(name="impala", type="impala"))
        c1, c2 = await _sync_catalog(db, hms1.id), await _sync_catalog(db, hms2.id)
        d1 = await service.create_dataset(db, DatasetCreate(
            name="orders", qualified_name="sales.orders", datasource_id=hms1.id))
        d1t = await service.create_dataset(db, DatasetCreate(
            name="daily", qualified_name="sales.daily", datasource_id=hms1.id))
        await service.create_dataset(db, DatasetCreate(
            name="orders", qualified_name="sales.orders", datasource_id=hms2.id))
        await _insert_lineage(db, 1, impala.datasource_id, "sales.orders", "sales.daily")
        assert await _lineage(db, 1) == (None, None)  # 연결 전 — 카탈로그를 정할 수 없다

        await catalogs_service.add_connection(db, c1, CatalogConnectionCreate(datasource_id=impala.id))
        assert await _lineage(db, 1) == (d1.id, d1t.id)

        # 카탈로그가 둘이 되면 이름 없는 테이블은 어느 쪽인지 모른다 → 예전 해석을 비운다
        await catalogs_service.add_connection(db, c2, CatalogConnectionCreate(datasource_id=impala.id))
        assert await _lineage(db, 1) == (None, None)

        await catalogs_service.remove_connection(db, c2, impala.id)
        assert await _lineage(db, 1) == (d1.id, d1t.id)


async def test_sync_run_end_resolves_new_datasets():
    async with _Db() as db:
        conn = await db.connection()
        await conn.run_sync(lambda sc: cm.DatasourceSyncRun.__table__.create(sc, checkfirst=True))
        mysql = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        cat = await _sync_catalog(db, mysql.id)
        await _insert_lineage(db, 1, mysql.datasource_id, "shop.orders", "shop.daily")
        # 수집이 데이터셋을 직접 만든 상황(create_dataset 의 증분 해석을 거치지 않음)
        for did, name in ((501, "orders"), (502, "daily")):
            db.add(cm.Dataset(id=did, urn=f"urn:{did}", name=f"shop.{name}", datasource_id=mysql.id,
                              catalog_id=cat, qualified_name=f"{mysql.datasource_id}.shop.{name}"))
        run = cm.DatasourceSyncRun(datasource_id=mysql.id, executor="BUILTIN", scope_json="{}",
                                   status="SUCCEEDED", created_count=2)
        db.add(run)
        await db.commit()
        assert await _lineage(db, 1) == (None, None)

        await sync_runs_service._after_terminal(db, run)
        assert await _lineage(db, 1) == (501, 502)


async def test_failed_sync_run_does_not_resolve():
    async with _Db() as db:
        conn = await db.connection()
        await conn.run_sync(lambda sc: cm.DatasourceSyncRun.__table__.create(sc, checkfirst=True))
        mysql = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        cat = await _sync_catalog(db, mysql.id)
        await _insert_lineage(db, 1, mysql.datasource_id, "shop.orders", "shop.daily")
        db.add(cm.Dataset(id=601, urn="urn:601", name="shop.orders", datasource_id=mysql.id,
                          catalog_id=cat, qualified_name=f"{mysql.datasource_id}.shop.orders"))
        run = cm.DatasourceSyncRun(datasource_id=mysql.id, executor="BUILTIN", scope_json="{}",
                                   status="CANCELLED", created_count=1)
        db.add(run)
        await db.commit()
        await sync_runs_service._after_terminal(db, run)
        assert await _lineage(db, 1) == (None, None)
