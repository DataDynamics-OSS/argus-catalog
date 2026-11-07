# SPDX-License-Identifier: Apache-2.0
"""권한 매트릭스 API.

- GET  /permissions/menus, /permissions/features : 전체 맵 {key: [role_id]} (admin)
- PUT  〃 : 전체 교체 (admin)
- GET  /permissions/me : 내 역할 기준 차단 목록 — 사이드바/기능 게이팅용

조회(me)는 "차단 목록"을 돌려준다: open-by-default 라 프런트가 전체
레지스트리를 알 필요 없이, 명시 설정 중 내 역할이 빠진 key 만 숨기면 된다.

메뉴 권한(MENU)의 강제 범위:
- 화면: 프런트 대시보드 레이아웃의 라우트 가드가 차단된 메뉴 페이지를 막는다(주소 직접 입력 포함).
- 서버: 메뉴 전용 API 를 가진 메뉴(``SERVER_ENFORCED_MENUS``)는 해당 라우터에 ``require_menu``
  의존성을 걸어 API 직접 호출도 403 으로 막는다. 여러 메뉴가 함께 쓰는 공용 API(/catalog 등)는
  다른 화면을 깨뜨리므로 서버 차단 대상이 아니다 — 권한 관리 화면에 "화면만" 으로 표시된다.
- 관리 메뉴(``ADMIN_MENUS``: 사용자·설정·권한 관리)는 매트릭스로 열 수 없다(항상 관리자 전용,
  각 API 가 AdminUser 로 강제).

예외 — ``DEFAULT_DENY_FEATURES`` 의 기능은 반대로 **명시 허용형**이다. 설정이 없으면
admin 만 허용하고, 매트릭스에 역할이 명시된 경우에만 그 역할을 허용한다(원래 admin
전용이던 기능을 매트릭스로 옮기면서 기본 권한이 넓어지지 않게 하기 위함). 이런 기능은
``/me`` 의 ``granted_features`` 로 허용 여부를 판단한다.

데이터셋 직접 등록(``datasets.create``)은 매트릭스 위에 설정 옵션이 하나 더 걸린다
(:func:`check_dataset_create`): 관리자·동기화 계정(``datasources.sync`` 명시 허용)은 항상 가능하고,
일반 사용자는 설정 ``catalog_dataset_create_by_users`` 가 켜져 있을 때만(그때도 매트릭스 적용) 가능하다.
옵션이 꺼져 있으면 ``/me`` 의 ``denied_features`` 에 ``datasets.create`` 가 들어간다.
"""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser, OptionalUser
from app.core.database import get_session
from app.permissions.models import Permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/permissions", tags=["permissions"])

# 매트릭스에서 다루는 비-admin 역할 — admin 은 항상 허용이라 저장하지 않는다
MANAGED_ROLES = ("argus-superuser", "argus-user")

# 명시 허용형 기능 — 미설정이면 admin 전용 (프런트 permission-registry 의 defaultDeny 와 일치)
DEFAULT_DENY_FEATURES = frozenset({
    "glossary.edit", "tags.manage", "standards.edit", "alerts.rules",
    # 데이터 소스 — 원래 admin 전용이던 관리/동기화와 접속 정보 조회
    "datasources.manage", "datasources.sync", "datasources.connection",
    # 분류 체계 — 원래 admin 전용이던 분류체계·분류·데이터셋 매핑 편집
    "taxonomies.edit",
    # 운영 영향이 큰 작업 — MLflow 운영(Production) 단계 변경, MCP 배포 자격증명 저장
    "models.promote", "mcp.credential",
})

# 관리 메뉴 — 항상 관리자 전용(매트릭스 대상 아님). 저장 시 무시한다.
# MLflow·OCI 모델 파일(저장소 전체 탐색)도 관리자 전용 — 일반 사용자는 모델 상세의 파일 탭으로 본다.
ADMIN_MENUS = frozenset({"users", "settings", "permissions", "mlflow-files", "oci-files"})

