# SPDX-License-Identifier: Apache-2.0
"""권한 보완 2단계 — 세분화한 기능 권한 키가 실제 API 에 걸렸는지 (TODO-122, 자기완결형, in-memory SQLite).

확인하는 규칙:
- 데이터셋 쓰기(정보 · 스키마 · 샘플 · 삭제)는 기능 권한을 따로 본다. 수집 권한(datasources.sync) 계정은
  정보 · 스키마 쓰기 권한을 막아도 통과한다(수집 어댑터가 같은 API 를 쓴다).
- 리니지 편집 · 연합 관리 · 연합 가져오기 · HF 가져오기 · 알림 정리는 명시 허용형 — 미설정이면 관리자만,
  역할에 허용하면 위임된다.
- 모델 · MCP 쓰기는 소유자 확인과 함께 기능 권한도 본다.
- MCP 통제 현황의 최근 호출(감사 이벤트)은 관리자는 전부, 그 밖에는 mcp.audit-view 가 있을 때 자기 배포만(DEF-131).

실행:
    cd backend
    pytest tests/test_permission_stage2.py
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.catalog.models  # noqa: F401 — 모델 테이블의 FK 대상(카탈로그 테이블)을 메타데이터에 올린다
from app.core.auth import TokenUser
from app.core.database import Base
from app.models.models import RegisteredModel
from app.permissions import router as perm
from app.permissions.models import Permission

ADMIN = TokenUser(sub="a", username="admin", roles=["argus-admin"])
SUPER = TokenUser(sub="s", username="sam", roles=["argus-superuser"])
ALICE = TokenUser(sub="alice", username="alice", email="alice@x.io", roles=["argus-user"])
BOB = TokenUser(sub="bob", username="bob", email="bob@x.io", roles=["argus-user"])


@pytest.fixture
async def factory():
    from app.mcp_servers.models import McpDeployment, McpServer

    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[
            Permission.__table__, RegisteredModel.__table__, McpServer.__table__, McpDeployment.__table__,
        ])
    make = async_sessionmaker(engine, expire_on_commit=False)
    perm._menu_cache.clear()
    yield make
    perm._menu_cache.clear()
    await engine.dispose()


async def _grant(s, key: str, role: str) -> None:
    s.add(Permission(kind="FEATURE", perm_key=key, role_id=role))
    await s.commit()


# ---------------------------------------------------------------------------
# 라우트에 걸린 기능 키 모으기
# ---------------------------------------------------------------------------

def _closure_strings(fn) -> set[str]:
    out: set[str] = set()
    for cell in getattr(fn, "__closure__", None) or ():
        try:
            v = cell.cell_contents
        except ValueError:
            continue
        if isinstance(v, str):
            out.add(v)
    return out


def _route_feature_keys(method: str, path: str) -> set[str]:
    from app.main import app

    def walk(dep) -> set[str]:
        keys = _closure_strings(dep.call) if dep.call else set()
        for sub in dep.dependencies:
            keys |= walk(sub)
        return keys

    for r in app.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", ()):
            return walk(r.dependant)
    raise AssertionError(f"route not found: {method} {path}")


@pytest.mark.parametrize(("method", "path", "key"), [
    ("PUT", "/api/v1/catalog/datasets/{dataset_id}", "datasets.edit"),
    ("PUT", "/api/v1/catalog/datasets/{dataset_id}/schema", "datasets.schema-edit"),
    ("DELETE", "/api/v1/catalog/datasets/{dataset_id}", "datasets.delete"),
    ("POST", "/api/v1/catalog/datasets/{dataset_id}/sample", "datasets.sample-manage"),
    ("POST", "/api/v1/catalog/lineage", "lineage.edit"),
    ("POST", "/api/v1/comments", "comments.write"),
    ("POST", "/api/v1/federation/instances", "federation.manage"),
    ("DELETE", "/api/v1/alerts/closed", "alerts.purge"),
])
def test_feature_keys_are_on_routes(method, path, key):
    keys = _route_feature_keys(method, path)
    assert key in keys, (method, path, keys)


# ---------------------------------------------------------------------------
# 데이터셋 쓰기 — 기본 허용형 + 수집 계정 예외
# ---------------------------------------------------------------------------

async def test_dataset_feature_allows_sync_account(factory):
    dep = perm.require_dataset_feature("datasets.edit")
    async with factory() as s:
        assert await dep(ALICE, s) is ALICE  # 미설정 — 모두 허용
        await _grant(s, "datasets.edit", "argus-superuser")
        with pytest.raises(HTTPException) as e:
            await dep(ALICE, s)
        assert (e.value.status_code, e.value.detail) == (403, "기능 권한이 없습니다: datasets.edit")
        assert await dep(SUPER, s) is SUPER
        assert await dep(ADMIN, s) is ADMIN
        # 수집 권한을 받은 역할은 정보 쓰기 권한이 막혀도 통과
        await _grant(s, "datasources.sync", "argus-user")
        assert await dep(ALICE, s) is ALICE


# ---------------------------------------------------------------------------
# 명시 허용형 위임
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["lineage.edit", "federation.manage", "federation.import",
                                 "oci.import-hf", "alerts.purge"])
async def test_delegable_features_default_admin_only(factory, key):
    assert key in perm.DEFAULT_DENY_FEATURES and key in perm.FEATURE_KEYS
    dep = perm.require_feature_granted(key)
    async with factory() as s:
        with pytest.raises(HTTPException) as e:
            await dep(SUPER, s)
        assert e.value.status_code == 403
        assert await dep(ADMIN, s) is ADMIN
        await _grant(s, key, "argus-superuser")
        assert await dep(SUPER, s) is SUPER
        with pytest.raises(HTTPException):
            await dep(ALICE, s)


# ---------------------------------------------------------------------------
# 모델 · MCP — 소유자 + 기능 권한
# ---------------------------------------------------------------------------

async def test_model_editor_checks_feature(factory):
    from app.models.router import assert_model_editor

    async with factory() as s:
        s.add(RegisteredModel(name="m1", urn="m1.model", status="active", created_by="alice"))
        await s.commit()
        await assert_model_editor(ALICE, s, "m1")
        with pytest.raises(HTTPException) as e:
            await assert_model_editor(BOB, s, "m1")
        assert e.value.status_code == 403
        await _grant(s, "models.delete", "argus-superuser")
        await assert_model_editor(ALICE, s, "m1")  # 수정은 그대로
        with pytest.raises(HTTPException) as e:
            await assert_model_editor(ALICE, s, "m1", feature="models.delete")
        assert e.value.detail == "기능 권한이 없습니다: models.delete"
        await assert_model_editor(ADMIN, s, "m1", feature="models.delete")


async def test_mcp_guard_checks_feature(factory, monkeypatch):
    from app.mcp_servers import router as mcp_router

    async def _created_by(_s, _name):
        return "alice"

    monkeypatch.setattr(mcp_router.service, "get_deployment_created_by", _created_by)
    async with factory() as s:
        await mcp_router._guard_deployment(ALICE, s, "d1", feature="mcp.delete")
        await _grant(s, "mcp.delete", "argus-superuser")
        with pytest.raises(HTTPException) as e:
            await mcp_router._guard_deployment(ALICE, s, "d1", feature="mcp.delete")
        assert e.value.detail == "기능 권한이 없습니다: mcp.delete"
        await mcp_router._guard_deployment(ALICE, s, "d1", feature="mcp.edit")


# ---------------------------------------------------------------------------
# MCP 통제 현황 최근 호출 (DEF-131)
# ---------------------------------------------------------------------------

async def test_control_overview_filters_recent_calls(factory, monkeypatch):
    from app.mcp_servers import router as mcp_router
    from app.mcp_servers.models import McpDeployment, McpServer
    from app.mcp_servers.schemas import AuditEventResponse, ControlOverview

    now = datetime.now(timezone.utc)

    def _ev(i: int, dep: str):
        return AuditEventResponse.model_construct(id=i, deployment_name=dep, created_at=now)

    overview = ControlOverview.model_construct(
        environments=[], matrix=[], risky=[], recent_calls=[_ev(1, "mine"), _ev(2, "other")], coverage=None,
    )

    async def _overview(_s):
        return overview

    monkeypatch.setattr(mcp_router.service, "get_control_overview", _overview)
    async with factory() as s:
        srv = McpServer(name="srv", urn="srv.mcp", created_by="alice")
        s.add(srv)
        await s.flush()
        s.add(McpDeployment(name="mine", urn="mine.dep", mcp_server_id=srv.id, created_by="alice"))
        await s.commit()

        assert len((await mcp_router.control_overview(ADMIN, s)).recent_calls) == 2
        mine = await mcp_router.control_overview(ALICE, s)
        assert [c.deployment_name for c in mine.recent_calls] == ["mine"]
        assert (await mcp_router.control_overview(BOB, s)).recent_calls == []
        await _grant(s, "mcp.audit-view", "argus-superuser")
        assert (await mcp_router.control_overview(ALICE, s)).recent_calls == []
