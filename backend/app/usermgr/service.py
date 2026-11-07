# SPDX-License-Identifier: Apache-2.0
"""사용자 관리 서비스(비즈니스 로직 계층).

라우터에서 호출하는 사용자/역할 CRUD 로직을 모은다. 모든 DB 상호작용은
SQLAlchemy 비동기 세션을 통해 이뤄진다.

구조 메모:
- 모든 public 함수는 첫 인자로 ``AsyncSession`` 을 받는다.
- 데이터 변경 함수는 ``session.commit()`` 후 ``session.refresh()`` 로 갱신된
  필드(``updated_at`` 등)를 다시 읽어와 응답에 반영한다.
- ``_build_user_response()`` 가 ``role_id`` 외래키를 역할 이름 문자열로 변환해준다.
- 비밀번호 해싱은 ``app.core.passwords`` (PBKDF2-HMAC-SHA256 + 사용자별 salt). 예전 SHA-256
  해시는 로그인 때 새 형식으로 다시 저장된다.
- "마지막 관리자 보호"는 **활성** 관리자 수 기준이다(비활성 관리자는 로그인할 수 없으므로).

로깅 정책: 생성·수정·삭제·역할 변경 등 변경 동작은 INFO, "마지막 관리자 보호"
같은 도메인 규칙 위반은 호출 측(router)에서 WARNING 처리한다.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import hash_password_async, password_problem
from app.core.sql import LIKE_ESCAPE, like_contains
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

# 로컬 로그인 잠금 — 비밀번호를 연속 LOGIN_MAX_FAILURES 번 틀리면 LOGIN_LOCK_MINUTES 분 잠근다.
# 관리자는 "잠금 해제"(PUT /usermgr/users/{id}/unlock) 또는 비밀번호 재설정으로 바로 풀 수 있다.
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_MINUTES = 15

# 목록 정렬 키 → 정렬 컬럼(이름은 성+이름)
SORT_COLUMNS = {
    "username": (ArgusUser.username,),
    "name": (ArgusUser.last_name, ArgusUser.first_name),
    "email": (ArgusUser.email,),
    "organization": (ArgusUser.organization,),
    "department": (ArgusUser.department,),
    "status": (ArgusUser.status,),
    "role": (ArgusRole.role_id,),
    "created_at": (ArgusUser.created_at,),
    "last_login_at": (ArgusUser.last_login_at,),
}


def utcnow() -> datetime:
    """현재 UTC 시각(timezone-aware)."""
    return datetime.now(timezone.utc)


def as_aware(dt: datetime | None) -> datetime | None:
    """DB 가 시간대 없는 값을 돌려주는 경우(SQLite 등) UTC 로 간주."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def active_lock(user: ArgusUser) -> datetime | None:
    """아직 유효한 잠금의 해제 시각(없거나 지났으면 None)."""
    until = as_aware(user.locked_until)
    return until if until and until > utcnow() else None


def to_response(user: ArgusUser, role_code: str | None) -> UserResponse:
    """ORM 사용자 + 역할 코드 → 응답 DTO. 역할을 모르면 "unknown", 만료된 잠금은 None."""
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        organization=user.organization,
        department=user.department,
        phone_number=user.phone_number,  # 비관리자 마스킹은 라우터(_mask_private, 본인 제외)
        status=UserStatus(user.status),
        role=role_code or "unknown",
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_login_at=user.last_login_at,
        locked_until=active_lock(user),
    )