# 서버에서도 API 를 차단하는 메뉴 → 그 메뉴 전용 API prefix (main.py 의 include_router 와 일치).
# 프런트 permission-registry 의 serverEnforced 와 일치해야 한다(수동 동기화). main.py 에 각 메뉴의
# ``_menu_guard`` 가 걸렸는지는 tests/test_admin_stage1.py::test_server_enforced_menus_are_guarded_in_main 이 검사한다.
SERVER_ENFORCED_MENUS: dict[str, tuple[str, ...]] = {
    "apis": ("/api/v1/apis",),
    "ai-agents": ("/api/v1/ai-agents",),
    "mcp-servers": ("/api/v1/mcp-servers", "/api/v1/mcp-deployments"),
    "models": ("/api/v1/models",),
    "oci-hub": ("/api/v1/oci-models",),
    "federation": ("/api/v1/federation",),
}

# 기능 권한 key — 실제로 화면/서버가 검사하는 것만 둔다(프런트 FEATURE_REGISTRY 와 일치).
# 목록에 없는 key 는 저장하지 않는다(효과 없는 체크박스로 관리자가 '잠갔다'고 오해하지 않도록).
FEATURE_KEYS = frozenset({
    "datasets.sample-view", "datasets.ddl-view", "datasets.transfer-code",
    "datasources.manage", "datasources.connection", "datasources.sync",
    "mcp.policy", "apis.credentials",
    "standards.edit", "glossary.edit", "tags.manage", "alerts.rules",
    "taxonomies.edit",
    "quality.rules-manage", "quality.run", "quality.schedule",
    "changes.create", "changes.approve", "changes.consumers",
    "ai.assistant", "ai.generate",
    # 등록 — 미설정이면 전 역할 허용(막고 싶은 역할만 해제)
    "datasets.create", "models.register", "oci.register", "agents.register", "mcp.register", "apis.register",
    "models.promote", "mcp.credential",
})


def _my_role(current) -> str:
    """현재 사용자의 매트릭스 역할 — superuser 이면 argus-superuser, 아니면 argus-user (admin 은 호출 전에 걸러짐)."""
    return "argus-superuser" if current.is_superuser else "argus-user"


async def _get_matrix(session: AsyncSession, kind: str) -> dict[str, list[str]]:
    """kind 의 전체 매트릭스 — {perm_key: [허용 role_id...]} (명시 설정만)."""
    rows = (await session.execute(
        select(Permission).where(Permission.kind == kind)
    )).scalars().all()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r.perm_key, []).append(r.role_id)
    return result


def _storable_key(kind: str, key: str) -> bool:
    """저장 가능한 key 인지 — MENU 는 관리 메뉴 제외 전부, FEATURE 는 ``FEATURE_KEYS`` 에 있는 것만."""
    if kind == "MENU":
        return key not in ADMIN_MENUS
    return key in FEATURE_KEYS


