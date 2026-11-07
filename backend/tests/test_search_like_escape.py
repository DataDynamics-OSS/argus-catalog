# SPDX-License-Identifier: Apache-2.0
"""검색어의 LIKE 와일드카드(% _ \\)를 글자로 매칭하는지 (자기완결형, in-memory SQLite).

- 통합/하이브리드 검색의 키워드 경로 ``keyword_search_ids``
- 데이터셋 목록 검색 ``list_datasets(search=)``

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_search_like_escape
"""

from __future__ import annotations

import asyncio

import app.catalog.models as cm
from app.catalog import service
from app.catalog.schemas import DatasourceCreate
from app.core.sql import like_contains
from app.search.service import keyword_search_ids
from tests.test_datasource_stage2 import _make_session

NAMES = ["db.a_b", "db.axb", "db.rate_100%", "db.rate_1000", "db.back\\slash", "db.backXslash"]


def _test_pattern() -> None:
    assert like_contains("a_b") == "%a\\_b%"
    assert like_contains("100%") == "%100\\%%"
    assert like_contains("x\\y") == "%x\\\\y%"
    assert like_contains("plain") == "%plain%"


async def _run_db() -> None:
    make_db = await _make_session()
    # list_datasets 가 집계하는 하위 테이블(태그·소유자·스키마 등)도 만든다
    async with make_db() as db:
        conn = await db.connection()

        def _create_catalog_tables(sc) -> None:
            # 공유 Base 전체 create_all 은 다른 모듈의 FK 대상이 없어 실패하므로 필요한 것만
            for model in (cm.Tag, cm.DatasetTag, cm.Owner, cm.Taxonomy, cm.Category,
                          cm.DatasetCategory):
                model.__table__.create(sc, checkfirst=True)

        await conn.run_sync(_create_catalog_tables)
        await db.commit()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        rows = {}
        for n in NAMES:
            d = cm.Dataset(urn=f"{ds.datasource_id}.{n}.dataset", name=n, datasource_id=ds.id,
                           origin="DEV", qualified_name=f"{ds.datasource_id}.{n}", status="active")
            db.add(d)
            await db.flush()
            rows[n] = d.id
        await db.commit()

        async def kw(q):
            return {n for n, i in rows.items() if i in await keyword_search_ids(db, q)}

        async def listed(q):
            page = await service.list_datasets(db, search=q, page_size=100)
            return {d.name for d in page.items}

        for search in (kw, listed):
            assert await search("%") == {"db.rate_100%"}, search.__name__        # 예전: 전체
            assert await search("a_b") == {"db.a_b"}, search.__name__              # 예전: a_b·axb
            assert await search("100%") == {"db.rate_100%"}, search.__name__      # 예전: 100%·1000
            assert await search("\\") == {"db.back\\slash"}, search.__name__
            assert await search("rate") == {"db.rate_100%", "db.rate_1000"}, search.__name__


async def _run() -> None:
    _test_pattern()
    await _run_db()


def test_search_like_escape() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_search_like_escape OK")
