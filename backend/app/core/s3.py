# SPDX-License-Identifier: Apache-2.0
"""카탈로그 서버용 S3 호환 오브젝트 스토리지 클라이언트.

aioboto3 를 통해 AWS S3 및 MinIO 와 호환되는 비동기 S3 작업을 제공한다.
설정은 기동 시 catalog_configuration DB 테이블에서 로드되어
전역 settings 객체에 저장된다.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import aioboto3

from app.core.config import settings

logger = logging.getLogger(__name__)

_session: aioboto3.Session | None = None


def _get_session() -> aioboto3.Session:
    """공유 aioboto3 세션을 가져오거나 생성한다 (지연 초기화)."""
    global _session
    if _session is None:
        _session = aioboto3.Session()
    return _session


@asynccontextmanager
async def get_s3_client() -> AsyncGenerator:
    """settings 로 구성된 비동기 S3 클라이언트를 yield 한다.

    사용 예::
        async with get_s3_client() as s3:
            await s3.list_objects_v2(Bucket="my-bucket")
    """
    session = _get_session()
    async with session.client(
        "s3",
        endpoint_url=settings.os_endpoint,
        aws_access_key_id=settings.os_access_key,
        aws_secret_access_key=settings.os_secret_key,
        region_name=settings.os_region,
        use_ssl=settings.os_use_ssl,
    ) as client:
        yield client


async def ensure_bucket(bucket: str | None = None) -> None:
    """버킷이 존재하지 않으면 생성한다.

    head_bucket 으로 버킷 존재 여부를 확인하고, 없으면 생성한다.
    생성 실패 시 예외를 올리지 않고 경고만 로깅한다.
    """
    bucket = bucket or settings.os_bucket
    async with get_s3_client() as s3:
        try:
            await s3.head_bucket(Bucket=bucket)
            logger.info("버킷 '%s' 존재 확인", bucket)
        except Exception:
            try:
                logger.info("버킷 '%s' 없음 — 생성 중...", bucket)
                await s3.create_bucket(Bucket=bucket)
                logger.info("버킷 '%s' 생성 완료", bucket)
            except Exception as create_err:
                logger.warning("버킷 '%s' 생성 실패: %s", bucket, create_err)
