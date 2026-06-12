# SPDX-License-Identifier: Apache-2.0
"""HARVEST 주기 스케줄러.

ACTIVE 이며 미러 모드(HARVEST/HYBRID)인 peer 를 각자의 ``sync_interval_sec`` 주기로
가져온다. 마지막 SUCCESS 실행 이후 주기가 지난 peer 만 실행한다.

- tick: ``settings.federation_harvest_tick_seconds`` (기본 300초) — 실행은 최대 그만큼 지연.
- 실패는 peer 단위로 격리(harvest_instance 가 예외를 던지지 않음).
- ``federation.harvest_enabled=false`` 면 lifespan 에서 기동하지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings

logger = logging.getLogger(__name__)


async def _due_peers() -> list[int]:
    """가져오기 주기가 도래한 peer id 목록."""
    from app.core.database import async_session
    from app.federation.models import FederatedInstance, FederationSyncRun

    now = datetime.now(timezone.utc)
    due: list[int] = []
    async with async_session() as session:
        peers = (await session.execute(
            select(FederatedInstance).where(
                FederatedInstance.status == "ACTIVE",
                FederatedInstance.mode.in_(("HARVEST", "HYBRID")),
            )
        )).scalars().all()
        for peer in peers:
            last = (await session.execute(
                select(FederationSyncRun.started_at)
                .where(
                    FederationSyncRun.instance_id == peer.id,
                    FederationSyncRun.status == "SUCCESS",
                )
                .order_by(FederationSyncRun.started_at.desc())
                .limit(1)
            )).scalar()
            if last is None or (now - last).total_seconds() >= peer.sync_interval_sec:
                due.append(peer.id)
    return due


async def _harvest_one(instance_id: int) -> None:
    """단일 peer 가져오기 — 독립 세션, 예외 격리."""
    from app.core.database import async_session
    from app.federation.harvester import harvest_instance
    from app.federation.models import FederatedInstance

    async with async_session() as session:
        peer = (await session.execute(
            select(FederatedInstance).where(FederatedInstance.id == instance_id)
        )).scalars().first()
        if peer is None:
            return
        await harvest_instance(session, peer)


async def federation_harvest_loop() -> None:
    """lifespan 에서 기동되는 메인 루프."""
    tick = settings.federation_harvest_tick_seconds
    logger.info("페더레이션 HARVEST 스케줄러 시작 (tick=%ds)", tick)
    while True:
        try:
            due = await _due_peers()
            if due:
                logger.info("HARVEST 스케줄러: 주기 도래 peer %d건", len(due))
            for instance_id in due:
                await _harvest_one(instance_id)
        except asyncio.CancelledError:
            logger.info("페더레이션 HARVEST 스케줄러 중지됨")
            raise
        except Exception as e:  # noqa: BLE001 — 루프 보호
            logger.warning("HARVEST 스케줄러 tick 실패: %s", e)
        await asyncio.sleep(tick)
