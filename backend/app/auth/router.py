# SPDX-License-Identifier: Apache-2.0
"""인증 엔드포인트.

Keycloak OIDC 와 로컬 JWT 두 인증 모드를 모두 지원한다. 모든 분기는
``settings.auth_type`` 값에 따라 ``_login_keycloak`` / ``_login_local`` 같이
프라이빗 헬퍼로 위임된다.

로깅 정책:
- 정상 처리는 INFO 로 결과 한 줄(사용자 식별자 + 동작) 기록
- 잘못된 입력/Keycloak 응답 실패/리프레시 실패 등 예외 가능 경로는 WARNING
- 시크릿(비밀번호·토큰)은 로그에 절대 노출하지 않는다
"""

import hashlib
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt as jose_jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    CurrentUser,
    _build_token_user,
    create_local_token,
)
from app.core.config import settings
from app.core.database import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])


# ---------------------------------------------------------------------------
# 스키마
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    refresh_expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class UserInfoResponse(BaseModel):
    sub: str
    username: str
    email: str
    first_name: str
    last_name: str
    organization: str | None = None
    department: str | None = None
    phone_number: str | None = None
    roles: list[str]
    realm_roles: list[str]
    role: str
    is_admin: bool
    is_superuser: bool
    avatar_preset_id: str | None = None
    # 최초 로그인 시 비밀번호 강제 변경 대상 여부(프론트 게이트가 사용). 기본 False.
    must_change_password: bool = False


class AvatarUpdateRequest(BaseModel):
    avatar_preset_id: str | None


class LogoutRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _token_url() -> str:
    return (
        f"{settings.auth_keycloak_server_url}"
        f"/realms/{settings.auth_keycloak_realm}"
        f"/protocol/openid-connect/token"
    )


def _logout_url() -> str:
    return (
        f"{settings.auth_keycloak_server_url}"
        f"/realms/{settings.auth_keycloak_realm}"
        f"/protocol/openid-connect/logout"
    )


def _hash_password(password: str) -> str:
    """SHA-256 으로 비밀번호를 해시한다(usermgr/service.py 와 동일)."""
    return hashlib.sha256(password.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 로그인
# ---------------------------------------------------------------------------

@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, session: AsyncSession = Depends(get_session)):
    """사용자 로그인. ``auth_type`` 에 따라 로컬 DB 또는 Keycloak 으로 위임한다."""
    if settings.auth_type == "local":
        return await _login_local(req, session)
    return await _login_keycloak(req, session)


async def _login_local(req: LoginRequest, session: AsyncSession) -> TokenResponse:
    """로컬 ``argus_users`` 테이블 기반 인증.

    실패 시 일관된 401 메시지를 반환해 username/password 어느 쪽이 틀렸는지
    드러내지 않는다(사용자 열거 공격 방지).
    """
    from app.usermgr.models import ArgusRole, ArgusUser

    result = await session.execute(
        select(ArgusUser, ArgusRole.role_id.label("role_code"))
        .join(ArgusRole, ArgusUser.role_id == ArgusRole.id)
        .where(ArgusUser.username == req.username)
    )
    row = result.first()

    if not row:
        logger.warning("로컬 로그인 실패(존재하지 않는 사용자): username=%s", req.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="사용자명 또는 비밀번호가 올바르지 않습니다.")

    user, role_code = row

    if user.password_hash != _hash_password(req.password):
        logger.warning("로컬 로그인 실패(비밀번호 불일치): username=%s", req.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="사용자명 또는 비밀번호가 올바르지 않습니다.")

    if user.status != "active":
        logger.warning("로컬 로그인 차단(비활성 계정): username=%s", req.username)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="비활성화된 계정입니다.")

    # JWT 토큰 생성 — must_change_password 를 claim 으로 실어 게이트가 판단하게 한다
    access_token = create_local_token(
        sub=str(user.id),
        username=user.username,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        role=role_code,
        must_change_password=user.must_change_password,
    )
    # 리프레시 토큰 — 더 긴 만료 시간
    refresh_token = create_local_token(
        sub=str(user.id),
        username=user.username,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        role=role_code,
        expire_minutes=60 * 24 * 7,  # 7일
        must_change_password=user.must_change_password,
    )

    logger.info("로컬 로그인: %s (role=%s)", user.username, role_code)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=480 * 60,
        refresh_expires_in=60 * 24 * 7 * 60,
    )


