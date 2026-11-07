# SPDX-License-Identifier: Apache-2.0
"""OCI 모델 허브 다운로드 로그.

OCI 전용 다운로드 로그 테이블(``catalog_oci_model_download_log``) 에 이벤트를
적재한다. MLflow 모델 레지스트리의 ``catalog_model_download_log`` 와는 분리.

로깅 정책: 적재 빈도가 매우 높으므로 일반 적재는 INFO 를 생략하고(DEBUG),
적재(커밋) 실패만 WARNING 으로 기록한다(주 요청 흐름은 계속). OCI 모델이 아닌 이름
(MLflow 모델)은 조용히 건너뛴다 — model-store 다운로드 API 가 두 레지스트리에 공용이기 때문.
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
    아울러 모델의 비정규화 컬럼 download_count 를 DB 에서 원자적으로(``+ 1``) 증가시킨다.
    user_agent 는 500자로 자른다. 실패는 WARNING 과 세션 롤백만 하고 전파하지 않는다.
    """
    try:
        # OCI 모델인지 확인
        result = await session.execute(
            select(OciModel.id).where(OciModel.name == model_name)
        )
        if not result.scalars().first():
            logger.debug("OCI 모델이 아니어서 다운로드 기록 생략: %s", model_name)
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
        logger.debug("OCI 다운로드 기록: %s v%d (%s)", model_name, version, download_type)
    except Exception as e:
        logger.warning("OCI 다운로드 기록 실패 %s v%d: %s", model_name, version, e)
        # 실패한 트랜잭션을 되돌려 호출자가 같은 세션을 계속 쓸 수 있게 한다
        try:
            await session.rollback()
        except Exception as rb_err:  # noqa: BLE001 — 롤백 실패도 다운로드 자체는 막지 않는다
            logger.debug("다운로드 기록 롤백 실패: %s", rb_err)