async def _is_last_admin(session: AsyncSession, user_id: int) -> bool:
    """주어진 사용자가 **마지막 활성 관리자**인지 — 삭제·강등·비활성화하면 활성 관리자가 0명이 되는지.

    비활성 관리자는 로그인할 수 없어 수에 넣지 않는다. 대상이 이미 비활성이면 활성 관리자 수가
    줄지 않으므로 False. 관리자 행을 잠가(PostgreSQL ``FOR UPDATE``) 동시 요청 두 건이 각각
    "다른 관리자가 남는다"고 판단해 둘 다 통과하는 경쟁을 막는다(SQLite 는 무시).
    """
    from app.usermgr.schemas import RoleName
    admin_role = await _get_role_by_role_id(session, RoleName.ADMIN.value)
    if not admin_role:
        return False
    admins = (await session.execute(
        select(ArgusUser.id, ArgusUser.status)
        .where(ArgusUser.role_id == admin_role.id)
        .with_for_update()
    )).all()
    active_ids = {uid for uid, st in admins if st == UserStatus.ACTIVE.value}
    return user_id in active_ids and len(active_ids) <= 1


async def password_hash_for(password: str) -> str:
    """비밀번호 해시(스레드에서 계산)."""
    return await hash_password_async(password)


async def _get_role_by_role_id(session: AsyncSession, role_id: str) -> ArgusRole | None:
    """``role_id`` 식별자(예: ``"argus-admin"``)로 역할 조회."""
    result = await session.execute(select(ArgusRole).where(ArgusRole.role_id == role_id))
    return result.scalars().first()


