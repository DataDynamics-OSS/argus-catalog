# SPDX-License-Identifier: Apache-2.0
"""MCP 배포 주기 작업 스케줄러 (Phase 2).

배포마다 ``collect_interval_minutes`` (0/NULL = 주기 수집 끔) 가 지나면 수집하고,
주기 수집을 켠 배포는 매 tick 헬스 체크도 한다(연속 실패 → ``unhealthy`` finding).
하루 한 번 감사 이벤트 보존 기간 정리와 전체 finding 재계산(감사 위반 7일 창 만료 반영)을 한다.

- tick: 5분 (TICK_SECONDS) — 실행은 최대 그만큼 늦을 수 있다.
- ``ARGUS_MCP_SCHEDULER=off`` 로 끌 수 있다.
- 배포 단위로 세션·예외를 격리한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

logger = logging.getLogger(__name__)

TICK_SECONDS = 300
DAILY = timedelta(days=1)
# 마지막 일일 작업 실행 시각(프로세스 메모리 — 재기동하면 첫 tick 에 다시 실행)
_last_daily: datetime | None = None


def enabled() -> bool:
    """``ARGUS_MCP_SCHEDULER`` 가 off/false/0 이 아니면 스케줄러 사용(기본 on)."""
    return os.environ.get("ARGUS_MCP_SCHEDULER", "on").lower() not in ("off", "false", "0")


def _aware(dt: datetime | None) -> datetime | None:
    """시간대 없는 datetime 을 UTC 로 간주해 aware 로 맞춘다."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _due() -> tuple[list[str], list[str]]:
    """(수집 대상, 헬스 체크 대상) 배포 이름.

    주기 수집이 켜진(>0) blocked/retired 아닌 배포 중 stdio·접속 정보 없는 것은 제외한다.
    수집 주기가 지났으면 수집 대상, 아니면 MCP endpoint 가 있을 때만 헬스 체크 대상.
    """
    from app.core.database import async_session
    from app.mcp_servers.models import McpDeployment

    now = datetime.now(timezone.utc)
    collect, health = [], []
    async with async_session() as session:
        rows = (await session.execute(
            select(McpDeployment.name, McpDeployment.collect_interval_minutes, McpDeployment.last_observed_at,
                   McpDeployment.transport, McpDeployment.endpoint, McpDeployment.rest_api_base)
            .where(McpDeployment.status.not_in(("retired", "blocked")),
                   McpDeployment.collect_interval_minutes > 0)
        )).all()
    for name, interval, last, transport, endpoint, rest in rows:
        if transport == "stdio" or not (endpoint or rest):
            continue
        last = _aware(last)
        if last is None or now - last >= timedelta(minutes=interval):
            collect.append(name)
        elif endpoint:
            health.append(name)
    logger.debug("MCP 스케줄러 대상 산출: candidates=%d collect=%d health=%d", len(rows), len(collect), len(health))
    return collect, health


async def run_collect(name: str) -> None:
    """배포 한 건 주기 수집(전용 세션·예외 격리). 수집이 실패하면 헬스 체크도 돌려 실패 횟수에 반영한다."""
    from app.core.database import async_session
    from app.mcp_servers.schemas import CollectRequest
    from app.mcp_servers.service import collect_deployment

    async with async_session() as session:
        try:
            r = await collect_deployment(session, name, CollectRequest(), by="scheduler")
            if r and not r.ok:
                # 수집 실패도 헬스 실패로 센다
                logger.debug("MCP 주기 수집 결과 실패 — 헬스 체크로 확인: deployment=%s message=%s", name, r.message)
                await run_health(name)
        except Exception as e:  # noqa: BLE001 — 격리
            await session.rollback()
            logger.warning("MCP 주기 수집 실패: %s, %s", name, e)