async def _login_keycloak(req: LoginRequest, session: AsyncSession) -> TokenResponse:
    """Keycloak token endpoint 를 통한 인증.

    1) password grant 로 토큰 요청
    2) 토큰 claim 에서 argus-* 역할 보유 여부 확인
    3) 역할이 없으면 발급된 refresh token 을 즉시 로그아웃해 회수 후 403
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _token_url(),
            data={
                "grant_type": "password",
                "client_id": settings.auth_keycloak_client_id,
                "client_secret": settings.auth_keycloak_client_secret,
                "username": req.username,
                "password": req.password,
            },
        )

    if resp.status_code != 200:
        detail = "사용자명 또는 비밀번호가 올바르지 않습니다."
        try:
            err = resp.json()
            if err.get("error_description"):
                detail = err["error_description"]
        except Exception:
            pass
        logger.warning(
            "Keycloak login failed: username=%s status=%d detail=%s",
            req.username, resp.status_code, detail,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

    data = resp.json()

    # JWT 에서 argus-* realm role 보유 여부 검증
    payload = jose_jwt.get_unverified_claims(data["access_token"])
    token_user = _build_token_user(payload)
    if not token_user.has_argus_role:
        # 역할 없는 사용자가 받아 간 refresh token 즉시 회수
        async with httpx.AsyncClient() as client:
            await client.post(
                _logout_url(),
                data={
                    "client_id": settings.auth_keycloak_client_id,
                    "client_secret": settings.auth_keycloak_client_secret,
                    "refresh_token": data["refresh_token"],
                },
            )
        logger.warning(
            "Keycloak login blocked (no argus-* role): username=%s sub=%s",
            req.username, token_user.sub,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Argus 역할이 할당되지 않았습니다. 접근 권한이 필요하면 관리자에게 문의하십시오.",
        )

    # JIT 프로비저닝 — 토큰 claim 으로 argus_users 동기화(사용자 관리 목록·소속/부서 노출).
    # 실패해도 로그인 자체는 진행(부가 동기화이므로).
    try:
        from app.usermgr.service import upsert_external_user
        role_code = (
            settings.auth_keycloak_admin_role if token_user.is_admin
            else settings.auth_keycloak_superuser_role if token_user.is_superuser
            else settings.auth_keycloak_user_role
        )
        await upsert_external_user(
            session,
            username=token_user.username,
            email=token_user.email,
            first_name=token_user.first_name,
            last_name=token_user.last_name,
            organization=token_user.organization,
            department=token_user.department,
            role_code=role_code,
        )
    except Exception as e:
        logger.warning("JIT upsert 실패(로그인은 계속 진행): username=%s err=%s", req.username, e)

    logger.info("Keycloak 로그인: username=%s sub=%s role=%s", req.username, token_user.sub, token_user.role)
    return TokenResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        token_type=data["token_type"],
        expires_in=data["expires_in"],
        refresh_expires_in=data["refresh_expires_in"],
    )


# ---------------------------------------------------------------------------
# 리프레시
# ---------------------------------------------------------------------------

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(req: RefreshRequest, session: AsyncSession = Depends(get_session)):
    """access token 재발급. ``auth_type`` 에 따라 로컬 또는 Keycloak 분기."""
    if settings.auth_type == "local":
        return await _refresh_local(req, session)
    return await _refresh_keycloak(req)


async def _refresh_local(req: RefreshRequest, session: AsyncSession) -> TokenResponse:
    """로컬 JWT refresh token 검증 후 새 토큰 쌍을 발급.

    이 시점에 사용자 상태(role/status)를 다시 확인해 토큰 발급 후 비활성화된
    계정이 계속 사용하는 상황을 차단한다.
    """
    from app.core.auth import _verify_token_local
    from app.usermgr.models import ArgusRole, ArgusUser

    try:
        payload = _verify_token_local(req.refresh_token)
    except HTTPException:
        logger.warning("로컬 리프레시 실패: 유효하지 않거나 만료된 토큰")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않거나 만료된 리프레시 토큰입니다. 다시 로그인하십시오.",
        )

    # 최신 role/status 를 반영하기 위해 DB 에서 사용자 재조회
    user_id = int(payload.get("sub", "0"))
    result = await session.execute(
        select(ArgusUser, ArgusRole.role_id.label("role_code"))
        .join(ArgusRole, ArgusUser.role_id == ArgusRole.id)
        .where(ArgusUser.id == user_id)
    )
    row = result.first()
    if not row or row[0].status != "active":
        logger.warning("로컬 리프레시 차단: user_id=%d 없음 또는 비활성", user_id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="계정을 찾을 수 없거나 비활성화되었습니다.")

    user, role_code = row

    access_token = create_local_token(
        sub=str(user.id), username=user.username, email=user.email,
        first_name=user.first_name, last_name=user.last_name, role=role_code,
        must_change_password=user.must_change_password,
    )
    refresh_token = create_local_token(
        sub=str(user.id), username=user.username, email=user.email,
        first_name=user.first_name, last_name=user.last_name, role=role_code,
        expire_minutes=60 * 24 * 7,
        must_change_password=user.must_change_password,
    )

    logger.info("로컬 토큰 갱신: username=%s role=%s", user.username, role_code)
    return TokenResponse(
        access_token=access_token, refresh_token=refresh_token,
        token_type="bearer", expires_in=480 * 60, refresh_expires_in=60 * 24 * 7 * 60,
    )


async def _refresh_keycloak(req: RefreshRequest) -> TokenResponse:
    """Keycloak token endpoint 의 refresh_token grant 를 사용해 갱신."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _token_url(),
            data={
                "grant_type": "refresh_token",
                "client_id": settings.auth_keycloak_client_id,
                "client_secret": settings.auth_keycloak_client_secret,
                "refresh_token": req.refresh_token,
            },
        )

    if resp.status_code != 200:
        logger.warning("Keycloak 리프레시 실패: status=%d", resp.status_code)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰 갱신에 실패했습니다. 다시 로그인하십시오.",
        )

    data = resp.json()
    logger.info("Keycloak 토큰 갱신")
    return TokenResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        token_type=data["token_type"],
        expires_in=data["expires_in"],
        refresh_expires_in=data["refresh_expires_in"],
    )


