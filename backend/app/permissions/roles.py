# SPDX-License-Identifier: Apache-2.0
"""사용자 정의 역할(TODO-124).

기본 역할(관리자 · 슈퍼유저 · 사용자) 외에 부서 · 직무별 역할을 만들어 권한 표의 열로 쓴다.

- 저장: 역할 이름 · 설명은 사용자와 같은 역할 표(``argus_roles``)에 — 사용자 · API 키에 그대로 지정할 수 있다.
  기반 역할은 권한 표(``argus_permissions``)의 ``kind='ROLE'`` 행(``perm_key`` = 사용자 정의 역할, ``role_id`` = 기반 역할)에 둔다.
  표 구조를 바꾸지 않으려고 권한 표를 함께 쓴다.
- 기반 역할(슈퍼유저 또는 사용자): 역할 이름으로 판정하는 코드(``is_superuser`` · ``has_argus_role`` 등)는 기반 역할로 본다.
  인증할 때 :func:`apply_custom_role` 이 사용자 역할 목록에 기반 역할을 더하고 ``custom_role`` 을 채운다.
  관리자를 기반으로 하는 역할은 만들 수 없다(관리자는 권한 표와 무관하게 전부 허용이라 나눌 의미가 없고, 권한 상승 경로가 된다).
- 권한 표: 메뉴 · 기능 권한은 ``custom_role`` 열로 판정한다(:func:`app.permissions.router._my_role`). 만들 때 기반 역할의
  메뉴 · 보기 전용 · 기능 행을 복사해 처음에는 기반 역할과 같게 시작한다.
- Keycloak 모드: realm 역할 이름이 같으면 그 역할로 본다(역할 지정은 Keycloak 에서).
"""

from __future__ import annotations

import logging
import re
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.permissions.models import Permission

logger = logging.getLogger(__name__)

ROLE_KIND = "ROLE"
# 사용자 정의 역할의 기반이 될 수 있는 역할 — 권한 표의 기본 열이기도 하다
BASE_ROLES: tuple[str, ...] = ("argus-superuser", "argus-user")
# 역할 식별자 — 소문자 · 숫자 · 하이픈, 3~50자 (Keycloak realm 역할 이름과 같아야 한다)
ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,48}[a-z0-9]$")
RESERVED_ROLE_IDS = frozenset({"__none__"})

# 테스트에서 세션 팩토리를 바꿔 끼우는 훅(None 이면 계정 확인과 같은 팩토리)
session_factory = None
_TTL = 5.0
_cache: tuple[float, dict[str, str]] | None = None


def builtin_role_ids() -> frozenset[str]:
    """기본 역할 식별자 — 설정으로 바꿀 수 있어 매번 읽는다."""
    return frozenset({settings.auth_keycloak_admin_role, settings.auth_keycloak_superuser_role,
                      settings.auth_keycloak_user_role, *BASE_ROLES, "argus-admin"})


def invalidate() -> None:
    """사용자 정의 역할 캐시를 비운다 — 역할을 만들거나 지운 직후."""
    global _cache
    _cache = None


async def load_custom_roles(session: AsyncSession) -> dict[str, str]:
    """{사용자 정의 역할: 기반 역할} — DB 에서 바로 읽는다(관리 화면 · 저장용)."""
    rows = (await session.execute(select(Permission).where(Permission.kind == ROLE_KIND))).scalars().all()
    return {r.perm_key: r.role_id for r in rows if r.role_id in BASE_ROLES}


async def managed_roles(session: AsyncSession) -> tuple[str, ...]:
    """권한 표의 열 — 기본 두 역할과 사용자 정의 역할(이름순)."""
    return (*BASE_ROLES, *sorted(await load_custom_roles(session)))


def _factory():
    if session_factory is not None:
        return session_factory
    from app.core import auth
    if auth.account_session_factory is not None:
        return auth.account_session_factory
    from app.core.database import async_session
    return async_session


async def custom_role_bases() -> dict[str, str]:
    """{사용자 정의 역할: 기반 역할} — 요청마다 DB 를 읽지 않도록 짧게 기억한다(만들기 · 지우기 때 즉시 무효화)."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _TTL:
        return _cache[1]
    try:
        async with _factory()() as session:
            value = await load_custom_roles(session)
    except Exception as e:  # noqa: BLE001 — 역할 확인 실패가 로그인 전체를 막지 않도록(사용자 정의 역할만 인정하지 않음)
        logger.warning("사용자 정의 역할 조회 실패 — 기본 역할만 인정: %s", e)
        value = {}
    _cache = (now, value)
    return value


async def apply_custom_role(user):
    """사용자의 역할 중 사용자 정의 역할이 있으면 ``custom_role`` 을 채우고 기반 역할을 더한다.

    관리자이면 건너뛴다(관리자는 권한 표와 무관). 기본 역할만 가진 사용자는 DB 를 보지 않는다.
    """
    if user.custom_role or user.is_admin:
        return user
    builtin = builtin_role_ids()
    candidates = [r for r in user.roles + user.realm_roles if r not in builtin]
    if not candidates:
        return user
    bases = await custom_role_bases()
    for role in candidates:
        base = bases.get(role)
        if base:
            user.custom_role = role
            if base not in user.roles + user.realm_roles:
                user.roles = [*user.roles, base]
            logger.debug("사용자 정의 역할 적용: user=%s role=%s base=%s", user.username, role, base)
            break
    return user
