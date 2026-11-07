# SPDX-License-Identifier: Apache-2.0
"""Impala 쿼리 프로파일 분석 프록시.

프로파일 분석 요청을 metadata-sync 서비스로 전달한다. metadata-sync 는 Impala 접속
설정, 쿼리 이력, 프로파일 분석 엔진을 보유한다. 이를 통해 카탈로그 서버를 AI 에이전트의
단일 게이트웨이로 유지한다.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.auth import CurrentUser
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/impala", tags=["impala-profile"])

_TIMEOUT = 60.0  # 프로파일 조회 + 분석은 느릴 수 있음


class ProfileTextRequest(BaseModel):
    """사용자가 붙여 넣은 Impala 런타임 프로파일 원문."""

    profile_text: str


@router.get("/queries/{query_id}/profile")
async def get_query_profile(query_id: str, _user: CurrentUser):
    """Impala 쿼리의 원본 런타임 프로파일을 조회한다."""
    url = f"{settings.metadata_sync_base_url}/collector/impala/queries/{query_id}/profile"
    logger.debug("Impala 프로파일 조회 프록시: query_id=%s", query_id)
    return await _proxy_get(url)


@router.post("/queries/{query_id}/analyze")
async def analyze_query_profile(query_id: str, _user: CurrentUser):
    """Impala 쿼리 프로파일을 조회해 병목 구간을 분석한다."""
    url = f"{settings.metadata_sync_base_url}/collector/impala/queries/{query_id}/analyze"
    logger.debug("Impala 프로파일 분석 프록시: query_id=%s", query_id)
    return await _proxy_post(url)


@router.post("/profile/analyze")
async def analyze_profile_text(req: ProfileTextRequest, _user: CurrentUser):
    """사용자가 제출한 Impala 프로파일 텍스트의 병목 구간을 분석한다."""
    url = f"{settings.metadata_sync_base_url}/collector/impala/profile/analyze"
    logger.debug("Impala 프로파일 텍스트 분석 프록시: length=%d", len(req.profile_text))
    return await _proxy_post(url, json={"profile_text": req.profile_text})


def metadata_sync_headers() -> dict:
    """metadata-sync 호출 헤더 — API 토큰이 설정돼 있으면 X-API-Token 으로 보낸다."""
    token = settings.metadata_sync_api_token
    return {"X-API-Token": token} if token else {}


def _raise_upstream(resp: httpx.Response) -> None:
    """metadata-sync 의 비정상 응답을 HTTPException 으로 바꿔 던진다(항상 예외).

    401/403 은 502 로 바꾸고, 그 밖의 상태코드는 본문과 함께 그대로 전달한다.
    """
    # 확장의 401/403 을 그대로 내보내면 프론트가 "사용자 세션 만료"로 오인해 로그아웃시킨다
    if resp.status_code in (401, 403):
        logger.warning("metadata-sync 인증 실패(%d): metadata_sync.api_token 설정을 확인하세요", resp.status_code)
        raise HTTPException(status_code=502, detail="metadata-sync 인증에 실패했습니다(서버 설정 확인 필요).")
    logger.warning("metadata-sync 비정상 응답 전달: status=%d", resp.status_code)
    raise HTTPException(status_code=resp.status_code, detail=resp.text)


async def _proxy_get(url: str) -> dict:
    """GET 요청을 metadata-sync 로 전달한다. 연결 실패는 503, 200 외 응답은 :func:`_raise_upstream`."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(url, headers=metadata_sync_headers())
        except httpx.ConnectError:
            logger.warning("metadata-sync GET 연결 실패: %s", url)
            raise HTTPException(
                status_code=503,
                detail="metadata-sync 서비스를 사용할 수 없습니다.",
            )
        if resp.status_code != 200:
            _raise_upstream(resp)
        return resp.json()


async def _proxy_post(url: str, json: dict | None = None) -> dict:
    """POST 요청을 metadata-sync 로 전달한다. 연결 실패는 503, 200 외 응답은 :func:`_raise_upstream`."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(url, json=json, headers=metadata_sync_headers())
        except httpx.ConnectError:
            logger.warning("metadata-sync POST 연결 실패: %s", url)
            raise HTTPException(
                status_code=503,
                detail="metadata-sync 서비스를 사용할 수 없습니다.",
            )
        if resp.status_code != 200:
            _raise_upstream(resp)
        return resp.json()