async def _build_user_response(session: AsyncSession, user: ArgusUser) -> UserResponse:
    """``ArgusUser`` ORM 객체에서 역할명을 resolve 해 ``UserResponse`` 로 변환."""
    result = await session.execute(select(ArgusRole).where(ArgusRole.id == user.role_id))
    role = result.scalars().first()
    return to_response(user, role.role_id if role else None)


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
    logger.debug("중복 확인: username=%s email_given=%s", username, bool(email))
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
    # 최초 로그인 때 바꾸게 하는 임시 비밀번호(LDAP 동기화의 생년월일 등)가 아니면 정책을 강제한다
    if not req.must_change_password and (problem := password_problem(req.password)):
        logger.warning("add_user: 비밀번호 정책 위반 username=%s", req.username)
        raise ValueError(problem)

    user = ArgusUser(
        username=req.username,
        email=req.email,
        first_name=req.first_name,
        last_name=req.last_name,
        organization=req.organization,
        department=req.department,
        phone_number=req.phone_number,
        password_hash=await password_hash_for(req.password),
        status=UserStatus.ACTIVE.value,
        must_change_password=req.must_change_password,
        role_id=role.id,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    logger.info("사용자 생성: %s (id=%d)", user.username, user.id)
    return await _build_user_response(session, user)


class OwnedAssetsError(ValueError):
    """소유 자산이 남아 있어 이관 대상 지정이 필요하다(라우터가 409 로 변환)."""

    def __init__(self, assets: list[dict]):
        self.assets = assets
        super().__init__("소유한 자산이 있어 삭제할 수 없습니다. 자산을 넘겨받을 사용자를 지정하세요.")


async def delete_user(session: AsyncSession, user_id: int, transfer_to: str | None = None) -> bool:
    """ID 로 사용자를 영구 삭제. 삭제했으면 ``True``, 사용자가 없으면 ``False`` 반환.

    - "마지막 관리자 보호" 규칙 위반 시 ``ValueError`` (라우터가 400).
    - 소유 자산(편집 권한이 아이디로 묶인 자산)이 있으면 ``transfer_to`` 사용자에게 같은 트랜잭션에서
      옮긴 뒤 삭제한다. 지정이 없으면 ``OwnedAssetsError`` (409) — 같은 아이디로 새 계정을 만들었을 때
      이전 자산의 권한을 이어받는 것을 막는다(``app.usermgr.ownership``).
    """
    from app.usermgr import ownership

    if await _is_last_admin(session, user_id):
        logger.warning("delete_user 차단(마지막 관리자): user_id=%d", user_id)
        raise ValueError("마지막 활성 관리자는 삭제할 수 없습니다. 활성 관리자가 한 명 이상 있어야 합니다.")

    result = await session.execute(select(ArgusUser).where(ArgusUser.id == user_id))
    user = result.scalars().first()
    if not user:
        logger.debug("delete_user 대상 없음: user_id=%d", user_id)
        return False

    owned = [a for a in await ownership.count_owned(session, user.username) if a["count"]]
    if owned:
        if not transfer_to:
            raise OwnedAssetsError(owned)
        if transfer_to == user.username:
            raise ValueError("삭제할 사용자에게는 자산을 넘길 수 없습니다.")
        heir = (await session.execute(
            select(ArgusUser.status).where(ArgusUser.username == transfer_to)
        )).scalar()
        if heir is None:
            logger.warning("delete_user: 이관 대상 사용자 없음 transfer_to=%s", transfer_to)
            raise ValueError(f"자산을 넘겨받을 사용자 '{transfer_to}' 를 찾을 수 없습니다.")
        if heir != UserStatus.ACTIVE.value:
            logger.warning("delete_user: 이관 대상 사용자 비활성 transfer_to=%s", transfer_to)
            raise ValueError(f"자산을 넘겨받을 사용자 '{transfer_to}' 가 비활성 상태입니다.")
        await ownership.transfer_owned(session, user.username, transfer_to)
        logger.info("소유 자산 이관: %s → %s %s", user.username, transfer_to,
                    {a["key"]: a["count"] for a in owned})

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
            raise ValueError("마지막 활성 관리자의 역할은 바꿀 수 없습니다. 활성 관리자가 한 명 이상 있어야 합니다.")

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
        raise ValueError("마지막 활성 관리자는 비활성화할 수 없습니다. 활성 관리자가 한 명 이상 있어야 합니다.")

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
    session: AsyncSession, user_id: int, new_password: str, *, must_change_password: bool = False,
) -> UserResponse | None:
    """관리자가 대상 사용자의 비밀번호를 직접 재설정한다(현재 비밀번호 불요).

    평문 ``new_password`` 를 정책 검사 후 해시해 저장한다(정책 위반은 ``ValueError``). 로컬 인증 모드 전용
    (호출 측 라우터에서 모드를 검증). 사용자가 없으면 ``None``.
    """
    result = await session.execute(select(ArgusUser).where(ArgusUser.id == user_id))
    user = result.scalars().first()
    if not user:
        return None

    if problem := password_problem(new_password):
        logger.warning("set_password: 비밀번호 정책 위반 user_id=%d", user_id)
        raise ValueError(problem)
    user.password_hash = await password_hash_for(new_password)
    # 관리자가 정해 준 비밀번호는 임시 비밀번호 — 다음 로그인 때 본인이 바꾸게 한다
    user.must_change_password = must_change_password
    # 재설정은 잠금도 푼다(잠긴 사용자를 도와주는 흔한 경로)
    user.failed_login_count = 0
    user.locked_until = None
    await session.commit()
    await session.refresh(user)
    logger.info("관리자에 의한 비밀번호 재설정: %s (id=%d)", user.username, user.id)
    return await _build_user_response(session, user)


