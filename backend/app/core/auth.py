# SPDX-License-Identifier: Apache-2.0
"""FastAPI 인증 — Keycloak OIDC 와 로컬 JWT 를 지원한다.

JWT 토큰 검증과 FastAPI 의존성(dependency) 함수를 제공한다.
auth_type 이 "keycloak" 이면 Keycloak 의 JWKS 엔드포인트로 검증하고,
auth_type 이 "local" 이면 로컬 HMAC 비밀키로 검증한다. 두 모드 모두 프로그램용 API 키(``X-API-Key`` 또는 ``Bearer argus_sk_…``)도 받는다.
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Annotated

import httpx
from cachetools import TTLCache
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt

from app.core.config import settings
from app.core.secrets import resolve_jwt_secret

logger = logging.getLogger(__name__)

# JWKS 키를 1시간 동안 캐시 (Keycloak 모드 전용)
_jwks_cache: TTLCache = TTLCache(maxsize=1, ttl=3600)
_JWKS_CACHE_KEY = "jwks"

# 로컬 JWT 설정
LOCAL_JWT_ALGORITHM = "HS256"
LOCAL_JWT_EXPIRE_MINUTES = 480  # 8시간

# 로컬 JWT 의 ``typ`` claim — 토큰 용도 구분 (access 를 refresh 로, refresh 를 access 로 쓰는 것 차단)
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"

# 서명키는 환경변수 ARGUS_JWT_SECRET 를 우선 사용한다. 미설정이면 데이터 디렉터리의 ``.jwt-secret``
# (없으면 임의 키를 만들어 저장)을 쓰고, 그 파일도 쓸 수 없을 때만 공개된 개발용 키로 대체한다(DEF-001).
# 서버를 여러 대 두고 토큰을 공유하려면 ARGUS_JWT_SECRET 로 같은 값을 준다.
# ARGUS_REQUIRE_SECRETS=true 이면 미설정·기본값·짧은 값일 때 여기서(모듈 로드 시) 예외로 기동을 멈춘다.
LOCAL_JWT_SECRET_KEY, LOCAL_JWT_SECRET_SOURCE = resolve_jwt_secret(settings.data_dir)
if LOCAL_JWT_SECRET_SOURCE == "dev":
    logger.warning("ARGUS_JWT_SECRET 미설정이고 서명키 파일도 만들 수 없어 개발용 기본 JWT 서명키 사용(운영 금지).")


# ---------------------------------------------------------------------------
# TokenUser dataclass
# ---------------------------------------------------------------------------

@dataclass
class TokenUser:
    """JWT 토큰에서 추출한 인증 사용자."""

    sub: str
    username: str
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    organization: str = ""
    department: str = ""
    roles: list[str] = field(default_factory=list)
    realm_roles: list[str] = field(default_factory=list)
    # True 면 최초 로그인 후 비밀번호 변경 전까지 일반 API 접근을 차단한다(게이트).
    must_change_password: bool = False
    # API 키로 인증했으면 키 id (사람 계정이 아님 — sub 는 ``apikey:<id>``, username 은 ``apikey#<id>``)
    api_key_id: int | None = None
    # 사용자 정의 역할(TODO-124) — 권한 표는 이 역할 열로 판정한다. roles 에는 기반 역할도 함께 들어 있다
    custom_role: str | None = None

    @property
    def is_api_key(self) -> bool:
        return self.api_key_id is not None

    @property
    def _all_roles(self) -> list[str]:
        """로컬(roles)과 Keycloak(realm_roles) 양쪽 역할을 합친 목록."""
        return self.roles + self.realm_roles

    @property
    def is_admin(self) -> bool:
        """관리자 역할(settings.auth_keycloak_admin_role, 기본 argus-admin) 보유 여부."""
        return settings.auth_keycloak_admin_role in self._all_roles

    @property
    def is_superuser(self) -> bool:
        """슈퍼유저 역할 보유 여부(관리자 여부와 별개로 판정)."""
        return settings.auth_keycloak_superuser_role in self._all_roles

    @property
    def is_user(self) -> bool:
        """일반 사용자 역할 보유 여부."""
        return settings.auth_keycloak_user_role in self._all_roles

    @property
    def has_argus_role(self) -> bool:
        """유효한 역할을 하나라도 가지고 있는지 확인."""
        return self.is_admin or self.is_superuser or self.is_user

    @property
    def role(self) -> str:
        """가장 높은 권한 역할 이름을 반환."""
        if self.is_admin:
            return "admin"
        if self.is_superuser:
            return "superuser"
        if self.is_user:
            return "user"
        return "none"


# ---------------------------------------------------------------------------
# Keycloak token verification
# ---------------------------------------------------------------------------

def _keycloak_base_url() -> str:
    """Keycloak realm 기본 URL — ``{server_url}/realms/{realm}`` (발급자 iss 와 같다)."""
    return f"{settings.auth_keycloak_server_url}/realms/{settings.auth_keycloak_realm}"


def _jwks_uri() -> str:
    return f"{_keycloak_base_url()}/protocol/openid-connect/certs"


def _issuer() -> str:
    return _keycloak_base_url()


async def _get_jwks() -> dict:
    """Keycloak JWKS 공개키를 조회하고 캐시한다."""
    cached = _jwks_cache.get(_JWKS_CACHE_KEY)
    if cached:
        logger.debug("JWKS 캐시 적중")
        return cached
    async with httpx.AsyncClient() as client:
        resp = await client.get(_jwks_uri())
        resp.raise_for_status()
        jwks = resp.json()
    _jwks_cache[_JWKS_CACHE_KEY] = jwks
    logger.info("JWKS 조회 완료: %s (키 %d개)", _jwks_uri(), len(jwks.get("keys", [])))
    return jwks


async def _verify_token_keycloak(token: str) -> dict:
    """JWKS 를 사용해 Keycloak JWT 토큰을 검증·디코딩한다.

    헤더의 kid 와 일치하는 키가 캐시에 없으면(키 회전) 캐시를 비우고 JWKS 를 한 번 다시 받는다.
    RS256 서명·발급자(iss)를 검증하고 aud 는 검증하지 않는다. 실패는 모두 401.
    JWKS 조회 자체의 HTTP 오류는 httpx 예외로 전파된다.
    """
    jwks = await _get_jwks()

    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as e:
        logger.debug("Keycloak 토큰 헤더 파싱 실패: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"유효하지 않은 토큰 헤더입니다: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    kid = unverified_header.get("kid")
    rsa_key = None
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            rsa_key = key
            break

    if not rsa_key:
        # 키 회전 대응 — 캐시를 비우고 최신 JWKS 로 한 번 더 찾는다
        logger.debug("JWKS 에 kid 없음 → 캐시 무효화 후 재조회: kid=%s", kid)
        _jwks_cache.pop(_JWKS_CACHE_KEY, None)
        jwks = await _get_jwks()
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                rsa_key = key
                break

    if not rsa_key:
        logger.warning("Keycloak 토큰 검증 키를 찾을 수 없음: kid=%s", kid)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰 검증에 필요한 키를 찾을 수 없습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            issuer=_issuer(),
            options={"verify_aud": False, "verify_iss": True},
        )
    except JWTError as e:
        logger.debug("Keycloak 토큰 검증 실패: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"토큰 검증에 실패했습니다: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Keycloak access token 은 typ=Bearer. 같은 realm 키로 서명된 ID token(typ=ID) 등은 거부.
    if payload.get("typ", "Bearer") != "Bearer":
        logger.warning("Keycloak 토큰 용도 불일치 거부: typ=%s sub=%s", payload.get("typ"), payload.get("sub"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰 용도가 올바르지 않습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


# ---------------------------------------------------------------------------
# Local token creation & verification
# ---------------------------------------------------------------------------

def create_local_token(
    sub: str,
    username: str,
    email: str,
    first_name: str,
    last_name: str,
    role: str,
    expire_minutes: int = LOCAL_JWT_EXPIRE_MINUTES,
    must_change_password: bool = False,
    token_type: str = TOKEN_TYPE_ACCESS,
    password_hash: str | None = None,
) -> str:
    """사용자에 대한 로컬 JWT 토큰을 생성한다.

    ``must_change_password`` 를 claim 으로 실어, 게이트 미들웨어가 DB 조회 없이
    토큰만으로 강제 변경 여부를 판단하게 한다. 비밀번호 변경 시 플래그가 False 인
    새 토큰을 재발급해 게이트가 즉시 풀리도록 한다.

    ``token_type`` 은 ``typ`` claim 으로 실린다(``access`` / ``refresh``). 검증 시
    용도가 다른 토큰을 거부해, refresh token(7일)을 API 호출용 Bearer 로 쓰거나
    access token 으로 토큰을 재발급받는 것을 막는다.

    ``password_hash`` 를 주면 그 지문(``pwv``)을 실어, 비밀번호가 바뀐(재설정 포함) 뒤에는
    이전에 발급된 토큰을 거부한다(``check_account_state``).
    """
    if token_type not in (TOKEN_TYPE_ACCESS, TOKEN_TYPE_REFRESH):
        raise ValueError(f"unknown token_type: {token_type}")
    now = int(time.time())
    payload = {
        "sub": sub,
        "preferred_username": username,
        "email": email,
        "given_name": first_name,
        "family_name": last_name,
        "roles": [role],
        "must_change_password": must_change_password,
        "iat": now,
        "exp": now + expire_minutes * 60,
        "iss": "argus-catalog-local",
        "typ": token_type,
    }
    if password_hash:
        payload["pwv"] = password_fingerprint(password_hash)
    logger.debug("로컬 토큰 발급: sub=%s username=%s typ=%s expire_minutes=%d",
                 sub, username, token_type, expire_minutes)
    return jwt.encode(payload, LOCAL_JWT_SECRET_KEY, algorithm=LOCAL_JWT_ALGORITHM)


def _verify_token_local(token: str, expected_type: str = TOKEN_TYPE_ACCESS) -> dict:
    """로컬 JWT 토큰을 검증·디코딩한다.

    ``typ`` claim 이 ``expected_type`` 과 다르면(``typ`` 이 없는 구버전 토큰 포함) 401.
    API 인증은 access, ``/auth/refresh`` 는 refresh 만 받는다.
    """
    try:
        payload = jwt.decode(
            token,
            LOCAL_JWT_SECRET_KEY,
            algorithms=[LOCAL_JWT_ALGORITHM],
            options={"verify_aud": False},
        )
    except JWTError as e:
        # 만료 등 — 미들웨어·의존성에서 반복 호출되므로 DEBUG(401 은 로그인 게이트가 WARNING 으로 남김), 토큰 값은 남기지 않는다
        logger.debug("로컬 토큰 검증 실패: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"토큰 검증에 실패했습니다: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("typ") != expected_type:
        logger.warning(
            "토큰 용도 불일치 거부: expected=%s typ=%s sub=%s",
            expected_type, payload.get("typ"), payload.get("sub"),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰 용도가 올바르지 않습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


# ---------------------------------------------------------------------------
# Build TokenUser from payload
# ---------------------------------------------------------------------------

def _build_token_user(payload: dict) -> TokenUser:
    """디코딩된 JWT 페이로드로부터 TokenUser 를 구성한다 (두 모드 모두 동작).

    로컬 모드: roles 에 role_id 값이 담긴다 (예: ["argus-admin"]).
    Keycloak 모드: realm_roles 에 Keycloak realm 역할 이름이 담긴다 (예: ["argus-admin"]).
    둘 다 동일한 role_id 식별자를 쓰므로 is_admin/is_superuser/is_user 가 동일하게 동작한다.
    """
    if settings.auth_type == "local":
        # 로컬 JWT 는 role_id 값을 "roles" claim 에 저장한다
        return TokenUser(
            sub=payload.get("sub", ""),
            username=payload.get("preferred_username", ""),
            email=payload.get("email", ""),
            first_name=payload.get("given_name", ""),
            last_name=payload.get("family_name", ""),
            organization=payload.get("organization", "") or "",
            department=payload.get("department", "") or "",
            roles=payload.get("roles", []),
            realm_roles=[],
            must_change_password=bool(payload.get("must_change_password", False)),
        )

    # Keycloak 모드 — realm_roles 에 role_id 와 일치하는 역할 이름이 담긴다
    realm_access = payload.get("realm_access", {})
    realm_roles = realm_access.get("roles", [])
    resource_access = payload.get("resource_access", {})
    client_roles = resource_access.get(settings.auth_keycloak_client_id, {}).get("roles", [])

    return TokenUser(
        sub=payload.get("sub", ""),
        username=payload.get("preferred_username", ""),
        email=payload.get("email", ""),
        first_name=payload.get("given_name", ""),
        last_name=payload.get("family_name", ""),
        # Keycloak Protocol Mapper(User Attribute → Token Claim)로 실린 커스텀 claim.
        organization=payload.get("organization", "") or "",
        department=payload.get("department", "") or "",
        roles=client_roles,
        realm_roles=realm_roles,
    )


# ---------------------------------------------------------------------------
# 계정 상태 확인 — 발급된 토큰을 즉시 무효화
# ---------------------------------------------------------------------------
#
# 토큰은 로그인 시점 스냅숏이라, 그대로 믿으면 비활성화·삭제·강등·비밀번호 재설정 뒤에도
# 만료(최대 8시간)까지 예전 권한으로 API 를 쓸 수 있다. 그래서 요청마다 사용자 행을 확인한다.
#   - 로컬: 행이 없거나 비활성이면 401, 토큰의 pwv(비밀번호 지문)가 현재와 다르면 401,
#           역할은 토큰이 아니라 DB 의 현재 역할을 쓴다(강등 즉시 반영).
#   - Keycloak: 역할·비밀번호는 Keycloak 이 원천이므로 그대로 두고, 로컬에서 비활성화한
#           계정만 401 로 막는다(행이 없으면 — JIT 전 — 통과).
# DB 부하를 줄이려 결과를 짧게(5초) 캐시한다. 같은 프로세스의 변경은 invalidate_account_cache()
# 로 바로 지우고, 다른 인스턴스(peer)에는 최대 5초 늦게 반영된다.

ACCOUNT_CACHE_TTL = 5
_account_cache: TTLCache = TTLCache(maxsize=4096, ttl=ACCOUNT_CACHE_TTL)
# 테스트에서 세션 팩토리를 바꿔 끼우기 위한 훅(None 이면 app.core.database.async_session)
account_session_factory = None


def password_fingerprint(password_hash: str) -> str:
    """비밀번호 해시의 SHA-256 앞 16자 — 토큰 ``pwv`` claim 으로 비밀번호 변경 여부를 비교한다."""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def invalidate_account_cache() -> None:
    """계정 상태 캐시를 비운다 — 사용자 비활성화·역할 변경·비밀번호 변경 직후 호출."""
    _account_cache.clear()
    logger.debug("계정 상태 캐시 무효화")


def _unauthorized(detail: str) -> HTTPException:
    """WWW-Authenticate 헤더를 붙인 401 예외를 만든다."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _load_account(key: tuple[str, str]) -> tuple[str, str, str] | None:
    """(status, role_code, pwv) — 사용자 행이 없으면 None.

    key 는 ("local", user_id 문자열) 또는 ("keycloak", username). 결과(None 포함)를
    ACCOUNT_CACHE_TTL 초 동안 캐시한다.
    """
    if key in _account_cache:
        return _account_cache[key]
    from sqlalchemy import select

    from app.usermgr.models import ArgusRole, ArgusUser

    factory = account_session_factory
    if factory is None:
        from app.core.database import async_session as factory
    mode, ident = key
    stmt = (
        select(ArgusUser.status, ArgusRole.role_id, ArgusUser.password_hash)
        .join(ArgusRole, ArgusUser.role_id == ArgusRole.id)
    )
    if mode == "local":
        try:
            stmt = stmt.where(ArgusUser.id == int(ident))
        except ValueError:
            return None
    else:
        stmt = stmt.where(ArgusUser.username == ident)
    async with factory() as session:
        row = (await session.execute(stmt)).first()
    value = None if row is None else (row[0], row[1], password_fingerprint(row[2] or ""))
    _account_cache[key] = value
    return value