async def _put_matrix(session: AsyncSession, kind: str, payload: dict, actor=None) -> dict:
    """전체 교체 저장 — 모든 관리 역할이 허용된 key 는 '미설정'으로 정규화(행 삭제).

    이렇게 하면 DB 에는 '제한이 걸린 항목'만 남아, open-by-default 의미가
    데이터 모양과 일치한다 (전부 체크 = 행 없음 = 기본 상태).

    ``actor`` 가 있으면 바뀐 key 의 전후(허용 역할 목록, 행 없음 = 기본)를 감사 로그에 남긴다.
    """
    logger.debug("권한 매트릭스 전체 교체 시작: kind=%s keys=%d", kind, len(payload or {}))
    before = await _get_matrix(session, kind)
    await session.execute(sql_delete(Permission).where(Permission.kind == kind))
    saved = 0
    for key, roles in (payload or {}).items():
        if not _storable_key(kind, str(key)):
            logger.debug("저장 대상이 아닌 권한 key 무시: kind=%s key=%s", kind, key)
            continue  # 관리 메뉴·알 수 없는 기능 key 는 저장하지 않음
        allowed = [r for r in (roles or []) if r in MANAGED_ROLES]
        default_deny = kind == "FEATURE" and key in DEFAULT_DENY_FEATURES
        if set(allowed) == set(MANAGED_ROLES) and not default_deny:
            continue  # 전 역할 허용 = 기본 상태 — 저장하지 않음 (명시 허용형은 행으로 남겨야 허용이 유지됨)
        for role in allowed:
            session.add(Permission(kind=kind, perm_key=str(key)[:100], role_id=role))
            saved += 1
        if not allowed:
            # 아무 역할도 체크 안 됨 = admin 전용 — 센티널 행으로 명시
            session.add(Permission(kind=kind, perm_key=str(key)[:100], role_id="__none__"))
            saved += 1
    await session.commit()
    _menu_cache.clear()
    logger.info("권한 업데이트: kind=%s, restricted_rows=%d", kind, saved)
    if actor is not None:
        after = await _get_matrix(session, kind)
        changes = {
            k: {"old": sorted(before.get(k, [])) or "기본", "new": sorted(after.get(k, [])) or "기본"}
            for k in sorted(set(before) | set(after))
            # 관리 메뉴 등 저장 대상이 아닌 key(예전 시드가 남긴 행 정리)는 권한 변화가 아니므로 제외
            if _storable_key(kind, k) and sorted(before.get(k, [])) != sorted(after.get(k, []))
        }
        if changes:
            from app.audit.service import record
            label = "메뉴" if kind == "MENU" else "기능"
            await record(
                session, actor, "permissions", f"permissions.{'menus' if kind == 'MENU' else 'features'}",
                target=kind, summary=f"{label} 권한 변경: {', '.join(changes)}", detail={"changes": changes},
            )
    return {"kind": kind, "restricted_rows": saved}


