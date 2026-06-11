# SPDX-License-Identifier: Apache-2.0
"""권한 매트릭스 API.

- GET  /permissions/menus, /permissions/features : 전체 맵 {key: [role_id]} (admin)
- PUT  〃 : 전체 교체 (admin)
- GET  /permissions/me : 내 역할 기준 차단 목록 — 사이드바/기능 게이팅용

조회(me)는 "차단 목록"을 돌려준다: open-by-default 라 프런트가 전체
레지스트리를 알 필요 없이, 명시 설정 중 내 역할이 빠진 key 만 숨기면 된다.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser
from app.core.database import get_session
from app.permissions.models import Permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/permissions", tags=["permissions"])

# 매트릭스에서 다루는 비-admin 역할 — admin 은 항상 허용이라 저장하지 않는다
MANAGED_ROLES = ("argus-superuser", "argus-user")


async def _get_matrix(session: AsyncSession, kind: str) -> dict[str, list[str]]:
    """kind 의 전체 매트릭스 — {perm_key: [허용 role_id...]} (명시 설정만)."""
    rows = (await session.execute(
        select(Permission).where(Permission.kind == kind)
    )).scalars().all()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r.perm_key, []).append(r.role_id)
    return result


async def _put_matrix(session: AsyncSession, kind: str, payload: dict) -> dict:
    """전체 교체 저장 — 모든 관리 역할이 허용된 key 는 '미설정'으로 정규화(행 삭제).

    이렇게 하면 DB 에는 '제한이 걸린 항목'만 남아, open-by-default 의미가
    데이터 모양과 일치한다 (전부 체크 = 행 없음 = 기본 상태).
    """
    await session.execute(sql_delete(Permission).where(Permission.kind == kind))
    saved = 0
    for key, roles in (payload or {}).items():
        allowed = [r for r in (roles or []) if r in MANAGED_ROLES]
        if set(allowed) == set(MANAGED_ROLES):
            continue  # 전 역할 허용 = 기본 상태 — 저장하지 않음
        for role in allowed:
            session.add(Permission(kind=kind, perm_key=str(key)[:100], role_id=role))
            saved += 1
        if not allowed:
            # 아무 역할도 체크 안 됨 = admin 전용 — 센티널 행으로 명시
            session.add(Permission(kind=kind, perm_key=str(key)[:100], role_id="__none__"))
            saved += 1
    await session.commit()
    logger.info("권한 업데이트: kind=%s, restricted_rows=%d", kind, saved)
    return {"kind": kind, "restricted_rows": saved}


@router.get("/menus")
async def get_menu_permissions(_admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """메뉴 권한 매트릭스 (admin) — 명시 설정만 반환, 없는 key 는 전체 허용."""
    return await _get_matrix(session, "MENU")


@router.put("/menus")
async def put_menu_permissions(payload: dict, _admin: AdminUser,
                               session: AsyncSession = Depends(get_session)):
    """메뉴 권한 전체 교체 (admin)."""
    return await _put_matrix(session, "MENU", payload)


@router.get("/features")
async def get_feature_permissions(_admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """기능 권한 매트릭스 (admin)."""
    return await _get_matrix(session, "FEATURE")


@router.put("/features")
async def put_feature_permissions(payload: dict, _admin: AdminUser,
                                  session: AsyncSession = Depends(get_session)):
    """기능 권한 전체 교체 (admin)."""
    return await _put_matrix(session, "FEATURE", payload)


@router.get("/me")
async def get_my_permissions(current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """내 역할 기준 차단된 메뉴/기능 key — 사이드바 필터·기능 게이팅용.

    admin 은 항상 빈 목록(전부 허용). 일반 역할은 '명시 설정이 있는데
    내 역할이 포함되지 않은' key 가 차단 목록에 들어간다.
    """
    if current.is_admin:
        return {"denied_menus": [], "denied_features": []}

    # 내 역할 결정 — superuser 우선
    my_role = "argus-superuser" if current.is_superuser else "argus-user"

    rows = (await session.execute(select(Permission))).scalars().all()
    by_key: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        by_key.setdefault((r.kind, r.perm_key), set()).add(r.role_id)

    denied_menus = [k for (kind, k), roles in by_key.items()
                    if kind == "MENU" and my_role not in roles]
    denied_features = [k for (kind, k), roles in by_key.items()
                       if kind == "FEATURE" and my_role not in roles]
    return {"denied_menus": sorted(denied_menus), "denied_features": sorted(denied_features)}


async def check_feature(session: AsyncSession, current, feature_key: str) -> bool:
    """서버측 기능 권한 체크 — 민감 API 에서 사용 (open-by-default)."""
    if current.is_admin:
        return True
    rows = (await session.execute(
        select(Permission.role_id).where(
            Permission.kind == "FEATURE", Permission.perm_key == feature_key)
    )).scalars().all()
    if not rows:
        return True  # 미설정 = 전체 허용
    my_role = "argus-superuser" if current.is_superuser else "argus-user"
    return my_role in rows


def require_feature(feature_key: str):
    """FastAPI 의존성 팩토리 — 기능 권한이 없으면 403.

    사용: ``_=Depends(require_feature("datasets.sample-view"))``
    open-by-default 라 매트릭스에 설정이 없으면 통과한다.
    """
    from fastapi import HTTPException

    async def _dep(current: CurrentUser, session: AsyncSession = Depends(get_session)):
        if not await check_feature(session, current, feature_key):
            raise HTTPException(status_code=403, detail=f"기능 권한이 없습니다: {feature_key}")
        return current

    return _dep


async def seed_default_permissions(session: AsyncSession) -> None:
    """기본 권한 시드 — 관리 메뉴 3종은 admin 전용으로 출고.

    이미 어떤 MENU 행이 존재하면(운영자가 설정한 상태) 건드리지 않는다.
    """
    existing = (await session.execute(
        select(Permission.id).where(Permission.kind == "MENU").limit(1)
    )).scalar()
    if existing:
        return
    for key in ("users", "settings", "permissions"):
        session.add(Permission(kind="MENU", perm_key=key, role_id="__none__"))
    await session.commit()
    logger.info("기본 권한 시드 완료: admin 전용 메뉴 (users/settings/permissions)")
