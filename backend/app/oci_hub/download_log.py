"""OCI 모델 허브 다운로드 로그.

OCI 전용 다운로드 로그 테이블(``catalog_oci_model_download_log``) 에 이벤트를
적재한다. MLflow 모델 레지스트리의 ``catalog_model_download_log`` 와는 분리.

로깅 정책: 적재 빈도가 매우 높으므로 일반 적재는 INFO 를 생략하고,
잘못된 모델/버전 참조처럼 일관성 깨짐만 WARNING 으로 기록한다.
"""

import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.oci_hub.models import OciModel, OciModelDownloadLog

logger = logging.getLogger(__name__)


async def log_oci_download(
    session: AsyncSession,
    model_name: str,
    version: int,
    download_type: str,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """OCI 모델 다운로드 이벤트를 기록한다.

    model_name 이 catalog_oci_models 에 존재할 때만 기록한다.
    아울러 모델의 비정규화 컬럼 download_count 를 증가시킨다.
    """
    try:
        # OCI 모델인지 확인
        result = await session.execute(
            select(OciModel.id).where(OciModel.name == model_name)
        )
        if not result.scalars().first():
            return  # OCI 모델이 아니면 건너뜀

        # 다운로드 이벤트 기록
        entry = OciModelDownloadLog(
            model_name=model_name,
            version=version,
            download_type=download_type,
            client_ip=client_ip,
            user_agent=user_agent[:500] if user_agent and len(user_agent) > 500 else user_agent,
        )
        session.add(entry)

        # 비정규화 카운터 증가
        await session.execute(
            update(OciModel)
            .where(OciModel.name == model_name)
            .values(download_count=OciModel.download_count + 1)
        )

        await session.commit()
        logger.info("OCI 다운로드 기록: %s v%d (%s)", model_name, version, download_type)
    except Exception as e:
        logger.warning("OCI 다운로드 기록 실패 %s v%d: %s", model_name, version, e)
