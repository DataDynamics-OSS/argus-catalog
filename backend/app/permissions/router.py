# SPDX-License-Identifier: Apache-2.0
"""권한 매트릭스 API.

- GET  /permissions/menus, /permissions/menu-views, /permissions/features : 전체 맵 {key: [role_id]} (admin).
  응답의 ``ETag`` 를 PUT 의 ``If-Match`` 로 보내면 그 사이 다른 관리자가 저장했을 때 409(TODO-124)
- PUT  〃 : 전체 교체 (admin)
- GET  /permissions/me : 내 역할 기준 차단 목록 — 사이드바/기능 게이팅용
- GET · POST /permissions/roles, PUT · DELETE /permissions/roles/{role_id} : 사용자 정의 역할(TODO-124, admin)

역할(열): 기본 두 역할(슈퍼유저 · 사용자)에 사용자 정의 역할이 더해진다(:mod:`app.permissions.roles`).
메뉴 보기 전용(``MENU_VIEW``): 열린 메뉴를 보기만 하게 한다 — 서버 API 차단 메뉴는 쓰기 요청을 403,
그 메뉴의 편집 기능(``WRITE_FEATURE_MENUS``)은 서버 · 화면 모두 막는다. 공용 메뉴(표준 · 용어집 · 태그 · 오류 신고)는
메뉴가 막혀도 조회 API 는 열어 두되 편집 기능(``SHARED_MENU_FEATURES``)은 막는다.
권한 거부(403)는 감사 로그(권한 범주, ``permission.denied``)에 사용자 · 권한마다 1분에 한 번 남긴다 — 메뉴 · 기능
가드뿐 아니라 :class:`~app.core.auth.PermissionDenied` 로 거부하는 관리자 전용 · 소유자 확인 · 위임 범위 가드도(DEF-159).

조회(me)는 "차단 목록"을 돌려준다: open-by-default 라 프런트가 전체
레지스트리를 알 필요 없이, 명시 설정 중 내 역할이 빠진 key 만 숨기면 된다.

메뉴 권한(MENU)의 강제 범위:
- 화면: 프런트 대시보드 레이아웃의 라우트 가드가 차단된 메뉴 페이지를 막는다(주소 직접 입력 포함).
- 서버: 메뉴 전용 API 를 가진 메뉴(``SERVER_ENFORCED_MENUS``)는 해당 라우터에 ``require_menu``
  의존성을 걸어 API 직접 호출도 403 으로 막는다. 여러 메뉴가 함께 쓰는 공용 API(/catalog 등)는
  다른 화면을 깨뜨리므로 서버 차단 대상이 아니다 — 권한 관리 화면에 "화면만" 으로 표시된다.
- 관리 메뉴(``ADMIN_MENUS``: 사용자·설정·권한 관리)는 매트릭스로 열 수 없다(항상 관리자 전용,
  각 API 가 AdminUser 로 강제). 예외로 ``ADMIN_MENU_GRANTS`` 의 메뉴(메타데이터 수집 · 데이터 품질 · 사용자 관리 · 설정 · 감사 로그)는
  연결된 기능 중 하나라도 명시 허용받은 역할에도 열린다(TODO-123). 권한 관리 메뉴는 항상 관리자 전용.

예외 — ``DEFAULT_DENY_FEATURES`` 의 기능은 반대로 **명시 허용형**이다. 설정이 없으면
admin 만 허용하고, 매트릭스에 역할이 명시된 경우에만 그 역할을 허용한다(원래 admin
전용이던 기능을 매트릭스로 옮기면서 기본 권한이 넓어지지 않게 하기 위함). 이런 기능은
``/me`` 의 ``granted_features`` 로 허용 여부를 판단한다.

데이터셋 직접 등록(``datasets.create``)은 매트릭스 위에 설정 옵션이 하나 더 걸린다
(:func:`check_dataset_create`): 관리자·동기화 계정(``datasources.sync`` 명시 허용)은 항상 가능하고,
일반 사용자는 설정 ``catalog_dataset_create_by_users`` 가 켜져 있을 때만(그때도 매트릭스 적용) 가능하다.
옵션이 꺼져 있으면 ``/me`` 의 ``denied_features`` 에 ``datasets.create`` 가 들어간다.
"""

import hashlib
import logging
import re
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser, OptionalUser, PermissionDenied
from app.core.database import get_session
from app.permissions import roles as custom_roles
from app.permissions.models import Permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/permissions", tags=["permissions"])

# 매트릭스의 기본 열(비-admin 역할) — admin 은 항상 허용이라 저장하지 않는다.
# 사용자 정의 역할(TODO-124)이 있으면 그 열이 더해진다(:func:`app.permissions.roles.managed_roles`).
MANAGED_ROLES = custom_roles.BASE_ROLES

# 메뉴 보기 전용(TODO-124) — (MENU_VIEW, 메뉴, 역할) 행이 있으면 그 역할은 메뉴를 보기만 한다.
# 메뉴가 열려 있을 때만 뜻이 있고, 설정하지 않으면(행 없음) 지금처럼 편집도 된다.
VIEW_KIND = "MENU_VIEW"
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# 보기 전용이어도 서버 API 차단 메뉴에서 부를 수 있는 쓰기 요청 — 자기 즐겨찾기 · 저장한 보기, 조회 기능(Try-it)
VIEW_ONLY_ALLOWED_WRITES: dict[str, tuple[re.Pattern, ...]] = {
    "apis": (re.compile(r"^/api/v1/apis/[^/]+/favorites$"), re.compile(r"^/api/v1/apis/invoke$")),
    "ai-agents": (re.compile(r"^/api/v1/ai-agents/views(/\d+)?$"),),
}

