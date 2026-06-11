"""강제 비밀번호 변경 게이트 미들웨어.

최초 로그인 시 비밀번호 변경이 필요한 계정(``must_change_password=true``, 예: LDAP
동기화로 생성돼 초기 비번이 생년월일인 계정)이 비밀번호를 바꾸기 전까지 일반 API 접근을
차단한다. 화이트리스트(로그인/리프레시/로그아웃/me/비밀번호변경/타입 + health/docs)만 허용.

판단은 로컬 JWT 의 ``must_change_password`` claim 으로 한다(DB 조회 없음). 비밀번호 변경
성공 시 백엔드가 플래그 False 인 새 토큰을 재발급하므로, 클라이언트가 교체하면 게이트가
즉시 풀린다. Keycloak 모드/토큰 없음/디코딩 실패는 통과시켜 일반 인증 흐름(401)에 맡긴다.
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.auth import _extract_bearer_token, _verify_token_local
from app.core.config import settings

logger = logging.getLogger(__name__)

# 강제 변경 중에도 허용해야 하는 경로(정확 일치)
_WHITELIST: frozenset[str] = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/api/v1/auth/me",
    "/api/v1/auth/change-password",
    "/api/v1/auth/type",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
})

# 클라이언트(프론트 인터셉터)가 식별하는 코드
PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"


class PasswordChangeGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 로컬 모드의 API 경로만 게이트 대상. 화이트리스트/비-API 는 통과.
        if (
            settings.auth_type != "local"
            or path in _WHITELIST
            or not path.startswith("/api/")
        ):
            return await call_next(request)

        token = _extract_bearer_token(request)
        if not token:
            # 토큰 없음 → 라우터의 일반 인증(401)에 맡긴다
            return await call_next(request)

        try:
            payload = _verify_token_local(token)
        except Exception:
            # 디코딩/검증 실패 → 게이트가 막지 않고 라우터로 넘긴다(401 처리)
            return await call_next(request)

        if payload.get("must_change_password"):
            logger.info("강제 변경 게이트 차단: sub=%s path=%s", payload.get("sub"), path)
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "비밀번호를 변경해야 계속 이용할 수 있습니다.",
                    "code": PASSWORD_CHANGE_REQUIRED,
                },
            )

        return await call_next(request)
