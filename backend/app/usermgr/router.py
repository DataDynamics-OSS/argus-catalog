"""사용자 관리(User management) API.

사용자/역할 CRUD 를 제공하는 FastAPI 라우터. 모든 엔드포인트는 ``/usermgr``
prefix 를 사용한다. 비즈니스 로직은 ``service`` 모듈에 위임하고 라우터는
HTTP 변환·예외 매핑만 담당한다.

엔드포인트 요약:
    - GET    /check-user             — username/email 중복 확인
    - POST   /users                  — 사용자 생성
    - GET    /users                  — 필터·페이지네이션 적용 목록 조회
    - GET    /users/{user_id}        — 단건 조회
    - PUT    /users/{user_id}        — 프로필 필드 수정(부분 갱신)

권한 정책:
    - 조회(GET 전체): 로그인 사용자 (소유자 픽커 등 비관리자 화면에서도 사용)
    - PUT /users/{id}: 관리자 또는 본인 (계정 설정의 자기 프로필 수정)
    - 생성/삭제/역할 변경/활성·비활성: 관리자 전용
    - DELETE /users/{user_id}        — 사용자 삭제(비가역)
    - PUT    /users/{user_id}/role   — 역할 변경
    - PUT    /users/{user_id}/activate   — 계정 활성화
    - PUT    /users/{user_id}/deactivate — 계정 비활성화
    - GET    /roles                  — 역할 목록

로깅 정책: 정상 처리 INFO, 실패(404/400 매핑되는 도메인 예외) WARNING.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser
from app.core.config import settings
from app.core.database import get_session
from app.usermgr import service
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

@router.get("/check-user")
async def check_user_exists(
    _user: CurrentUser,
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
    try:
        return await service.add_user(session, req)
    except ValueError as e:
        logger.warning("add_user 실패(검증): %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/users", response_model=PaginatedUserResponse)
async def list_users(
    _user: CurrentUser,
    status: str | None = Query(None, description="Filter by status (active/inactive)"),
    role: str | None = Query(None, description="Filter by role name (Admin/User)"),
    search: str | None = Query(None, description="Search username, name, email, phone, org"),
    organization: str | None = Query(None, description="Filter by organization (exact)"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(10, ge=0, le=1000, description="Items per page (0 = all)"),
    session: AsyncSession = Depends(get_session),
):
    """필터·페이지네이션을 적용해 사용자 목록을 반환한다.

    세 가지 필터(모두 선택사항, 조합 가능):
        - ``status``: 계정 상태 정확 일치 ("active" / "inactive")
        - ``role``:   역할 이름 정확 일치 ("Admin" / "User")
        - ``search``: ``username``, ``first_name``, ``last_name``, ``email``,
                      ``phone_number`` 에 대해 ILIKE 부분 일치 (대소문자 무시)

    생성일 내림차순 정렬 후 ``page``/``page_size`` 로 페이지네이션.
    """
    result = await service.list_users(
        session, status=status, role=role, search=search, organization=organization,
        page=page, page_size=page_size,
    )
    logger.info(
        "list_users 조회: total=%d returned=%d page=%d/%d filters{status=%s role=%s search=%s}",
        result.total, len(result.items), page,
        # 0 페이지 사이즈는 "전체" 라서 총 페이지 수를 1 로 표기
        max(1, (result.total + page_size - 1) // page_size) if page_size else 1,
        status, role, search,
    )
    return result


@router.get("/users/organizations", response_model=list[str])
async def list_user_organizations(_user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """소속(조직) 고유 목록 — 소속 드롭다운 필터용. ``/users/{user_id}`` 보다 먼저 선언."""
    return await service.list_organizations(session)


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, _user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """ID 로 사용자 단건 조회. 없으면 404."""
    user = await service.get_user(session, user_id)
    if not user:
        logger.warning("get_user 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    return user


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
    user = await service.modify_user(session, user_id, req)
    if not user:
        logger.warning("modify_user 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    return user


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, _admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """사용자 영구 삭제(비가역). 마지막 관리자 삭제 등은 service 가 ``ValueError`` 로 차단."""
    try:
        if not await service.delete_user(session, user_id):
            logger.warning("delete_user 대상 없음: user_id=%d", user_id)
            raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    except ValueError as e:
        logger.warning("delete_user 차단: user_id=%d reason=%s", user_id, e)
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "message": "User deleted"}


@router.put("/users/{user_id}/role", response_model=UserResponse)
async def change_role(
    user_id: int, req: UserChangeRoleRequest, _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """사용자 역할 변경. 알 수 없는 역할이면 400."""
    try:
        user = await service.change_role(session, user_id, req)
    except ValueError as e:
        logger.warning("change_role 실패(검증): user_id=%d reason=%s", user_id, e)
        raise HTTPException(status_code=400, detail=str(e))
    if not user:
        logger.warning("change_role 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    return user


@router.put("/users/{user_id}/password", response_model=UserResponse)
async def set_password(
    user_id: int, req: UserSetPasswordRequest, _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """관리자가 대상 사용자의 비밀번호를 재설정한다(현재 비밀번호 불요).

    로컬 인증 모드 전용 — Keycloak 등 외부 인증에서는 비밀번호가 외부에서 관리되므로 400.
    """
    if settings.auth_type != "local":
        raise HTTPException(
            status_code=400,
            detail="비밀번호 변경은 로컬 인증 모드에서만 사용할 수 있습니다.",
        )
    user = await service.set_password(session, user_id, req.password)
    if not user:
        logger.warning("set_password 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    return user


@router.put("/users/{user_id}/activate", response_model=UserResponse)
async def activate_user(user_id: int, _admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """계정 상태를 ``active`` 로 변경한다."""
    user = await service.activate_user(session, user_id)
    if not user:
        logger.warning("activate_user 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    return user


@router.put("/users/{user_id}/deactivate", response_model=UserResponse)
async def deactivate_user(user_id: int, _admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """계정 상태를 ``inactive`` 로 변경한다(데이터 보존, 추후 재활성화 가능)."""
    try:
        user = await service.deactivate_user(session, user_id)
    except ValueError as e:
        logger.warning("deactivate_user 차단: user_id=%d reason=%s", user_id, e)
        raise HTTPException(status_code=400, detail=str(e))
    if not user:
        logger.warning("deactivate_user 대상 없음: user_id=%d", user_id)
        raise HTTPException(status_code=404, detail="사용자을(를) 찾을 수 없습니다.")
    return user


# ---------------------------------------------------------------------------
# 역할 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/roles", response_model=list[RoleResponse])
async def list_roles(_user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """모든 역할 목록 반환. UI 의 역할 셀렉트·필터 옵션에 사용."""
    return await service.list_roles(session)
