# SPDX-License-Identifier: Apache-2.0
"""페더레이션 성능·정확성 보강 테스트 (in-memory SQLite, 자기완결형).

검증:
- circuit breaker: half-open 에서 프로브 1건만 통과, 4xx 는 장애로 세지 않음
- 미러 배치 재임베딩: 묶음 호출, 변경 없으면 건너뜀, 임베딩 모델이 바뀌면 전량 재임베딩
- 리니지 그래프: frontier 조회로도 마지막 단계 노드끼리의 엣지를 포함
- 키워드 검색 LIKE 와일드카드 이스케이프

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_federation_perf
    # 또는: pytest tests/test_federation_perf.py
"""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
import app.federation.models as fm
from app.core.config import settings
from app.federation import breaker, harvester, service

_TABLES = [
    "catalog_datasources", "catalog_datasets", "argus_dataset_lineage",
    "federation_instances", "federation_datasets", "federation_dataset_embeddings",
    "federation_lineage",
]


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [cm.Base.metadata.tables[t] for t in _TABLES]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


def _test_breaker_half_open_single_probe() -> None:
    orig_t = settings.federation_breaker_threshold
    orig_c = settings.federation_breaker_cooldown_seconds
    breaker.reset()
    try:
        settings.federation_breaker_threshold = 1
        settings.federation_breaker_cooldown_seconds = 10
        breaker.record_failure("k", now=100)                 # open until 110
        assert breaker.is_open("k", now=105)
        assert not breaker.is_open("k", now=111)             # 프로브 1건 허용
        assert breaker.is_open("k", now=111.5)               # 프로브 진행 중 → 나머지 차단
        breaker.record_failure("k", now=112)                 # 프로브 실패 → 다시 open
        assert breaker.is_open("k", now=115)
        assert not breaker.is_open("k", now=123)             # 다음 cooldown 후 새 프로브
        breaker.record_success("k")                          # 프로브 성공 → closed
        assert not breaker.is_open("k", now=123.1)
        assert not breaker.is_open("k", now=123.2)
    finally:
        settings.federation_breaker_threshold = orig_t
        settings.federation_breaker_cooldown_seconds = orig_c
        breaker.reset()


def _http_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "http://p/x")
    return httpx.HTTPStatusError("err", request=req, response=httpx.Response(code, request=req))


async def _test_breaker_ignores_4xx() -> None:
    orig_t = settings.federation_breaker_threshold
    breaker.reset()
    inst = fm.FederatedInstance(instance_key="p", name="P", base_url="http://p")
    try:
        settings.federation_breaker_threshold = 2
        assert breaker.is_peer_fault(_http_error(503))
        assert breaker.is_peer_fault(_http_error(429))
        assert breaker.is_peer_fault(httpx.ConnectError("down"))
        assert not breaker.is_peer_fault(_http_error(404))

        async def not_found():
            raise _http_error(404)

        for _ in range(5):                                   # 샘플 없는 데이터셋 반복 조회
            try:
                await service._call_peer_with_breaker(inst, not_found)
            except httpx.HTTPStatusError:
                pass
        assert not breaker.is_open("p")                      # 404 로는 회로가 열리지 않음
    finally:
        settings.federation_breaker_threshold = orig_t
        breaker.reset()


class _FakeProvider:
    def __init__(self, model: str, dim: int = 384) -> None:
        self.model, self.dim, self.calls = model, dim, []

    def model_name(self) -> str:
        return self.model

    def provider_name(self) -> str:
        return "fake"

    def dimension(self) -> int:
        return self.dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        return [[0.1] * self.dim for _ in texts]


