# SPDX-License-Identifier: Apache-2.0
"""데이터셋 목록이 데이터 소스·환경·상태 필터를 쉼표로 여러 값 받는지 (DEF-067, in-memory SQLite).

예전에는 화면이 2개 이상 고르면 필터를 아예 보내지 않아 전체 목록이 나왔다.
"""

from __future__ import annotations

import asyncio

import app.catalog.models as cm
from app.catalog import service
from app.catalog.schemas import DatasourceCreate
from tests.test_datasource_stage2 import _make_session


async def _run() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        conn = await db.connection()

        def _create_catalog_tables(sc) -> None:
            for model in (cm.Tag, cm.DatasetTag, cm.Owner, cm.Taxonomy, cm.Category,
                          cm.DatasetCategory):
                model.__table__.create(sc, checkfirst=True)

        await conn.run_sync(_create_catalog_tables)
        await db.commit()

    async with make_db() as db:
        srcs = [await service.create_datasource(db, DatasourceCreate(name=f"s{i}", type="mysql"))
                for i in range(3)]
        # (데이터 소스, 이름, 환경, 상태)
        rows = [
            (0, "a", "PROD", "active"),
            (1, "b", "DEV", "active"),
            (2, "c", "STAGING", "active"),
            (2, "d", "PROD", "removed"),
        ]
        for si, n, origin, status in rows:
            ds = srcs[si]
            db.add(cm.Dataset(urn=f"{ds.datasource_id}.{n}.dataset", name=n, datasource_id=ds.id,
                              origin=origin, qualified_name=f"{ds.datasource_id}.{n}", status=status))
        await db.commit()

        async def names(**kw) -> set[str]:
            page = await service.list_datasets(db, page_size=100, **kw)
            return {d.name for d in page.items}

        ids = [s.datasource_id for s in srcs]
        # 단일 값은 예전과 같다
        assert await names(datasource=ids[0]) == {"a"}
        # 여러 데이터 소스 — 하나라도 맞으면 포함(removed 는 상태 미지정이면 제외)
        assert await names(datasource=f"{ids[0]},{ids[2]}") == {"a", "c"}
        assert await names(datasource=f" {ids[0]} , ,{ids[1]}") == {"a", "b"}
        # 환경·상태도 여러 값
        assert await names(origin="PROD,DEV") == {"a", "b"}
        assert await names(status="active,removed") == {"a", "b", "c", "d"}
        assert await names(status="removed") == {"d"}
        # 빈 값은 필터 없음
        assert await names(datasource="") == {"a", "b", "c"}


def test_dataset_list_multi_filter() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_dataset_list_multi_filter OK")