# ---------------------------------------------------------------------------
# 사용자 정보
# ---------------------------------------------------------------------------

async def _load_avatar_preset_id(session: AsyncSession, sub: str) -> str | None:
    """``argus_user_preferences`` 에서 사용자 아바타 preset id 조회.

    행이 없으면 ``None`` 반환. 두 인증 모드 모두 ``sub`` 로 키를 잡으므로
    분기 없이 단일 쿼리로 처리한다.
    """
    from app.usermgr.models import ArgusUserPreference

    result = await session.execute(
        select(ArgusUserPreference.avatar_preset_id).where(ArgusUserPreference.sub == sub)
    )
    return result.scalar_one_or_none()


async def _resolve_profile(
    session: AsyncSession, user: "CurrentUser",
) -> tuple[str, str, str, str | None, str | None, str | None]:
    """이름/이메일/소속/부서/전화 해석
    → (first_name, last_name, email, organization, department, phone_number).

    - Keycloak 모드: 토큰 claim 사용 (전화번호 claim 은 없어 None).
    - 로컬 모드: argus_users(sub=id) 조회 — 토큰은 로그인 시점 스냅숏이라
      프로필 수정 후 재로그인 없이도 최신 값이 반영되도록 DB 를 우선한다.
    """
    if settings.auth_type != "local":
        return (
            user.first_name, user.last_name, user.email,
            (user.organization or None), (user.department or None), None,
        )
    from app.usermgr.models import ArgusUser
    try:
        uid = int(user.sub)
    except (TypeError, ValueError):
        return user.first_name, user.last_name, user.email, None, None, None
    row = (await session.execute(
        select(
            ArgusUser.first_name, ArgusUser.last_name, ArgusUser.email,
            ArgusUser.organization, ArgusUser.department, ArgusUser.phone_number,
        ).where(ArgusUser.id == uid)
    )).first()
    if not row:
        return user.first_name, user.last_name, user.email, None, None, None
    return row[0], row[1], row[2], row[3], row[4], row[5]


async def _load_must_change_password(session: AsyncSession, user: "CurrentUser") -> bool:
    """강제 비밀번호 변경 플래그를 DB 에서 fresh 로 읽는다(로컬 모드 전용).

    토큰 claim 은 로그인 시점 스냅숏이라, 변경 직후 게이트가 즉시 풀리도록 DB 를 우선한다.
    Keycloak 모드는 로컬 비밀번호가 없으므로 항상 False.
    """
    if settings.auth_type != "local":
        return False
    from app.usermgr.models import ArgusUser
    try:
        uid = int(user.sub)
    except (TypeError, ValueError):
        return False
    val = (await session.execute(
        select(ArgusUser.must_change_password).where(ArgusUser.id == uid)
    )).scalar()
    return bool(val)


