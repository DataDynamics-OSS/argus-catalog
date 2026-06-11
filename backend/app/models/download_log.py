"""모델 다운로드 로그 기록 및 사용량 통계.

모델 다운로드 이벤트(``model_download_logs`` 테이블)를 적재하고, 일/주/월
단위 집계를 대시보드용으로 제공한다. 차트는 보통 최근 30일을 ``date``
단위로 그룹핑한 ``(date, count)`` 시퀀스를 사용한다.

로깅 정책: 로그 적재 자체는 빈도가 매우 높아 INFO 를 생략하고, 일관성을
깨뜨릴 수 있는 외래 키 누락(예: 존재하지 않는 모델·버전 참조) 정도만
WARNING 으로 기록한다.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import ModelDownloadLog, ModelVersion

logger = logging.getLogger(__name__)


async def log_download(
    session: AsyncSession,
    model_name: str,
    version: int,
    download_type: str,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """모델 다운로드 이벤트를 기록한다.

    user_agent 는 500자로 잘라낸다. 커밋 실패는 로깅하되 전파하지 않는다 —
    다운로드 로깅이 주 요청 흐름을 깨뜨려서는 안 된다.
    """
    try:
        entry = ModelDownloadLog(
            model_name=model_name,
            version=version,
            download_type=download_type,
            client_ip=client_ip,
            user_agent=user_agent[:500] if user_agent and len(user_agent) > 500 else user_agent,
        )
        session.add(entry)
        await session.commit()
        logger.info("다운로드 로깅: %s v%d (%s), 출처 %s", model_name, version, download_type, client_ip)
    except Exception as e:
        logger.warning("다운로드 로깅 실패 %s v%d: %s", model_name, version, e)


async def get_total_download_count(session: AsyncSession) -> int:
    """전체 모델의 총 다운로드 수를 반환."""
    result = await session.execute(select(func.count()).select_from(ModelDownloadLog))
    return result.scalar() or 0


async def get_download_count_by_model(session: AsyncSession) -> dict[str, int]:
    """모델별 총 다운로드 수를 반환."""
    result = await session.execute(
        select(ModelDownloadLog.model_name, func.count())
        .group_by(ModelDownloadLog.model_name)
        .order_by(func.count().desc())
    )
    return {name: count for name, count in result.all()}


async def get_hourly_download(
    session: AsyncSession, hours: int = 24,
) -> list[dict]:
    """최근 N시간의 시간별 다운로드 수를 반환."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await session.execute(
        select(
            func.date_trunc("hour", ModelDownloadLog.downloaded_at).label("hour"),
            func.count().label("count"),
        )
        .where(ModelDownloadLog.downloaded_at >= since)
        .group_by(text("hour"))
        .order_by(text("hour"))
    )
    return [
        {"date": row.hour.strftime("%H:%M") if row.hour else "", "count": row.count}
        for row in result.all()
    ]


async def get_daily_download(
    session: AsyncSession, days: int = 30,
) -> list[dict]:
    """최근 N일의 일별 다운로드 수를 반환."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await session.execute(
        select(
            func.date(ModelDownloadLog.downloaded_at).label("day"),
            func.count().label("count"),
        )
        .where(ModelDownloadLog.downloaded_at >= since)
        .group_by(func.date(ModelDownloadLog.downloaded_at))
        .order_by(text("day"))
    )
    return [{"date": str(row.day), "count": row.count} for row in result.all()]


async def get_weekly_download(
    session: AsyncSession, weeks: int = 12,
) -> list[dict]:
    """최근 N주의 주별 다운로드 수를 반환."""
    since = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    result = await session.execute(
        select(
            func.date_trunc("week", ModelDownloadLog.downloaded_at).label("week"),
            func.count().label("count"),
        )
        .where(ModelDownloadLog.downloaded_at >= since)
        .group_by(text("week"))
        .order_by(text("week"))
    )
    return [{"date": str(row.week)[:10], "count": row.count} for row in result.all()]


async def get_monthly_download(
    session: AsyncSession, months: int = 12,
) -> list[dict]:
    """최근 N개월의 월별 다운로드 수를 반환."""
    since = datetime.now(timezone.utc) - timedelta(days=months * 30)
    result = await session.execute(
        select(
            func.date_trunc("month", ModelDownloadLog.downloaded_at).label("month"),
            func.count().label("count"),
        )
        .where(ModelDownloadLog.downloaded_at >= since)
        .group_by(text("month"))
        .order_by(text("month"))
    )
    return [{"date": str(row.month)[:7], "count": row.count} for row in result.all()]


# ---------------------------------------------------------------------------
# 게시 통계 (catalog_model_versions.finished_at 중 status=READY 기준)
# ---------------------------------------------------------------------------


async def get_total_publish_count(session: AsyncSession) -> int:
    """READY 상태(게시된) 모델 버전의 총 개수를 반환."""
    result = await session.execute(
        select(func.count()).where(
            ModelVersion.status == "READY",
            ModelVersion.finished_at.is_not(None),
        )
    )
    return result.scalar() or 0


async def get_hourly_publish(
    session: AsyncSession, hours: int = 24,
) -> list[dict]:
    """최근 N시간의 시간별 게시 수를 반환."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await session.execute(
        select(
            func.date_trunc("hour", ModelVersion.finished_at).label("hour"),
            func.count().label("count"),
        )
        .where(ModelVersion.status == "READY", ModelVersion.finished_at >= since)
        .group_by(text("hour"))
        .order_by(text("hour"))
    )
    return [
        {"date": row.hour.strftime("%H:%M") if row.hour else "", "count": row.count}
        for row in result.all()
    ]


async def get_daily_publish(
    session: AsyncSession, days: int = 30,
) -> list[dict]:
    """최근 N일의 일별 게시 수를 반환."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await session.execute(
        select(
            func.date(ModelVersion.finished_at).label("day"),
            func.count().label("count"),
        )
        .where(ModelVersion.status == "READY", ModelVersion.finished_at >= since)
        .group_by(func.date(ModelVersion.finished_at))
        .order_by(text("day"))
    )
    return [{"date": str(row.day), "count": row.count} for row in result.all()]
