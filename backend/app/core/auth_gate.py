# SPDX-License-Identifier: Apache-2.0
"""API 로그인 게이트 — ``/api/v1/*`` 는 기본적으로 로그인한 사용자만 쓸 수 있다.

예전에는 엔드포인트마다 인증 의존성을 달았고, 의존성이 없는 조회 API(데이터셋 목록·상세, 모델,
AI Agent, 표준 사전 등)는 토큰 없이도 응답했다. 서버 API 차단 메뉴의 검사(``require_menu``)도
사용자가 없으면 통과시켜, 토큰만 빼면 막아 둔 메뉴의 데이터를 볼 수 있었다.

이 미들웨어는 사용자 토큰(로컬 JWT 또는 Keycloak)을 검증해 통과시키고, 그 밖의 요청은 401 로
막는다. 사용자 토큰 대신 자체 자격을 쓰는 경로만 예외로 두며, 그 경로는 핸들러가 직접 검증한다.

- 로그인·토큰 갱신·로그아웃·인증 방식 조회 (로그인 전에 부름)
- 페더레이션 export (``/federation/export/*`` — peer 의 export 서비스 토큰)
- MCP 배포 정책 번들·감사 이벤트 수집 (배포 서비스 토큰, 없으면 핸들러가 로그인 검사)
- 다운로드 링크 (``?token=`` — 파일 하나에만 유효한 짧은 토큰, 핸들러가 검증)
"""

from __future__ import annotations

import logging
import re

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.auth import get_current_user

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1/"

# 로그인 전에 쓰는 경로 — 정확히 일치
PUBLIC_PATHS = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/api/v1/auth/type",
})

# 자체 서비스 토큰으로 검증하는 경로
SERVICE_TOKEN_PATTERNS = (
    re.compile(r"^/api/v1/federation/export(/.*)?$"),
    re.compile(r"^/api/v1/mcp-deployments/[^/]+/(policy-bundle|audit-events)$"),
)

# 다운로드 링크 토큰(``?token=``)으로 여는 파일 경로 — 토큰이 있을 때만 예외
DOWNLOAD_TOKEN_PATHS = frozenset({
    "/api/v1/filesystem/download",
    "/api/v1/model-store/browse/file",
})


def is_exempt(request: Request) -> bool:
    """사용자 로그인 없이 핸들러까지 보내는 요청인가.

    OPTIONS(CORS preflight)·``/api/v1/`` 밖의 경로(정적 파일 등)·공개 경로·서비스 토큰 경로,
    그리고 ``?token=`` 이 붙은 다운로드 경로가 예외다.
    """
    path = request.url.path
    if request.method == "OPTIONS" or not path.startswith(API_PREFIX):
        return True
    if path in PUBLIC_PATHS:
        return True
    if any(p.match(path) for p in SERVICE_TOKEN_PATTERNS):
        return True
    if path in DOWNLOAD_TOKEN_PATHS and request.query_params.get("token"):
        return True
    return False


class ApiAuthGateMiddleware(BaseHTTPMiddleware):
    """예외 경로가 아닌 ``/api/v1/*`` 요청을 ``get_current_user`` 로 검증하고, 실패하면 그 상태코드
    (401/403)와 detail 로 바로 응답한다. 핸들러의 개별 인증 의존성은 그대로 다시 실행된다.
    """

    async def dispatch(self, request: Request, call_next):
        if is_exempt(request):
            return await call_next(request)
        try:
            await get_current_user(request)
        except HTTPException as e:
            logger.warning("API 로그인 게이트 차단: %s %s status=%d",
                           request.method, request.url.path, e.status_code)
            return JSONResponse(status_code=e.status_code, content={"detail": e.detail},
                                headers=getattr(e, "headers", None) or None)
        return await call_next(request)