async def check_account_state(user: TokenUser, payload: dict) -> TokenUser:
    """토큰 사용자를 현재 계정 상태로 검증·보정한다(무효면 401)."""
    if settings.auth_type == "local":
        account = await _load_account(("local", user.sub))
        if account is None:
            logger.warning("인증 거부(계정 없음): sub=%s username=%s", user.sub, user.username)
            raise _unauthorized("계정을 찾을 수 없습니다. 다시 로그인하십시오.")
        acc_status, role_code, pwv = account
        if acc_status != "active":
            logger.warning("인증 거부(비활성 계정): username=%s status=%s", user.username, acc_status)
            raise _unauthorized("비활성화된 계정입니다.")
        # pwv 가 없는 예전 토큰은 만료까지 허용(배포 직후 전원 로그아웃 방지)
        if payload.get("pwv") and payload["pwv"] != pwv:
            logger.warning("인증 거부(비밀번호 변경 후 이전 토큰): username=%s", user.username)
            raise _unauthorized("비밀번호가 변경되어 다시 로그인해야 합니다.")
        user.roles = [role_code]
        return await _apply_custom_role(user)
    if user.username:
        account = await _load_account(("keycloak", user.username))
        if account is not None and account[0] != "active":
            logger.warning("인증 거부(로컬에서 비활성화된 Keycloak 계정): username=%s", user.username)
            raise _unauthorized("비활성화된 계정입니다.")
    return await _apply_custom_role(user)


