# SPDX-License-Identifier: Apache-2.0
"""카탈로그 페더레이션 단위 테스트 (자기완결형, in-memory SQLite).

외부 DB/peer 없이 federation_instances 테이블만 메모리 SQLite 에 생성해
peer 레지스트리 CRUD 와 scatter-gather 통합 검색의 병합/degrade 로직을 검증한다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_federation
    # 또는: pytest tests/test_federation.py
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.federation.models as fm
from app.federation import client, service
from app.federation import schemas as s
from app.federation.schemas import (
    FederatedDatasetHit,
    FederatedExportSearchResponse,
)
from app.search import service as search_service


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    table = fm.Base.metadata.tables["federation_instances"]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: fm.Base.metadata.create_all(sc, tables=[table]))
    return async_sessionmaker(engine, expire_on_commit=False)


def _hit(urn: str, name: str, score: float) -> FederatedDatasetHit:
    return FederatedDatasetHit(
        urn=urn, name=name, datasource_name="ds", datasource_type="mysql",
        description=None, origin="PROD", score=score, match_type="hybrid",
    )


async def _run() -> None:
    Session = await _make_session()

    # --- peer 레지스트리 CRUD ---
    async with Session() as db:
        created = await service.create_instance(db, s.FederatedInstanceCreate(
            instance_key="team-payments",
            name="Payments Catalog",
            base_url="https://catalog.payments.internal/",
            auth_token="secret-xyz",
            mode="LIVE",
        ))
        assert created.instance_key == "team-payments"
        # 토큰은 응답에 노출되지 않고 보유 여부만 표시
        assert created.has_auth_token is True
        assert not hasattr(created, "auth_token")

        listed = await service.list_instances(db)
        assert len(listed) == 1

        inst = await service.get_instance(db, created.id)
        assert inst is not None and inst.auth_token == "secret-xyz"

        updated = await service.update_instance(
            db, inst, s.FederatedInstanceUpdate(status="PAUSED"),
        )
        assert updated.status == "PAUSED"

        # PAUSED 는 ACTIVE 목록에서 제외
        assert await service.list_active_peers(db) == []

        await service.update_instance(db, inst, s.FederatedInstanceUpdate(status="ACTIVE"))
        assert len(await service.list_active_peers(db)) == 1

    # --- scatter-gather 통합 검색: 로컬 + 정상 peer + 실패 peer ---
    async with Session() as db:
        # peer 2개 추가 (정상 1, 실패 1)
        await service.create_instance(db, s.FederatedInstanceCreate(
            instance_key="team-risk", name="Risk", base_url="https://risk.internal",
        ))
        # team-payments 는 위에서 ACTIVE 로 복구됨 → 총 ACTIVE peer 2개

        # 로컬 hybrid_search → (dataset_id, score, match_type)
        async def fake_hybrid(session, query, limit=20, threshold=0.3):
            return [(1, 0.9, "hybrid")]

        async def fake_summary(session, dataset_id):
            return SimpleNamespace(
                urn="local-mysql.db.local_tbl.dataset", name="local_tbl",
                datasource_name="local-mysql", datasource_type="mysql",
                description="local", origin="PROD",
            )

        # peer 호출: team-payments 는 정상, team-risk 는 네트워크 오류
        async def fake_search_peer(instance, query, limit=20, threshold=0.3):
            if instance.instance_key == "team-payments":
                return FederatedExportSearchResponse(
                    items=[_hit("team-payments::mysql.pay.txn.dataset", "txn", 0.95)],
                    total=1, query=query,
                )
            raise client.httpx.ConnectError("unreachable")

        orig_hybrid = search_service.hybrid_search
        orig_summary = search_service._build_dataset_summary
        orig_peer = client.search_peer
        search_service.hybrid_search = fake_hybrid
        search_service._build_dataset_summary = fake_summary
        client.search_peer = fake_search_peer
        try:
            resp = await service.federated_search(db, "txn", limit=10)
        finally:
            search_service.hybrid_search = orig_hybrid
            search_service._build_dataset_summary = orig_summary
            client.search_peer = orig_peer

        # 로컬 + 2 peer 시도 = 3
        assert resp.instances_queried == 3
        # team-risk 는 실패로 보고, 나머지로 degrade
        assert resp.instances_failed == ["team-risk"]
        # 정상 결과 2건(로컬 1 + payments 1), 점수 내림차순 병합 → payments(0.95) 먼저
        assert resp.total == 2
        assert resp.items[0].source_instance_key == "team-payments"
        assert resp.items[0].score == 0.95
        assert resp.items[1].source_instance_key is None  # 로컬

        # include_local=False 면 로컬 제외 (peer 2 시도, 1 실패 → 1건)
        search_service.hybrid_search = fake_hybrid
        search_service._build_dataset_summary = fake_summary
        client.search_peer = fake_search_peer
        try:
            resp2 = await service.federated_search(db, "txn", include_local=False)
        finally:
            search_service.hybrid_search = orig_hybrid
            search_service._build_dataset_summary = orig_summary
            client.search_peer = orig_peer
        assert resp2.instances_queried == 2
        assert resp2.total == 1
        assert resp2.items[0].source_instance_key == "team-payments"

    # --- 삭제 ---
    async with Session() as db:
        peers = await service.list_active_peers(db)
        for p in peers:
            await service.delete_instance(db, p)
        assert await service.list_instances(db) == []


def test_federation_lifecycle() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_federation OK")
