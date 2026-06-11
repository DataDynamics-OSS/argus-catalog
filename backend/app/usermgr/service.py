"""사용자 관리 서비스(비즈니스 로직 계층).

라우터에서 호출하는 사용자/역할 CRUD 로직을 모은다. 모든 DB 상호작용은
SQLAlchemy 비동기 세션을 통해 이뤄진다.

구조 메모:
- 모든 public 함수는 첫 인자로 ``AsyncSession`` 을 받는다.
- 데이터 변경 함수는 ``session.commit()`` 후 ``session.refresh()`` 로 갱신된
  필드(``updated_at`` 등)를 다시 읽어와 응답에 반영한다.
- ``_build_user_response()`` 가 ``role_id`` 외래키를 역할 이름 문자열로 변환해준다.
- 비밀번호 해싱은 ``_hash_password()`` (SHA-256). 운영 환경에서는 더 강한
  해시(bcrypt/argon2)로 교체할 여지가 있다.

로깅 정책: 생성·수정·삭제·역할 변경 등 변경 동작은 INFO, "마지막 관리자 보호"
같은 도메인 규칙 위반은 호출 측(router)에서 WARNING 처리한다.
"""

import hashlib
import logging

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.usermgr.models import ArgusRole, ArgusUser
from app.usermgr.schemas import (
    PaginatedUserResponse,
    RoleName,
    RoleResponse,
    UserAddRequest,
    UserChangeRoleRequest,
    UserModifyRequest,
    UserResponse,
    UserStatus,
)

logger = logging.getLogger(__name__)


async def _is_last_admin(session: AsyncSession, user_id: int) -> bool:
    """주어진 user_id 가 시스템의 유일한 관리자(admin)인지 확인.

    "마지막 관리자 보호" 규칙을 적용해 admin 0명 상태(시스템 락아웃)를 막는다.
    """
    from app.usermgr.schemas import RoleName
    admin_role = await _get_role_by_role_id(session, RoleName.ADMIN.value)
    if not admin_role:
        return False
    # 관리자 수 카운트
    admin_count = (await session.execute(
        select(func.count()).where(ArgusUser.role_id == admin_role.id)
    )).scalar() or 0
    if admin_count > 1:
        return False
    # 이 사용자가 관리자인지 확인
    result = await session.execute(select(ArgusUser).where(ArgusUser.id == user_id))
    user = result.scalars().first()
    return user is not None and user.role_id == admin_role.id


def _hash_password(password: str) -> str:
    """SHA-256 비밀번호 해시.

    평문 비밀번호를 UTF-8 로 인코딩한 뒤 16진 다이제스트를 반환한다.
    사용자 생성·비밀번호 변경 시 사용.
    """
    return hashlib.sha256(password.encode()).hexdigest()


async def _get_role_by_role_id(session: AsyncSession, role_id: str) -> ArgusRole | None:
    """``role_id`` 식별자(예: ``"argus-admin"``)로 역할 조회."""
    result = await session.execute(select(ArgusRole).where(ArgusRole.role_id == role_id))
    return result.scalars().first()


async def _build_user_response(session: AsyncSession, user: ArgusUser) -> UserResponse:
    """``ArgusUser`` ORM 객체에서 역할명을 resolve 해 ``UserResponse`` 로 변환."""
    result = await session.execute(select(ArgusRole).where(ArgusRole.id == user.role_id))
    role = result.scalars().first()
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        organization=user.organization,
        department=user.department,
        phone_number=user.phone_number,
        status=UserStatus(user.status),
        role=role.role_id if role else "unknown",
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


# ---------------------------------------------------------------------------
# 사용자 처리
# ---------------------------------------------------------------------------

async def check_user_exists(
    session: AsyncSession, username: str | None = None, email: str | None = None
) -> dict[str, bool]:
    """``argus_users`` 에서 username/email 중복 여부 확인.

    제공된 필드만 검사한다. UI 의 실시간 중복 검증에 사용.

    Returns:
        제공된 필드별 boolean flag dict. 예: ``{"username_exists": True, "email_exists": False}``
    """
    result: dict[str, bool] = {}
    if username:
        row = await session.execute(
            select(ArgusUser).where(ArgusUser.username == username)
        )
        result["username_exists"] = row.scalars().first() is not None
    if email:
        row = await session.execute(
            select(ArgusUser).where(ArgusUser.email == email)
        )
        result["email_exists"] = row.scalars().first() is not None
    return result