async def unlock_user(session: AsyncSession, user_id: int) -> UserResponse | None:
    """로그인 실패 잠금 해제(실패 횟수 초기화). 사용자가 없으면 None."""
    user = (await session.execute(select(ArgusUser).where(ArgusUser.id == user_id))).scalars().first()
    if not user:
        return None
    user.failed_login_count = 0
    user.locked_until = None
    await session.commit()
    await session.refresh(user)
    logger.info("로그인 잠금 해제: %s (id=%d)", user.username, user.id)
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
    include_private: bool = True,
    sort: str | None = None,
    order: str = "desc",
) -> PaginatedUserResponse:
    """필터·페이지네이션을 적용한 사용자 목록 조회.

    ``include_private=False`` 면 전화번호를 검색 대상에서 뺀다(비관리자 조회).
    ``sort``(``SORT_COLUMNS`` 키)·``order``(asc/desc)로 전체 결과를 정렬한다(기본: 생성일 최신순).
    빈 값(NULL)은 방향과 관계없이 뒤로, 동률은 id 순.

    동적 WHERE 절을 구성한다:
        1. ``argus_users`` ↔ ``argus_roles`` 조인으로 역할명 함께 조회
        2. ``status`` / ``role``(role_id) / ``organization`` / ``search`` 가 주어지면 각각 WHERE 추가
        3. 페이지네이션 메타용 총 건수 별도 카운트
        4. ``sort``/``order`` 로 정렬(기본 ``created_at`` 내림차순) 후 OFFSET/LIMIT 적용

    ``page_size=0`` 은 페이지네이션 없이 전체 반환.

    Raises:
        ValueError: ``sort`` 가 SORT_COLUMNS 키가 아닐 때(라우터에서 400).
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
        pattern = like_contains(search)
        base = base.where(
            or_(
                ArgusUser.username.ilike(pattern, escape=LIKE_ESCAPE),
                ArgusUser.first_name.ilike(pattern, escape=LIKE_ESCAPE),
                ArgusUser.last_name.ilike(pattern, escape=LIKE_ESCAPE),
                func.concat(ArgusUser.last_name, ArgusUser.first_name).ilike(pattern, escape=LIKE_ESCAPE),
                ArgusUser.email.ilike(pattern, escape=LIKE_ESCAPE),
                # 전화번호는 관리자 조회에서만 검색 대상 — 아니면 검색으로 번호를 알아낼 수 있다
                *([ArgusUser.phone_number.ilike(pattern, escape=LIKE_ESCAPE)] if include_private else []),
                ArgusUser.organization.ilike(pattern, escape=LIKE_ESCAPE),
                ArgusUser.department.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )

    # 페이지네이션 전 총 건수 별도 카운트(응답 메타에 포함됨)
    count_query = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_query)).scalar() or 0
    logger.debug("사용자 목록 조건: status=%s role=%s org=%s search=%s sort=%s/%s total=%d",
                 status, role, organization, bool(search), sort, order, total)

    # 정렬(기본 최신순) 후 OFFSET/LIMIT 적용. page_size=0 이면 전체 반환.
    if sort and sort not in SORT_COLUMNS:
        raise ValueError(f"정렬 기준은 {', '.join(SORT_COLUMNS)} 중 하나입니다.")
    cols = SORT_COLUMNS[sort or "created_at"]
    desc = (order or "desc").lower() != "asc"
    order_by = [(c.desc() if desc else c.asc()).nulls_last() for c in cols]
    query = base.order_by(*order_by, ArgusUser.id.desc() if desc else ArgusUser.id.asc())
    if page_size > 0:
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
    result = await session.execute(query)
    rows = result.all()

    # (ArgusUser, role_code) 튜플을 UserResponse Pydantic 모델로 변환
    items = [to_response(user, role_code) for user, role_code in rows]

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
        existing.last_login_at = utcnow()
        # status 는 건드리지 않는다 — 로컬에서 비활성화한 계정이 다음 로그인 때 되살아나지 않도록
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
            last_login_at=utcnow(),
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
        logger.debug("기본 관리자 시드 건너뜀: 기존 사용자 %d명", count)
        return

    admin_role = await _get_role_by_role_id(session, RoleName.ADMIN.value)
    if not admin_role:
        logger.warning("기본 관리자 시드 건너뜀: 관리자 역할(%s) 없음", RoleName.ADMIN.value)
        return

    user = ArgusUser(
        username="admin",
        email="admin@argus.local",
        first_name="Admin",
        last_name="User",
        password_hash=await password_hash_for("admin"),
        status="active",
        role_id=admin_role.id,
        # 공개된 기본 비밀번호 — 최초 로그인 때 변경을 강제한다(변경 전에는 다른 API 사용 불가)
        must_change_password=True,
    )
    session.add(user)
    await session.commit()
    logger.info("기본 관리자 계정 시드 생성: admin/admin (최초 로그인 후 비밀번호 변경 필요)")
