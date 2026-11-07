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
    logger.debug("S3 클라이언트 생성: endpoint=%s region=%s", settings.os_endpoint, settings.os_region)
    async with session.client(
        "s3",
        endpoint_url=settings.os_endpoint,
        aws_access_key_id=settings.os_access_key,
        aws_secret_access_key=settings.os_secret_key,
        region_name=settings.os_region,
        use_ssl=settings.os_use_ssl,
    ) as client:
        yield client


def presign_endpoint() -> str:
    """presigned URL 에 넣을 주소 — 외부용 주소가 있으면 그것, 없으면 서버가 붙는 주소."""
    return (settings.os_public_endpoint or "").strip() or settings.os_endpoint


async def presigned_url(method: str, bucket: str, key: str, expires_in: int) -> str:
    """presigned URL 을 외부용 주소로 서명해 만든다.

    서명에는 호스트가 들어가므로 서버가 붙는 내부 주소(예: ``http://minio:9000``)로 만든 URL 은
    호스트만 바꿔 쓸 수 없다. 서명은 로컬 계산이라 외부용 주소에 실제로 연결하지 않는다.
    """
    session = _get_session()
    logger.debug("presigned URL 생성: method=%s bucket=%s key=%s expires_in=%d",
                 method, bucket, key, expires_in)
    async with session.client(
        "s3",
        endpoint_url=presign_endpoint(),
        aws_access_key_id=settings.os_access_key,
        aws_secret_access_key=settings.os_secret_key,
        region_name=settings.os_region,
        use_ssl=settings.os_use_ssl,
    ) as client:
        return await client.generate_presigned_url(
            method, Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires_in)


async def ensure_bucket(bucket: str | None = None, *, strict: bool = False) -> None:
    """버킷이 존재하지 않으면 생성한다.

    head_bucket 으로 버킷 존재 여부를 확인하고, 없으면 생성한다.
    생성 실패 시 기본은 예외를 올리지 않고 경고만 로깅한다(기동 시 등).
    ``strict=True`` 면 생성 실패(저장소에 닿지 않는 경우 포함)를 그대로 올린다 — presigned URL 처럼 저장소에
    묻지 않고 만들어지는 결과를 장애 중에 내주지 않도록, 호출자가 503 으로 바꿀 수 있게 한다.
    """
    bucket = bucket or settings.os_bucket
    async with get_s3_client() as s3:
        try:
            await s3.head_bucket(Bucket=bucket)
            logger.debug("버킷 '%s' 존재 확인", bucket)
        except Exception:
            # head_bucket 실패(없음·권한·연결 오류 모두) → 생성 시도로 판별한다
            try:
                logger.info("버킷 '%s' 없음 — 생성 중...", bucket)
                await s3.create_bucket(Bucket=bucket)
                logger.info("버킷 '%s' 생성 완료", bucket)
            except Exception as create_err:
                logger.warning("버킷 '%s' 생성 실패: %s", bucket, create_err)
                if strict:
                    raise
