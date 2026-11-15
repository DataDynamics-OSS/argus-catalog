# SPDX-License-Identifier: Apache-2.0
"""통합 검색이 메뉴 권한으로 결과 종류를 거르는지 (DEF-064).

검색 서비스는 가짜로 바꾸고, 메뉴 매트릭스(Permission 행)만 실제 sqlite 에 넣어 확인한다.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.auth import TokenUser, get_optional_user
from app.core.database import Base, get_session
from app.permissions import router as perm
from app.permissions.models import Permission
from app.search import router as search_router
from app.search import service as search_service


@pytest.fixture
async def make():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[Permission.__table__])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    perm._menu_cache.clear()
    yield factory
    perm._menu_cache.clear()
    await engine.dispose()


@pytest.fixture
def calls(monkeypatch):
    """검색 서비스 가짜 — 요청된 엔티티 종류를 기록하고 종류마다 결과 1건을 돌려준다."""
    seen: dict = {"entity_types": None, "dataset_searched": False}

    async def fake_hybrid(session, q, limit=20, threshold=0.3, **_):
        seen["dataset_searched"] = True
        return [(1, 0.9, "keyword")]

    async def fake_entities(session, q, entity_types, limit=5, threshold=0.3, **_):
        seen["entity_types"] = list(entity_types)
        return {t: [(10, 0.8, "keyword")] for t in entity_types}

    async def fake_entity_result(session, entity_type, eid):
        return {"id": eid, "name": f"{entity_type}-{eid}", "extra": {}}

    async def fake_summary(session, ds_id):
        return None  # 요약 스키마 구성은 이 테스트 범위 밖 — 검색 호출 여부로 확인

    async def no_provider():
        return None

    monkeypatch.setattr(search_service, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(search_service, "hybrid_search_entities", fake_entities)
    monkeypatch.setattr(search_service, "build_entity_result", fake_entity_result)
    monkeypatch.setattr(search_service, "_build_dataset_summary", fake_summary)
    monkeypatch.setattr(search_router, "get_provider", no_provider)
    return seen


def _app(make, user: TokenUser | None) -> FastAPI:
    app = FastAPI()
    app.include_router(search_router.router, prefix="/api/v1")

    async def _session():
        async with make() as db:
            yield db

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_optional_user] = lambda: user
    return app


async def _search(app: FastAPI) -> dict:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/catalog/search/unified", params={"q": "orders"})
    assert r.status_code == 200, r.text
    return r.json()


async def _restrict(make, menu_key: str, role: str = "argus-superuser") -> None:
    """메뉴를 특정 역할만 허용(다른 역할은 차단)."""
    async with make() as s:
        s.add(Permission(role_id=role, kind="MENU", perm_key=menu_key))
        await s.commit()


async def test_unrestricted_menus_return_all_sections(make, calls):
    user = TokenUser(sub="u", username="u", roles=["argus-user"])
    body = await _search(_app(make, user))
    assert calls["dataset_searched"] is True
    assert set(calls["entity_types"]) == {"glossary_term", "ai_agent", "api", "mcp_server"}
    assert len(body["ai_agents"]) == 1
    assert len(body["glossary_terms"]) == 1


async def test_blocked_menus_are_filtered(make, calls):
    await _restrict(make, "ai-agents")
    await _restrict(make, "datasets")
    user = TokenUser(sub="u", username="u", roles=["argus-user"])
    body = await _search(_app(make, user))
    # 닫힌 메뉴의 종류는 검색하지도 돌려주지도 않는다
    assert calls["dataset_searched"] is False
    assert "ai_agent" not in calls["entity_types"]
    assert body["ai_agents"] == []
    assert body["datasets"] == []
    assert len(body["apis"]) == 1
    assert len(body["mcp_servers"]) == 1
    assert body["total"] == 3  # 용어집·API·MCP 각 1건


async def test_allowed_role_and_admin_see_restricted_sections(make, calls):
    await _restrict(make, "ai-agents")
    allowed = TokenUser(sub="s", username="s", roles=["argus-superuser"])
    body = await _search(_app(make, allowed))
    assert len(body["ai_agents"]) == 1

    admin = TokenUser(sub="a", username="a", roles=["argus-admin"])
    body = await _search(_app(make, admin))
    assert len(body["ai_agents"]) == 1
