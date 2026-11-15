# SPDX-License-Identifier: Apache-2.0
"""데이터셋 목록의 태그 id(쉼표 여러 개)·용어집 용어 필터 (TODO-076, in-memory SQLite).

태그 화면의 "사용 중인 데이터셋", 용어 화면의 "연결된 데이터셋", 목록 화면 태그 필터가 이 필터를 쓴다.
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
                          cm.DatasetCategory, cm.GlossaryTerm, cm.DatasetGlossaryTerm):
                model.__table__.create(sc, checkfirst=True)

        await conn.run_sync(_create_catalog_tables)
        await db.commit()

    async with make_db() as db:
        src = await service.create_datasource(db, DatasourceCreate(name="s", type="mysql"))
        ds = {}
        for n in ("a", "b", "c", "d"):
            row = cm.Dataset(urn=f"{src.datasource_id}.{n}.dataset", name=n, datasource_id=src.id,
                             origin="PROD", qualified_name=f"{src.datasource_id}.{n}",
                             status="removed" if n == "d" else "active")
            db.add(row)
            ds[n] = row
        t1, t2, t3 = cm.Tag(name="pii"), cm.Tag(name="gold"), cm.Tag(name="unused")
        term1, term2 = cm.GlossaryTerm(name="고객", term_type="TERM"), cm.GlossaryTerm(name="주문", term_type="TERM")
        db.add_all([t1, t2, t3, term1, term2])
        await db.flush()
        db.add_all([
            cm.DatasetTag(dataset_id=ds["a"].id, tag_id=t1.id),
            cm.DatasetTag(dataset_id=ds["b"].id, tag_id=t2.id),
            cm.DatasetTag(dataset_id=ds["d"].id, tag_id=t1.id),
            cm.DatasetGlossaryTerm(dataset_id=ds["a"].id, term_id=term1.id),
            cm.DatasetGlossaryTerm(dataset_id=ds["c"].id, term_id=term1.id),
        ])
        await db.commit()

        async def names(**kw) -> set[str]:
            page = await service.list_datasets(db, page_size=100, **kw)
            return {d.name for d in page.items}

        # 태그 id — 하나라도 붙어 있으면 포함, removed 는 상태 미지정이면 제외
        assert await names(tag_id=str(t1.id)) == {"a"}
        assert await names(tag_id=f"{t1.id},{t2.id}") == {"a", "b"}
        assert await names(tag_id=f" {t2.id} , ,x") == {"b"}
        assert await names(tag_id=str(t3.id)) == set()
        assert await names(tag_id=str(t1.id), status="active,removed") == {"a", "d"}
        assert await names(tag_id="") == {"a", "b", "c"}
        # 예전 이름 필터는 그대로
        assert await names(tag="gold") == {"b"}
        # 용어집 용어
        assert await names(term_id=term1.id) == {"a", "c"}
        assert await names(term_id=term2.id) == set()
        # 함께 쓰면 교집합
        assert await names(term_id=term1.id, tag_id=str(t1.id)) == {"a"}
        # 용어 목록의 연결 데이터셋 수(removed 제외, 연결 없으면 0)
        db.add(cm.DatasetGlossaryTerm(dataset_id=ds["d"].id, term_id=term1.id))
        await db.commit()
        counts = {t.name: t.dataset_count for t in await service.list_glossary_terms(db)}
        assert counts == {"고객": 2, "주문": 0}


def test_dataset_list_tag_term_filter() -> None:
    """pytest 진입점."""
    asyncio.run(_run())



async def _run_tag_counts() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        conn = await db.connection()
        await conn.run_sync(lambda sc: [m.__table__.create(sc, checkfirst=True)
                                        for m in (cm.Tag, cm.DatasetTag, cm.Owner)])
        await db.commit()
    async with make_db() as db:
        src = await service.create_datasource(db, DatasourceCreate(name="s", type="mysql"))
        rows = {n: cm.Dataset(urn=f"{src.datasource_id}.{n}.dataset", name=n, datasource_id=src.id,
                              origin="PROD", qualified_name=f"{src.datasource_id}.{n}",
                              status="removed" if n == "c" else "active") for n in ("a", "b", "c")}
        db.add_all(rows.values())
        t1, t2 = cm.Tag(name="pii"), cm.Tag(name="gold")
        db.add_all([t1, t2])
        await db.flush()
        db.add_all([cm.DatasetTag(dataset_id=rows[n].id, tag_id=t1.id) for n in ("a", "b", "c")])
        await db.commit()
        counts = {t.name: t.dataset_count for t in await service.list_tags(db)}
        # removed 데이터셋은 세지 않는다(목록 필터와 같은 기준), 안 쓰인 태그는 0
        assert counts == {"pii": 2, "gold": 0}


def test_tag_list_dataset_count() -> None:
    """태그 목록의 dataset_count (TODO-076)."""
    asyncio.run(_run_tag_counts())


if __name__ == "__main__":
    asyncio.run(_run())
    asyncio.run(_run_tag_counts())
    print("test_dataset_list_tag_term_filter OK")