async def run_health(name: str) -> None:
    """배포 한 건 주기 헬스 체크(전용 세션·예외 격리)."""
    from app.core.database import async_session
    from app.mcp_servers.service import health_check

    async with async_session() as session:
        try:
            await health_check(session, name, by="scheduler")
        except Exception as e:  # noqa: BLE001
            await session.rollback()
            logger.warning("MCP 주기 헬스 체크 실패: %s, %s", name, e)


async def run_daily() -> dict:
    """감사 이벤트 보존 정리 + 전체 finding 재계산(retired 제외, 배포 단위로 커밋·롤백).

    반환: ``{"evaluated": 재계산한 배포 수, "purged": 삭제한 감사 이벤트 수}``.
    """
    logger.info("MCP 일일 작업 시작")
    from app.core.database import async_session
    from app.mcp_servers.models import McpAuditEvent, McpDeployment
    from app.mcp_servers.policy import evaluate_findings

    purged = evaluated = 0
    async with async_session() as session:
        deps = (await session.execute(
            select(McpDeployment).where(McpDeployment.status != "retired")
        )).scalars().all()
        now = datetime.now(timezone.utc)
        for dep in deps:
            try:
                cutoff = now - timedelta(days=dep.audit_retention_days or 180)
                purged += (await session.execute(
                    delete(McpAuditEvent).where(McpAuditEvent.deployment_id == dep.id, McpAuditEvent.ts < cutoff)
                )).rowcount or 0
                await evaluate_findings(session, dep)
                await session.commit()
                evaluated += 1
            except Exception as e:  # noqa: BLE001
                await session.rollback()
                logger.warning("MCP 일일 작업 실패: %s, %s", dep.name, e)
    logger.info("MCP 일일 작업: 배포 %d개 finding 재계산, 감사 이벤트 %d건 정리", evaluated, purged)
    return {"evaluated": evaluated, "purged": purged}


async def run_enforce_check() -> int:
    """enforce 배포의 정책 번들 적용 상태(policy_not_applied) 재계산 — pull 이 멈춘 것은 시간이 지나야 드러난다."""
    from app.core.database import async_session
    from app.mcp_servers.models import McpDeployment
    from app.mcp_servers.policy import evaluate_findings

    n = 0
    async with async_session() as session:
        deps = (await session.execute(
            select(McpDeployment).where(McpDeployment.policy_mode == "enforce", McpDeployment.status != "retired")
        )).scalars().all()
        for dep in deps:
            try:
                await evaluate_findings(session, dep)
                await session.commit()
                n += 1
            except Exception as e:  # noqa: BLE001
                await session.rollback()
                logger.warning("MCP enforce 상태 점검 실패: %s, %s", dep.name, e)
    logger.debug("MCP enforce 상태 점검 완료: deployments=%d evaluated=%d", len(deps), n)
    return n


async def tick() -> None:
    """한 주기 작업 — 수집·헬스 체크(배포 순차), enforce 점검, 하루 한 번 일일 작업."""
    global _last_daily
    collect, health = await _due()
    if collect:
        logger.info("MCP 스케줄러: 주기 수집 %d건", len(collect))
    if health:
        logger.debug("MCP 스케줄러: 헬스 체크 %d건", len(health))
    for name in collect:
        await run_collect(name)
    for name in health:
        await run_health(name)
    await run_enforce_check()
    now = datetime.now(timezone.utc)
    if _last_daily is None or now - _last_daily >= DAILY:
        _last_daily = now
        await run_daily()


async def mcp_scheduler_loop() -> None:
    """lifespan 에서 기동되는 메인 루프."""
    logger.info("MCP 스케줄러 시작 (tick=%ds)", TICK_SECONDS)
    # 기동 직후 한꺼번에 몰리지 않도록 60초 쉬고 시작
    await asyncio.sleep(60)
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            logger.info("MCP 스케줄러 중지됨")
            raise
        except Exception as e:  # noqa: BLE001 — 루프 보호
            logger.warning("MCP 스케줄러 tick 실패: %s", e)
        await asyncio.sleep(TICK_SECONDS)