async def _test_batch_embedding_and_model_change() -> None:
    session_factory = await _make_session()
    async with session_factory() as db:
        inst = fm.FederatedInstance(instance_key="t", name="t", base_url="http://t", mode="HARVEST")
        db.add(inst)
        await db.commit()
        rows = []
        for i in range(5):
            r = fm.FederatedDataset(
                instance_id=inst.id, remote_urn=f"u{i}", federated_urn=f"t::u{i}",
                name=f"n{i}", source_text=f"text {i}",
            )
            db.add(r)
            rows.append(r)
        await db.commit()
        items = [(r.id, r.source_text) for r in rows]

        p1 = _FakeProvider("model-a")
        assert await harvester._embed_mirror_batch(db, p1, items) == 5
        await db.commit()
        assert p1.calls == [5]                               # 한 번의 묶음 호출

        assert await harvester._embed_mirror_batch(db, p1, items) == 0   # 변경 없음 → 건너뜀
        assert p1.calls == [5]

        # 한 건만 원문 변경 → 그 건만 재임베딩
        changed = [(fid, t + " v2") if i == 0 else (fid, t) for i, (fid, t) in enumerate(items)]
        assert await harvester._embed_mirror_batch(db, p1, changed) == 1
        await db.commit()

        # 임베딩 모델 교체 → 원문이 같아도 전량 재임베딩
        p2 = _FakeProvider("model-b")
        assert await harvester._embed_mirror_batch(db, p2, changed) == 5
        await db.commit()
        models = set((await db.execute(
            select(fm.FederatedDatasetEmbedding.model_name)
        )).scalars().all())
        assert models == {"model-b"}


async def _test_lineage_includes_last_level_edges() -> None:
    session_factory = await _make_session()
    async with session_factory() as db:
        ds = cm.Datasource(datasource_id="hub", name="hub", type="mysql", origin="PROD")
        db.add(ds)
        await db.commit()
        nodes = {}
        for n in ("R", "A", "B"):
            d = cm.Dataset(urn=f"hub.{n}.dataset", name=n, datasource_id=ds.id,
                           status="active", origin="PROD")
            db.add(d)
            nodes[n] = d
        await db.commit()
        # R→A, R→B, A→B : depth=1 에서 A-B 는 마지막 단계 노드끼리의 엣지
        for a, b in (("R", "A"), ("R", "B"), ("A", "B")):
            db.add(cm.DatasetLineage(
                source_dataset_id=nodes[a].id, target_dataset_id=nodes[b].id,
                relation_type="READ_WRITE",
            ))
        await db.commit()

        g = await service.build_lineage_graph(db, "hub.R.dataset", depth=1)
        pairs = {(e.source_urn, e.target_urn) for e in g.edges}
        assert pairs == {
            ("hub.R.dataset", "hub.A.dataset"),
            ("hub.R.dataset", "hub.B.dataset"),
            ("hub.A.dataset", "hub.B.dataset"),
        }
        g0 = await service.build_lineage_graph(db, "hub.R.dataset", depth=0)
        assert g0.edges == [] and [n.urn for n in g0.nodes] == ["hub.R.dataset"]


async def _test_keyword_like_escape() -> None:
    session_factory = await _make_session()
    async with session_factory() as db:
        inst = fm.FederatedInstance(instance_key="t", name="t", base_url="http://t", mode="HARVEST")
        db.add(inst)
        await db.commit()
        for name in ("order_items", "orderXitems", "100% done"):
            db.add(fm.FederatedDataset(
                instance_id=inst.id, remote_urn=name, federated_urn=f"t::{name}", name=name,
            ))
        await db.commit()

        hits = await service._mirror_keyword_ids(db, "order_items", 10)
        names = set((await db.execute(
            select(fm.FederatedDataset.name).where(fm.FederatedDataset.id.in_(hits))
        )).scalars().all())
        assert names == {"order_items"}                      # _ 가 임의 1글자로 매칭되지 않음

        hits = await service._mirror_keyword_ids(db, "%", 10)
        names = set((await db.execute(
            select(fm.FederatedDataset.name).where(fm.FederatedDataset.id.in_(hits))
        )).scalars().all())
        assert names == {"100% done"}

        inst_row = await service.get_instance(db, inst.id)
        browse = await service.browse_instance_datasets(db, inst_row, q="r_i")
        found = {d.name for g in browse.datasources for d in g.datasets}
        assert found == {"order_items"}


async def _run() -> None:
    _test_breaker_half_open_single_probe()
    await _test_breaker_ignores_4xx()
    await _test_batch_embedding_and_model_change()
    await _test_lineage_includes_last_level_edges()
    await _test_keyword_like_escape()


def test_federation_perf() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_federation_perf OK")
