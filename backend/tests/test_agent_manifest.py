# SPDX-License-Identifier: Apache-2.0
"""AI 에이전트 정의 파일 일괄 반영(``POST /ai-agents/apply``·``/prune``) 단위 테스트.

in-memory SQLite 에 에이전트·도구 서버·MLflow 모델 테이블을 만들고 라우터를 HTTP 로 호출한다.
설계: design/ai-agent-bulk-registration-design.md

실행:
    cd backend
    pytest tests/test_agent_manifest.py
"""

from __future__ import annotations

import httpx
import pytest
import yaml
from fastapi import FastAPI, HTTPException
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import app.agents.models  # noqa: F401  (에이전트 테이블 등록)
import app.catalog.models  # noqa: F401  (FK 대상)
import app.embedding.models  # noqa: F401
import app.mcp_servers.models as mcpm
import app.models.models as mm
import app.usermgr.models as um
from app.agents.router import router as agents_router
from app.core.auth import TokenUser, get_current_user, get_optional_user
from app.core.config import settings
from app.core.database import Base, get_session


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


ADMIN = TokenUser(sub="1", username="admin", email="admin@corp.example", roles=[settings.auth_keycloak_admin_role])
ALICE = TokenUser(sub="2", username="alice", email="alice@corp.example", roles=[settings.auth_keycloak_user_role])
BOB = TokenUser(sub="3", username="bob", email="bob@corp.example", roles=[settings.auth_keycloak_user_role])


def agent_doc(name: str = "cs.refund", **spec) -> dict:
    base_spec = {
        "description": "환불 문의 처리",
        "version": "1.0.0",
        "status": "active",
        "model": {"name": "gpt-4o", "provider": "openai"},
        "runtime": {"framework": "langgraph", "maxSteps": 12},
        "interface": {"endpoint": "https://agents.corp.example/refund", "streaming": True},
        "governance": {"piiHandling": "masked", "hitlRequired": True, "budgetLimit": 300},
        "capabilities": ["환불 접수", "상담원 연결"],
        "tools": [
            {"name": "refund.create", "risk": "high", "requiresApproval": True},
            {"name": "order.lookup", "risk": "low"},
        ],
        "lineage": [{"targetType": "dataset", "targetRef": "pay.refunds.dataset", "relation": "reads"}],
        "release": {"changelog": "첫 배포", "systemPrompt": "너는 환불 상담원이다."},
    }
    base_spec.update(spec)
    return {
        "apiVersion": "argus.catalog/v1",
        "kind": "Agent",
        "metadata": {"name": name, "displayName": "환불 도우미", "owner": "alice@corp.example",
                     "department": "고객지원팀", "category": "customer-service", "tags": ["환불"]},
        "spec": base_spec,
    }


# 화면 등록(POST /ai-agents)의 필수 항목
REQUIRED = {"owner_email": "alice@corp.example", "department": "고객지원팀", "category": "customer-service"}


async def legacy_agent(api: "Api", name: str, created_by: str, **fields) -> None:
    """필수 항목 강제 이전에 만들어진(소유자·부서·분류가 빈) 에이전트를 직접 넣는다."""
    from app.agents import service
    from app.agents.schemas import AIAgentCreate

    async with api.factory() as db:
        await service.create_agent(db, AIAgentCreate(name=name, **fields), created_by=created_by)


