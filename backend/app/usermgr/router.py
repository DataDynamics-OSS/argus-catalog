# SPDX-License-Identifier: Apache-2.0
"""사용자 관리(User management) API.

사용자/역할 CRUD 를 제공하는 FastAPI 라우터. 모든 엔드포인트는 ``/usermgr``
prefix 를 사용한다. 비즈니스 로직은 ``service`` 모듈에 위임하고 라우터는
HTTP 변환·예외 매핑만 담당한다.

엔드포인트 요약:
    - GET    /check-user             — username/email 중복 확인 (관리자)
    - POST   /users                  — 사용자 생성
    - GET    /users                  — 필터·정렬·페이지네이션 적용 목록 조회
    - GET    /users/organizations    — 소속 고유 목록
    - GET    /users/{user_id}        — 단건 조회
    - GET    /users/{user_id}/owned-assets — 소유 자산 건수 (관리자, 삭제 전 이관 안내)
    - PUT    /users/{user_id}        — 프로필 필드 수정(부분 갱신)
    - DELETE /users/{user_id}        — 사용자 삭제(비가역, transfer_to 로 자산 이관)
    - PUT    /users/{user_id}/role   — 역할 변경
    - PUT    /users/{user_id}/password   — 관리자 비밀번호 재설정
    - PUT    /users/{user_id}/activate   — 계정 활성화
    - PUT    /users/{user_id}/unlock     — 로그인 잠금 해제
    - PUT    /users/{user_id}/deactivate — 계정 비활성화
    - GET    /roles                  — 역할 목록

권한 정책:
    - 조회(GET 전체): 로그인 사용자 (소유자 픽커 등 비관리자 화면에서도 사용)
    - PUT /users/{id}: 관리자 또는 본인 (계정 설정의 자기 프로필 수정)
    - 생성/삭제/역할 변경/활성·비활성: 관리자 전용
    - 본인 계정은 삭제·비활성화·역할 변경할 수 없다(다른 관리자가 처리 — 실수로 자기 권한을 잃지 않도록)
    - 아이디·이메일 중복(DB 고유 제약)은 409
    - 비관리자 조회에는 전화번호를 싣지 않고 전화번호 검색도 하지 않는다(소유자 선택 등에는
      이름·아이디·이메일·소속만 필요). 중복 확인(check-user)은 관리자 전용.
    - Keycloak 모드: 계정·역할·비밀번호·이름·이메일·소속은 Keycloak 이 원천(로그인마다 덮어씀)이라
      생성·역할 변경·비밀번호 재설정·해당 필드 수정을 400 으로 거부한다. 활성/비활성·삭제·전화번호는
      로컬에서 관리한다(비활성은 Keycloak 로그인도 막는다).
    - 상태·역할·비밀번호를 바꾸면 계정 상태 캐시를 비워, 이미 발급된 토큰에도 바로 반영한다.
    - 소유 자산이 있는 사용자 삭제는 transfer_to 로 넘겨받을 사용자를 지정해야 한다(없으면 409).
    - 생성·수정·삭제·역할·비밀번호·활성/비활성·잠금 해제는 관리 감사 로그(category=users)에 남긴다.

로깅 정책: 변경 처리 INFO(서비스 계층), 조회는 DEBUG, 실패(404/400/403/409 로 매핑되는 예외) WARNING.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record as audit_record
from app.audit.service import secret_change
from app.core.auth import AdminUser, CurrentUser, invalidate_account_cache
from app.core.config import settings
from app.core.database import get_session
from app.usermgr import ownership, service
from app.usermgr.schemas import (
    PaginatedUserResponse,
    RoleResponse,
    UserAddRequest,
    UserChangeRoleRequest,
    UserModifyRequest,
    UserResponse,
    UserSetPasswordRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/usermgr", tags=["usermgr"])


# ---------------------------------------------------------------------------
# 사용자 엔드포인트
# ---------------------------------------------------------------------------


async def _is_self(session: AsyncSession, current, user_id: int) -> bool:
    """대상이 요청자 본인인지 — 로컬 모드는 sub(=사용자 ID), Keycloak 모드는 아이디로 비교."""
    if current.sub == str(user_id):
        return True
    target = await service.get_user(session, user_id)
    return target is not None and bool(current.username) and target.username == current.username


async def _forbid_self(session: AsyncSession, current, user_id: int, action: str) -> None:
    """대상이 본인 계정이면 400 — 관리자가 실수로 자기 권한·계정을 잃지 않도록."""
    if await _is_self(session, current, user_id):
        logger.warning("본인 계정 %s 차단: actor=%s target=%d", action, current.username, user_id)
        raise HTTPException(status_code=400, detail=f"본인 계정은 {action}할 수 없습니다. 다른 관리자에게 요청하세요.")


KEYCLOAK_MANAGED = "Keycloak 인증 모드에서는 계정·역할·비밀번호·프로필을 Keycloak 에서 관리합니다."
# Keycloak 로그인 때마다 토큰 claim 으로 덮어쓰는 필드 — 로컬 수정이 남지 않으므로 거부한다
_KEYCLOAK_SYNCED_FIELDS = {"first_name", "last_name", "email", "organization", "department"}


def _forbid_keycloak(action: str) -> None:
    """Keycloak(비로컬) 모드면 400 — 계정·역할·비밀번호는 Keycloak 이 원천이다."""
    if settings.auth_type != "local":
        logger.warning("keycloak 모드에서 %s 거부", action)
        raise HTTPException(status_code=400, detail=KEYCLOAK_MANAGED)


def _mask_private(user, current):
    """비관리자에게는 본인 외 사용자의 전화번호를 보이지 않는다."""
    if current.is_admin or current.sub == str(user.id) or current.username == user.username:
        return user
    return user.model_copy(update={"phone_number": None})


_MODIFY_FIELDS = ("first_name", "last_name", "email", "organization", "department", "phone_number")
_ROLE_LABEL = {"argus-admin": "관리자", "argus-superuser": "슈퍼유저", "argus-user": "사용자"}


async def _audit(session, actor, action: str, target: str | None, summary: str, detail=None) -> None:
    """사용자 관리 감사 로그(category=users) 기록."""
    await audit_record(session, actor, "users", action, target=target, summary=summary, detail=detail)


def _conflict(e: IntegrityError) -> HTTPException:
    """고유 제약 위반(아이디·이메일 중복) → 409 예외로 변환."""
    logger.warning("사용자 저장 충돌(고유 제약): %s", e.orig)
    return HTTPException(status_code=409, detail="이미 사용 중인 아이디 또는 이메일입니다.")

@router.get("/check-user")
async def check_user_exists(
    _admin: AdminUser,
    username: str | None = Query(None, description="Username to check"),
    email: str | None = Query(None, description="Email to check"),
    session: AsyncSession = Depends(get_session),
):
    """사용자명 또는 이메일이 이미 존재하는지 확인한다.

    UI 의 실시간 중복 검사에 사용. ``username`` / ``email`` 중 최소 하나는 필수.

    응답 예: ``{"username_exists": true, "email_exists": false}`` — 요청된 필드만 포함.
    """
    if not username and not email:
        logger.warning("check-user 호출 시 username/email 누락")
        raise HTTPException(status_code=400, detail="사용자명 또는 이메일은(는) 필수입니다.")
    result = await service.check_user_exists(session, username=username, email=email)
    # 결과가 모두 false 일 때는 noisy 하니까 적중한 경우만 INFO 로 흔적을 남긴다.
    if result.get("username_exists") or result.get("email_exists"):
        logger.info(
            "check-user 중복 감지: username=%s(%s) email=%s(%s)",
            username, result.get("username_exists", False),
            email, result.get("email_exists", False),
        )
    return result


@router.post("/users", response_model=UserResponse)
async def add_user(req: UserAddRequest, _admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """새 사용자 생성.

    비밀번호는 저장 전 해시 처리한다. 지정한 역할이 DB 에 없으면 400.
    신규 사용자는 ``active`` 상태로 생성된다.
    """
    _forbid_keycloak("사용자 생성")
    try:
        user = await service.add_user(session, req)
    except IntegrityError as e:
        await session.rollback()
        raise _conflict(e)
    except ValueError as e:
        logger.warning("add_user 실패(검증): %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    await _audit(session, _admin, "user.create", user.username,
                 f"사용자 생성: {user.username} ({_ROLE_LABEL.get(user.role, user.role)})",
                 {"role": user.role, "email": user.email, "must_change_password": req.must_change_password})
    return user


@router.get("/users", response_model=PaginatedUserResponse)
async def list_users(
    current: CurrentUser,
    status: str | None = Query(None, description="Filter by status (active/inactive)"),
    role: str | None = Query(None, description="role_id 정확 일치 (argus-admin/argus-superuser/argus-user)"),
    search: str | None = Query(None, description="Search username, name, email, phone, org"),
    organization: str | None = Query(None, description="Filter by organization (exact)"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(10, ge=0, le=1000, description="Items per page (0 = all)"),
    sort: str | None = Query(None, description="정렬 기준: username·name·email·organization·department·status·role·created_at·last_login_at"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    session: AsyncSession = Depends(get_session),
):
    """필터·페이지네이션을 적용해 사용자 목록을 반환한다.

    필터(모두 선택사항, 조합 가능):
        - ``status``: 계정 상태 정확 일치 ("active" / "inactive")
        - ``role``:   역할 식별자(role_id) 정확 일치 ("argus-admin" / "argus-superuser" / "argus-user")
        - ``organization``: 소속 정확 일치
        - ``search``: ``username``, ``first_name``, ``last_name``, 성+이름, ``email``, 소속, 부서
                      (관리자면 ``phone_number`` 도)에 대해 ILIKE 부분 일치 (대소문자 무시)

    ``sort``/``order`` 로 정렬(기본 생성일 내림차순) 후 ``page``/``page_size`` 로 페이지네이션.
    잘못된 ``sort`` 는 400. 비관리자 응답은 본인 외 전화번호를 지운다.
    """
    try:
        result = await service.list_users(
            session, status=status, role=role, search=search, organization=organization,
            page=page, page_size=page_size, include_private=current.is_admin, sort=sort, order=order,
        )
    except ValueError as e:
        logger.warning("list_users 잘못된 정렬 기준: sort=%s", sort)
        raise HTTPException(status_code=400, detail=str(e))
    if not current.is_admin:
        result.items = [_mask_private(u, current) for u in result.items]
    logger.debug(
        # 검색어는 이름·이메일·전화번호일 수 있어 길이만 남긴다
        "list_users 조회: total=%d returned=%d page=%d/%d filters{status=%s role=%s search_len=%d}",
        result.total, len(result.items), page,
        # 0 페이지 사이즈는 "전체" 라서 총 페이지 수를 1 로 표기
        max(1, (result.total + page_size - 1) // page_size) if page_size else 1,
        status, role, len(search or ""),
    )
    return result


@router.get("/users/organizations", response_model=list[str])
async def list_user_organizations(_user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """소속(조직) 고유 목록 — 소속 드롭다운 필터용. ``/users/{user_id}`` 보다 먼저 선언."""
    return await service.list_organizations(session)


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """ID 로 사용자 단건 조회. 없으면 404."""
    user = await service.get_user(session, user_id)
    if not user:
        logger.warning("get_user 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    return _mask_private(user, current)


@router.get("/users/{user_id}/owned-assets")
async def owned_assets(user_id: int, _admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """사용자가 소유한(편집 권한이 아이디로 묶인) 자산의 종류별 건수 — 삭제 전 이관 안내용."""
    user = await service.get_user(session, user_id)
    if not user:
        logger.warning("owned_assets 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    items = await ownership.count_owned(session, user.username)
    return {"username": user.username, "total": sum(i["count"] for i in items), "items": items}


@router.put("/users/{user_id}", response_model=UserResponse)
async def modify_user(
    user_id: int,
    req: UserModifyRequest,
    current: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """사용자 프로필 부분 갱신. ``username`` 과 ``role`` 은 이 엔드포인트로 변경할 수 없다.

    권한: 관리자 또는 본인(계정 설정의 자기 프로필 수정)만 허용.
    """
    if not current.is_admin and current.sub != str(user_id):
        logger.warning("modify_user 권한 없음: actor=%s target=%d", current.sub, user_id)
        raise HTTPException(status_code=403, detail="본인 또는 관리자만 수정할 수 있습니다.")
    if settings.auth_type != "local" and req.model_fields_set & _KEYCLOAK_SYNCED_FIELDS:
        logger.warning("keycloak 모드에서 동기화 필드 수정 거부: target=%d fields=%s",
                       user_id, sorted(req.model_fields_set & _KEYCLOAK_SYNCED_FIELDS))
        raise HTTPException(status_code=400, detail=KEYCLOAK_MANAGED + " 이 화면에서는 전화번호만 바꿀 수 있습니다.")
    before = await service.get_user(session, user_id)
    try:
        user = await service.modify_user(session, user_id, req)
    except IntegrityError as e:
        await session.rollback()
        raise _conflict(e)
    if not user:
        logger.warning("modify_user 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    changes = {}
    for f in _MODIFY_FIELDS:
        old, new = getattr(before, f, None), getattr(user, f, None)
        if (old or None) != (new or None):
            # 전화번호는 개인정보 — 값 없이 변경 사실만
            changes[f] = secret_change(old, new) if f == "phone_number" else {"old": old, "new": new}
    if changes:
        await _audit(session, current, "user.modify", user.username,
                     f"사용자 정보 수정: {user.username} ({', '.join(changes)})", {"changes": changes})
    return user


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int, admin: AdminUser,
    transfer_to: str | None = Query(None, description="소유 자산을 넘겨받을 사용자 아이디"),
    session: AsyncSession = Depends(get_session),
):
    """사용자 영구 삭제(비가역). 본인 계정·마지막 활성 관리자는 차단.

    소유 자산이 있으면 ``transfer_to`` 로 넘겨받을 활성 사용자를 지정해야 하며, 없으면 409
    ``{"code": "owned_assets", "assets": [...]}``.
    """
    await _forbid_self(session, admin, user_id, "삭제")
    target = await service.get_user(session, user_id)
    owned = ([a for a in await ownership.count_owned(session, target.username) if a["count"]]
             if target else [])
    try:
        if not await service.delete_user(session, user_id, transfer_to=transfer_to or None):
            logger.warning("delete_user 대상 없음: user_id=%d", user_id)
            raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    except service.OwnedAssetsError as e:
        await session.rollback()
        logger.warning("delete_user 차단(소유 자산): user_id=%d assets=%s", user_id, e.assets)
        raise HTTPException(status_code=409, detail={"code": "owned_assets", "message": str(e), "assets": e.assets})
    except ValueError as e:
        await session.rollback()
        logger.warning("delete_user 차단: user_id=%d reason=%s", user_id, e)
        raise HTTPException(status_code=400, detail=str(e))
    invalidate_account_cache()
    await _audit(session, admin, "user.delete", target.username if target else str(user_id),
                 f"사용자 삭제: {target.username if target else user_id}"
                 + (f" (자산 → {transfer_to})" if owned else ""),
                 {"transfer_to": transfer_to if owned else None, "owned_assets": owned})
    return {"status": "ok", "message": "User deleted"}


@router.put("/users/{user_id}/role", response_model=UserResponse)
async def change_role(
    user_id: int, req: UserChangeRoleRequest, admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """사용자 역할 변경. 알 수 없는 역할·본인 계정·마지막 활성 관리자 강등이면 400."""
    _forbid_keycloak("역할 변경")
    target = await service.get_user(session, user_id)
    if target is not None and target.role != req.role.value:
        await _forbid_self(session, admin, user_id, "역할 변경")
    try:
        user = await service.change_role(session, user_id, req)
    except ValueError as e:
        logger.warning("change_role 실패(검증): user_id=%d reason=%s", user_id, e)
        raise HTTPException(status_code=400, detail=str(e))
    if not user:
        logger.warning("change_role 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    invalidate_account_cache()
    if target is not None and target.role != user.role:
        await _audit(session, admin, "user.role", user.username,
                     f"역할 변경: {user.username} {_ROLE_LABEL.get(target.role, target.role)} → "
                     f"{_ROLE_LABEL.get(user.role, user.role)}",
                     {"old": target.role, "new": user.role})
    return user


@router.put("/users/{user_id}/password", response_model=UserResponse)
async def set_password(
    user_id: int, req: UserSetPasswordRequest, admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """관리자가 대상 사용자의 비밀번호를 재설정한다(현재 비밀번호 불요).

    로컬 인증 모드 전용 — Keycloak 등 외부 인증에서는 비밀번호가 외부에서 관리되므로 400.
    """
    _forbid_keycloak("비밀번호 재설정")
    must_change = req.must_change_password and not await _is_self(session, admin, user_id)
    try:
        user = await service.set_password(session, user_id, req.password, must_change_password=must_change)
    except ValueError as e:
        logger.warning("set_password 실패(정책): user_id=%d", user_id)
        raise HTTPException(status_code=400, detail=str(e))
    if not user:
        logger.warning("set_password 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    invalidate_account_cache()  # 비밀번호 지문이 바뀌어 대상의 기존 로그인이 끊긴다
    await _audit(session, admin, "user.password_reset", user.username,
                 f"비밀번호 재설정: {user.username}" + (" (다음 로그인 때 변경)" if must_change else ""),
                 {"must_change_password": must_change})
    return user


@router.put("/users/{user_id}/activate", response_model=UserResponse)
async def activate_user(user_id: int, _admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """계정 상태를 ``active`` 로 변경한다."""
    user = await service.activate_user(session, user_id)
    if not user:
        logger.warning("activate_user 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    invalidate_account_cache()
    await _audit(session, _admin, "user.activate", user.username, f"계정 활성화: {user.username}")
    return user


@router.put("/users/{user_id}/unlock", response_model=UserResponse)
async def unlock_user(user_id: int, admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """로그인 실패 누적 잠금을 해제한다(실패 횟수 초기화)."""
    user = await service.unlock_user(session, user_id)
    if not user:
        logger.warning("unlock_user 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    await _audit(session, admin, "user.unlock", user.username, f"로그인 잠금 해제: {user.username}")
    return user


@router.put("/users/{user_id}/deactivate", response_model=UserResponse)
async def deactivate_user(user_id: int, admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """계정 상태를 ``inactive`` 로 변경한다(데이터 보존, 추후 재활성화 가능). 본인·마지막 활성 관리자는 차단."""
    await _forbid_self(session, admin, user_id, "비활성화")
    try:
        user = await service.deactivate_user(session, user_id)
    except ValueError as e:
        logger.warning("deactivate_user 차단: user_id=%d reason=%s", user_id, e)
        raise HTTPException(status_code=400, detail=str(e))
    if not user:
        logger.warning("deactivate_user 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    invalidate_account_cache()  # 이미 로그인한 세션도 다음 요청부터 401
    await _audit(session, admin, "user.deactivate", user.username, f"계정 비활성화: {user.username}")
    return user


# ---------------------------------------------------------------------------
# 역할 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/roles", response_model=list[RoleResponse])
async def list_roles(_user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """모든 역할 목록 반환. UI 의 역할 셀렉트·필터 옵션에 사용."""
    logger.debug("역할 목록 조회")
    return await service.list_roles(session)
