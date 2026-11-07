# SPDX-License-Identifier: Apache-2.0
"""검색 결과 데이터셋 요약의 컬럼 수(schema_field_count)가 실제 값인지 (자기완결형, in-memory SQLite).

통합·하이브리드·시맨틱 검색과 페더레이션 export 가 같이 쓰는 ``_build_dataset_summary`` 가
예전에는 ``schema_field_count=0`` 을 고정으로 넣었다. 데이터셋 목록과 같은 기준으로 센다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_search_field_count
"""

from __future__ import annotations

import asyncio

import app.catalog.models as cm
from app.catalog import service
from app.catalog.schemas import DatasourceCreate
from app.search.service import _build_dataset_summary
from tests.test_datasource_stage2 import _make_session


async def _run() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        conn = await db.connection()
        await conn.run_sync(lambda sc: [m.__table__.create(sc, checkfirst=True)
                                        for m in (cm.Tag, cm.DatasetTag, cm.Owner)])
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        d = cm.Dataset(urn=f"{ds.datasource_id}.db.film.dataset", name="db.film", datasource_id=ds.id,
                       origin="DEV", qualified_name=f"{ds.datasource_id}.db.film", status="active",
                       display_name="영화", summary="영화 목록")
        empty = cm.Dataset(urn=f"{ds.datasource_id}.db.e.dataset", name="db.e", datasource_id=ds.id,
                           origin="DEV", qualified_name=f"{ds.datasource_id}.db.e", status="active")
        db.add_all([d, empty])
        await db.flush()
        db.add_all([cm.DatasetSchema(dataset_id=d.id, field_path=f"c{i}", field_type="STRING", ordinal=i)
                    for i in range(3)])
        await db.commit()

        s = await _build_dataset_summary(db, d.id)
        assert s.schema_field_count == 3, s
        assert s.display_name == "영화" and s.summary == "영화 목록"

        # 데이터셋 목록 API 와 같은 값
        listed = {x.id: x for x in (await service.list_datasets(db, search="db.", page_size=10)).items}
        assert listed[d.id].schema_field_count == s.schema_field_count

        assert (await _build_dataset_summary(db, empty.id)).schema_field_count == 0
        assert await _build_dataset_summary(db, 999999) is None


def test_search_field_count() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_search_field_count OK")