def to_yaml(*docs: dict) -> str:
    return yaml.safe_dump_all(docs, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------


async def _make_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    seeds = [t for name, t in Base.metadata.tables.items()
             if name.startswith(("catalog_ai_agent", "catalog_mcp_"))]
    import app.permissions.models  # noqa: F401  — 기능 권한(require_feature) 조회용 테이블 등록
    seeds += [mm.RegisteredModel.__table__, Base.metadata.tables["catalog_entity_embeddings"], um.ArgusUser.__table__,
              Base.metadata.tables["argus_permissions"]]
    tables, pending, seen = [], list(seeds), set()
    while pending:
        t = pending.pop()
        if t.name in seen:
            continue
        seen.add(t.name)
        tables.append(t)
        pending.extend(fk.column.table for fk in t.foreign_keys)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


class Api:
    def __init__(self, factory):
        self.factory = factory
        self.user: TokenUser | None = ADMIN
        app = FastAPI()
        app.include_router(agents_router, prefix="/api/v1")

        async def _session():
            async with factory() as db:
                yield db

        async def _user():
            if self.user is None:
                raise HTTPException(status_code=401, detail="인증이 필요합니다.")
            return self.user

        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_optional_user] = lambda: self.user
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    def as_(self, user: TokenUser | None) -> "Api":
        self.user = user
        return self

    async def apply(self, *docs: dict, dry_run: bool = False, source_ref: str | None = None) -> httpx.Response:
        params = {"dry_run": str(dry_run).lower()}
        if source_ref:
            params["source_ref"] = source_ref
        return await self.client.post("/api/v1/ai-agents/apply", params=params, content=to_yaml(*docs).encode(),
                                      headers={"Content-Type": "application/yaml"})

    async def get(self, path: str) -> httpx.Response:
        return await self.client.get("/api/v1/ai-agents" + path)


@pytest.fixture
async def api():
    a = Api(await _make_factory())
    yield a
    await a.client.aclose()


def by_name(resp: httpx.Response) -> dict[str, dict]:
    assert resp.status_code == 200, resp.text
    return {r["name"]: r for r in resp.json()["results"]}


# ---------------------------------------------------------------------------
# 생성 · 멱등 · 갱신
# ---------------------------------------------------------------------------


async def test_apply_creates_agents_with_children_and_first_version(api):
    r = await api.as_(ALICE).apply(agent_doc("cs.refund"), agent_doc("cs.faq", tools=[]), source_ref="git:cs/agents")
    assert r.json()["summary"] == {"created": 2, "updated": 0, "unchanged": 0, "failed": 0}

    d = (await api.get("/cs.refund")).json()
    assert d["owner_email"] == "alice@corp.example" and d["department"] == "고객지원팀"
    assert d["framework"] == "langgraph" and d["max_steps"] == 12 and d["streaming"] is True
    assert d["pii_handling"] == "masked" and d["hitl_required"] is True and float(d["budget_limit"]) == 300
    assert d["status"] == "active" and d["version"] == "1.0.0"
    assert d["managed_by"] == "manifest" and d["source_ref"] == "git:cs/agents" and d["last_applied_by"] == "alice"
    assert d["created_by"] == "alice"
    assert sorted((t["name"], t["risk"], t["requires_approval"]) for t in d["tools"]) == [
        ("order.lookup", "low", False), ("refund.create", "high", True)]
    assert [(x["target_ref"], x["relation"]) for x in d["lineage"]] == [("pay.refunds.dataset", "reads")]
    assert [(v["version"], v["changelog"], v["system_prompt"]) for v in d["versions"]] == [
        ("1.0.0", "첫 배포", "너는 환불 상담원이다.")]
    assert (await api.get("/cs.faq")).json()["tools"] == []


async def test_reapply_same_documents_is_unchanged(api):
    api.as_(ALICE)
    await api.apply(agent_doc("cs.refund"))
    r = await api.apply(agent_doc("cs.refund"))
    assert r.json()["summary"]["unchanged"] == 1
    assert by_name(r)["cs.refund"]["changes"] == []
    assert len((await api.get("/cs.refund/versions")).json()) == 1


