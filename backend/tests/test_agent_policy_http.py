# SPDX-License-Identifier: Apache-2.0
"""실행 환경 연동 HTTP 규칙 — 정책 받기 ETag/304, 정책 판정 기록 값 검증.

실행:
    cd backend
    pytest tests/test_agent_policy_http.py
"""

from __future__ import annotations

import pytest

from tests.test_agent_manifest import Api, _make_factory, agent_doc


@pytest.fixture
async def api():
    a = Api(await _make_factory())
    assert (await a.apply(agent_doc("cs.refund"))).json()["summary"]["created"] == 1
    yield a
    await a.client.aclose()


async def test_policy_etag_and_not_modified(api):
    r = await api.get("/cs.refund/policy")
    assert r.status_code == 200
    etag = r.headers["etag"]
    assert etag.startswith('"') and r.headers["cache-control"] == "no-cache"
    assert r.json()["name"] == "cs.refund" and r.json()["policy_version"]

    for inm in (etag, f"W/{etag}", f'"other", {etag}', "*"):
        r2 = await api.client.get("/api/v1/ai-agents/cs.refund/policy", headers={"If-None-Match": inm})
        assert r2.status_code == 304, inm
        assert r2.content == b"" and r2.headers["etag"] == etag

    r3 = await api.client.get("/api/v1/ai-agents/cs.refund/policy", headers={"If-None-Match": '"stale"'})
    assert r3.status_code == 200 and r3.headers["etag"] == etag

    # 정책이 바뀌면(도구 목록 변경) ETag 도 바뀌고 이전 값으로는 304 가 아니다
    assert (await api.apply(agent_doc("cs.refund", tools=[{"name": "order.lookup", "risk": "low"}]))).json()[
        "summary"]["updated"] == 1
    r4 = await api.client.get("/api/v1/ai-agents/cs.refund/policy", headers={"If-None-Match": etag})
    assert r4.status_code == 200 and r4.headers["etag"] != etag
    assert [t["name"] for t in r4.json()["allowed_tools"]] == ["order.lookup"]


async def test_policy_unknown_agent_404(api):
    assert (await api.get("/no-such/policy")).status_code == 404


@pytest.mark.parametrize("stage", ["access", "pre_exec", "post_exec"])
async def test_hook_event_valid_stages(api, stage):
    r = await api.client.post("/api/v1/ai-agents/cs.refund/hook-events", json={"stage": stage, "decision": "allow"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("decision", ["allow", "deny", "mask", "require_approval", "approved", "modified"])
async def test_hook_event_valid_decisions(api, decision):
    r = await api.client.post("/api/v1/ai-agents/cs.refund/hook-events",
                              json={"stage": "pre_exec", "decision": decision})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("body", [
    {"stage": "bogus", "decision": "allow"},
    {"stage": "pre_exec", "decision": "whatever"},
    {"stage": "PRE_EXEC", "decision": "allow"},
    {"stage": "pre_exec"},
])
async def test_hook_event_invalid_values_rejected(api, body):
    r = await api.client.post("/api/v1/ai-agents/cs.refund/hook-events", json=body)
    assert r.status_code == 422, r.text
    events = (await api.get("/cs.refund/hook-events")).json()
    assert events == []


async def test_policy_tool_schema_key_is_schema(api):
    """정책 번들의 도구 입력 형식 키는 'schema'(내부 이름 'schema_' 가 새어 나가지 않음) — 통합 시험 AGT-T04 에서 발견."""
    r = await api.client.post("/api/v1/ai-agents/cs.refund/tools",
                              json={"name": "search", "risk": "high", "tool_schema": {"type": "object"}})
    assert r.status_code == 200, r.text
    tool = next(t for t in (await api.get("/cs.refund/policy")).json()["allowed_tools"] if t["name"] == "search")
    assert tool["schema"] == {"type": "object"} and "schema_" not in tool


async def test_eval_records_creator(api):
    """평가 기록에 등록한 사람이 남는다 — 통합 시험 AGT-T07 에서 발견."""
    r = await api.client.post("/api/v1/ai-agents/cs.refund/evals",
                              json={"eval_type": "accuracy", "metric_key": "acc", "metric_value": 0.9, "passed": True})
    assert r.status_code == 200 and r.json()["created_by"] == "admin"
    assert [e["created_by"] for e in (await api.get("/cs.refund/evals")).json()] == ["admin"]


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/ai-agents/cs.refund/policy"),
    ("POST", "/api/v1/ai-agents/cs.refund/invocations"),
    ("POST", "/api/v1/ai-agents/cs.refund/hook-events"),
    ("GET", "/api/v1/ai-agents/cs.refund/hook-events"),
    ("GET", "/api/v1/ai-agents/cs.refund/card"),
])
async def test_runtime_endpoints_require_login(method, path):
    """실행 환경 연동 경로는 로그인(사용자·서비스 계정 토큰)이 필요하다 — 정책 번들(허용 도구·MCP·거버넌스)도
    비로그인 401. MCP 배포 번들(`/mcp-deployments/*/policy-bundle`)과 달리 로그인 게이트 예외가 아니다
    (통합 시험 AGT 알려진 이슈 #2)."""
    import httpx

    from app.core import auth_gate
    from app.main import app

    assert not any(p.match(path) for p in auth_gate.SERVICE_TOKEN_PATTERNS)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.request(method, path, json={} if method == "POST" else None)
        assert r.status_code == 401, (path, r.text)
        r = await c.request(method, path, headers={"If-None-Match": "*"})
        assert r.status_code == 401, path