# 명시 허용형 기능 — 미설정이면 admin 전용 (프런트 permission-registry 의 defaultDeny 와 일치)
DEFAULT_DENY_FEATURES: set[str] = {
    "glossary.edit", "tags.manage", "standards.edit", "alerts.rules",
    # 데이터 소스 — 원래 admin 전용이던 관리/동기화와 접속 정보 조회
    "datasources.manage", "datasources.sync", "datasources.connection",
    # 분류 체계 — 원래 admin 전용이던 분류체계·분류·데이터셋 매핑 편집
    "taxonomies.edit",
    # 운영 영향이 큰 작업 — MLflow 운영(Production) 단계 변경, MCP 배포 자격증명 저장
    "models.promote", "mcp.credential",
    # 관리 > 데이터 품질 메뉴(전체 품질 작업 · 진단 이력) — 원래 관리자 전용
    "quality.manage",
    # 권한 2단계(TODO-122) — 원래 관리자 전용이던 작업을 역할에 맡길 수 있게
    "lineage.edit", "federation.manage", "federation.import", "oci.import-hf", "alerts.purge",
    # 권한 3단계(TODO-123) — 관리 기능 위임. 인증 · CORS · 권한 관리 · 역할 변경은 관리자 고정
    "users.manage", "audit.view",
    # 분류 체계 — 데이터셋 매핑을 정의 편집(taxonomies.edit)에서 떼어 냄(TODO-138)
    "taxonomies.map",
    # 카탈로그 등록 · 연결, 수동 데이터셋 원천 매핑(TODO-CAT-001) — 데이터 소스 관리 권한 없이 맡길 수 있게
    "catalogs.manage",
    "settings.llm", "settings.embedding", "settings.assistant", "settings.ai-ready", "settings.notify",
    "settings.mail", "settings.object-storage", "settings.metadata-sync", "settings.cache", "settings.api-keys",
}

# 설정 메뉴를 여는 위임 키 — 하나라도 받으면 설정 메뉴가 열리고 받은 항목의 탭만 보인다(TODO-123)
SETTINGS_DELEGATE_FEATURES: tuple[str, ...] = (
    "settings.llm", "settings.embedding", "settings.assistant", "settings.ai-ready", "settings.notify",
    "settings.mail", "settings.object-storage", "settings.metadata-sync", "settings.cache", "settings.api-keys",
)

# 관리 메뉴 — 항상 관리자 전용(매트릭스 대상 아님). 저장 시 무시한다.
# MLflow·OCI 모델 파일(저장소 전체 탐색)도 관리자 전용 — 일반 사용자는 모델 상세의 파일 탭으로 본다.
# 플러그인이 관리자 전용 메뉴를 선언하면 여기에 더해진다(register_plugin_permissions) — 그래서 frozenset 이 아니다.
ADMIN_MENUS: set[str] = {"users", "settings", "permissions", "mlflow-files", "oci-files", "sync-runs", "quality-runs",
                         "audit"}

# 관리 메뉴 중 기능 권한을 명시 허용받은 역할에도 여는 메뉴 → 그 기능 key.
# 메타데이터 수집(진행·결과 화면)은 수집 권한(datasources.sync)을 받은 사람도 본다(TODO-074) —
# 조회 API 는 로그인 사용자 공개, 취소·재시도는 API 가 datasources.sync 로 강제한다.
# 프런트 permission-registry 의 grantedBy 와 일치해야 한다(수동 동기화).
# 데이터 품질은 품질 관리 권한(quality.manage, 명시 허용형)을 받은 사람이 작업 · 진행 · 결과를 보고 취소 · 다시 실행한다.
# 데이터셋 품질 탭의 진단 실행(quality.run, 기본 허용형)과는 따로 둔다(DEF-128).
# 사용자 관리는 users.manage, 설정은 위임 키 중 하나(TODO-123). 값은 "이 중 하나라도" 허용받으면 연다.
# 감사 로그(전 범주 — 사용자 · 설정 · 권한 · 운영)는 audit.view 를 받으면 연다(TODO-152). 조회 API 도 같은 키로 강제한다.
ADMIN_MENU_GRANTS: dict[str, tuple[str, ...]] = {
    "sync-runs": ("datasources.sync",),
    "quality-runs": ("quality.manage",),
    "users": ("users.manage",),
    "settings": SETTINGS_DELEGATE_FEATURES,
    "audit": ("audit.view",),
}

# 편집 기능 → 소속 메뉴(TODO-124). 메뉴가 보기 전용인 역할은 이 기능을 쓸 수 없다(서버 · 화면 모두).
# 조회 기능(샘플 · DDL · 내려받기 · 감사 이벤트 조회 등)은 넣지 않는다 — 보기 전용이어도 쓴다.
# 관리 메뉴를 여는 위임 키(datasources.sync · quality.manage)도 넣지 않는다 — 수집 계정 · 관리 메뉴가 메뉴 보기 전용에 묶이지 않게.
# 프런트 permission-registry 의 menuKey(view 표시가 없는 기능)와 일치해야 한다 — tests/test_permission_stage4.py 가 비교한다.
WRITE_FEATURE_MENUS: dict[str, str] = {
    **dict.fromkeys(("datasets.edit", "datasets.schema-edit", "datasets.sample-manage", "datasets.delete", "datasets.create",
                     "lineage.edit", "comments.write", "quality.rules-manage", "quality.run", "quality.schedule",
                     "ai.generate", "standards.map", "aiready.judge", "taxonomies.map"), "datasets"),
    "data-reports.handle": "data-reports",
    **dict.fromkeys(("federation.manage", "federation.import"), "federation"),
    "datasources.manage": "datasources",
    "catalogs.manage": "catalogs",
    **dict.fromkeys(("models.register", "models.edit", "models.delete", "models.promote"), "models"),
    **dict.fromkeys(("oci.register", "oci.edit", "oci.delete", "oci.import-hf"), "oci-hub"),
    **dict.fromkeys(("agents.register", "agents.edit", "agents.bulk-edit", "agents.delete"), "ai-agents"),
    **dict.fromkeys(("mcp.register", "mcp.edit", "mcp.delete", "mcp.collect", "mcp.audit-ingest", "mcp.credential",
                     "mcp.policy"), "mcp-servers"),
    **dict.fromkeys(("apis.register", "apis.edit", "apis.delete"), "apis"),
    "standards.edit": "standards",
    "glossary.edit": "glossary",
    "tags.manage": "tags",
    **dict.fromkeys(("alerts.purge", "alerts.rules", "alerts.ack"), "alerts"),
    "taxonomies.edit": "taxonomies",
    **dict.fromkeys(("changes.create", "changes.approve", "changes.consumers", "changes.rollback"), "changes"),
}

# 여러 메뉴가 함께 쓰는 공용 API 라 메뉴 자체는 서버에서 막지 않지만, 편집은 메뉴 권한으로도 막는 기능(TODO-124).
# 데이터셋 화면도 같은 목록 API(GET)를 쓰므로 조회는 열어 두고, 정의를 바꾸는 편집만 메뉴가 막힌 역할에 403.
# 분류 체계는 데이터셋 매핑(taxonomies.map)을 떼어 낸 뒤 정의 편집만 넣었다(TODO-138).
SHARED_MENU_FEATURES: dict[str, str] = {
    "standards.edit": "standards", "glossary.edit": "glossary", "tags.manage": "tags",
    "data-reports.handle": "data-reports", "taxonomies.edit": "taxonomies",
}

