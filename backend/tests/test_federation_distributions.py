# SPDX-License-Identifier: Apache-2.0
"""페더레이션 배포본 (TODO-096 남은 일 3) — 자기완결형, in-memory SQLite, 네트워크 호출 없음.

- 노출 항목 레지스트리에 ``distributions``(배포본, 데이터 묶음) — 노출 정책에서 빼면 상세에 없음.
- ``augment_and_filter_detail`` 이 배포본을 순서(ordinal)대로 붙임, 내부 id·시각은 내보내지 않음.
- 가져오기(``importer._copy_distributions``) — 받은 순서대로 복사, 틀린 항목은 건너뜀, 배포본이 없는 구버전 peer 는 그대로.

실행:
    cd backend
    pytest tests/test_federation_distributions.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
from app.catalog.ai_ready_export import DIST_FIELDS, list_distributions
from app.federation import capabilities as cap
from app.federation import service
from app.federation.importer import _copy_distributions

SHA = "0123456789abcdef" * 4


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for t in ("catalog_datasources", "catalog_datasets", "catalog_dataset_schemas",
                  "catalog_dataset_distribution"):
            await conn.run_sync(lambda sc, t=t: cm.Base.metadata.tables[t].create(sc, checkfirst=True))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed(db) -> int:
    ds = cm.Datasource(datasource_id="d1", name="ds", type="mysql", origin="PROD")
    db.add(ds)
    await db.flush()
    d = cm.Dataset(urn="d1.t.dataset", name="t", datasource_id=ds.id, origin="PROD", status="active")
    db.add(d)
    await db.flush()
    db.add_all([
        cm.DatasetDistribution(dataset_id=d.id, title="둘째", format="JSON", access_url="https://x.go.kr/api",
                               ordinal=1),
        cm.DatasetDistribution(dataset_id=d.id, title="첫째", format="CSV", access_url="https://x.go.kr/f",
                               download_url="https://x.go.kr/f.csv", byte_size=2048,
                               checksum_algorithm="SHA-256", checksum=SHA, ordinal=0),
    ])
    await db.commit()
    return d.id


def test_registry_has_distributions():
    assert "distributions" in cap.all_keys()
    item = next(i for i in cap.registry_items() if i["key"] == "distributions")
    assert item["group"] == "data" and item["label"] == "배포본"
    assert "distributions" in cap.default_exposed()
    md = {"dataset_id": 1, "urn": "u", "name": "n", "distributions": [{"access_url": "https://x"}]}
    assert cap.filter_metadata(md, ["distributions"])["distributions"] == md["distributions"]
    assert "distributions" not in cap.filter_metadata(md, ["description"])


def test_export_detail_distributions():
    async def run():
        Session = await _make_session()
        async with Session() as db:
            ds_id = await _seed(db)
            base = {"dataset_id": ds_id, "urn": "d1.t.dataset", "name": "t"}
            out = await service.augment_and_filter_detail(db, dict(base), ["distributions"])
            dists = out["distributions"]
            assert [d["title"] for d in dists] == ["첫째", "둘째"]           # ordinal 순
            assert set(dists[0]) == set(DIST_FIELDS)                       # id·dataset_id·시각 없음
            assert dists[0]["byte_size"] == 2048 and dists[0]["checksum"] == SHA
            # 노출 정책에서 빼면 조회도 하지 않고 응답에도 없음
            out2 = await service.augment_and_filter_detail(db, dict(base), ["description"])
            assert "distributions" not in out2
    asyncio.run(run())


def test_import_copies_distributions():
    async def run():
        Session = await _make_session()
        async with Session() as db:
            src_id = await _seed(db)
            ds = (await db.execute(select(cm.Datasource))).scalars().first()
            dst = cm.Dataset(urn="d1.copy.dataset", name="copy", datasource_id=ds.id, origin="PROD", status="active")
            db.add(dst)
            await db.commit()

            exported = (await service.augment_and_filter_detail(
                db, {"dataset_id": src_id, "urn": "u", "name": "t"}, ["distributions"],
            ))["distributions"]
            peer_items = [
                *exported,
                {"title": "주소 틀림", "access_url": "ftp://x"},              # 검사 실패 → 건너뜀
                {"title": "체크섬 틀림", "access_url": "https://x.go.kr/z", "checksum": "abc"},
                {"title": 123, "access_url": "https://x.go.kr/n", "byte_size": "10", "extra": "무시"},
                "문자열은 무시",
            ]
            n = await _copy_distributions(db, dst.id, peer_items)
            await db.commit()
            assert n == 3
            rows = await list_distributions(db, dst.id)
            assert [r.title for r in rows] == ["첫째", "둘째", None]          # 받은 순서 = ordinal
            assert [r.ordinal for r in rows] == [0, 1, 2]
            assert rows[0].download_url == "https://x.go.kr/f.csv" and rows[0].checksum == SHA
            assert rows[2].byte_size is None                               # 타입이 틀린 값은 비움

            # 배포본을 보내지 않는 구버전 peer · 노출 정책에서 뺀 peer
            assert await _copy_distributions(db, dst.id, None) == 0
            assert await _copy_distributions(db, dst.id, {"x": 1}) == 0
    asyncio.run(run())