async def test_update_reports_changes_bumps_version_and_syncs_children(api):
    api.as_(ALICE)
    await api.apply(agent_doc("cs.refund"))
    changed = agent_doc("cs.refund", description="부분 환불 지원", version="1.1.0",
                        tools=[{"name": "refund.create", "risk": "critical", "requiresApproval": True}],
                        release={"changelog": "부분 환불"})
    res = by_name(await api.apply(changed))["cs.refund"]
    assert res["action"] == "updated"
    assert "description: 환불 문의 처리 → 부분 환불 지원" in res["changes"]
    assert "version: 1.0.0 → 1.1.0" in res["changes"]
    assert "tools: -order.lookup" in res["changes"] and "tools: ~refund.create (risk)" in res["changes"]

    d = (await api.get("/cs.refund")).json()
    assert d["version"] == "1.1.0" and [t["name"] for t in d["tools"]] == ["refund.create"]
    assert [v["version"] for v in d["versions"]] == ["1.1.0", "1.0.0"]
    assert d["last_applied_by"] == "alice" and d["last_applied_at"]


async def test_omitted_children_key_keeps_rows_but_empty_list_clears(api):
    api.as_(ALICE)
    await api.apply(agent_doc("cs.refund"))
    doc = agent_doc("cs.refund")
    del doc["spec"]["tools"]
    assert by_name(await api.apply(doc))["cs.refund"]["action"] == "unchanged"
    assert len((await api.get("/cs.refund")).json()["tools"]) == 2
    res = by_name(await api.apply(agent_doc("cs.refund", tools=[], lineage=[])))["cs.refund"]
    assert res["action"] == "updated"
    d = (await api.get("/cs.refund")).json()
    assert d["tools"] == [] and d["lineage"] == []


async def test_omitted_status_and_version_keep_current_values(api):
    api.as_(ALICE)
    await api.apply(agent_doc("cs.refund"))
    doc = agent_doc("cs.refund")
    del doc["spec"]["status"], doc["spec"]["version"]
    assert by_name(await api.apply(doc))["cs.refund"]["action"] == "unchanged"
    d = (await api.get("/cs.refund")).json()
    assert d["status"] == "active" and d["version"] == "1.0.0"


async def test_ui_edit_is_reverted_by_next_apply(api):
    api.as_(ALICE)
    await api.apply(agent_doc("cs.refund"))
    r = await api.client.patch("/api/v1/ai-agents/cs.refund", json={"description": "화면에서 고침"})
    assert r.status_code == 200
    res = by_name(await api.apply(agent_doc("cs.refund")))["cs.refund"]
    assert res["changes"] == ["description: 화면에서 고침 → 환불 문의 처리"]


async def test_status_change_is_recorded_in_history(api):
    api.as_(ALICE)
    await api.apply(agent_doc("cs.refund"))
    await api.apply(agent_doc("cs.refund", status="deprecated"))
    hist = (await api.get("/cs.refund/status-history")).json()
    assert [(h["from_status"], h["to_status"], h["changed_by"]) for h in hist] == [("active", "deprecated", "alice")]


async def test_existing_ui_agent_becomes_manifest_managed(api):
    api.as_(ALICE)
    r = await api.client.post("/api/v1/ai-agents", json={**REQUIRED, "name": "cs.refund", "status": "active"})
    assert r.status_code == 200 and r.json()["managed_by"] == "ui"
    res = by_name(await api.apply(agent_doc("cs.refund")))["cs.refund"]
    assert res["action"] == "updated" and "managed_by: ui → manifest" in res["changes"]


# ---------------------------------------------------------------------------
# 미리 보기 · 검증 · 부분 실패
# ---------------------------------------------------------------------------


async def test_dry_run_reports_without_saving(api):
    api.as_(ALICE)
    r = await api.apply(agent_doc("cs.refund"), dry_run=True)
    assert r.json()["dry_run"] is True and r.json()["summary"]["created"] == 1
    assert (await api.get("/cs.refund")).status_code == 404

    await api.apply(agent_doc("cs.refund"))
    res = by_name(await api.apply(agent_doc("cs.refund", description="바뀜"), dry_run=True))["cs.refund"]
    assert res["action"] == "updated"
    assert (await api.get("/cs.refund")).json()["description"] == "환불 문의 처리"