# 서버에서도 API 를 차단하는 메뉴 → 그 메뉴 전용 API prefix (main.py 의 include_router 와 일치).
# 프런트 permission-registry 의 serverEnforced 와 일치해야 한다(수동 동기화). main.py 에 각 메뉴의
# ``_menu_guard`` 가 걸렸는지는 tests/test_admin_stage1.py::test_server_enforced_menus_are_guarded_in_main 이 검사한다.
SERVER_ENFORCED_MENUS: dict[str, tuple[str, ...]] = {
    "apis": ("/api/v1/apis",),
    "ai-agents": ("/api/v1/ai-agents",),
    "mcp-servers": ("/api/v1/mcp-servers", "/api/v1/mcp-deployments"),
    "models": ("/api/v1/models", "/api/2.1/unity-catalog"),
    "oci-hub": ("/api/v1/oci-models",),
    "federation": ("/api/v1/federation",),
    "alerts": ("/api/v1/alerts",),
}

# 기능 권한 key — 실제로 화면/서버가 검사하는 것만 둔다(프런트 FEATURE_REGISTRY 와 일치).
# 목록에 없는 key 는 저장하지 않는다(효과 없는 체크박스로 관리자가 '잠갔다'고 오해하지 않도록).
# 플러그인이 선언한 기능 키도 기동 시 여기에 더해진다(register_plugin_permissions).
FEATURE_KEYS: set[str] = {
    "datasets.sample-view", "datasets.ddl-view", "datasets.transfer-code",
    "datasources.manage", "datasources.connection", "datasources.sync",
    "mcp.policy", "apis.credentials",
    "standards.edit", "glossary.edit", "tags.manage", "alerts.rules",
    "taxonomies.edit",
    "quality.rules-manage", "quality.run", "quality.schedule", "quality.manage", "quality.export",
    # 권한 2단계(TODO-122) — 수정 · 삭제 · 다운로드. 기본 허용형은 소유자 · 관리자 규칙 위에 역할별로 막는다
    "datasets.edit", "datasets.delete", "datasets.schema-edit", "datasets.sample-manage", "lineage.edit",
    "comments.write", "data-reports.handle", "federation.manage", "federation.import",
    "agents.edit", "agents.delete", "agents.bulk-edit",
    "mcp.edit", "mcp.delete", "mcp.collect", "mcp.audit-view", "mcp.audit-ingest",
    "models.edit", "models.delete", "models.download",
    "oci.edit", "oci.delete", "oci.import-hf", "oci.download",
    "apis.edit", "apis.delete", "apis.try-it", "alerts.purge",
    "changes.create", "changes.approve", "changes.consumers",
    "ai.assistant", "ai.generate",
    # 등록 — 미설정이면 전 역할 허용(막고 싶은 역할만 해제)
    "datasets.create", "models.register", "oci.register", "agents.register", "mcp.register", "apis.register",
    "models.promote", "mcp.credential",
    # 관리 > 데이터 품질 메뉴(전체 품질 작업 · 진단 이력) — 원래 관리자 전용
    "quality.manage",
    # 권한 3단계(TODO-123) — 관리 기능 위임(모두 명시 허용형)
    "users.manage", "audit.view", *SETTINGS_DELEGATE_FEATURES,
    # 권한 4단계(TODO-124) — 기본 허용형(소유자 · 담당자 확인은 그대로)
    "standards.map", "aiready.judge", "changes.rollback", "alerts.ack",
    # 권한 5단계(TODO-138) — 데이터셋 ↔ 분류 매핑(명시 허용형)
    "taxonomies.map",
    # 카탈로그 등록 · 연결, 수동 데이터셋 원천 매핑(TODO-CAT-001, 명시 허용형)
    "catalogs.manage",
}


def _my_role(current) -> str:
    """현재 사용자의 매트릭스 역할 — 사용자 정의 역할이 있으면 그 역할, 아니면 superuser 이면 argus-superuser,
    아니면 argus-user (admin 은 호출 전에 걸러짐)."""
    custom = getattr(current, "custom_role", None)
    if custom:
        return custom
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


def _viewable_menu(key: str) -> bool:
    """보기 전용을 둘 수 있는 메뉴인가 — 관리 메뉴가 아니고 막을 편집 기능(``WRITE_FEATURE_MENUS``)이 있는 메뉴.

    대시보드 · 내 결재함 · 즐겨찾기 · AI-Ready 점검은 편집 기능이 없어 보기 전용이 효과가 없다(DEF-162).
    결재(``changes.approve``)는 변경 관리 메뉴의 기능이라 내 결재함 보기 전용으로 막지 않는다 — 결재를 막으려면 기능 권한을 끈다.
    프런트 권한 관리 화면의 ``VIEWABLE_MENUS`` 와 같은 기준.
    """
    return key not in ADMIN_MENUS and key in WRITE_FEATURE_MENUS.values()


def _storable_key(kind: str, key: str) -> bool:
    """저장 가능한 key 인지 — MENU 는 관리 메뉴 제외 전부, MENU_VIEW 는 보기 전용이 뜻 있는 메뉴만, FEATURE 는 ``FEATURE_KEYS`` 에 있는 것만."""
    if kind == VIEW_KIND:
        return _viewable_menu(key)
    if kind == "MENU":
        return key not in ADMIN_MENUS
    return key in FEATURE_KEYS


def matrix_etag(matrix: dict[str, list[str]]) -> str:
    """매트릭스 버전 — 저장된 행의 지문. 동시 편집 감지용(GET 의 ETag, PUT 의 If-Match)."""
    text = "\n".join(f"{k}|{r}" for k in sorted(matrix) for r in sorted(matrix[k]))
    return '"' + hashlib.sha256(text.encode("utf-8")).hexdigest()[:20] + '"'


_KIND_LABEL = {"MENU": "메뉴", "FEATURE": "기능", VIEW_KIND: "메뉴 보기 전용"}
_KIND_ACTION = {"MENU": "permissions.menus", "FEATURE": "permissions.features", VIEW_KIND: "permissions.menu-views"}