@router.get("/menus")
async def get_menu_permissions(_admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """메뉴 권한 매트릭스 (admin) — 명시 설정만 반환, 없는 key 는 전체 허용."""
    return await _get_matrix(session, "MENU")


@router.put("/menus")
async def put_menu_permissions(payload: dict, _admin: AdminUser,
                               session: AsyncSession = Depends(get_session)):
    """메뉴 권한 전체 교체 (admin)."""
    return await _put_matrix(session, "MENU", payload, actor=_admin)


@router.get("/features")
async def get_feature_permissions(_admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """기능 권한 매트릭스 (admin)."""
    return await _get_matrix(session, "FEATURE")


@router.put("/features")
async def put_feature_permissions(payload: dict, _admin: AdminUser,
                                  session: AsyncSession = Depends(get_session)):
    """기능 권한 전체 교체 (admin)."""
    return await _put_matrix(session, "FEATURE", payload, actor=_admin)


@router.get("/me")
async def get_my_permissions(current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """내 역할 기준 차단된 메뉴/기능 key — 사이드바 필터·기능 게이팅용.

    admin 은 항상 빈 목록(전부 허용). 일반 역할은 '명시 설정이 있는데
    내 역할이 포함되지 않은' key 가 차단 목록에 들어간다.
    ``granted_features`` 는 내 역할이 명시 허용된 기능 key — 명시 허용형 기능 판단용.
    """
    if current.is_admin:
        return {"denied_menus": [], "denied_features": [], "granted_features": []}

    # 내 역할 결정 — superuser 우선
    my_role = _my_role(current)

    rows = (await session.execute(select(Permission))).scalars().all()
    logger.debug("내 권한 조회: user=%s role=%s rows=%d", getattr(current, "username", None), my_role, len(rows))
    by_key: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        by_key.setdefault((r.kind, r.perm_key), set()).add(r.role_id)

    denied_menus = [k for (kind, k), roles in by_key.items()
                    if kind == "MENU" and my_role not in roles]
    # 관리 메뉴는 매트릭스와 무관하게 항상 차단(관리자 전용)
    denied_menus = sorted(set(denied_menus) | ADMIN_MENUS)
    denied_features = [k for (kind, k), roles in by_key.items()
                       if kind == "FEATURE" and my_role not in roles]
    granted_features = [k for (kind, k), roles in by_key.items()
                        if kind == "FEATURE" and my_role in roles]
    # 데이터셋 직접 등록은 설정 옵션이 꺼져 있으면(기본) 매트릭스와 무관하게 막힌다 — 서버 판정과 맞춘다
    if DATASET_CREATE_FEATURE not in denied_features and await check_dataset_create(session, current) is not None:
        denied_features.append(DATASET_CREATE_FEATURE)
    return {
        "denied_menus": sorted(denied_menus),
        "denied_features": sorted(denied_features),
        "granted_features": sorted(granted_features),
    }


async def check_feature(session: AsyncSession, current, feature_key: str) -> bool:
    """서버측 기능 권한 체크 — 민감 API 에서 사용 (open-by-default).

    ``current`` 가 None(비로그인 허용 API)이면 설정이 없을 때만 허용한다.
    """
    if current is not None and current.is_admin:
        return True
    rows = (await session.execute(
        select(Permission.role_id).where(
            Permission.kind == "FEATURE", Permission.perm_key == feature_key)
    )).scalars().all()
    if not rows:
        return True  # 미설정 = 전체 허용
    return current is not None and _my_role(current) in rows


async def check_feature_granted(session: AsyncSession, current, feature_key: str) -> bool:
    """명시 허용형 기능 권한 체크 — admin 이거나 매트릭스에 내 역할이 명시된 경우만 허용."""
    if current.is_admin:
        return True
    rows = (await session.execute(
        select(Permission.role_id).where(
            Permission.kind == "FEATURE", Permission.perm_key == feature_key)
    )).scalars().all()
    return _my_role(current) in rows


def require_feature_granted(feature_key: str):
    """FastAPI 의존성 팩토리 — 명시 허용형 기능 권한이 없으면 403 (미설정이면 admin 전용)."""

    async def _dep(current: CurrentUser, session: AsyncSession = Depends(get_session)):
        if not await check_feature_granted(session, current, feature_key):
            logger.warning("명시 허용형 기능 권한 거부(403): user=%s feature=%s", current.username, feature_key)
            raise HTTPException(status_code=403, detail=f"기능 권한이 없습니다: {feature_key}")
        return current

    return _dep


DATASET_CREATE_FEATURE = "datasets.create"
DATASET_CREATE_DISABLED_DETAIL = (
    "일반 사용자의 데이터셋 직접 등록이 허용되어 있지 않습니다. "
    "관리자에게 등록을 요청하거나, 관리자가 설정 > 데이터셋 등록에서 허용해야 합니다."
)


async def check_dataset_create(session: AsyncSession, current) -> str | None:
    """데이터셋 직접 등록(POST /catalog/datasets) 가능 여부 — 가능하면 None, 아니면 403 사유.

    - 관리자: 항상 가능.
    - 동기화 계정: ``datasources.sync`` 가 명시 허용된 역할(외부 metadata-sync 가 이 API 로 데이터셋을
      만든다)은 옵션과 무관하게 가능 — 동기화로 데이터셋이 생기는 경로를 막지 않기 위함.
    - 일반 사용자: 설정 ``catalog_dataset_create_by_users`` 가 켜져 있고(기본 꺼짐) 기능 권한
      ``datasets.create`` 가 허용된 경우만.
    """
    if current.is_admin:
        return None
    if await check_feature_granted(session, current, "datasources.sync"):
        return None
    from app.settings.service import dataset_create_by_users_enabled
    if not await dataset_create_by_users_enabled(session):
        return DATASET_CREATE_DISABLED_DETAIL
    if not await check_feature(session, current, DATASET_CREATE_FEATURE):
        return f"기능 권한이 없습니다: {DATASET_CREATE_FEATURE}"
    return None


async def require_dataset_create(current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """FastAPI 의존성 — 데이터셋 직접 등록 권한(:func:`check_dataset_create`)이 없으면 403."""
    if (reason := await check_dataset_create(session, current)) is not None:
        raise HTTPException(status_code=403, detail=reason)
    return current


def require_feature(feature_key: str):
    """FastAPI 의존성 팩토리 — 기능 권한이 없으면 403.

    사용: ``_=Depends(require_feature("datasets.sample-view"))``
    open-by-default 라 매트릭스에 설정이 없으면 통과한다.
    """

    async def _dep(current: CurrentUser, session: AsyncSession = Depends(get_session)):
        if not await check_feature(session, current, feature_key):
            logger.warning("기능 권한 거부(403): user=%s feature=%s", current.username, feature_key)
            raise HTTPException(status_code=403, detail=f"기능 권한이 없습니다: {feature_key}")
        return current

    return _dep


# ---------------------------------------------------------------------------
# 메뉴 권한 서버 강제
# ---------------------------------------------------------------------------

# 메뉴 매트릭스 캐시 — 요청마다 DB 를 읽지 않도록 짧게(저장 시 즉시 무효화)
_MENU_CACHE_TTL = 5.0
_menu_cache: dict[str, tuple[float, set[str] | None]] = {}


async def _menu_roles(session: AsyncSession, menu_key: str) -> set[str] | None:
    """메뉴에 명시 설정된 허용 역할 집합(설정 없으면 None = 전체 허용)."""
    hit = _menu_cache.get(menu_key)
    now = time.monotonic()
    if hit and now - hit[0] < _MENU_CACHE_TTL:
        logger.debug("메뉴 권한 캐시 적중: menu=%s", menu_key)
        return hit[1]
    rows = (await session.execute(
        select(Permission.role_id).where(Permission.kind == "MENU", Permission.perm_key == menu_key)
    )).scalars().all()
    roles = set(rows) if rows else None
    _menu_cache[menu_key] = (now, roles)
    return roles


async def check_menu(session: AsyncSession, current, menu_key: str) -> bool:
    """메뉴 권한 — admin 이거나 미설정이거나 내 역할이 허용된 경우 True."""
    if current is None or current.is_admin:
        return True
    roles = await _menu_roles(session, menu_key)
    return roles is None or _my_role(current) in roles


def require_menu(menu_key: str):
    """라우터 의존성 팩토리 — 차단된 메뉴의 전용 API 를 403 으로 막는다.

    사용자 토큰이 아닌 요청은 자체 서비스 토큰을 쓰는 경로(배포 수집 토큰 등 — ``auth_gate`` 의 예외
    경로)만 각 엔드포인트의 인증에 맡기고, 그 밖에는 401 로 막는다(로그인 게이트와 같은 기준 — 토큰을
    빼서 메뉴 차단을 우회하지 못하게).
    """
    async def _dep(request: Request, current: OptionalUser, session: AsyncSession = Depends(get_session)):
        if current is None:
            from app.core.auth_gate import is_exempt
            if not is_exempt(request):
                logger.warning("메뉴 API 미인증 요청 차단(401): menu=%s path=%s", menu_key, request.url.path)
                raise HTTPException(status_code=401, detail="인증이 필요합니다.")
            return
        if not await check_menu(session, current, menu_key):
            logger.warning("메뉴 권한 거부(403): user=%s menu=%s path=%s",
                           current.username, menu_key, request.url.path)
            raise HTTPException(status_code=403, detail=f"메뉴 접근 권한이 없습니다: {menu_key}")

    return _dep


async def seed_default_permissions(session: AsyncSession) -> None:
    """(호환용 no-op) 예전에는 관리 메뉴 3종을 admin 전용 행으로 시드했다.

    관리 메뉴는 이제 매트릭스와 무관하게 항상 관리자 전용(``ADMIN_MENUS``)이라 시드하지 않는다.
    예전에 시드된 행은 남아 있어도 영향이 없고, 메뉴 권한을 저장하면 정리된다.
    """
    return None