async def test_invalid_documents_fail_individually(api):
    unknown = agent_doc("cs.unknown")
    unknown["spec"]["descrption"] = "오타"
    no_owner = agent_doc("cs.no-owner")
    del no_owner["metadata"]["owner"]
    bad_name = agent_doc("CS Refund")
    bad_risk = agent_doc("cs.bad-risk", tools=[{"name": "x", "risk": "extreme"}])
    bad_api = agent_doc("cs.bad-api")
    bad_api["apiVersion"] = "v0"
    dup_tool = agent_doc("cs.dup-tool", tools=[{"name": "x"}, {"name": "x"}])

    r = await api.as_(ALICE).apply(agent_doc("cs.ok"), unknown, no_owner, bad_name, bad_risk, bad_api, dup_tool,
                                   agent_doc("cs.ok"))
    results = r.json()["results"]
    assert r.json()["summary"] == {"created": 1, "updated": 0, "unchanged": 0, "failed": 7}
    errors = {x["name"]: x.get("error", "") for x in results if x["action"] == "failed"}
    assert "descrption" in errors["cs.unknown"]
    assert "owner" in errors["cs.no-owner"]
    assert "name" in errors["CS Refund"]
    assert "risk" in errors["cs.bad-risk"]
    assert "apiVersion" in errors["cs.bad-api"]
    assert "두 번 이상" in errors["cs.dup-tool"]
    assert results[-1]["action"] == "failed" and "두 번 이상" in results[-1]["error"]  # 같은 요청 안 중복 이름
    assert (await api.get("/cs.dup-tool")).status_code == 404  # 실패한 문서는 흔적 없음


async def test_request_level_errors(api):
    api.as_(ALICE)
    post = api.client.post
    hdr = {"Content-Type": "application/yaml"}
    assert (await post("/api/v1/ai-agents/apply", content=b"a: [1, 2", headers=hdr)).status_code == 400
    assert (await post("/api/v1/ai-agents/apply", content=b"", headers=hdr)).status_code == 400
    many = to_yaml(*[agent_doc(f"a.n{i}") for i in range(201)]).encode()
    r = await post("/api/v1/ai-agents/apply", content=many, headers=hdr)
    assert r.status_code == 400 and "200" in r.json()["detail"]
    assert (await post("/api/v1/ai-agents/apply", json={"items": []})).status_code == 400
    r = await post("/api/v1/ai-agents/apply", json={"agents": [agent_doc("cs.json")]})
    assert r.status_code == 200 and r.json()["summary"]["created"] == 1
    assert (await api.as_(None).apply(agent_doc("cs.anon"))).status_code == 401


async def test_registry_ref_resolves_mlflow_model(api):
    async with api.factory() as db:
        db.add(mm.RegisteredModel(name="argus.cs.intent", urn="argus.cs.intent.PROD.model"))
        await db.commit()
        mid = (await db.execute(select(mm.RegisteredModel.id))).scalar_one()
    ok = agent_doc("cs.refund", model={"name": "intent", "registryRef": "argus.cs.intent"})
    bad = agent_doc("cs.bad", model={"registryRef": "argus.none"})
    res = by_name(await api.as_(ALICE).apply(ok, bad))
    assert res["cs.refund"]["action"] == "created"
    assert res["cs.bad"]["action"] == "failed" and "argus.none" in res["cs.bad"]["error"]
    assert (await api.get("/cs.refund")).json()["base_model_ref"] == mid


