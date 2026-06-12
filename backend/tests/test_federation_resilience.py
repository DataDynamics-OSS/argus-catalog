# SPDX-License-Identifier: Apache-2.0
"""페더레이션 Phase 3 단위 테스트 — circuit breaker + 증분 동기화.

자기완결형(in-memory SQLite). 검증 범위:
- breaker: threshold 도달 시 open, cooldown 경과 시 half-open, success 시 reset
- _peer_hits: 회로가 열리면 네트워크 호출 없이 CircuitOpenError
- 증분 harvest: 첫 동기화=full(+prune), 이후=watermark 기반 증분(prune 생략),
  full=True 강제 재동기화(+prune)

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_federation_resilience
    # 또는: pytest tests/test_federation_resilience.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.federation.models as fm
from app.core.config import settings
from app.federation import breaker, client, harvester, service
from app.federation import schemas as s

_TABLES = ["federation_instances", "federation_datasets", "federation_sync_runs"]


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [fm.Base.metadata.tables[t] for t in _TABLES]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: fm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


def _export_ds(urn: str, name: str) -> s.FederatedExportDataset:
    return s.FederatedExportDataset(
        urn=urn, name=name, datasource_name="mysql-1", datasource_type="mysql",
        description="d", qualified_name=f"mysql-1.{name}", origin="PROD",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        fields=[],
    )


def _test_breaker_unit() -> None:
    orig_t = settings.federation_breaker_threshold
    orig_c = settings.federation_breaker_cooldown_seconds
    breaker.reset()
    try:
        settings.federation_breaker_threshold = 2
        settings.federation_breaker_cooldown_seconds = 10

        assert not breaker.is_open("k", now=100)
        breaker.record_failure("k", now=100)            # 1
        assert not breaker.is_open("k", now=100)
        breaker.record_failure("k", now=101)            # 2 → open until 111
        assert breaker.is_open("k", now=105)
        assert breaker.is_open("k", now=110)
        assert not breaker.is_open("k", now=112)        # cooldown 경과 → half-open
        breaker.record_success("k")                     # reset
        assert not breaker.is_open("k", now=112)
    finally:
        settings.federation_breaker_threshold = orig_t
        settings.federation_breaker_cooldown_seconds = orig_c
        breaker.reset()


async def _test_peer_hits_breaker() -> None:
    orig_t = settings.federation_breaker_threshold
    breaker.reset()
    inst = fm.FederatedInstance(instance_key="p", name="P", base_url="http://p")
    calls = {"n": 0}

    async def boom(instance, query, limit=20, threshold=0.3):
        calls["n"] += 1
        raise httpx.ConnectError("unreachable")

    orig_sp = client.search_peer
    client.search_peer = boom
    try:
        settings.federation_breaker_threshold = 2
        # 2회 실패 → 회로 open
        for _ in range(2):
            try:
                await service._peer_hits(inst, "q", 20, 0.3)
            except httpx.HTTPError:
                pass
        assert calls["n"] == 2

        # 다음 호출은 네트워크 호출 없이 CircuitOpenError
        try:
            await service._peer_hits(inst, "q", 20, 0.3)
            raise AssertionError("expected CircuitOpenError")
        except breaker.CircuitOpenError:
            pass
        assert calls["n"] == 2  # client 미호출

        # 성공 경로 → breaker reset
        async def ok(instance, query, limit=20, threshold=0.3):
            return s.FederatedExportSearchResponse(items=[], total=0, query=query)

        breaker.reset()
        client.search_peer = ok
        hits = await service._peer_hits(inst, "q", 20, 0.3)
        assert hits == []
        assert not breaker.is_open("p")
    finally:
        client.search_peer = orig_sp
        settings.federation_breaker_threshold = orig_t
        breaker.reset()


async def _test_incremental_harvest() -> None:
    Session = await _make_session()
    async with Session() as db:
        peer = await service.create_instance(db, s.FederatedInstanceCreate(
            instance_key="team-a", name="Team A", base_url="https://a.internal",
            mode="HARVEST",
        ))
        peer_id = peer.id

    seen_after: list = []   # 각 호출에서 받은 updated_after 기록

    full_page = s.FederatedExportDatasetsResponse(
        items=[_export_ds("mysql-1.orders.dataset", "orders"),
               _export_ds("mysql-1.customers.dataset", "customers")],
        total=2, limit=200, offset=0,
    )

    async def fetch_full(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
        seen_after.append(updated_after)
        if offset == 0:
            return full_page
        return s.FederatedExportDatasetsResponse(items=[], total=2, limit=200, offset=offset)

    async def no_provider():
        return None

    orig_fetch = client.fetch_export_datasets
    orig_hp = harvester.get_provider
    orig_sp = service.get_provider
    client.fetch_export_datasets = fetch_full
    harvester.get_provider = no_provider
    service.get_provider = no_provider
    try:
        # 1차: watermark 없음 → full, updated_after=None, prune 가능(없음)
        async with Session() as db:
            inst = await service.get_instance(db, peer_id)
            r1 = await harvester.harvest_instance(db, inst)
        assert seen_after[0] is None
        assert r1.datasets_seen == 2 and r1.datasets_pruned == 0

        # 2차(증분): watermark = 2026-01-01. customers 가 응답에서 빠져도 prune 안 함.
        seen_after.clear()

        async def fetch_incr(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
            seen_after.append(updated_after)
            return s.FederatedExportDatasetsResponse(items=[], total=0, limit=200, offset=offset)

        client.fetch_export_datasets = fetch_incr
        async with Session() as db:
            inst = await service.get_instance(db, peer_id)
            r2 = await harvester.harvest_instance(db, inst)   # 기본 증분
        # watermark 전달 확인 (SQLite 는 tz 를 보존하지 않으므로 tz 무관 비교)
        assert seen_after[0] is not None
        assert seen_after[0].replace(tzinfo=None) == datetime(2026, 1, 1)
        assert r2.datasets_pruned == 0                        # 증분은 prune 생략
        # 미러는 여전히 2건 유지(증분이라 customers 삭제 안 됨)
        async with Session() as db:
            from sqlalchemy import func, select
            cnt = (await db.execute(
                select(func.count()).select_from(fm.FederatedDataset)
            )).scalar()
            assert cnt == 2

        # 3차(full 강제): orders 만 응답 → customers prune
        seen_after.clear()
        one_page = s.FederatedExportDatasetsResponse(
            items=[_export_ds("mysql-1.orders.dataset", "orders")],
            total=1, limit=200, offset=0,
        )

        async def fetch_one(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
            seen_after.append(updated_after)
            if offset == 0:
                return one_page
            return s.FederatedExportDatasetsResponse(items=[], total=1, limit=200, offset=offset)

        client.fetch_export_datasets = fetch_one
        async with Session() as db:
            inst = await service.get_instance(db, peer_id)
            r3 = await harvester.harvest_instance(db, inst, full=True)
        assert seen_after[0] is None          # full → updated_after 없음
        assert r3.datasets_pruned == 1        # customers 제거
    finally:
        client.fetch_export_datasets = orig_fetch
        harvester.get_provider = orig_hp
        service.get_provider = orig_sp


async def _run() -> None:
    _test_breaker_unit()
    await _test_peer_hits_breaker()
    await _test_incremental_harvest()


def test_federation_resilience() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_federation_resilience OK")
