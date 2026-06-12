# SPDX-License-Identifier: Apache-2.0
"""페더레이션 관측성(stats) 테스트 (in-memory SQLite, 자기완결형).

검증: federation_stats 가 peer 별 미러 데이터셋/리니지 카운트, 최근 동기화 이력,
breaker 상태를 집계하고 전체 요약을 만든다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_federation_stats
    # 또는: pytest tests/test_federation_stats.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.federation.models as fm
from app.core.config import settings
from app.federation import breaker, service
from app.federation import schemas as s

_TABLES = [
    "federation_instances",
    "federation_datasets",
    "federation_lineage",
    "federation_sync_runs",
]


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [fm.Base.metadata.tables[t] for t in _TABLES]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: fm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _run() -> None:
    Session = await _make_session()
    breaker.reset()

    async with Session() as db:
        a = await service.create_instance(db, s.FederatedInstanceCreate(
            instance_key="team-a", name="Team A", base_url="https://a.internal", mode="HARVEST",
        ))
        b = await service.create_instance(db, s.FederatedInstanceCreate(
            instance_key="team-b", name="Team B", base_url="https://b.internal", mode="LIVE",
        ))
        a_inst = await service.get_instance(db, a.id)
        b_inst = await service.get_instance(db, b.id)

        # team-a: 미러 2 데이터셋, 1 리니지, 성공 동기화 이력
        db.add_all([
            fm.FederatedDataset(instance_id=a_inst.id, remote_urn="x.1.dataset",
                                federated_urn="team-a::x.1.dataset", name="1"),
            fm.FederatedDataset(instance_id=a_inst.id, remote_urn="x.2.dataset",
                                federated_urn="team-a::x.2.dataset", name="2"),
            fm.FederationLineage(instance_id=a_inst.id, source_urn="x.1.dataset",
                                 target_urn="x.2.dataset", relation_type="ETL"),
            fm.FederationSyncRun(instance_id=a_inst.id, status="SUCCESS",
                                 datasets_seen=2, datasets_embedded=2),
        ])
        await db.commit()

        # team-b: breaker 가 열린 상태로 시뮬레이션
        settings.federation_breaker_threshold = 1
        breaker.record_failure("team-b")
        assert breaker.is_open("team-b")

        stats = await service.federation_stats(db)

    assert stats.total_instances == 2
    assert stats.active_instances == 2
    assert stats.total_mirror_datasets == 2
    assert stats.total_mirror_lineage == 1

    by_key = {i.instance_key: i for i in stats.instances}
    a_row = by_key["team-a"]
    assert a_row.mirror_datasets == 2
    assert a_row.mirror_lineage == 1
    assert a_row.last_sync_status == "SUCCESS"
    assert a_row.last_sync_seen == 2
    assert a_row.breaker_open is False

    b_row = by_key["team-b"]
    assert b_row.mirror_datasets == 0
    assert b_row.last_sync_status is None       # 동기화 이력 없음
    assert b_row.breaker_open is True
    assert b_row.breaker_failures >= 1

    breaker.reset()


def test_federation_stats() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_federation_stats OK")
