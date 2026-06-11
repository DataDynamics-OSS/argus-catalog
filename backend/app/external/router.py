# SPDX-License-Identifier: Apache-2.0
"""외부 시스템(Schema Registry, 데이터 파이프라인 등) 용 dataset 메타데이터/Avro 스키마 API.

dataset_id(int) 와 URN(string) 양쪽 식별자를 모두 지원하며, TTL 캐시(``app.external.cache``)
를 사이에 끼워 서버 부하를 줄인다. URN 예: ``mysql-19d0bfe954e2cfdaa.sakila.actor.PROD.dataset``.
캐시 진단 정보는 응답 본문 대신 ``X-Cache-*`` 헤더로 분리해 표준 Avro record 호환성을 유지한다.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser, OptionalUser
from app.core.config import settings
from app.core.database import get_session
from app.external.cache import get_cache
from app.external.schemas import CacheConfigResponse, CacheConfigUpdate
from app.external.service import get_dataset_avro_schema, get_dataset_metadata

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/external", tags=["external"])


def _no_cache(no_cache: bool) -> bool:
    """전역 설정이 캐시를 꺼 두었으면 호출자 요청과 무관하게 강제로 no-cache 처리."""
    if not settings.cache_enabled and not no_cache:
        return True
    return no_cache


def _avro_response(avro: dict, hit: bool) -> JSONResponse:
    """Avro 본문은 표준 record 만, 캐시 진단은 ``X-Cache-*`` 헤더로 분리."""
    cache = get_cache()
    headers = {
        "X-Cache-Hit": "true" if hit else "false",
        "X-Cache-Ttl-Seconds": str(cache.ttl_seconds),
    }
    return JSONResponse(content=avro, headers=headers)


# ---------------------------------------------------------------------------
# URN(문자열) 기준 — 주 외부 API
# ---------------------------------------------------------------------------


@router.get("/metadata")
async def get_metadata_by_urn(
    urn: str = Query(..., description="Dataset URN, e.g. mysql-xxx.sakila.actor.PROD.dataset"),
    no_cache: bool = Query(False),
    user: OptionalUser = None,
    session: AsyncSession = Depends(get_session),
):
    """URN 으로 dataset 메타데이터를 조회한다.

    성능: 첫 호출 ~20ms(DB), 두 번째 이후 호출 < 1ms(캐시).
    URN→ID 매핑도 캐시된다.
    """
    result = await get_dataset_metadata(session, urn, no_cache=_no_cache(no_cache))
    if result is None:
        raise HTTPException(status_code=404, detail=f"데이터셋을(를) 찾을 수 없습니다: {urn}")
    return result


@router.get("/avro-schema")
async def get_avro_by_urn(
    urn: str = Query(..., description="Dataset URN"),
    no_cache: bool = Query(False),
    user: OptionalUser = None,
    session: AsyncSession = Depends(get_session),
):
    """URN 으로 Avro 스키마를 조회한다.

    표준 Avro record 만 본문에 반환. 캐시 진단은 ``X-Cache-*`` 응답 헤더로 노출.
    """
    result = await get_dataset_avro_schema(session, urn, no_cache=_no_cache(no_cache))
    if result is None:
        raise HTTPException(status_code=404, detail=f"데이터셋을(를) 찾을 수 없습니다: {urn}")
    avro, hit = result
    return _avro_response(avro, hit)


# ---------------------------------------------------------------------------
# dataset_id(int) 기준 — 하위 호환
# ---------------------------------------------------------------------------


@router.get("/datasets/{dataset_id}/metadata")
async def get_metadata_by_id(
    dataset_id: int,
    no_cache: bool = Query(False),
    user: OptionalUser = None,
    session: AsyncSession = Depends(get_session),
):
    """내부 ID 로 dataset 메타데이터를 조회한다."""
    result = await get_dataset_metadata(session, dataset_id, no_cache=_no_cache(no_cache))
    if result is None:
        raise HTTPException(status_code=404, detail="데이터셋을(를) 찾을 수 없습니다.")
    return result


@router.get("/datasets/{dataset_id}/avro-schema")
async def get_avro_by_id(
    dataset_id: int,
    no_cache: bool = Query(False),
    user: OptionalUser = None,
    session: AsyncSession = Depends(get_session),
):
    """내부 ID 로 Avro 스키마를 조회한다."""
    result = await get_dataset_avro_schema(session, dataset_id, no_cache=_no_cache(no_cache))
    if result is None:
        raise HTTPException(status_code=404, detail="데이터셋을(를) 찾을 수 없습니다.")
    avro, hit = result
    return _avro_response(avro, hit)


# ---------------------------------------------------------------------------
# 캐시 관리
# ---------------------------------------------------------------------------


@router.delete("/datasets/{dataset_id}/cache")
async def invalidate_by_id(_guard: AdminUser, dataset_id: int, user: OptionalUser = None):
    """ID 로 지정한 dataset 의 캐시 데이터를 무효화한다."""
    cache = get_cache()
    count = await cache.invalidate(f"metadata:{dataset_id}")
    count += await cache.invalidate(f"avro:{dataset_id}")
    logger.info("dataset id 기준 캐시 무효화: dataset_id=%d, entries=%d", dataset_id, count)
    return {"invalidated": count, "dataset_id": dataset_id}


@router.delete("/cache")
async def clear_cache(_guard: AdminUser, user: OptionalUser = None):
    """전체 캐시를 비운다. 운영 중 dataset 일괄 재동기화 후 사용."""
    cache = get_cache()
    cleared = await cache.clear()
    logger.info("캐시 비움: entries=%d", cleared)
    return {"cleared": cleared}


@router.get("/cache/stats")
async def cache_stats(user: OptionalUser = None):
    cache = get_cache()
    return await cache.stats()


@router.get("/cache/config", response_model=CacheConfigResponse)
async def get_cache_config(user: OptionalUser = None):
    cache = get_cache()
    return CacheConfigResponse(
        max_size=cache.max_size,
        ttl_seconds=cache.ttl_seconds,
        enabled=settings.cache_enabled,
        current_size=(await cache.stats())["size"],
    )


@router.put("/cache/config", response_model=CacheConfigResponse)
async def update_cache_config(body: CacheConfigUpdate, user: CurrentUser = None):
    settings.cache_max_size = body.max_size
    settings.cache_ttl_seconds = body.ttl_seconds
    settings.cache_enabled = body.enabled

    cache = get_cache()
    await cache.reconfigure(max_size=body.max_size, ttl_seconds=body.ttl_seconds)

    logger.info(
        "캐시 설정 갱신: max_size=%d, ttl=%ds, enabled=%s",
        body.max_size,
        body.ttl_seconds,
        body.enabled,
    )
    return CacheConfigResponse(
        max_size=body.max_size,
        ttl_seconds=body.ttl_seconds,
        enabled=body.enabled,
        current_size=(await cache.stats())["size"],
    )