async def add_user(session: AsyncSession, req: UserAddRequest) -> UserResponse:
    """새 사용자 계정 생성.

    역할 이름을 ``role_id`` 로 변환하고 비밀번호를 해싱한 뒤 새 row 를 삽입한다.

    Raises:
        ValueError: 지정된 역할이 DB 에 없으면 발생. 라우터가 400 으로 변환.
    """
    role = await _get_role_by_role_id(session, req.role.value)
    if not role:
        logger.warning("add_user: 알 수 없는 역할 '%s'", req.role.value)
        raise ValueError(f"Role '{req.role.value}' not found")

    user = ArgusUser(
        username=req.username,
        email=req.email,
        first_name=req.first_name,
        last_name=req.last_name,
        organization=req.organization,
        department=req.department,
        phone_number=req.phone_number,
        password_hash=_hash_password(req.password),
        status=UserStatus.ACTIVE.value,
        must_change_password=req.must_change_password,
        role_id=role.id,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    logger.info("사용자 생성: %s (id=%d)", user.username, user.id)
    return await _build_user_response(session, user)


async def delete_user(session: AsyncSession, user_id: int) -> bool:
    """ID 로 사용자를 영구 삭제. 삭제했으면 ``True``, 사용자가 없으면 ``False`` 반환.

    "마지막 관리자 보호" 규칙 위반 시 ``ValueError`` 를 발생시킨다(라우터가 400 으로 변환).
    """
    if await _is_last_admin(session, user_id):
        logger.warning("delete_user 차단(마지막 관리자): user_id=%d", user_id)
        raise ValueError("Cannot delete the only admin. At least one admin must remain.")

    result = await session.execute(select(ArgusUser).where(ArgusUser.id == user_id))
    user = result.scalars().first()
    if not user:
        return False
    await session.delete(user)
    await session.commit()
    logger.info("사용자 삭제: %s (id=%d)", user.username, user.id)
    return True


async def modify_user(
    session: AsyncSession, user_id: int, req: UserModifyRequest
) -> UserResponse | None:
    """사용자 프로필 부분 갱신.

    요청에 명시된 필드(``None`` 아님)만 갱신한다. ``username`` 과 ``role_id`` 는
    이 함수로 변경할 수 없다(별도 엔드포인트 사용).
    """
    result = await session.execute(select(ArgusUser).where(ArgusUser.id == user_id))
    user = result.scalars().first()
    if not user:
        return None

    # 요청에 명시적으로 들어온 필드만 갱신 — None 인 필드는 기존 값 유지
    if req.first_name is not None:
        user.first_name = req.first_name
    if req.last_name is not None:
        user.last_name = req.last_name
    if req.email is not None:
        user.email = req.email
    if req.organization is not None:
        user.organization = req.organization
    if req.department is not None:
        user.department = req.department
    if req.phone_number is not None:
        user.phone_number = req.phone_number

    await session.commit()
    await session.refresh(user)
    logger.info("사용자 수정: %s (id=%d)", user.username, user.id)
    return await _build_user_response(session, user)


async def change_role(
    session: AsyncSession, user_id: int, req: UserChangeRoleRequest
) -> UserResponse | None:
    """사용자 역할 변경.

    Raises:
        ValueError: 알 수 없는 역할이거나, 마지막 admin 을 다른 역할로 강등하려는 경우.
    """
    result = await session.execute(select(ArgusUser).where(ArgusUser.id == user_id))
    user = result.scalars().first()
    if not user:
        return None

    # 마지막 관리자 보호: admin → 다른 역할로 바꾸려는 경우 차단
    from app.usermgr.schemas import RoleName
    admin_role = await _get_role_by_role_id(session, RoleName.ADMIN.value)
    if admin_role and user.role_id == admin_role.id and req.role.value != RoleName.ADMIN.value:
        if await _is_last_admin(session, user_id):
            logger.warning("change_role 차단(마지막 관리자 강등): user_id=%d target=%s", user_id, req.role.value)
            raise ValueError("Cannot change the role of the only admin. At least one admin must remain.")

    role = await _get_role_by_role_id(session, req.role.value)
    if not role:
        logger.warning("change_role: 알 수 없는 역할 '%s' user_id=%d", req.role.value, user_id)
        raise ValueError(f"Role '{req.role.value}' not found")

    user.role_id = role.id
    await session.commit()
    await session.refresh(user)
    logger.info("사용자 역할 변경: %s -> %s (id=%d)", user.username, req.role.value, user.id)
    return await _build_user_response(session, user)


async def activate_user(session: AsyncSession, user_id: int) -> UserResponse | None:
    """계정 상태를 ``active`` 로 변경(로그인 허용)."""
    result = await session.execute(select(ArgusUser).where(ArgusUser.id == user_id))
    user = result.scalars().first()
    if not user:
        return None

    user.status = UserStatus.ACTIVE.value
    await session.commit()
    await session.refresh(user)
    logger.info("사용자 활성화: %s (id=%d)", user.username, user.id)
    return await _build_user_response(session, user)


async def deactivate_user(session: AsyncSession, user_id: int) -> UserResponse | None:
    """계정 상태를 ``inactive`` 로 변경(로그인 차단, 데이터 보존).

    Raises:
        ValueError: 마지막 관리자를 비활성화하려는 경우 차단.
    """
    if await _is_last_admin(session, user_id):
        logger.warning("deactivate_user 차단(마지막 관리자): user_id=%d", user_id)
        raise ValueError("Cannot deactivate the only admin. At least one active admin must remain.")

    result = await session.execute(select(ArgusUser).where(ArgusUser.id == user_id))
    user = result.scalars().first()
    if not user:
        return None

    user.status = UserStatus.INACTIVE.value
    await session.commit()
    await session.refresh(user)
    logger.info("사용자 비활성화: %s (id=%d)", user.username, user.id)
    return await _build_user_response(session, user)


async def set_password(
    session: AsyncSession, user_id: int, new_password: str,
) -> UserResponse | None:
    """관리자가 대상 사용자의 비밀번호를 직접 재설정한다(현재 비밀번호 불요).

    평문 ``new_password`` 를 ``_hash_password`` 로 해시해 저장한다. 로컬 인증 모드 전용
    (호출 측 라우터에서 모드를 검증). 사용자가 없으면 ``None``.
    """
    result = await session.execute(select(ArgusUser).where(ArgusUser.id == user_id))
    user = result.scalars().first()
    if not user:
        return None

    user.password_hash = _hash_password(new_password)
    await session.commit()
    await session.refresh(user)
    logger.info("관리자에 의한 비밀번호 재설정: %s (id=%d)", user.username, user.id)
    return await _build_user_response(session, user)


async def get_user(session: AsyncSession, user_id: int) -> UserResponse | None:
    """ID 로 단건 조회. 없으면 ``None`` 반환."""
    result = await session.execute(select(ArgusUser).where(ArgusUser.id == user_id))
    user = result.scalars().first()
    if not user:
        return None
    return await _build_user_response(session, user)


async def list_users(
    session: AsyncSession,
    status: str | None = None,
    role: str | None = None,
    search: str | None = None,
    organization: str | None = None,
    page: int = 1,
    page_size: int = 10,
) -> PaginatedUserResponse:
    """필터·페이지네이션을 적용한 사용자 목록 조회.

    동적 WHERE 절을 구성한다:
        1. ``argus_users`` ↔ ``argus_roles`` 조인으로 역할명 함께 조회
        2. ``status`` / ``role`` / ``search`` 가 주어지면 각각 WHERE 추가
        3. 페이지네이션 메타용 총 건수 별도 카운트
        4. ``created_at`` 내림차순 정렬 후 OFFSET/LIMIT 적용

    ``page_size=0`` 은 페이지네이션 없이 전체 반환.
    """
    # 사용자와 역할을 조인해 결과에 역할명을 포함시킨다
    base = (
        select(ArgusUser, ArgusRole.role_id.label("role_code"))
        .join(ArgusRole, ArgusUser.role_id == ArgusRole.id)
    )

    # 상태 정확 일치 필터(추후 다중 값 확장을 위해 IN 사용)
    if status:
        base = base.where(ArgusUser.status.in_([status]))

    # role_id 정확 일치 필터
    if role:
        base = base.where(ArgusRole.role_id.in_([role]))

    # 소속 정확 일치 필터(드롭다운 선택)
    if organization:
        base = base.where(ArgusUser.organization == organization)

    # 다중 필드 ILIKE 부분 일치(대소문자 무시). 앞뒤로 % 와일드카드를 자동 부착.
    # ``last_name+first_name`` 을 합쳐서도 비교 — 한국식 "홍길동" 처럼 통째로
    # 입력되는 경우를 받기 위해 (last_name="홍", first_name="길동").
    if search:
        pattern = f"%{search}%"
        base = base.where(
            or_(
                ArgusUser.username.ilike(pattern),
                ArgusUser.first_name.ilike(pattern),
                ArgusUser.last_name.ilike(pattern),
                func.concat(ArgusUser.last_name, ArgusUser.first_name).ilike(pattern),
                ArgusUser.email.ilike(pattern),
                ArgusUser.phone_number.ilike(pattern),
                ArgusUser.organization.ilike(pattern),
                ArgusUser.department.ilike(pattern),
            )
        )

    # 페이지네이션 전 총 건수 별도 카운트(응답 메타에 포함됨)
    count_query = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    # 최신순 정렬 후 OFFSET/LIMIT 적용. page_size=0 이면 전체 반환.
    query = base.order_by(ArgusUser.created_at.desc())
    if page_size > 0:
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
    result = await session.execute(query)
    rows = result.all()

    # (ArgusUser, role_code) 튜플을 UserResponse Pydantic 모델로 변환
    items = [
        UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            organization=user.organization,
            department=user.department,
            phone_number=user.phone_number,
            status=UserStatus(user.status),
            role=role_code or "unknown",
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
        for user, role_code in rows
    ]

    return PaginatedUserResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


async def upsert_external_user(
    session: AsyncSession,
    *,
    username: str,
    email: str,
    first_name: str,
    last_name: str,
    organization: str | None,
    department: str | None,
    role_code: str,
) -> None:
    """Keycloak/LDAP 로그인 사용자를 ``argus_users`` 에 JIT upsert.

    인증은 Keycloak 이 담당하므로 로컬 비밀번호는 사용하지 않는다(placeholder 저장).
    소속/부서 등 프로필은 토큰 claim 기준으로 매 로그인 시 동기화(원천=Keycloak).
    역할(role_code)에 해당하는 argus_roles 가 없으면 조용히 skip.
    """
    role = (await session.execute(
        select(ArgusRole).where(ArgusRole.role_id == role_code)
    )).scalars().first()
    if role is None:
        logger.warning("JIT upsert 건너뜀: 역할 '%s' 없음", role_code)
        return

    org = organization or None
    dept = department or None
    safe_email = email or f"{username}@keycloak.local"

    existing = (await session.execute(
        select(ArgusUser).where(ArgusUser.username == username)
    )).scalars().first()

    if existing:
        existing.email = safe_email
        existing.first_name = first_name or existing.first_name or username
        existing.last_name = last_name or existing.last_name or ""
        existing.organization = org
        existing.department = dept
        existing.role_id = role.id
        existing.status = "active"
    else:
        session.add(ArgusUser(
            username=username,
            email=safe_email,
            first_name=first_name or username,
            last_name=last_name or "",
            organization=org,
            department=dept,
            phone_number=None,
            password_hash="(external/keycloak)",  # 로컬 인증 미사용
            status="active",
            role_id=role.id,
        ))
    await session.commit()
    logger.info("외부 사용자 JIT upsert 완료: %s (role=%s)", username, role_code)


async def list_organizations(session: AsyncSession) -> list[str]:
    """등록된 소속(조직) 고유 목록 — 소속 드롭다운 필터용. 빈 값 제외, 이름순."""
    rows = (await session.execute(
        select(ArgusUser.organization)
        .where(ArgusUser.organization.isnot(None), ArgusUser.organization != "")
        .distinct()
        .order_by(ArgusUser.organization)
    )).scalars().all()
    return [o for o in rows if o]


# ---------------------------------------------------------------------------
# 역할 처리
# ---------------------------------------------------------------------------

async def list_roles(session: AsyncSession) -> list[RoleResponse]:
    """모든 역할 목록 반환. ID 오름차순.

    UI 의 역할 셀렉트·필터 옵션 채우기에 사용.
    """
    result = await session.execute(select(ArgusRole).order_by(ArgusRole.id))
    roles = result.scalars().all()
    return [RoleResponse.model_validate(r) for r in roles]


async def seed_roles(session: AsyncSession) -> None:
    """기본 역할(Admin/Superuser/User) 이 없으면 삽입한다.

    애플리케이션 startup lifespan 에서 호출되며, 이미 존재하는 역할은 건너뛰어
    멱등성을 보장한다(여러 번 호출해도 안전).
    """
    default_roles = [
        (RoleName.ADMIN.value, "Admin", "Administrator with full access"),
        (RoleName.SUPERUSER.value, "Superuser", "Superuser with elevated access"),
        (RoleName.USER.value, "User", "Standard user with limited access"),
    ]
    for rid, display_name, desc in default_roles:
        existing = await _get_role_by_role_id(session, rid)
        if not existing:
            session.add(ArgusRole(role_id=rid, name=display_name, description=desc))
            logger.info("역할 시드 생성: %s (%s)", rid, display_name)
    await session.commit()


async def seed_admin_user(session: AsyncSession) -> None:
    """로컬 인증 모드에서 사용자가 한 명도 없으면 기본 관리자 계정(admin/admin)을 생성.

    ``auth_type != "local"`` 이거나 ``argus_users`` 가 비어 있지 않으면 아무 일도 하지 않는다.
    최초 로그인 후 반드시 비밀번호를 변경해야 한다.
    """
    from app.core.config import settings
    if settings.auth_type != "local":
        return

    count = (await session.execute(select(func.count()).select_from(ArgusUser))).scalar() or 0
    if count > 0:
        return

    admin_role = await _get_role_by_role_id(session, RoleName.ADMIN.value)
    if not admin_role:
        return

    user = ArgusUser(
        username="admin",
        email="admin@argus.local",
        first_name="Admin",
        last_name="User",
        password_hash=_hash_password("admin"),
        status="active",
        role_id=admin_role.id,
    )
    session.add(user)
    await session.commit()
    logger.info("기본 관리자 계정 시드 생성: admin/admin (최초 로그인 후 비밀번호 변경 필요)")