async def _put_matrix(session: AsyncSession, kind: str, payload: dict, actor=None) -> dict:
    """전체 교체 저장 — 모든 관리 역할이 허용된 key 는 '미설정'으로 정규화(행 삭제).

    이렇게 하면 DB 에는 '제한이 걸린 항목'만 남아, open-by-default 의미가
    데이터 모양과 일치한다 (전부 체크 = 행 없음 = 기본 상태).

    ``actor`` 가 있으면 바뀐 key 의 전후(허용 역할 목록, 행 없음 = 기본)를 감사 로그에 남긴다.
    """
    logger.debug("권한 매트릭스 전체 교체 시작: kind=%s keys=%d", kind, len(payload or {}))
    before = await _get_matrix(session, kind)
    managed = await custom_roles.managed_roles(session)
    await session.execute(sql_delete(Permission).where(Permission.kind == kind))
    saved = 0
    for key, roles in (payload or {}).items():
        if not _storable_key(kind, str(key)):
            logger.debug("저장 대상이 아닌 권한 key 무시: kind=%s key=%s", kind, key)
            continue  # 관리 메뉴·알 수 없는 기능 key 는 저장하지 않음
        allowed = sorted({r for r in (roles or []) if r in managed})
        if kind == VIEW_KIND:
            # 보기 전용은 체크한 역할만 행으로 — 아무도 없으면 행 없음(편집 가능)
            for role in allowed:
                session.add(Permission(kind=kind, perm_key=str(key)[:100], role_id=role))
                saved += 1
            continue
        default_deny = kind == "FEATURE" and key in DEFAULT_DENY_FEATURES
        if set(allowed) == set(managed) and not default_deny:
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
            await record(
                session, actor, "permissions", _KIND_ACTION[kind],
                target=kind, summary=f"{_KIND_LABEL[kind]} 권한 변경: {', '.join(changes)}", detail={"changes": changes},
            )
    return {"kind": kind, "restricted_rows": saved}


async def _get_with_etag(session: AsyncSession, kind: str, response: Response) -> dict[str, list[str]]:
    matrix = await _get_matrix(session, kind)
    response.headers["ETag"] = matrix_etag(matrix)
    return matrix


async def _put_checked(session: AsyncSession, kind: str, payload: dict, actor, if_match: str | None,
                       response: Response) -> dict:
    """``If-Match`` 가 있으면 저장된 버전과 비교해 다르면 409 — 다른 관리자가 먼저 저장한 내용을 덮어쓰지 않게(TODO-124).

    헤더가 없으면(예전 화면 · 스크립트) 그대로 저장한다.
    """
    if if_match:
        current = matrix_etag(await _get_matrix(session, kind))
        if if_match.strip() != current:
            logger.warning("권한 저장 충돌(409): kind=%s by=%s", kind, getattr(actor, "username", None))
            raise HTTPException(
                status_code=409,
                detail="다른 관리자가 먼저 권한을 저장했습니다. 다시 불러온 뒤 변경을 적용하세요.",
            )
    result = await _put_matrix(session, kind, payload, actor=actor)
    response.headers["ETag"] = matrix_etag(await _get_matrix(session, kind))
    return result


@router.get("/menus")
async def get_menu_permissions(_admin: AdminUser, response: Response, session: AsyncSession = Depends(get_session)):
    """메뉴 권한 매트릭스 (admin) — 명시 설정만 반환, 없는 key 는 전체 허용. ``ETag`` 는 저장 충돌 확인용."""
    return await _get_with_etag(session, "MENU", response)


@router.put("/menus")
async def put_menu_permissions(payload: dict, _admin: AdminUser, response: Response,
                               if_match: str | None = Header(None),
                               session: AsyncSession = Depends(get_session)):
    """메뉴 권한 전체 교체 (admin)."""
    return await _put_checked(session, "MENU", payload, _admin, if_match, response)


@router.get("/menu-views")
async def get_menu_view_permissions(_admin: AdminUser, response: Response,
                                    session: AsyncSession = Depends(get_session)):
    """메뉴 보기 전용 매트릭스 (admin) — {메뉴: [보기 전용 역할]}. 없는 key 는 편집 가능."""
    return await _get_with_etag(session, VIEW_KIND, response)


@router.put("/menu-views")
async def put_menu_view_permissions(payload: dict, _admin: AdminUser, response: Response,
                                    if_match: str | None = Header(None),
                                    session: AsyncSession = Depends(get_session)):
    """메뉴 보기 전용 전체 교체 (admin)."""
    return await _put_checked(session, VIEW_KIND, payload, _admin, if_match, response)


@router.get("/features")
async def get_feature_permissions(_admin: AdminUser, response: Response,
                                  session: AsyncSession = Depends(get_session)):
    """기능 권한 매트릭스 (admin)."""
    return await _get_with_etag(session, "FEATURE", response)


@router.put("/features")
async def put_feature_permissions(payload: dict, _admin: AdminUser, response: Response,
                                  if_match: str | None = Header(None),
                                  session: AsyncSession = Depends(get_session)):
    """기능 권한 전체 교체 (admin)."""
    return await _put_checked(session, "FEATURE", payload, _admin, if_match, response)


