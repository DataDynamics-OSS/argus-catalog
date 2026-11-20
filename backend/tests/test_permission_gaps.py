# SPDX-License-Identifier: Apache-2.0
"""권한 보완 1단계 — 메뉴 · 소유자 가드가 빠져 있던 API (자기완결형, in-memory SQLite).

확인하는 규칙:
- model-store 의 다운로드 주소 · 파일 목록 · manifest 는 그 모델이 속한 메뉴(OCI 허브 · MLflow 모델) 권한을 본다(DEF-119).
  MLflow 호환 API(``/api/2.1/unity-catalog``)와 알림 API 도 메뉴 가드가 걸린다.
- 데이터셋 시맨틱 · 하이브리드 검색은 데이터셋 메뉴 권한이 있어야 한다(DEF-123).
- MCP 감사 이벤트는 배포 소유자 · 관리자만, 전체 목록은 관리자만(DEF-124).
- AI 에이전트 호출 기록 · 훅 이벤트는 등록자 · 소유자 · 관리자만 보낼 수 있다(DEF-125).
- 캐시 통계 · 설정 조회는 관리자만(DEF-127).
- 관리 › 데이터 품질 메뉴는 quality.manage(명시 허용형)로 열린다 — quality.run 과 따로(DEF-128).

실행:
    cd backend
    pytest tests/test_permission_gaps.py
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.catalog.models  # noqa: F401 — 모델 테이블의 FK 대상(카탈로그 테이블)을 메타데이터에 올린다
from app.core.auth import TokenUser
from app.core.database import Base
from app.models.models import RegisteredModel
from app.oci_hub.models import OciModel
from app.permissions import router as perm
from app.permissions.models import Permission
from app.settings.models import CatalogConfiguration

ADMIN = TokenUser(sub="a", username="admin", roles=["argus-admin"])
SUPER = TokenUser(sub="s", username="sam", roles=["argus-superuser"])
ALICE = TokenUser(sub="alice", username="alice", email="alice@x.io", roles=["argus-user"])
BOB = TokenUser(sub="bob", username="bob", email="bob@x.io", roles=["argus-user"])


@pytest.fixture
async def factory():
    from app.agents.models import AIAgent

    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[
            Permission.__table__, OciModel.__table__, RegisteredModel.__table__, AIAgent.__table__,
            CatalogConfiguration.__table__,
        ])
    make = async_sessionmaker(engine, expire_on_commit=False)
    perm._menu_cache.clear()
    yield make
    perm._menu_cache.clear()
    await engine.dispose()


async def _only_superuser(s, kind: str, key: str) -> None:
    s.add(Permission(kind=kind, perm_key=key, role_id="argus-superuser"))
    await s.commit()
    perm._menu_cache.clear()


# ---------------------------------------------------------------------------
# model-store (DEF-119)
# ---------------------------------------------------------------------------

async def test_model_store_reads_follow_model_menu(factory):
    from app.models.store_router import _require_model_menu

    async with factory() as s:
        s.add(OciModel(name="oci-a"))
        s.add(RegisteredModel(name="argus.ml.iris", urn="argus.ml.iris.model", status="active"))
        await s.commit()
        # 미설정 — 로그인 사용자 모두
        await _require_model_menu(ALICE, s, "oci-a")
        await _require_model_menu(ALICE, s, "argus.ml.iris")
        # 등록되지 않은 이름은 관리자가 아니면 404
        with pytest.raises(HTTPException) as e:
            await _require_model_menu(ALICE, s, "nope")
        assert e.value.status_code == 404
        await _require_model_menu(ADMIN, s, "nope")

        await _only_superuser(s, "MENU", "oci-hub")
        with pytest.raises(HTTPException) as e:
            await _require_model_menu(ALICE, s, "oci-a")
        assert (e.value.status_code, e.value.detail) == (403, "메뉴 접근 권한이 없습니다: oci-hub")
        await _require_model_menu(ALICE, s, "argus.ml.iris")  # MLflow 메뉴는 그대로
        await _require_model_menu(SUPER, s, "oci-a")

        await _only_superuser(s, "MENU", "models")
        with pytest.raises(HTTPException) as e:
            await _require_model_menu(ALICE, s, "argus.ml.iris")
        assert e.value.status_code == 403
        await _require_model_menu(ADMIN, s, "argus.ml.iris")


def test_model_store_read_endpoints_call_menu_guard():
    from app.models import store_router

    for fn in (store_router.get_download_url, store_router.get_download_urls,
               store_router.list_version_files, store_router.get_manifest):
        assert "_require_model_menu(" in inspect.getsource(fn), fn.__name__


def test_menu_guards_in_main():
    import app.main as main

    src = inspect.getsource(main)
    assert 'include_router(uc_compat_router, dependencies=_menu_guard("models"))' in src
    assert 'include_router(alert_router, prefix="/api/v1", dependencies=_menu_guard("alerts"))' in src
    assert "/api/2.1/unity-catalog" in perm.SERVER_ENFORCED_MENUS["models"]
    assert perm.SERVER_ENFORCED_MENUS["alerts"] == ("/api/v1/alerts",)


# ---------------------------------------------------------------------------
# 데이터셋 검색 (DEF-123)
# ---------------------------------------------------------------------------

async def test_semantic_and_hybrid_search_need_dataset_menu(factory):
    from app.search import router as search_router

    async with factory() as s:
        await search_router._require_dataset_menu(s, ALICE)
        await _only_superuser(s, "MENU", "datasets")
        with pytest.raises(HTTPException) as e:
            await search_router._require_dataset_menu(s, ALICE)
        assert (e.value.status_code, e.value.detail) == (403, "메뉴 접근 권한이 없습니다: datasets")
        await search_router._require_dataset_menu(s, SUPER)
        await search_router._require_dataset_menu(s, ADMIN)
    for fn in (search_router.semantic_search, search_router.hybrid_search):
        assert "_require_dataset_menu(" in inspect.getsource(fn), fn.__name__


# ---------------------------------------------------------------------------
# MCP 감사 이벤트 (DEF-124)
# ---------------------------------------------------------------------------

async def test_mcp_audit_events_owner_or_admin(monkeypatch):
    from app.mcp_servers import router as mcp_router

    with pytest.raises(HTTPException) as e:
        await mcp_router.list_all_audit_events(current=ALICE, tool=None, user=None, decision=None,
                                               page=1, page_size=50, session=None)
    assert e.value.status_code == 403

    async def _created_by(_s, _name):
        return "alice"

    monkeypatch.setattr(mcp_router.service, "get_deployment_created_by", _created_by)
    with pytest.raises(HTTPException) as e:
        await mcp_router._guard_deployment(BOB, None, "d1")
    assert e.value.status_code == 403
    await mcp_router._guard_deployment(ALICE, None, "d1")
    assert "_guard_deployment(" in inspect.getsource(mcp_router.list_audit_events)


# ---------------------------------------------------------------------------
# AI 에이전트 텔레메트리 (DEF-125)
# ---------------------------------------------------------------------------

async def test_agent_telemetry_sender_must_edit_agent(factory):
    from app.agents.models import AIAgent
    from app.agents.router import _assert_telemetry_sender

    async with factory() as s:
        s.add(AIAgent(name="ag", urn="ag.agent", version="0.1.0", status="active",
                      owner_email="Alice@x.io", created_by="carol"))
        await s.commit()
        assert await _assert_telemetry_sender(s, "unknown", BOB) is False  # 미등록 — 발견 기록으로
        with pytest.raises(HTTPException) as e:
            await _assert_telemetry_sender(s, "ag", BOB)
        assert e.value.status_code == 403
        assert await _assert_telemetry_sender(s, "ag", ALICE) is True  # 소유자(이메일 대소문자 무시)
        assert await _assert_telemetry_sender(s, "ag", TokenUser(sub="c", username="carol", roles=["argus-user"]))
        assert await _assert_telemetry_sender(s, "ag", ADMIN)


# ---------------------------------------------------------------------------
# 캐시 통계 · 설정 (DEF-127)
# ---------------------------------------------------------------------------

def test_cache_stats_and_config_admin_only():
    """관리자 전용이던 캐시 통계 · 설정은 3단계에서 위임 기능 settings.cache(명시 허용형)로 — 미설정이면 여전히 관리자만."""
    from app.external import router as ext

    assert "settings.cache" in perm.DEFAULT_DENY_FEATURES
    for fn in (ext.cache_stats, ext.get_cache_config):
        ann = inspect.signature(fn).parameters["_admin"].annotation
        assert ann is ext.CacheManager, fn.__name__


# ---------------------------------------------------------------------------
# 관리 › 데이터 품질 메뉴 (DEF-128)
# ---------------------------------------------------------------------------

async def test_quality_menu_opens_by_quality_manage_not_quality_run(factory):
    assert perm.ADMIN_MENU_GRANTS["quality-runs"] == ("quality.manage",)
    assert "quality.manage" in perm.DEFAULT_DENY_FEATURES and "quality.manage" in perm.FEATURE_KEYS
    async with factory() as s:
        # 진단 실행을 superuser 에만 허용해도 메뉴는 열리지 않는다
        await _only_superuser(s, "FEATURE", "quality.run")
        me = await perm.get_my_permissions(SUPER, s)
        assert "quality-runs" in me["denied_menus"]
        # 품질 관리 권한을 받으면 열린다
        await _only_superuser(s, "FEATURE", "quality.manage")
        me = await perm.get_my_permissions(SUPER, s)
        assert "quality-runs" not in me["denied_menus"]
        assert "quality-runs" in (await perm.get_my_permissions(ALICE, s))["denied_menus"]


def test_quality_menu_api_uses_quality_manage():
    from app.quality import router as q

    src = inspect.getsource(q)
    assert 'require_feature_granted("quality.run")' not in src
    assert q.QUALITY_MANAGE == "quality.manage"


def test_quality_definition_writes_use_quality_manage():
    """공유 품질 지표(정의) 쓰기는 quality.manage(명시 허용형) — 일반 사용자가 남의 규칙까지 바꾸지 못하게(DEF-135).
    할당 · 해제는 데이터셋 소유자 가드가 있어 기본 허용형 quality.rules-manage 그대로."""
    from app.quality import router as q

    for fn in (q.create_definition, q.add_default_definitions, q.update_definition, q.delete_definition):
        assert inspect.signature(fn).parameters["_guard"].annotation is q.MenuUser, fn.__name__
    for fn in (q.assign_definition, q.unassign_definition):
        assert inspect.signature(fn).parameters["user"].annotation is q.RuleManager, fn.__name__


# ---------------------------------------------------------------------------
# 관리 › 감사 로그 메뉴 (TODO-152)
# ---------------------------------------------------------------------------

async def test_audit_menu_opens_by_audit_view_only(factory):
    assert "audit" in perm.ADMIN_MENUS and perm.ADMIN_MENU_GRANTS["audit"] == ("audit.view",)
    async with factory() as s:
        me = await perm.get_my_permissions(SUPER, s)
        assert "audit" in me["denied_menus"], "미설정이면 관리자만"
        await _only_superuser(s, "FEATURE", "audit.view")
        me = await perm.get_my_permissions(SUPER, s)
        assert "audit" not in me["denied_menus"] and "users" in me["denied_menus"], "audit.view 만으로 사용자 관리는 안 열린다"
        assert await perm.check_menu(s, SUPER, "audit")
        assert not await perm.check_menu(s, ALICE, "audit")


def test_audit_api_uses_audit_view():
    """감사 로그 API 는 메뉴와 같은 키(audit.view)로 강제한다 — 범주 필터에 권한(permissions)도 있다."""
    from app.audit import router as audit_router
    from app.audit import service as audit_service

    assert 'require_feature_granted("audit.view")' in inspect.getsource(audit_router.list_audit_logs)
    assert "permissions" in audit_service.CATEGORIES