async def _apply_custom_role(user: TokenUser) -> TokenUser:
    """사용자 정의 역할이면 기반 역할을 더한다(TODO-124, :mod:`app.permissions.roles`)."""
    from app.permissions.roles import apply_custom_role
    return await apply_custom_role(user)


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def _extract_bearer_token(request: Request) -> str | None:
    """Authorization 헤더에서 Bearer 토큰을 추출한다."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


# ---------------------------------------------------------------------------
# API 키 (TODO-056) — 프로그램용 장기 자격
# ---------------------------------------------------------------------------

API_KEY_PREFIX = "argus_sk_"   # app.apikeys.service.KEY_PREFIX 와 같다(순환 임포트를 피해 상수로 둔다)


def extract_api_key(request: Request) -> str | None:
    """``X-API-Key`` 헤더, 또는 ``Authorization: Bearer argus_sk_…`` 의 키."""
    key = request.headers.get("X-API-Key")
    if key:
        return key.strip()
    token = _extract_bearer_token(request)
    if token and token.startswith(API_KEY_PREFIX):
        return token
    return None


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def authenticate_api_key(raw_key: str, request: Request) -> TokenUser:
    """API 키 → 사용자. 키가 무효면 401, 범위·역할 밖이면 403.

    검증 결과는 ``app.apikeys.service`` 가 짧게 기억하므로 요청마다 DB 를 보지 않는다.
    """
    from app.apikeys import service as keys

    if not settings.api_keys_enabled:
        raise _unauthorized("API 키 인증이 꺼져 있습니다.")
    info = await keys.lookup(raw_key) if raw_key.startswith(API_KEY_PREFIX) else None
    reason = keys.unusable_reason(info)
    if reason:
        logger.warning("API 키 인증 거부: %s prefix=%s path=%s", reason, raw_key[:16], request.url.path)
        raise _unauthorized(reason)
    path, method = request.url.path, request.method
    if not keys.allows(info.scopes, method, path):
        logger.warning("API 키 범위 밖 요청 거부: key=%s scopes=%s %s %s",
                       info.id, ",".join(sorted(info.scopes)), method, path)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=("API 키로는 계정·키 관리 API 를 부를 수 없습니다." if path.startswith(keys.KEY_FORBIDDEN_PREFIXES)
                    else f"API 키 범위({', '.join(sorted(info.scopes))})로는 이 요청({method} {path})을 할 수 없습니다."),
        )
    user = TokenUser(sub=f"apikey:{info.id}", username=keys.owner_identity(info.id), first_name=info.name,
                     roles=[info.role], api_key_id=info.id)
    await _apply_custom_role(user)
    if not user.has_argus_role:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API 키에 역할이 없습니다.")
    keys.touch(info.id, _client_ip(request))
    return user


# ---------------------------------------------------------------------------
# 요청 범위 인증 캐시 (TODO-086)
# ---------------------------------------------------------------------------
# 한 요청에서 로그인 게이트(미들웨어)·비밀번호 변경 게이트·핸들러 의존성이 같은 토큰을 각각 검증하던 것을
# 한 번으로 줄인다. ASGI ``scope`` 는 요청마다 새로 만들어지고 미들웨어·라우터·핸들러가 같은 dict 를 보므로
# 여기에 둔 결과는 그 요청 안에서만 쓰인다. 성공한 결과만 담는다(실패는 매번 같은 예외를 다시 만든다).

_REQUEST_AUTH_KEY = "argus.auth"


def _request_cache(request: Request) -> dict | None:
    scope = getattr(request, "scope", None)
    if not isinstance(scope, dict):
        return None
    return scope.setdefault(_REQUEST_AUTH_KEY, {})


def verify_local_token_cached(request: Request, token: str) -> dict:
    """``_verify_token_local``(access) 결과를 요청 범위에 기억한다 — 비밀번호 변경 게이트와 인증이 함께 쓴다."""
    cache = _request_cache(request)
    if cache is not None and ("jwt", token) in cache:
        return cache[("jwt", token)]
    payload = _verify_token_local(token)
    if cache is not None:
        cache[("jwt", token)] = payload
    return payload


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> TokenUser:
    """FastAPI 의존성: API 키 또는 JWT 토큰에서 현재 사용자를 추출·검증한다.

    같은 요청에서 다시 부르면(로그인 게이트 → 핸들러 의존성) 요청 범위에 기억한 결과를 돌려준다.
    """
    raw_key = extract_api_key(request)
    token = None if raw_key else _extract_bearer_token(request)
    cache = _request_cache(request)
    cache_key = ("user", raw_key or token)
    if cache is not None and (raw_key or token) and cache_key in cache:
        return cache[cache_key]
    if raw_key:
        user = await authenticate_api_key(raw_key, request)
        if cache is not None:
            cache[cache_key] = user
        return user
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if settings.auth_type == "local":
        payload = verify_local_token_cached(request, token)
    else:
        payload = await _verify_token_keycloak(token)

    user = await check_account_state(_build_token_user(payload), payload)
    if not user.has_argus_role:
        logger.warning("인가 거부(Argus 역할 없음): username=%s", user.username)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="역할이 할당되지 않았습니다. 관리자에게 문의하십시오.",
        )
    if cache is not None:
        cache[cache_key] = user
    return user


async def get_optional_user(request: Request) -> TokenUser | None:
    """FastAPI 의존성: 토큰이 있으면 사용자를 추출하고, 없으면 None 을 반환한다."""
    cache = _request_cache(request)
    if cache is not None:
        cred = extract_api_key(request) or _extract_bearer_token(request)
        if cred and ("user", cred) in cache:
            return cache[("user", cred)]
    raw_key = extract_api_key(request)
    if raw_key:
        try:
            return await authenticate_api_key(raw_key, request)
        except HTTPException as e:
            logger.debug("선택 인증 API 키 무효 → 익명 처리: %s", e.detail)
            return None
    token = _extract_bearer_token(request)
    if not token:
        return None
    try:
        if settings.auth_type == "local":
            payload = verify_local_token_cached(request, token)
        else:
            payload = await _verify_token_keycloak(token)
        return await check_account_state(_build_token_user(payload), payload)
    except HTTPException as e:
        # 선택 인증 — 토큰이 무효해도 익명으로 처리한다
        logger.debug("선택 인증 토큰 무효 → 익명 처리: %s", e.detail)
        return None


async def require_superuser(
    user: Annotated[TokenUser, Depends(get_current_user)],
) -> TokenUser:
    """FastAPI 의존성: 슈퍼유저 또는 관리자 역할을 요구한다."""
    if not (user.is_admin or user.is_superuser):
        logger.warning("인가 거부(슈퍼유저/관리자 필요): username=%s role=%s", user.username, user.role)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="슈퍼유저 또는 관리자 역할이 필요합니다.",
        )
    return user


async def require_admin(
    user: Annotated[TokenUser, Depends(get_current_user)],
) -> TokenUser:
    """FastAPI 의존성: 현재 사용자가 관리자 역할을 가질 것을 요구한다."""
    if not user.is_admin:
        logger.warning("인가 거부(관리자 필요): username=%s role=%s", user.username, user.role)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자 역할이 필요합니다.",
        )
    return user


# 라우트 시그니처에서 사용하는 타입 별칭
CurrentUser = Annotated[TokenUser, Depends(get_current_user)]
OptionalUser = Annotated[TokenUser | None, Depends(get_optional_user)]
SuperUser = Annotated[TokenUser, Depends(require_superuser)]
AdminUser = Annotated[TokenUser, Depends(require_admin)]


def assert_owner_or_admin(current: "TokenUser", created_by: str | None,
                          what: str = "이 리소스") -> None:
    """행 단위 소유권 가드 — admin 이거나 생성자 본인이면 통과, 아니면 403.

    created_by 가 NULL(과거 데이터 등)이면 안전하게 admin 만 허용한다.
    """
    if current.is_admin:
        return
    if created_by and created_by == current.username:
        return
    logger.warning("소유권 거부: user=%s owner=%s target=%s", current.username, created_by, what)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"{what}의 소유자 또는 관리자만 수행할 수 있습니다.",
    )
