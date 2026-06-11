"""FastAPI 인증 — Keycloak OIDC 와 로컬 JWT 를 지원한다.

JWT 토큰 검증과 FastAPI 의존성(dependency) 함수를 제공한다.
auth_type 이 "keycloak" 이면 Keycloak 의 JWKS 엔드포인트로 검증하고,
auth_type 이 "local" 이면 로컬 HMAC 비밀키로 검증한다.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Annotated

import httpx
from cachetools import TTLCache
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

# JWKS 키를 1시간 동안 캐시 (Keycloak 모드 전용)
_jwks_cache: TTLCache = TTLCache(maxsize=1, ttl=3600)
_JWKS_CACHE_KEY = "jwks"

# 로컬 JWT 설정
LOCAL_JWT_ALGORITHM = "HS256"
LOCAL_JWT_EXPIRE_MINUTES = 480  # 8시간

# 서명키는 환경변수 ARGUS_JWT_SECRET 를 우선 사용한다. 미설정 시 개발용 기본키로
# fallback 하되(기존 배포 호환 — 발급된 토큰 무효화 없음) 경고를 남긴다.
# 운영에서는 반드시 ARGUS_JWT_SECRET 를 설정할 것.
_DEV_JWT_SECRET = "argus-catalog-local-jwt-secret-key-change-in-production"
LOCAL_JWT_SECRET_KEY = os.environ.get("ARGUS_JWT_SECRET") or _DEV_JWT_SECRET
if LOCAL_JWT_SECRET_KEY == _DEV_JWT_SECRET:
    logger.warning("ARGUS_JWT_SECRET 미설정 — 개발용 기본 JWT 서명키 사용(운영 비권장).")


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

    @property
    def _all_roles(self) -> list[str]:
        """로컬(roles)과 Keycloak(realm_roles) 양쪽 역할을 합친 목록."""
        return self.roles + self.realm_roles

    @property
    def is_admin(self) -> bool:
        return settings.auth_keycloak_admin_role in self._all_roles

    @property
    def is_superuser(self) -> bool:
        return settings.auth_keycloak_superuser_role in self._all_roles

    @property
    def is_user(self) -> bool:
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
    return f"{settings.auth_keycloak_server_url}/realms/{settings.auth_keycloak_realm}"


def _jwks_uri() -> str:
    return f"{_keycloak_base_url()}/protocol/openid-connect/certs"


def _issuer() -> str:
    return _keycloak_base_url()


async def _get_jwks() -> dict:
    """Keycloak JWKS 공개키를 조회하고 캐시한다."""
    cached = _jwks_cache.get(_JWKS_CACHE_KEY)
    if cached:
        return cached
    async with httpx.AsyncClient() as client:
        resp = await client.get(_jwks_uri())
        resp.raise_for_status()
        jwks = resp.json()
    _jwks_cache[_JWKS_CACHE_KEY] = jwks
    logger.info("JWKS 조회 완료: %s (키 %d개)", _jwks_uri(), len(jwks.get("keys", [])))
    return jwks


async def _verify_token_keycloak(token: str) -> dict:
    """JWKS 를 사용해 Keycloak JWT 토큰을 검증·디코딩한다."""
    jwks = await _get_jwks()

    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as e:
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
        _jwks_cache.pop(_JWKS_CACHE_KEY, None)
        jwks = await _get_jwks()
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                rsa_key = key
                break

    if not rsa_key:
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"토큰 검증에 실패했습니다: {e}",
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
) -> str:
    """사용자에 대한 로컬 JWT 토큰을 생성한다.

    ``must_change_password`` 를 claim 으로 실어, 게이트 미들웨어가 DB 조회 없이
    토큰만으로 강제 변경 여부를 판단하게 한다. 비밀번호 변경 시 플래그가 False 인
    새 토큰을 재발급해 게이트가 즉시 풀리도록 한다.
    """
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
    }
    return jwt.encode(payload, LOCAL_JWT_SECRET_KEY, algorithm=LOCAL_JWT_ALGORITHM)


def _verify_token_local(token: str) -> dict:
    """로컬 JWT 토큰을 검증·디코딩한다."""
    try:
        payload = jwt.decode(
            token,
            LOCAL_JWT_SECRET_KEY,
            algorithms=[LOCAL_JWT_ALGORITHM],
            options={"verify_aud": False},
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"토큰 검증에 실패했습니다: {e}",
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
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> TokenUser:
    """FastAPI 의존성: JWT 토큰에서 현재 사용자를 추출·검증한다."""
    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if settings.auth_type == "local":
        payload = _verify_token_local(token)
    else:
        payload = await _verify_token_keycloak(token)

    user = _build_token_user(payload)
    if not user.has_argus_role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="역할이 할당되지 않았습니다. 관리자에게 문의하십시오.",
        )
    return user


async def get_optional_user(request: Request) -> TokenUser | None:
    """FastAPI 의존성: 토큰이 있으면 사용자를 추출하고, 없으면 None 을 반환한다."""
    token = _extract_bearer_token(request)
    if not token:
        return None
    try:
        if settings.auth_type == "local":
            payload = _verify_token_local(token)
        else:
            payload = await _verify_token_keycloak(token)
        return _build_token_user(payload)
    except HTTPException:
        return None


async def require_superuser(
    user: Annotated[TokenUser, Depends(get_current_user)],
) -> TokenUser:
    """FastAPI 의존성: 슈퍼유저 또는 관리자 역할을 요구한다."""
    if not (user.is_admin or user.is_superuser):
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
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"{what}의 소유자 또는 관리자만 수행할 수 있습니다.",
    )