async def test_mcp_servers_deployment_and_direct_entries(api):
    async with api.factory() as db:
        srv = mcpm.McpServer(name="payments", urn="payments.mcp", status="active")
        db.add(srv)
        await db.flush()
        db.add(mcpm.McpServerTool(mcp_server_id=srv.id, name="order.lookup"))
        db.add(mcpm.McpDeployment(mcp_server_id=srv.id, name="payments-prod", urn="payments-prod.mcpd",
                                  endpoint="https://mcp.corp.example/payments", auth_method="oauth2"))
        await db.commit()

    doc = agent_doc("cs.refund", mcpServers=[
        {"deployment": "payments-prod", "allowedTools": ["order.lookup"]},
        {"name": "legacy-mcp", "url": "https://legacy.example/mcp"},
    ])
    assert by_name(await api.as_(ALICE).apply(doc))["cs.refund"]["action"] == "created"
    mcps = {m["name"]: m for m in (await api.get("/cs.refund")).json()["mcp_servers"]}
    assert mcps["payments-prod"]["url"] == "https://mcp.corp.example/payments"
    assert mcps["payments-prod"]["allowed_tools"] == ["order.lookup"]
    assert mcps["legacy-mcp"]["mcp_deployment_id"] is None
    assert by_name(await api.apply(doc))["cs.refund"]["action"] == "unchanged"

    res = by_name(await api.apply(agent_doc("cs.refund", mcpServers=[{"deployment": "payments-prod"}])))["cs.refund"]
    assert "mcpServers: -legacy-mcp" in res["changes"]
    assert "mcpServers: ~payments-prod (allowed_tools)" in res["changes"]

    for bad in ({"deployment": "nope"}, {"deployment": "payments-prod", "allowedTools": ["refund.create"]}, {}):
        r = by_name(await api.apply(agent_doc("cs.refund", mcpServers=[bad])))["cs.refund"]
        assert r["action"] == "failed", bad


# ---------------------------------------------------------------------------
# 권한
# ---------------------------------------------------------------------------


async def test_only_creator_owner_or_admin_can_update(api):
    await api.as_(ALICE).apply(agent_doc("cs.refund"))
    res = by_name(await api.as_(BOB).apply(agent_doc("cs.refund", description="bob")))["cs.refund"]
    assert res["action"] == "failed" and "소유자" in res["error"]

    # 소유자를 bob 으로 바꾸면 bob 도 반영할 수 있다(이메일 기준)
    handover = agent_doc("cs.refund")
    handover["metadata"]["owner"] = "BOB@corp.example"
    assert by_name(await api.as_(ALICE).apply(handover))["cs.refund"]["action"] == "updated"
    handover["spec"]["description"] = "bob 이 고침"
    assert by_name(await api.as_(BOB).apply(handover))["cs.refund"]["action"] == "updated"
    assert by_name(await api.as_(ADMIN).apply(agent_doc("cs.refund")))["cs.refund"]["action"] == "updated"


async def test_blocked_status_is_admin_only(api):
    api.as_(ALICE)
    assert by_name(await api.apply(agent_doc("cs.blocked", status="blocked")))["cs.blocked"]["action"] == "failed"
    await api.apply(agent_doc("cs.refund"))
    assert by_name(await api.apply(agent_doc("cs.refund", status="blocked")))["cs.refund"]["action"] == "failed"
    assert by_name(await api.as_(ADMIN).apply(agent_doc("cs.refund", status="blocked")))["cs.refund"]["action"] == "updated"
    # 차단 해제도 관리자만, 상태를 생략하면 차단 유지
    assert by_name(await api.as_(ALICE).apply(agent_doc("cs.refund")))["cs.refund"]["action"] == "failed"
    keep = agent_doc("cs.refund")
    del keep["spec"]["status"]
    assert by_name(await api.apply(keep))["cs.refund"]["action"] == "unchanged"
    assert (await api.get("/cs.refund")).json()["status"] == "blocked"


# ---------------------------------------------------------------------------
# 정리(prune)
# ---------------------------------------------------------------------------