@router.get("/me", response_model=UserInfoResponse)
async def get_me(user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """현재 로그인한 사용자 정보 반환. 아바타 preset 도 함께 채워서 응답한다."""
    avatar_preset_id = await _load_avatar_preset_id(session, user.sub)
    first_name, last_name, email, organization, department, phone_number = await _resolve_profile(session, user)
    must_change_password = await _load_must_change_password(session, user)
    return UserInfoResponse(
        sub=user.sub,
        username=user.username,
        email=email,
        first_name=first_name,
        last_name=last_name,
        organization=organization,
        department=department,
        phone_number=phone_number,
        roles=user.roles,
        realm_roles=user.realm_roles,
        role=user.role,
        is_admin=user.is_admin,
        is_superuser=user.is_superuser,
        avatar_preset_id=avatar_preset_id,
        must_change_password=must_change_password,
    )


@router.put("/me/avatar", response_model=UserInfoResponse)
async def update_avatar(
    req: AvatarUpdateRequest,
    user: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """현재 사용자의 아바타 preset 을 설정하거나 ``None`` 으로 비운다.

    두 인증 모드 모두 토큰의 ``sub`` 를 키로 사용해 upsert 한다. ``sub`` 형식은
    로컬은 ``str(argus_users.id)``, Keycloak 은 UUID 이지만 같은 deployment 안에서
    ``auth_type`` 이 고정이라 충돌하지 않는다.
    """
    from app.usermgr.models import ArgusUserPreference

    result = await session.execute(
        select(ArgusUserPreference).where(ArgusUserPreference.sub == user.sub)
    )
    pref = result.scalars().first()

    if pref is None:
        # 최초 설정 — 새 row 생성
        pref = ArgusUserPreference(sub=user.sub, avatar_preset_id=req.avatar_preset_id)
        session.add(pref)
    else:
        # 기존 row 갱신 — None 으로 초기화도 허용
        pref.avatar_preset_id = req.avatar_preset_id

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.warning("아바타 업데이트 실패(commit 오류): sub=%s", user.sub, exc_info=True)
        raise HTTPException(status_code=500, detail="아바타를 업데이트하지 못했습니다.")

    logger.info("아바타 업데이트: sub=%s preset=%s", user.sub, req.avatar_preset_id)

    first_name, last_name, email, organization, department, phone_number = await _resolve_profile(session, user)
    return UserInfoResponse(
        sub=user.sub,
        username=user.username,
        email=email,
        first_name=first_name,
        last_name=last_name,
        organization=organization,
        department=department,
        phone_number=phone_number,
        roles=user.roles,
        realm_roles=user.realm_roles,
        role=user.role,
        is_admin=user.is_admin,
        is_superuser=user.is_superuser,
        avatar_preset_id=req.avatar_preset_id,
    )


class UpdateProfileRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone_number: str | None = None


@router.put("/me", response_model=UserInfoResponse)
async def update_me(
    req: UpdateProfileRequest,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """현재 사용자 프로필(이름·이메일·전화번호) 갱신.

    로컬 인증 모드 전용. Keycloak 사용자는 Keycloak Account Console 에서 직접
    변경해야 하므로 400 으로 거절한다.
    """
    if settings.auth_type != "local":
        logger.warning("keycloak 모드에서 프로필 수정 거부: sub=%s", current_user.sub)
        raise HTTPException(status_code=400, detail="프로필 수정은 로컬 인증 모드에서만 사용할 수 있습니다.")

    from app.usermgr.models import ArgusUser
    result = await session.execute(
        select(ArgusUser).where(ArgusUser.id == int(current_user.sub))
    )
    user = result.scalars().first()
    if not user:
        logger.warning("프로필 수정 대상 없음: sub=%s", current_user.sub)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")

    if req.first_name is not None:
        user.first_name = req.first_name
    if req.last_name is not None:
        user.last_name = req.last_name
    if req.email is not None:
        user.email = req.email
    if req.phone_number is not None:
        user.phone_number = req.phone_number

    await session.commit()
    await session.refresh(user)
    logger.info("프로필 수정 완료: %s", user.username)

    avatar_preset_id = await _load_avatar_preset_id(session, current_user.sub)
    return UserInfoResponse(
        sub=str(user.id),
        username=user.username,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        organization=user.organization,
        department=user.department,
        roles=current_user.roles,
        realm_roles=current_user.realm_roles,
        role=current_user.role,
        is_admin=current_user.is_admin,
        is_superuser=current_user.is_superuser,
        avatar_preset_id=avatar_preset_id,
    )


# ---------------------------------------------------------------------------
# 비밀번호 변경 (로컬 모드 전용)
# ---------------------------------------------------------------------------

@router.post("/change-password", response_model=TokenResponse)
async def change_password(
    req: ChangePasswordRequest,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """현재 사용자의 비밀번호 변경. 로컬 인증 모드 전용.

    성공 시 ``must_change_password`` 플래그를 해제하고, 해당 플래그가 False 인 새
    토큰 쌍을 재발급해 반환한다 → 강제 변경 게이트가 즉시 풀린다(최초 로그인 강제
    변경 흐름). Keycloak 사용자는 Keycloak Account Console 을 사용해야 한다.
    """
    if settings.auth_type != "local":
        logger.warning("keycloak 모드에서 비밀번호 변경 거부: sub=%s", current_user.sub)
        raise HTTPException(status_code=400, detail="비밀번호 변경은 로컬 인증 모드에서만 사용할 수 있습니다.")

    from app.usermgr.models import ArgusRole, ArgusUser

    result = await session.execute(
        select(ArgusUser, ArgusRole.role_id.label("role_code"))
        .join(ArgusRole, ArgusUser.role_id == ArgusRole.id)
        .where(ArgusUser.id == int(current_user.sub))
    )
    row = result.first()
    if not row:
        logger.warning("비밀번호 변경 대상 없음: sub=%s", current_user.sub)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")

    user, role_code = row

    if user.password_hash != _hash_password(req.current_password):
        logger.warning("비밀번호 변경 실패(현재 비밀번호 불일치): username=%s", user.username)
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")

    user.password_hash = _hash_password(req.new_password)
    user.must_change_password = False  # 강제 변경 게이트 해제
    await session.commit()
    logger.info("비밀번호 변경 완료: %s (강제변경 플래그 해제)", user.username)

    # 플래그가 해제된 새 토큰 쌍 재발급 → 클라이언트가 교체하면 게이트가 즉시 풀린다
    access_token = create_local_token(
        sub=str(user.id), username=user.username, email=user.email,
        first_name=user.first_name, last_name=user.last_name, role=role_code,
        must_change_password=False,
    )
    refresh_token = create_local_token(
        sub=str(user.id), username=user.username, email=user.email,
        first_name=user.first_name, last_name=user.last_name, role=role_code,
        expire_minutes=60 * 24 * 7, must_change_password=False,
    )
    return TokenResponse(
        access_token=access_token, refresh_token=refresh_token,
        token_type="bearer", expires_in=480 * 60, refresh_expires_in=60 * 24 * 7 * 60,
    )


# ---------------------------------------------------------------------------
# 로그아웃
# ---------------------------------------------------------------------------

@router.post("/logout")
async def logout(req: LogoutRequest):
    """로그아웃. Keycloak 모드에서는 refresh token 을 회수한다.

    로컬 모드의 JWT 는 stateless 라 서버 측에서 무효화할 상태가 없으므로 단순히
    성공 응답만 반환한다(실제 무효화는 클라이언트에서 토큰을 폐기해 처리).
    """
    if settings.auth_type == "local":
        logger.info("로컬 로그아웃(stateless, no-op)")
        return {"detail": "Logged out successfully"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _logout_url(),
            data={
                "client_id": settings.auth_keycloak_client_id,
                "client_secret": settings.auth_keycloak_client_secret,
                "refresh_token": req.refresh_token,
            },
        )

    if resp.status_code not in (200, 204):
        # 200/204 가 아니면 logout 실패지만 클라이언트 입장에서는 토큰을 버리면
        # 그만이므로 굳이 에러로 전파하지 않고 경고만 남긴다.
        logger.warning("Keycloak 로그아웃이 비정상 상태 코드 %d 반환", resp.status_code)
    else:
        logger.info("Keycloak 로그아웃 성공")

    return {"detail": "Logged out successfully"}


# ---------------------------------------------------------------------------
# 인증 모드 정보
# ---------------------------------------------------------------------------

@router.get("/type")
async def get_auth_type():
    """현재 deployment 의 인증 모드(local/keycloak) 반환.

    프론트엔드가 모드 의존 UI(예: "계정 설정" 메뉴 노출 여부)를 결정할 때 사용.
    """
    return {"auth_type": settings.auth_type}
