# SPDX-License-Identifier: Apache-2.0
"""스키마 레지스트리 인증 — API 키, Bearer(로그인 토큰) 또는 Basic(사용자 이름·비밀번호).

권장은 API 키(TODO-056): ``X-API-Key``·``Bearer argus_sk_…``, Basic 만 되는 클라이언트는 비밀번호 자리에 키를 넣는다
(사용자 이름은 보지 않음 — 키 이름 등 아무 값). 키는 Keycloak 모드에서도 Basic 으로 받는다.

NiFi ``ConfluentSchemaRegistry`` 같은 레지스트리 클라이언트는 Basic 인증만 지원하므로 Basic 을 받는다.
비밀번호 검증(PBKDF2 60만 회, 수백 ms)을 요청마다 하면 레지스트리 응답이 느려지므로, 성공한 자격의
지문(사용자 이름·비밀번호의 SHA-256 — 비밀번호 원문은 두지 않음)을 ``basic_auth_cache_seconds`` 동안 기억한다.
기억한 동안에도 계정 상태(비활성·비밀번호 변경)는 ``app.core.auth._load_account``(5초 캐시)로 매번 확인한다.
실패는 로그인 API 와 같이 연속 실패 횟수를 세고 잠근다. Keycloak 모드에서는 Basic 을 받지 않는다(Bearer 사용).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
from datetime import timedelta

from cachetools import TTLCache
from fastapi import HTTPException, Request, status
from sqlalchemy import select

from app.core.auth import (
    API_KEY_PREFIX,
    TokenUser,
    _load_account,
    authenticate_api_key,
    extract_api_key,
    get_current_user,
    password_fingerprint,
)
from app.core.config import settings
from app.core.passwords import verify_password_async

logger = logging.getLogger(__name__)

REALM = 'Basic realm="Argus Schema Registry"'
# 테스트에서 세션 팩토리를 바꿔 끼우기 위한 훅(None 이면 app.core.database.async_session)
session_factory = None
_verified: TTLCache | None = None


def _verified_cache() -> TTLCache:
    global _verified
    if _verified is None:
        _verified = TTLCache(maxsize=1024, ttl=max(1, settings.schema_registry_basic_auth_cache_seconds))
    return _verified


def reset_cache() -> None:
    global _verified
    _verified = None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": REALM})


def _parse_basic(credentials: str) -> tuple[str, str]:
    try:
        decoded = base64.b64decode(credentials, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise _unauthorized("Basic 인증 값이 올바르지 않습니다.") from None
    username, sep, password = decoded.partition(":")
    if not sep or not username:
        raise _unauthorized("Basic 인증 값이 올바르지 않습니다.")
    return username, password


async def _verify_full(username: str, password: str) -> tuple[int, str, str, str]:
    """DB 로 비밀번호를 검증한다 → (user_id, username, role_code, pwv). 실패하면 예외."""
    from app.usermgr.models import ArgusRole, ArgusUser
    from app.usermgr.service import LOGIN_LOCK_MINUTES, LOGIN_MAX_FAILURES, active_lock, utcnow

    factory = session_factory
    if factory is None:
        from app.core.database import async_session as factory
    async with factory() as session:
        row = (await session.execute(
            select(ArgusUser, ArgusRole.role_id)
            .join(ArgusRole, ArgusUser.role_id == ArgusRole.id)
            .where(ArgusUser.username == username)
        )).first()
        if row is None:
            logger.warning("스키마 레지스트리 Basic 인증 실패(사용자 없음): username=%s", username)
            raise _unauthorized("사용자명 또는 비밀번호가 올바르지 않습니다.")
        user, role_code = row
        if locked := active_lock(user):
            logger.warning("스키마 레지스트리 Basic 인증 차단(잠금): username=%s until=%s", username, locked)
            raise HTTPException(status_code=423, detail="로그인 실패가 반복되어 계정이 잠겼습니다.")
        if not await verify_password_async(password, user.password_hash):
            user.failed_login_count = (user.failed_login_count or 0) + 1
            if user.failed_login_count >= LOGIN_MAX_FAILURES:
                user.locked_until = utcnow() + timedelta(minutes=LOGIN_LOCK_MINUTES)
                user.failed_login_count = 0
                logger.warning("스키마 레지스트리 Basic 인증 %d회 연속 실패 — 계정 잠금: username=%s",
                               LOGIN_MAX_FAILURES, username)
            else:
                logger.warning("스키마 레지스트리 Basic 인증 실패(비밀번호 불일치): username=%s", username)
            await session.commit()
            raise _unauthorized("사용자명 또는 비밀번호가 올바르지 않습니다.")
        if user.status != "active":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="비활성화된 계정입니다.")
        if user.must_change_password:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="비밀번호를 먼저 바꿔야 합니다. 화면에서 로그인해 비밀번호를 바꾸세요.")
        if user.failed_login_count:
            user.failed_login_count = 0
            await session.commit()
        return user.id, user.username, role_code, password_fingerprint(user.password_hash or "")


async def verify_basic(username: str, password: str) -> TokenUser:
    """Basic 자격 → 사용자. 성공한 자격은 기억해 다음 요청부터 비밀번호 검증을 건너뛴다."""
    if settings.auth_type != "local":
        raise _unauthorized("Keycloak 인증을 쓰는 서버에서는 Basic 인증을 받지 않습니다. Bearer 토큰을 쓰세요.")
    key = hashlib.sha256(f"{username}\x00{password}".encode("utf-8")).hexdigest()
    cache = _verified_cache()
    cached = cache.get(key)
    if cached is not None:
        user_id, uname, pwv = cached
        account = await _load_account(("local", str(user_id)))
        if account is not None and account[0] == "active" and account[2] == pwv:
            return _token_user(user_id, uname, account[1])
        cache.pop(key, None)       # 비활성·비밀번호 변경 — 처음부터 다시 검증
    user_id, uname, role_code, pwv = await _verify_full(username, password)
    cache[key] = (user_id, uname, pwv)
    logger.info("스키마 레지스트리 Basic 인증: username=%s", uname)
    return _token_user(user_id, uname, role_code)


def _token_user(user_id: int, username: str, role_code: str) -> TokenUser:
    user = TokenUser(sub=str(user_id), username=username, roles=[role_code])
    if not user.has_argus_role:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="역할이 할당되지 않았습니다. 관리자에게 문의하십시오.")
    return user


async def registry_user(request: Request) -> TokenUser:
    """FastAPI 의존성 — API 키·Bearer 면 일반 인증, Basic 이면 비밀번호 자리의 API 키 또는 ``verify_basic``."""
    if not settings.schema_registry_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="스키마 레지스트리가 꺼져 있습니다.")
    if extract_api_key(request):
        return await get_current_user(request)
    scheme, _, credentials = (request.headers.get("Authorization") or "").partition(" ")
    scheme = scheme.lower()
    if scheme == "bearer":
        return await get_current_user(request)
    if scheme == "basic" and settings.schema_registry_basic_auth:
        username, password = _parse_basic(credentials.strip())
        if password.startswith(API_KEY_PREFIX):
            return await authenticate_api_key(password, request)
        return await verify_basic(username, password)
    raise _unauthorized("인증이 필요합니다.")
