# SPDX-License-Identifier: Apache-2.0
"""권한 우회 보강 테스트 (자기완결형).

확인하는 규칙:
- ``/api/v1/*`` 는 사용자 토큰이 있어야 한다(로그인 게이트). 로그인 전 경로·자체 서비스 토큰 경로·
  다운로드 링크 토큰 경로만 예외다. 실제 앱에도 게이트가 걸려 있다.
- AI 메타데이터 생성·제안 적용/거절은 데이터셋 생성자 또는 관리자만, 일괄 생성은 관리자만.

실행:
    cd backend
    pytest tests/test_authz_gaps.py
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.core import auth_gate
from app.core.auth import TokenUser, get_current_user

ADMIN = TokenUser(sub="a", username="admin", roles=["argus-admin"])
ALICE = TokenUser(sub="alice", username="alice", roles=["argus-user"])
BOB = TokenUser(sub="bob", username="bob", roles=["argus-user"])


# ---------------------------------------------------------------------------
# 로그인 게이트
# ---------------------------------------------------------------------------

@pytest.fixture
def gate_app(monkeypatch):
    async def fake_user(request):
        if request.headers.get("Authorization") == "Bearer good":
            return ALICE
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")

    monkeypatch.setattr(auth_gate, "get_current_user", fake_user)
    app = FastAPI()
    app.add_middleware(auth_gate.ApiAuthGateMiddleware)

    @app.api_route("/{path:path}", methods=["GET", "POST", "OPTIONS"])
    async def any_path(path: str):
        return {"path": path}

    return app


async def _call(app, method, url, **kw):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, url, **kw)


@pytest.mark.parametrize("url", [
    "/api/v1/catalog/datasets", "/api/v1/catalog/datasets/1", "/api/v1/models", "/api/v1/ai-agents",
    "/api/v1/standards/words", "/api/v1/mcp-deployments/d1", "/api/v1/filesystem/download?path=/x",
])
async def test_gate_blocks_anonymous(gate_app, url):
    r = await _call(gate_app, "GET", url)
    assert r.status_code == 401 and r.json()["detail"] == "인증이 필요합니다."
    # 사용자 토큰이 아니면(가짜·서비스 토큰) 막힌다
    assert (await _call(gate_app, "GET", url, headers={"Authorization": "Bearer mcpi_x"})).status_code == 401
    assert (await _call(gate_app, "GET", url, headers={"Authorization": "Bearer good"})).status_code == 200


@pytest.mark.parametrize("method,url", [
    ("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/refresh"), ("POST", "/api/v1/auth/logout"),
    ("GET", "/api/v1/auth/type"),
    ("GET", "/api/v1/federation/export/datasets"), ("GET", "/api/v1/federation/export/capabilities"),
    ("GET", "/api/v1/mcp-deployments/d1/policy-bundle"), ("POST", "/api/v1/mcp-deployments/d1/audit-events"),
    ("GET", "/api/v1/filesystem/download?path=/x&token=t"), ("GET", "/api/v1/model-store/browse/file?path=/x&token=t"),
    ("OPTIONS", "/api/v1/catalog/datasets"), ("GET", "/health"),
])
async def test_gate_exempt_paths(gate_app, method, url):
    assert (await _call(gate_app, method, url)).status_code == 200


async def test_gate_exempt_is_exact():
    """예외는 정확한 경로만 — 비슷한 이름이나 하위 경로로 새지 않는다."""
    from starlette.requests import Request

    def req(path, qs=b"", method="GET"):
        return Request({"type": "http", "method": method, "path": path, "query_string": qs, "headers": []})

    assert not auth_gate.is_exempt(req("/api/v1/auth/me"))
    assert not auth_gate.is_exempt(req("/api/v1/auth/login/x"))
    assert not auth_gate.is_exempt(req("/api/v1/federation/exporter"))
    assert not auth_gate.is_exempt(req("/api/v1/mcp-deployments/d1/policy-bundle/x"))
    assert not auth_gate.is_exempt(req("/api/v1/filesystem/download"))           # 토큰 없음
    assert not auth_gate.is_exempt(req("/api/v1/filesystem/list", b"token=t"))   # 다운로드 경로만
    assert auth_gate.is_exempt(req("/api/v1/filesystem/download", b"path=/x&token=t"))


async def test_main_app_requires_login():
    """실제 앱 — 예전에 익명으로 열려 있던 조회 API 가 401."""
    from app.main import app

    for url in ("/api/v1/catalog/datasets", "/api/v1/models", "/api/v1/ai-agents", "/api/v1/apis",
                "/api/v1/mcp-servers", "/api/v1/oci-models", "/api/v1/changes", "/api/v1/ai/stats",
                "/api/v1/catalog/glossary", "/api/v1/standards/terms"):
        r = await _call(app, "GET", url)
        assert r.status_code == 401, url


# ---------------------------------------------------------------------------
# AI 생성·제안 — 데이터셋 편집 권한
# ---------------------------------------------------------------------------

@pytest.fixture
async def ai_app(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.ai import router as ai_router
    from app.ai.models import AIGenerationLog
    from app.catalog import service as catalog_service
    from app.core.database import Base, get_session
    from app.permissions.models import Permission

    owners = {1: "alice", 2: "bob"}

    async def created_by(session, dataset_id):
        return owners.get(dataset_id)

    async def exists(session, dataset_id):
        return dataset_id in owners

    monkeypatch.setattr(catalog_service, "get_dataset_created_by", created_by)
    monkeypatch.setattr(catalog_service, "dataset_exists", exists)

    # 권한 검사를 통과하면 서비스가 부르는 지점 — 400 으로 끝내 통과 여부만 본다
    async def passed(*a, **k):
        raise ValueError("passed")

    for name in ("generate_dataset_description", "generate_dataset_summary", "generate_column_descriptions",
                 "suggest_tags", "detect_pii", "generate_all_for_dataset", "bulk_generate"):
        assert hasattr(ai_router, name), name
        monkeypatch.setattr(ai_router, name, passed)

    async def applied(session, suggestion_id):
        return {"id": suggestion_id, "applied": True}

    async def rejected(session, suggestion_id):
        return {"id": suggestion_id, "rejected": True}
    monkeypatch.setattr(ai_router, "apply_suggestion", applied)
    monkeypatch.setattr(ai_router, "reject_suggestion", rejected)

    async def apply_many(session, ids):
        return {"applied": len(ids), "failed": 0, "results": []}
    monkeypatch.setattr(ai_router, "apply_suggestions", apply_many)

    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[AIGenerationLog.__table__, Permission.__table__])
    make = async_sessionmaker(engine, expire_on_commit=False)
    async with make() as s:
        s.add_all([
            AIGenerationLog(id=10, entity_type="dataset", entity_id=1, dataset_id=1, generation_type="description",
                            generated_text="a", provider="p", model="m"),
            AIGenerationLog(id=20, entity_type="dataset", entity_id=2, dataset_id=2, generation_type="description",
                            generated_text="b", provider="p", model="m"),
        ])
        await s.commit()

    who = {"user": ALICE}
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: who["user"]

    async def _session():
        async with make() as s:
            yield s
    app.dependency_overrides[get_session] = _session
    yield app, who
    await engine.dispose()


@pytest.mark.parametrize("kind", ["describe", "summarize", "describe-columns", "suggest-tags", "detect-pii",
                                  "generate-all"])
async def test_ai_generate_requires_dataset_editor(ai_app, kind):
    app, who = ai_app
    who["user"] = ALICE
    assert (await _call(app, "POST", f"/api/v1/ai/datasets/1/{kind}", json={})).status_code == 400   # 본인 것
    r = await _call(app, "POST", f"/api/v1/ai/datasets/2/{kind}", json={})                          # 남의 것
    assert r.status_code == 403 and "소유자 또는 관리자" in r.json()["detail"]
    assert (await _call(app, "POST", f"/api/v1/ai/datasets/99/{kind}", json={})).status_code == 404
    who["user"] = ADMIN
    assert (await _call(app, "POST", f"/api/v1/ai/datasets/2/{kind}", json={})).status_code == 400


async def test_ai_bulk_generate_admin_only(ai_app):
    app, who = ai_app
    body = {"generation_types": ["description"]}
    who["user"] = ALICE
    assert (await _call(app, "POST", "/api/v1/ai/bulk-generate", json=body)).status_code == 403
    who["user"] = ADMIN
    assert (await _call(app, "POST", "/api/v1/ai/bulk-generate", json=body)).status_code == 400


async def test_ai_suggestions_require_dataset_editor(ai_app):
    app, who = ai_app
    who["user"] = ALICE
    assert (await _call(app, "POST", "/api/v1/ai/suggestions/10/apply")).status_code == 200
    assert (await _call(app, "POST", "/api/v1/ai/suggestions/20/apply")).status_code == 403
    assert (await _call(app, "POST", "/api/v1/ai/suggestions/20/reject")).status_code == 403
    assert (await _call(app, "POST", "/api/v1/ai/suggestions/10/reject")).status_code == 200
    # 일괄 — 남의 제안이 하나라도 섞이면 전체 거부
    r = await _call(app, "POST", "/api/v1/ai/suggestions/apply-batch", json={"suggestion_ids": [10, 20]})
    assert r.status_code == 403
    r = await _call(app, "POST", "/api/v1/ai/suggestions/apply-batch", json={"suggestion_ids": [10]})
    assert r.status_code == 200
    who["user"] = BOB
    assert (await _call(app, "POST", "/api/v1/ai/suggestions/20/apply")).status_code == 200
    who["user"] = ADMIN
    r = await _call(app, "POST", "/api/v1/ai/suggestions/apply-batch", json={"suggestion_ids": [10, 20]})
    assert r.status_code == 200
