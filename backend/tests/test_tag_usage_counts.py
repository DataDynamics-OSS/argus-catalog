# SPDX-License-Identifier: Apache-2.0
"""태그 사용처(데이터셋 목록)의 태그·오너·컬럼 수가 실제 값인지 (자기완결형, in-memory SQLite).

``get_tag_usage`` 가 예전에는 ``tag_count``·``owner_count``·``schema_field_count`` 를 0,
``is_synced`` 를 "false" 로 고정해 넣었다. 데이터셋 목록과 같은 기준으로 센다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_tag_usage_counts
"""

from __future__ import annotations

import asyncio

import app.catalog.models as cm
from app.alert.models import AlertRule
from app.catalog import service
from app.catalog.schemas import DatasourceCreate
from app.oci_hub.models import OciModel, OciModelTag
from tests.test_datasource_stage2 import _make_session


async def _run() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        conn = await db.connection()
        await conn.run_sync(lambda sc: [m.__table__.create(sc, checkfirst=True)
                                        for m in (cm.Tag, cm.DatasetTag, cm.Owner,
                                                  OciModel, OciModelTag, AlertRule)])
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        pii, tier = cm.Tag(name="pii"), cm.Tag(name="tier1")
        d = cm.Dataset(urn=f"{ds.datasource_id}.db.film.dataset", name="db.film", datasource_id=ds.id,
                       origin="DEV", qualified_name=f"{ds.datasource_id}.db.film", status="active",
                       is_synced="true")
        bare = cm.Dataset(urn=f"{ds.datasource_id}.db.e.dataset", name="db.e", datasource_id=ds.id,
                          origin="DEV", qualified_name=f"{ds.datasource_id}.db.e", status="active")
        db.add_all([pii, tier, d, bare])
        await db.flush()
        db.add_all([cm.DatasetSchema(dataset_id=d.id, field_path=f"c{i}", field_type="STRING", ordinal=i)
                     for i in range(3)])
        db.add_all([cm.DatasetTag(dataset_id=d.id, tag_id=pii.id), cm.DatasetTag(dataset_id=d.id, tag_id=tier.id),
                    cm.DatasetTag(dataset_id=bare.id, tag_id=pii.id),
                    cm.Owner(dataset_id=d.id, owner_name="kim", owner_type="TECHNICAL_OWNER")])
        await db.commit()

        usage = await service.get_tag_usage(db, pii.id)
        by_name = {x.name: x for x in usage.datasets}
        f = by_name["db.film"]
        assert (f.tag_count, f.owner_count, f.schema_field_count, f.is_synced) == (2, 1, 3, "true"), f
        e = by_name["db.e"]
        assert (e.tag_count, e.owner_count, e.schema_field_count, e.is_synced) == (1, 0, 0, "false"), e

        # 데이터셋 목록 API 와 같은 값
        listed = {x.id: x for x in (await service.list_datasets(db, tag="pii", page_size=10)).items}
        for x in usage.datasets:
            l = listed[x.id]
            assert (x.tag_count, x.owner_count, x.schema_field_count, x.is_synced) == \
                (l.tag_count, l.owner_count, l.schema_field_count, l.is_synced)

        # 사용처 없는 태그
        t = cm.Tag(name="unused")
        db.add(t)
        await db.commit()
        assert (await service.get_tag_usage(db, t.id)).datasets == []


def test_tag_usage_counts() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_tag_usage_counts OK")
