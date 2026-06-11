"""Impala 쿼리 프로파일 분석 프록시.

프로파일 분석 요청을 metadata-sync 서비스로 전달한다. metadata-sync 는 Impala 접속
설정, 쿼리 이력, 프로파일 분석 엔진을 보유한다. 이를 통해 카탈로그 서버를 AI 에이전트의
단일 게이트웨이로 유지한다.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/impala", tags=["impala-profile"])

_TIMEOUT = 60.0  # 프로파일 조회 + 분석은 느릴 수 있음


class ProfileTextRequest(BaseModel):
    profile_text: str


@router.get("/queries/{query_id}/profile")
async def get_query_profile(query_id: str):
    """Impala 쿼리의 원본 런타임 프로파일을 조회한다."""
    url = f"{settings.metadata_sync_base_url}/collector/impala/queries/{query_id}/profile"
    return await _proxy_get(url)


@router.post("/queries/{query_id}/analyze")
async def analyze_query_profile(query_id: str):
    """Impala 쿼리 프로파일을 조회해 병목 구간을 분석한다."""
    url = f"{settings.metadata_sync_base_url}/collector/impala/queries/{query_id}/analyze"
    return await _proxy_post(url)


@router.post("/profile/analyze")
async def analyze_profile_text(req: ProfileTextRequest):
    """사용자가 제출한 Impala 프로파일 텍스트의 병목 구간을 분석한다."""
    url = f"{settings.metadata_sync_base_url}/collector/impala/profile/analyze"
    return await _proxy_post(url, json={"profile_text": req.profile_text})


async def _proxy_get(url: str) -> dict:
    """GET 요청을 metadata-sync 로 전달한다."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(url)
        except httpx.ConnectError:
            logger.warning("metadata-sync GET 연결 실패: %s", url)
            raise HTTPException(
                status_code=503,
                detail="metadata-sync 서비스를 사용할 수 없습니다.",
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()


async def _proxy_post(url: str, json: dict | None = None) -> dict:
    """POST 요청을 metadata-sync 로 전달한다."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(url, json=json)
        except httpx.ConnectError:
            logger.warning("metadata-sync POST 연결 실패: %s", url)
            raise HTTPException(
                status_code=503,
                detail="metadata-sync 서비스를 사용할 수 없습니다.",
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()
