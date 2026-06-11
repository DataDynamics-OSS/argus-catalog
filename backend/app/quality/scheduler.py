"""품질 검증 주기 실행 스케줄러.

데이터셋 property ``argus.quality_schedule``(hourly/daily/weekly)가 설정된
데이터셋을 주기적으로 확인해, 마지막 점수 기록(scored_at) 이후 주기가
지났으면 서버 검증(run_quality_check)을 자동 실행한다.

- 검사 주기: 10분 (TICK_SECONDS) — 실행 시점은 최대 그만큼 지연될 수 있다.
- 서버 검증은 프로파일 기반이므로 가볍다. 전체 데이터 평가(CUSTOM_* 포함)가
  필요하면 quality/*.py 배치를 외부 스케줄러(cron/Airflow)에 등록할 것.
- 실패는 데이터셋 단위로 격리 — 한 건의 오류가 다른 검증을 막지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

logger = logging.getLogger(__name__)

QUALITY_SCHEDULE_PROPERTY = "argus.quality_schedule"
TICK_SECONDS = 600  # 10분

INTERVALS: dict[str, timedelta] = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}


async def _due_dataset_ids() -> list[int]:
    """주기가 도래한 데이터셋 ID 목록."""
    from app.catalog.models import DatasetProperty
    from app.core.database import async_session
    from app.quality.models import QualityScore

    now = datetime.now(timezone.utc)
    due: list[int] = []
    async with async_session() as session:
        rows = (await session.execute(
            select(DatasetProperty.dataset_id, DatasetProperty.property_value).where(
                DatasetProperty.property_key == QUALITY_SCHEDULE_PROPERTY,
            )
        )).all()
        for dataset_id, value in rows:
            interval = INTERVALS.get((value or "").strip())
            if not interval:
                continue
            last = (await session.execute(
                select(QualityScore.scored_at)
                .where(QualityScore.dataset_id == dataset_id)
                .order_by(QualityScore.scored_at.desc())
                .limit(1)
            )).scalar()
            if last is None or (now - last) >= interval:
                due.append(dataset_id)
    return due


async def _run_one(dataset_id: int) -> None:
    """단일 데이터셋 검증 — 독립 세션, 예외 격리."""
    from app.core.database import async_session
    from app.quality.service import run_quality_check

    async with async_session() as session:
        try:
            resp = await run_quality_check(session, dataset_id)
            await session.commit()
            logger.info("예약 품질 검증: dataset_id=%d, score=%.1f%%",
                        dataset_id, resp.score)
        except Exception as e:  # noqa: BLE001 — 격리
            await session.rollback()
            logger.warning("예약 품질 검증 실패: dataset_id=%d, %s", dataset_id, e)


async def quality_scheduler_loop() -> None:
    """lifespan 에서 기동되는 메인 루프."""
    logger.info("품질 스케줄러 시작 (tick=%ds)", TICK_SECONDS)
    while True:
        try:
            due = await _due_dataset_ids()
            if due:
                logger.info("품질 스케줄러: 주기 도래 데이터셋 %d건", len(due))
            for dataset_id in due:
                await _run_one(dataset_id)
        except asyncio.CancelledError:
            logger.info("품질 스케줄러 중지됨")
            raise
        except Exception as e:  # noqa: BLE001 — 루프 보호
            logger.warning("품질 스케줄러 tick 실패: %s", e)
        await asyncio.sleep(TICK_SECONDS)