@router.get("/me")
async def get_my_permissions(current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """내 역할 기준 차단된 메뉴/기능 key — 사이드바 필터·기능 게이팅용.

    admin 은 항상 빈 목록(전부 허용). 일반 역할은 '명시 설정이 있는데
    내 역할이 포함되지 않은' key 가 차단 목록에 들어간다.
    ``granted_features`` 는 내 역할이 명시 허용된 기능 key — 명시 허용형 기능 판단용.
    ``readonly_menus`` 는 보기 전용 메뉴(TODO-124) — 그 메뉴의 편집 기능은 ``denied_features`` 에 들어가고
    ``granted_features`` 에서 빠진다(서버 판정 :func:`_feature_blocked_by_menu` 와 같다).
    """
    if current.is_admin:
        return {"denied_menus": [], "denied_features": [], "granted_features": [], "readonly_menus": [],
                "role": None}

    # 내 역할 결정 — 사용자 정의 역할 우선, 다음 superuser
    my_role = _my_role(current)

    rows = (await session.execute(select(Permission))).scalars().all()
    logger.debug("내 권한 조회: user=%s role=%s rows=%d", getattr(current, "username", None), my_role, len(rows))
    by_key: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        by_key.setdefault((r.kind, r.perm_key), set()).add(r.role_id)

    denied_menus = [k for (kind, k), roles in by_key.items()
                    if kind == "MENU" and my_role not in roles]
    denied_features = {k for (kind, k), roles in by_key.items()
                       if kind == "FEATURE" and my_role not in roles}
    granted_features = {k for (kind, k), roles in by_key.items()
                        if kind == "FEATURE" and my_role in roles}
    # 관리 메뉴는 매트릭스와 무관하게 항상 차단(관리자 전용) — 연결된 기능을 명시 허용받았으면 연다
    admin_menus = {m for m in ADMIN_MENUS if not granted_features.intersection(ADMIN_MENU_GRANTS.get(m, ()))}
    denied_menu_set = set(denied_menus) | admin_menus
    readonly_menus = sorted(k for (kind, k), roles in by_key.items()
                            if kind == VIEW_KIND and my_role in roles and k not in denied_menu_set
                            and _viewable_menu(k))
    # 보기 전용 메뉴의 편집 기능, 메뉴가 막힌 공용 메뉴의 편집 기능은 쓸 수 없다
    readonly = set(readonly_menus)
    for key, menu in WRITE_FEATURE_MENUS.items():
        if menu in readonly or (SHARED_MENU_FEATURES.get(key) == menu and menu in denied_menu_set):
            denied_features.add(key)
            granted_features.discard(key)
    # 데이터셋 직접 등록은 설정 옵션이 꺼져 있으면(기본) 매트릭스와 무관하게 막힌다 — 서버 판정과 맞춘다
    if DATASET_CREATE_FEATURE not in denied_features and await check_dataset_create(session, current) is not None:
        denied_features.add(DATASET_CREATE_FEATURE)
    return {
        "denied_menus": sorted(denied_menu_set),
        "denied_features": sorted(denied_features),
        "granted_features": sorted(granted_features),
        "readonly_menus": readonly_menus,
        "role": my_role,
    }


async def _menu_view_roles(session: AsyncSession, menu_key: str) -> set[str]:
    """메뉴를 보기 전용으로 쓰는 역할 집합(없으면 빈 집합) — 짧게 기억한다(저장 시 무효화)."""
    cache_key = f"{VIEW_KIND}:{menu_key}"
    hit = _menu_cache.get(cache_key)
    now = time.monotonic()
    if hit and now - hit[0] < _MENU_CACHE_TTL:
        return hit[1] or set()
    rows = (await session.execute(
        select(Permission.role_id).where(Permission.kind == VIEW_KIND, Permission.perm_key == menu_key)
    )).scalars().all()
    roles = set(rows)
    _menu_cache[cache_key] = (now, roles)
    return roles


async def is_menu_readonly(session: AsyncSession, current, menu_key: str) -> bool:
    """이 사용자에게 메뉴가 보기 전용인가 — admin · 비로그인은 False."""
    if current is None or current.is_admin or menu_key in ADMIN_MENUS:
        return False
    return _my_role(current) in await _menu_view_roles(session, menu_key)


async def _feature_blocked_by_menu(session: AsyncSession, current, feature_key: str) -> bool:
    """기능이 메뉴 때문에 막혔는가(TODO-124) — 메뉴가 보기 전용이거나, 공용 메뉴의 편집인데 메뉴가 막혔을 때."""
    menu = WRITE_FEATURE_MENUS.get(feature_key)
    if not menu or current is None or current.is_admin:
        return False
    if await is_menu_readonly(session, current, menu):
        logger.debug("보기 전용 메뉴의 편집 기능 거부: user=%s feature=%s menu=%s", current.username, feature_key, menu)
        return True
    if SHARED_MENU_FEATURES.get(feature_key) == menu and not await check_menu(session, current, menu):
        logger.debug("막힌 메뉴의 편집 기능 거부: user=%s feature=%s menu=%s", current.username, feature_key, menu)
        return True
    return False


# 같은 사용자 · 같은 권한의 거부는 이 시간(초)에 한 번만 감사 로그에 남긴다 — 화면이 되풀이해 부를 때 로그가 넘치지 않게
_DENY_AUDIT_WINDOW = 60.0
_deny_seen: dict[tuple[str, ...], float] = {}


# 감사 요약에 쓰는 거부 종류 이름 — 메뉴 · 기능 가드 밖의 403 도 같은 형식으로 남긴다(DEF-159)
_DENY_KIND_LABELS = {"MENU": "메뉴", "FEATURE": "기능", "ADMIN": "관리자 전용", "OWNER": "소유자", "DELEGATE": "위임 범위"}


async def audit_denied(session: AsyncSession | None, current, kind: str, key: str, path: str | None = None) -> None:
    """권한 거부(403)를 감사 로그(권한 범주, ``permission.denied``)에 남긴다(TODO-124). 실패해도 예외를 내지 않는다.

    요청 세션으로 기록한다 — 거부는 핸들러 전에 일어나 그 세션에 쓰지 않은 변경이 없다. ``session`` 이
    None 이면 감사 모듈이 별도 세션을 연다. 메뉴 · 기능은 사용자 · 권한마다, 관리자 전용 · 소유자 · 위임 범위
    거부(키가 여러 API 에 같음)는 사용자 · 권한 · 경로마다 1분에 한 번 남긴다.
    """
    if current is None:
        return
    seen_key = (str(current.username), kind, key) if kind in ("MENU", "FEATURE") \
        else (str(current.username), kind, key, path or "")
    now = time.monotonic()
    if now - _deny_seen.get(seen_key, -_DENY_AUDIT_WINDOW) < _DENY_AUDIT_WINDOW:
        return
    if len(_deny_seen) > 2000:
        _deny_seen.clear()
    _deny_seen[seen_key] = now
    from app.audit.service import record
    label = _DENY_KIND_LABELS.get(kind, kind)
    await record(
        session, current, "permissions", "permission.denied", target=key,
        summary=f"권한 거부({label}): {key}" + (f" — {path}" if path else ""),
        detail={"kind": kind, "key": key, "path": path, "role": _my_role(current) if not current.is_admin else None},
    )


async def audit_permission_denied(request: Request | None, exc: Exception) -> None:
    """앱 HTTP 예외 처리기에서 부른다 — :class:`~app.core.auth.PermissionDenied` 403 을 감사 로그에 남긴다(DEF-159).

    관리자 전용(``require_admin``) · 소유자 확인(``assert_owner_or_admin``) · 데이터셋 직접 등록 · 사용자 관리와
    API 키의 위임 범위 가드처럼 세션 없이 거부하는 곳도 메뉴 · 기능 거부와 같은 형식으로 남긴다. 별도 세션으로
    기록하고(요청 세션은 이미 닫혔을 수 있다), 어떤 실패도 응답을 바꾸지 않는다.
    """
    if not isinstance(exc, PermissionDenied):
        return
    try:
        await audit_denied(None, exc.user, exc.kind, exc.key, _path(request))
    except Exception as e:  # noqa: BLE001 — 감사 실패가 403 응답을 바꾸지 않도록
        logger.error("권한 거부 감사 실패: kind=%s key=%s: %s", exc.kind, exc.key, e)


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
    if rows and (current is None or _my_role(current) not in rows):
        return False
    # 미설정 = 전체 허용 — 단 메뉴가 보기 전용이거나(편집 기능) 공용 메뉴가 막혔으면 거부(TODO-124)
    return not await _feature_blocked_by_menu(session, current, feature_key)


async def check_feature_granted(session: AsyncSession, current, feature_key: str) -> bool:
    """명시 허용형 기능 권한 체크 — admin 이거나 매트릭스에 내 역할이 명시된 경우만 허용."""
    if current.is_admin:
        return True
    rows = (await session.execute(
        select(Permission.role_id).where(
            Permission.kind == "FEATURE", Permission.perm_key == feature_key)
    )).scalars().all()
    if _my_role(current) not in rows:
        return False
    return not await _feature_blocked_by_menu(session, current, feature_key)


def _path(request: Request | None) -> str | None:
    return request.url.path if request is not None else None


def require_feature_granted(feature_key: str):
    """FastAPI 의존성 팩토리 — 명시 허용형 기능 권한이 없으면 403 (미설정이면 admin 전용)."""

    async def _dep(current: CurrentUser, session: AsyncSession = Depends(get_session),
                   request: Request = None):  # type: ignore[assignment] — FastAPI 가 채운다(직접 부를 땐 생략)
        if not await check_feature_granted(session, current, feature_key):
            logger.warning("명시 허용형 기능 권한 거부(403): user=%s feature=%s", current.username, feature_key)
            await audit_denied(session, current, "FEATURE", feature_key, _path(request))
            raise HTTPException(status_code=403, detail=f"기능 권한이 없습니다: {feature_key}")
        return current

    return _dep


def require_any_feature_granted(*feature_keys: str):
    """명시 허용형 기능 중 하나라도 허용받았으면 통과(관리자는 항상) — 아니면 403."""

    async def _dep(current: CurrentUser, session: AsyncSession = Depends(get_session),
                   request: Request = None):  # type: ignore[assignment] — FastAPI 가 채운다(직접 부를 땐 생략)
        for key in feature_keys:
            if await check_feature_granted(session, current, key):
                return current
        logger.warning("명시 허용형 기능 권한 거부(403): user=%s features=%s", current.username, feature_keys)
        await audit_denied(session, current, "FEATURE", " · ".join(feature_keys), _path(request))
        raise HTTPException(status_code=403, detail=f"기능 권한이 없습니다: {' · '.join(feature_keys)}")

    return _dep


def register_plugin_permissions(name: str, *, features=(), menus=()) -> None:
    """플러그인이 선언한 기능 키 · 관리자 전용 메뉴를 권한 표에 더한다(TODO-123).

    - ``features``: ``{"key": "acme.billing-export", "default_deny": True, "menu": "plugin.acme-billing", "view": False}``
      또는 키 문자열. 키는 ``<플러그인>.`` 으로 시작하는 것을 권장하며, 코어 키와 겹치면 무시한다(코어 의미를 바꾸지 않도록).
      ``menu`` 를 주면 그 메뉴가 보기 전용인 역할은 이 기능을 쓸 수 없다(편집 기능, TODO-138). ``view: True`` 면 조회 기능이라 묶지 않는다.
      관리자 전용 메뉴(``admin_only``)는 보기 전용이 없어 묶지 않는다.
    - ``menus``: ``{"key": "plugin.acme-billing", "admin_only": True, "granted_by": ["acme.billing-admin"]}``.
      ``admin_only`` 면 관리 메뉴처럼 매트릭스로 열 수 없고, ``granted_by`` 기능을 명시 허용받은 역할에만 열린다.
    """
    menus = list(menus or ())
    admin_only = {str(m.get("key")) for m in menus if isinstance(m, dict) and m.get("admin_only")}
    for f in features or ():
        key, deny = (f, False) if isinstance(f, str) else (str(f.get("key") or ""), bool(f.get("default_deny")))
        menu = None if isinstance(f, str) else (str(f.get("menu") or "") or None)
        is_view = False if isinstance(f, str) else bool(f.get("view"))
        if not key or len(key) > 100:
            logger.warning("플러그인 '%s' 기능 키 무시(형식): %r", name, f)
            continue
        if key in FEATURE_KEYS:
            logger.warning("플러그인 '%s' 기능 키가 이미 있음 — 무시: %s", name, key)
            continue
        FEATURE_KEYS.add(key)
        if deny:
            DEFAULT_DENY_FEATURES.add(key)
        if menu and not is_view:
            if menu.startswith("plugin.") and menu not in admin_only and menu not in ADMIN_MENUS:
                WRITE_FEATURE_MENUS[key] = menu
            else:
                logger.warning("플러그인 '%s' 기능 '%s' 의 메뉴 무시(plugin. 메뉴 · 관리자 전용 아님만): %s", name, key, menu)
        logger.info("플러그인 '%s' 기능 키 등록: %s (%s)", name, key, "명시 허용형" if deny else "기본 허용형")
    for m in menus:
        key = str((m or {}).get("key") or "")
        if not key.startswith("plugin."):
            logger.warning("플러그인 '%s' 메뉴 키 무시(plugin. 으로 시작해야 함): %r", name, m)
            continue
        if m.get("admin_only"):
            ADMIN_MENUS.add(key)
            grants = tuple(str(g) for g in (m.get("granted_by") or ()))
            if grants:
                ADMIN_MENU_GRANTS[key] = grants
            logger.info("플러그인 '%s' 관리자 전용 메뉴 등록: %s (위임 %s)", name, key, grants or "없음")


def require_dataset_feature(feature_key: str):
    """데이터셋 정보 · 스키마 쓰기 기능 권한(기본 허용형) — 메타데이터 수집 계정은 예외.

    수집 어댑터가 같은 API(속성 · 스키마)로 원본 메타데이터를 반영하므로, 수집 권한(``datasources.sync``,
    명시 허용형)을 받은 계정은 역할에서 이 기능을 막아도 통과시킨다(TODO-122).
    """

    async def _dep(current: CurrentUser, session: AsyncSession = Depends(get_session),
                   request: Request = None):  # type: ignore[assignment] — FastAPI 가 채운다(직접 부를 땐 생략)
        if await check_feature(session, current, feature_key):
            return current
        if await check_feature_granted(session, current, "datasources.sync"):
            return current
        logger.warning("기능 권한 거부(403): user=%s feature=%s", current.username, feature_key)
        await audit_denied(session, current, "FEATURE", feature_key, _path(request))
        raise HTTPException(status_code=403, detail=f"기능 권한이 없습니다: {feature_key}")

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
        logger.warning("데이터셋 직접 등록 거부(403): user=%s", current.username)
        raise PermissionDenied(reason, user=current, kind="FEATURE", key=DATASET_CREATE_FEATURE)
    return current


def require_feature(feature_key: str):
    """FastAPI 의존성 팩토리 — 기능 권한이 없으면 403.

    사용: ``_=Depends(require_feature("datasets.sample-view"))``
    open-by-default 라 매트릭스에 설정이 없으면 통과한다.
    """

    async def _dep(current: CurrentUser, session: AsyncSession = Depends(get_session),
                   request: Request = None):  # type: ignore[assignment] — FastAPI 가 채운다(직접 부를 땐 생략)
        if not await check_feature(session, current, feature_key):
            logger.warning("기능 권한 거부(403): user=%s feature=%s", current.username, feature_key)
            await audit_denied(session, current, "FEATURE", feature_key, _path(request))
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
    if menu_key in ADMIN_MENUS:
        # 관리 메뉴(플러그인의 관리자 전용 메뉴 포함)는 매트릭스와 무관 — 연결된 위임 기능을 받았을 때만
        for key in ADMIN_MENU_GRANTS.get(menu_key, ()):
            if await check_feature_granted(session, current, key):
                return True
        return False
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
                logger.warning("메뉴 API 미인증 요청 차단(401): menu=%s path=%s", menu_key, _path(request))
                raise HTTPException(status_code=401, detail="인증이 필요합니다.")
            return
        if not await check_menu(session, current, menu_key):
            logger.warning("메뉴 권한 거부(403): user=%s menu=%s path=%s",
                           current.username, menu_key, _path(request))
            await audit_denied(session, current, "MENU", menu_key, _path(request))
            raise HTTPException(status_code=403, detail=f"메뉴 접근 권한이 없습니다: {menu_key}")
        if request.method not in READ_METHODS and await is_menu_readonly(session, current, menu_key):
            path = request.url.path
            if any(p.match(path) for p in VIEW_ONLY_ALLOWED_WRITES.get(menu_key, ())):
                return
            logger.warning("보기 전용 메뉴 쓰기 거부(403): user=%s menu=%s %s %s",
                           current.username, menu_key, request.method, path)
            await audit_denied(session, current, "MENU", f"{menu_key}(보기 전용)", path)
            raise HTTPException(status_code=403, detail=f"이 메뉴는 보기 전용입니다: {menu_key}")

    return _dep


# ---------------------------------------------------------------------------
# 사용자 정의 역할(TODO-124) — 권한 표의 열을 더한다
# ---------------------------------------------------------------------------

class RoleCreate(BaseModel):
    role_id: str = Field(..., min_length=3, max_length=50, description="역할 식별자 — 소문자 · 숫자 · 하이픈(Keycloak realm 역할 이름과 같게)")
    name: str = Field(..., min_length=1, max_length=50)
    description: str | None = Field(None, max_length=255)
    base_role: str = Field("argus-user", description="기반 역할 — argus-user 또는 argus-superuser")


class RoleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = Field(None, max_length=255)
    base_role: str | None = None


def _check_base(base: str) -> None:
    if base not in custom_roles.BASE_ROLES:
        raise HTTPException(status_code=422, detail=f"기반 역할은 {', '.join(custom_roles.BASE_ROLES)} 중 하나입니다.")


async def _role_usage(session: AsyncSession, role_id: str) -> tuple[int, int]:
    """(이 역할의 사용자 수, 폐기하지 않은 API 키 수)."""
    from app.apikeys.models import ArgusApiKey
    from app.usermgr.models import ArgusRole, ArgusUser
    users = (await session.execute(
        select(func.count()).select_from(ArgusUser).join(ArgusRole, ArgusUser.role_id == ArgusRole.id)
        .where(ArgusRole.role_id == role_id)
    )).scalar_one()
    keys = (await session.execute(
        select(func.count()).select_from(ArgusApiKey)
        .where(ArgusApiKey.role_id == role_id, ArgusApiKey.status != "revoked")
    )).scalar_one()
    return int(users), int(keys)


def _roles_changed() -> None:
    custom_roles.invalidate()
    _menu_cache.clear()


@router.get("/roles")
async def list_permission_roles(_admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """역할 목록 (admin) — 기본 역할과 사용자 정의 역할, 기반 역할 · 사용자 수 · API 키 수. ``columns`` 는 권한 표의 열 순서."""
    from app.usermgr.models import ArgusRole
    customs = await custom_roles.load_custom_roles(session)
    rows = (await session.execute(select(ArgusRole).order_by(ArgusRole.id))).scalars().all()
    builtin = custom_roles.builtin_role_ids()
    items = []
    for r in rows:
        if r.role_id not in builtin and r.role_id not in customs:
            continue  # 기반 역할 정보가 없는 역할(예전에 손으로 넣은 행)은 권한 표 열이 아니다
        users, keys = await _role_usage(session, r.role_id)
        items.append({
            "role_id": r.role_id, "name": r.name, "description": r.description,
            "builtin": r.role_id in builtin, "base_role": customs.get(r.role_id),
            "user_count": users, "api_key_count": keys,
        })
    return {"roles": items, "columns": list(await custom_roles.managed_roles(session))}


@router.post("/roles", status_code=201)
async def create_permission_role(req: RoleCreate, _admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """사용자 정의 역할 만들기 (admin) — 기반 역할의 메뉴 · 보기 전용 · 기능 권한을 복사해 같게 시작한다."""
    from app.usermgr.models import ArgusRole
    role_id = req.role_id.strip()
    if not custom_roles.ROLE_ID_RE.match(role_id) or role_id in custom_roles.RESERVED_ROLE_IDS:
        raise HTTPException(status_code=422, detail="역할 식별자는 소문자로 시작하는 소문자 · 숫자 · 하이픈 3~50자입니다.")
    if role_id in custom_roles.builtin_role_ids():
        raise HTTPException(status_code=409, detail="기본 역할과 같은 식별자는 쓸 수 없습니다.")
    _check_base(req.base_role)
    exists = (await session.execute(select(ArgusRole.id).where(ArgusRole.role_id == role_id))).scalar()
    if exists is not None:
        raise HTTPException(status_code=409, detail=f"이미 있는 역할입니다: {role_id}")
    session.add(ArgusRole(role_id=role_id, name=req.name.strip(), description=req.description))
    session.add(Permission(kind=custom_roles.ROLE_KIND, perm_key=role_id, role_id=req.base_role))
    base_rows = (await session.execute(
        select(Permission).where(Permission.kind.in_(("MENU", VIEW_KIND, "FEATURE")), Permission.role_id == req.base_role)
    )).scalars().all()
    for r in base_rows:
        session.add(Permission(kind=r.kind, perm_key=r.perm_key, role_id=role_id))
    await session.commit()
    _roles_changed()
    logger.info("사용자 정의 역할 생성: %s (기반 %s, 권한 행 %d개 복사) by %s",
                role_id, req.base_role, len(base_rows), _admin.username)
    from app.audit.service import record
    await record(session, _admin, "permissions", "role.create", target=role_id,
                 summary=f"역할 추가: {req.name} ({role_id}, 기반 {req.base_role})",
                 detail={"base_role": req.base_role, "copied_rows": len(base_rows)})
    return {"role_id": role_id, "name": req.name.strip(), "description": req.description,
            "builtin": False, "base_role": req.base_role, "user_count": 0, "api_key_count": 0}


async def _custom_role_or_404(session: AsyncSession, role_id: str):
    from app.usermgr.models import ArgusRole
    customs = await custom_roles.load_custom_roles(session)
    if role_id not in customs:
        if role_id in custom_roles.builtin_role_ids():
            raise HTTPException(status_code=409, detail="기본 역할은 바꾸거나 지울 수 없습니다.")
        raise HTTPException(status_code=404, detail=f"사용자 정의 역할이 없습니다: {role_id}")
    row = (await session.execute(select(ArgusRole).where(ArgusRole.role_id == role_id))).scalar_one_or_none()
    return row, customs[role_id]


@router.put("/roles/{role_id}")
async def update_permission_role(role_id: str, req: RoleUpdate, _admin: AdminUser,
                                 session: AsyncSession = Depends(get_session)):
    """사용자 정의 역할의 이름 · 설명 · 기반 역할 변경 (admin). 권한 표의 체크는 그대로 둔다."""
    row, base = await _custom_role_or_404(session, role_id)
    changes: dict = {}
    if row is not None and req.name is not None and req.name.strip() != row.name:
        changes["name"] = {"old": row.name, "new": req.name.strip()}
        row.name = req.name.strip()
    if row is not None and req.description is not None and req.description != (row.description or ""):
        changes["description"] = {"old": row.description, "new": req.description}
        row.description = req.description or None
    if req.base_role is not None and req.base_role != base:
        _check_base(req.base_role)
        changes["base_role"] = {"old": base, "new": req.base_role}
        await session.execute(sql_delete(Permission).where(
            Permission.kind == custom_roles.ROLE_KIND, Permission.perm_key == role_id))
        session.add(Permission(kind=custom_roles.ROLE_KIND, perm_key=role_id, role_id=req.base_role))
        base = req.base_role
    await session.commit()
    _roles_changed()
    if changes:
        from app.audit.service import record
        await record(session, _admin, "permissions", "role.update", target=role_id,
                     summary=f"역할 변경: {role_id} ({', '.join(changes)})", detail=changes)
    users, keys = await _role_usage(session, role_id)
    return {"role_id": role_id, "name": row.name if row else role_id, "description": row.description if row else None,
            "builtin": False, "base_role": base, "user_count": users, "api_key_count": keys}


@router.delete("/roles/{role_id}")
async def delete_permission_role(role_id: str, _admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """사용자 정의 역할 지우기 (admin) — 사용자나 쓰는 API 키가 있으면 409.

    이 역할만 허용하던 메뉴 · 기능은 관리자 전용 센티널을 남긴다(행이 모두 사라져 '전체 허용'이 되지 않게).
    """
    row, _base = await _custom_role_or_404(session, role_id)
    users, keys = await _role_usage(session, role_id)
    if users or keys:
        raise HTTPException(status_code=409,
                            detail=f"이 역할을 쓰는 사용자 {users}명 · API 키 {keys}개가 있습니다. 다른 역할로 바꾼 뒤 지우세요.")
    touched = (await session.execute(
        select(Permission.kind, Permission.perm_key).where(
            Permission.kind.in_(("MENU", "FEATURE")), Permission.role_id == role_id)
    )).all()
    await session.execute(sql_delete(Permission).where(or_(
        Permission.role_id == role_id,
        (Permission.kind == custom_roles.ROLE_KIND) & (Permission.perm_key == role_id),
    )))
    for kind, key in touched:
        left = (await session.execute(select(func.count()).select_from(Permission).where(
            Permission.kind == kind, Permission.perm_key == key))).scalar_one()
        if not left:
            session.add(Permission(kind=kind, perm_key=key, role_id="__none__"))
    if row is not None:
        await session.delete(row)
    await session.commit()
    _roles_changed()
    logger.info("사용자 정의 역할 삭제: %s by %s", role_id, _admin.username)
    from app.audit.service import record
    await record(session, _admin, "permissions", "role.delete", target=role_id, summary=f"역할 삭제: {role_id}")
    return {"status": "ok"}


# 기능 키를 나눌 때 예전 키를 받은 역할이 새 키도 받게 하는 이전 표 — {새 키: 예전 키}.
# 새 키에 행이 하나도 없을 때만(한 번도 저장하지 않았을 때) 예전 키의 행을 복사한다. 명시 허용형 키는 저장하면
# 미허용도 센티널 행으로 남으므로, 관리자가 새 키를 한 번이라도 저장한 뒤에는 다시 복사하지 않는다.
SPLIT_FEATURES: dict[str, str] = {
    "taxonomies.map": "taxonomies.edit",   # TODO-138 — 데이터셋 ↔ 분류 매핑을 정의 편집에서 떼어 냄
}


async def seed_default_permissions(session: AsyncSession) -> None:
    """기동 때 권한 행 보정 — 나눈 기능 키(:data:`SPLIT_FEATURES`)의 허용을 이어받게 한다.

    관리 메뉴는 매트릭스와 무관하게 항상 관리자 전용(``ADMIN_MENUS``)이라 시드하지 않는다.
    """
    copied = 0
    for new_key, old_key in SPLIT_FEATURES.items():
        has_new = (await session.execute(select(func.count()).select_from(Permission).where(
            Permission.kind == "FEATURE", Permission.perm_key == new_key))).scalar_one()
        if has_new:
            continue
        old_roles = (await session.execute(select(Permission.role_id).where(
            Permission.kind == "FEATURE", Permission.perm_key == old_key))).scalars().all()
        for role in old_roles:
            session.add(Permission(kind="FEATURE", perm_key=new_key, role_id=role))
            copied += 1
        if old_roles:
            logger.info("나눈 기능 키 권한 이어받기: %s ← %s (%s)", new_key, old_key, ", ".join(sorted(old_roles)))
    if copied:
        await session.commit()
        _menu_cache.clear()