async def test_prune_deprecates_missing_agents_in_source_only(api):
    api.as_(ALICE)
    await api.apply(agent_doc("cs.a"), agent_doc("cs.b"), source_ref="git:cs/agents/a.yaml")
    await api.apply(agent_doc("hr.x"), source_ref="git:hr/agents")
    await api.client.post("/api/v1/ai-agents", json={**REQUIRED, "name": "cs.ui-only"})

    body = {"source_prefix": "git:cs/", "keep": ["cs.a"], "dry_run": True}
    r = await api.client.post("/api/v1/ai-agents/prune", json=body)
    assert [(x["name"], x["action"]) for x in r.json()["results"]] == [("cs.b", "deprecated")]
    assert (await api.get("/cs.b")).json()["status"] == "active"

    body["dry_run"] = False
    await api.client.post("/api/v1/ai-agents/prune", json=body)
    assert (await api.get("/cs.b")).json()["status"] == "deprecated"
    assert (await api.get("/hr.x")).json()["status"] == "active"
    hist = (await api.get("/cs.b/status-history")).json()
    assert hist[0]["to_status"] == "deprecated" and hist[0]["note"] == "정의 파일에서 제외됨"
    # 다시 실행해도 대상 없음, 빈 접두사는 거절
    assert (await api.client.post("/api/v1/ai-agents/prune", json=body)).json()["results"] == []
    r = await api.client.post("/api/v1/ai-agents/prune", json={"source_prefix": " ", "keep": []})
    assert r.status_code == 400


async def test_prune_skips_agents_without_permission(api):
    await api.as_(ALICE).apply(agent_doc("cs.a"), source_ref="git:cs/agents")
    r = await api.as_(BOB).client.post("/api/v1/ai-agents/prune", json={"source_prefix": "git:cs/", "keep": []})
    assert r.json()["results"] == [{"name": "cs.a", "action": "skipped", "error": "권한 없음"}]
    assert (await api.get("/cs.a")).json()["status"] == "active"


# ---------------------------------------------------------------------------
# 내보내기 (GET /ai-agents/{name}/manifest)
# ---------------------------------------------------------------------------


async def test_export_round_trips_to_unchanged(api):
    async with api.factory() as db:
        db.add(mm.RegisteredModel(name="argus.cs.intent", urn="argus.cs.intent.PROD.model"))
        srv = mcpm.McpServer(name="payments", urn="payments.mcp", status="active")
        db.add(srv)
        await db.flush()
        db.add(mcpm.McpDeployment(mcp_server_id=srv.id, name="payments-prod", urn="payments-prod.mcpd",
                                  endpoint="https://mcp.corp.example/payments"))
        await db.commit()
    doc = agent_doc("cs.refund", model={"name": "intent", "registryRef": "argus.cs.intent"},
                    inputSchema={"type": "object"}, mcpServers=[{"deployment": "payments-prod"},
                                                                {"name": "legacy", "url": "https://l.example"}])
    await api.as_(ALICE).apply(doc)

    r = await api.as_(None).get("/cs.refund/manifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/yaml")
    exported = yaml.safe_load(r.text)
    assert exported["metadata"]["owner"] == "alice@corp.example"
    assert exported["spec"]["model"]["registryRef"] == "argus.cs.intent"
    assert exported["spec"]["governance"]["budgetLimit"] == 300
    assert [m.get("deployment", m.get("name")) for m in exported["spec"]["mcpServers"]] == ["payments-prod", "legacy"]
    assert "release" not in exported["spec"]

    res = by_name(await api.as_(ALICE).apply(exported))["cs.refund"]
    assert res["action"] == "unchanged", res
    assert (await api.get("/cs.none/manifest")).status_code == 404


async def test_export_of_ui_agent_keeps_required_keys_empty(api):
    api.as_(ALICE)
    await legacy_agent(api, "cs.ui", "alice", status="active")
    exported = yaml.safe_load((await api.get("/cs.ui/manifest")).text)
    assert exported["metadata"] == {"name": "cs.ui", "owner": None, "department": None, "category": None}
    assert exported["spec"]["tools"] == [] and exported["spec"]["status"] == "active"
    res = by_name(await api.apply(exported))["cs.ui"]
    assert res["action"] == "failed" and "owner" in res["error"]  # 채워야 반영된다
