# SPDX-License-Identifier: Apache-2.0
"""페더레이션 Phase 1(HARVEST) 단위 테스트 (자기완결형, in-memory SQLite).

pgvector/임베딩 제공자 없이 검증 가능한 범위:
- build_source_text (재임베딩 입력 구성)
- harvest_instance: upsert + federated_urn 네임스페이싱 + prune (provider 없음 → 재임베딩 skip)
- search_mirror: 임베딩 제공자 없음 → 키워드 폴백 경로
- federated_search: HARVEST peer 를 미러로 라우팅
- verify_export_token: 서비스 토큰 인증

시맨틱(pgvector) 경로는 PostgreSQL 통합 테스트에서 별도 검증한다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_federation_harvest
    # 또는: pytest tests/test_federation_harvest.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.federation.models as fm
from app.core.config import settings
from app.federation import harvester, service
from app.federation import schemas as s
from app.federation.auth import verify_export_token
from app.search import service as search_service

# 메모리 SQLite 에 만들 테이블 (Vector 컬럼이 있는 embedding 테이블은 제외 —
# provider 가 없어 재임베딩 경로가 타지 않으므로 불필요)
_TABLES = ["federation_instances", "federation_datasets", "federation_sync_runs"]


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [fm.Base.metadata.tables[t] for t in _TABLES]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: fm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


def _export_ds(urn: str, name: str, desc: str = "") -> s.FederatedExportDataset:
    return s.FederatedExportDataset(
        urn=urn, name=name, datasource_name="mysql-1", datasource_type="mysql",
        summary=None, description=desc, qualified_name=f"mysql-1.{name}",
        origin="PROD", updated_at=datetime.now(timezone.utc),
        fields=[s.FederatedExportField(field_path="id", field_type="int",
                                       description="식별자")],
    )


def _test_build_source_text() -> None:
    ds = _export_ds("u1", "orders", "주문 테이블")
    text = harvester.build_source_text(ds)
    assert "orders" in text and "주문 테이블" in text
    assert "schema: id int -- 식별자" in text


async def _test_harvest_and_search() -> None:
    Session = await _make_session()

    # peer 등록 (HARVEST 모드)
    async with Session() as db:
        peer = await service.create_instance(db, s.FederatedInstanceCreate(
            instance_key="team-a", name="Team A", base_url="https://a.internal",
            mode="HARVEST",
        ))
        peer_id = peer.id

    # 제공자 없음 → 재임베딩 skip. client.fetch_export_datasets 모킹.
    page1 = s.FederatedExportDatasetsResponse(
        items=[_export_ds("mysql-1.orders.dataset", "orders", "주문"),
               _export_ds("mysql-1.customers.dataset", "customers", "고객")],
        total=2, limit=200, offset=0,
    )

    async def fake_fetch(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
        if offset == 0:
            return page1
        return s.FederatedExportDatasetsResponse(items=[], total=2, limit=200, offset=offset)

    async def no_provider():
        return None

    from app.federation import client as fed_client
    orig_fetch = fed_client.fetch_export_datasets
    orig_hp = harvester.get_provider
    orig_sp = service.get_provider
    fed_client.fetch_export_datasets = fake_fetch
    harvester.get_provider = no_provider
    service.get_provider = no_provider
    try:
        # 1차 가져오기 — 2건 upsert
        async with Session() as db:
            inst = await service.get_instance(db, peer_id)
            result = await harvester.harvest_instance(db, inst)
        assert result.status == "SUCCESS"
        assert result.datasets_seen == 2
        assert result.datasets_upserted == 2
        assert result.datasets_embedded == 0      # provider 없음
        assert result.datasets_pruned == 0

        # federated_urn 네임스페이싱 확인
        async with Session() as db:
            from sqlalchemy import select
            urns = sorted((await db.execute(
                select(fm.FederatedDataset.federated_urn)
            )).scalars().all())
            assert urns == [
                "team-a::mysql-1.customers.dataset",
                "team-a::mysql-1.orders.dataset",
            ]

        # 2차 가져오기 — customers 가 사라짐 → prune 1, 변경 없는 orders 는 upsert 카운트 안 됨
        page2 = s.FederatedExportDatasetsResponse(
            items=[_export_ds("mysql-1.orders.dataset", "orders", "주문")],
            total=1, limit=200, offset=0,
        )

        async def fake_fetch2(instance, limit=200, offset=0, updated_after=None, timeout=30.0):
            if offset == 0:
                return page2
            return s.FederatedExportDatasetsResponse(items=[], total=1, limit=200, offset=offset)

        fed_client.fetch_export_datasets = fake_fetch2
        async with Session() as db:
            inst = await service.get_instance(db, peer_id)
            # full=True 로 전체 재동기화 — prune 검증(증분은 prune 생략)
            result2 = await harvester.harvest_instance(db, inst, full=True)
        assert result2.datasets_seen == 1
        assert result2.datasets_pruned == 1
        assert result2.datasets_upserted == 0      # orders source_text 불변

        # 미러 키워드 검색 (provider 없음 → 키워드 폴백)
        async with Session() as db:
            hits = await service.search_mirror(db, "orders")
            assert len(hits) == 1
            assert hits[0].source_instance_key == "team-a"
            assert hits[0].urn == "team-a::mysql-1.orders.dataset"
            assert hits[0].match_type == "keyword"

        # federated_search: HARVEST peer → 미러 경로 (LIVE fan-out 아님)
        async def fake_local(session, query, limit=20, threshold=0.3):
            return []  # 로컬 결과 없음

        orig_hybrid = search_service.hybrid_search
        search_service.hybrid_search = fake_local
        try:
            async with Session() as db:
                resp = await service.federated_search(db, "orders")
        finally:
            search_service.hybrid_search = orig_hybrid

        assert resp.instances_failed == []          # 미러는 fan-out 실패 대상 아님
        assert resp.instances_queried == 2          # 로컬 + 미러 커버 peer 1
        assert resp.total == 1
        assert resp.items[0].source_instance_key == "team-a"
    finally:
        fed_client.fetch_export_datasets = orig_fetch
        harvester.get_provider = orig_hp
        service.get_provider = orig_sp


def _make_request(headers: dict[str, str]):
    from starlette.requests import Request
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw})


async def _test_export_token_auth() -> None:
    from fastapi import HTTPException

    orig = settings.federation_export_token
    try:
        # 토큰 미설정 → 항상 통과
        settings.federation_export_token = ""
        await verify_export_token(_make_request({}))

        # 토큰 설정 → 일치해야 통과
        settings.federation_export_token = "s3cr3t"
        await verify_export_token(_make_request({"Authorization": "Bearer s3cr3t"}))

        # 누락 → 401
        for bad in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "s3cr3t"}):
            try:
                await verify_export_token(_make_request(bad))
                raise AssertionError(f"expected 401 for {bad}")
            except HTTPException as e:
                assert e.status_code == 401
    finally:
        settings.federation_export_token = orig


async def _run() -> None:
    _test_build_source_text()
    await _test_harvest_and_search()
    await _test_export_token_auth()


def test_federation_harvest() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_federation_harvest OK")
