# SPDX-License-Identifier: Apache-2.0
"""MCP Server 카탈로그 단위 테스트 (자기완결형, in-memory SQLite + httpx.MockTransport).

검증 범위 (design/mcp-server-catalog.md Phase 1):
  - Argus Forge 시드, 설정(config.yaml) 가져오기, Forge 도구 REST 수집
  - 표준 MCP 서버(Streamable HTTP, JSON·SSE 응답) 수집과 annotation 기반 그룹 분류
  - 리소스 범위 시뮬레이터, 자격증명 암호화
  - server.json / mcp.json 가져오기·내보내기(Argus 전용 정보는 _meta 네임스페이스)
  - AI Agent 연계(배포 참조, 허용 그룹, 정책 번들, 미등록 MCP, 사용 중 retire 거부)

Phase 2:
  - 승인 정책 저장·관측값 채택·이력, finding 생성/자동 해소/재오픈, 엔터티 알림(MCP_POLICY_DRIFT)
  - manifest 수집(Forge ``/api/v1/manifest``, 없으면 도구 REST 폴백)·파일 가져오기, 서버 버전 이력
  - 감사 이벤트 수집(수집 토큰, JSONL, 중복 제거, 리소스 추출·정책 판정), 헬스 연속 실패
  - 계층별 시뮬레이터, 통제 현황

실행:
    cd backend && pytest tests/test_mcp_servers.py
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.agents.models as am
import app.alert.models  # noqa: F401  (알림 테이블)
import app.catalog.models  # noqa: F401  (FK 대상 catalog_datasources)
import app.embedding.models  # noqa: F401
import app.mcp_servers.models as mm
import app.models.models  # noqa: F401
import app.usermgr.models  # noqa: F401  (책임자 상태 계산의 사용자 목록)
from app.agents import schemas as agent_schemas
from app.agents import service as agent_service
from app.alert import schemas as alert_schemas
from app.alert import service as alert_service
from app.mcp_servers import audit, interop, normalize, policy, service, versions
from app.mcp_servers import schemas as s
from app.mcp_servers.forge_mapping import FORGE_BY_NAME
from app.mcp_servers.origins import ARGUS_META_NAMESPACE

# 테스트 호스트(*.example.com)는 DNS 로 풀리지 않는다 — 수집 허용 목록에 넣어 SSRF 가드(DNS 해석)를 건너뛴다
os.environ["ARGUS_MCP_COLLECT_ALLOWED_HOSTS"] = "*.example.com"

_TABLES = [
    "catalog_datasources",
    "catalog_registered_models",
    "catalog_entity_embeddings",
    "catalog_ai_agents",
    "catalog_ai_agent_tools",
    "catalog_ai_agent_mcp_servers",
    "catalog_ai_agent_lineage",
    "catalog_ai_agent_versions",
    "catalog_ai_agent_status_history",
    "catalog_ai_agent_discoveries",
    "argus_roles",
    "argus_users",
    "catalog_ai_agent_evals",
    "catalog_mcp_servers",
    "catalog_mcp_server_tool_groups",
    "catalog_mcp_server_tools",
    "catalog_mcp_server_resources",
    "catalog_mcp_server_prompts",
    "catalog_mcp_deployments",
    "catalog_mcp_deployment_credentials",
    "catalog_mcp_deployment_group_states",
    "catalog_mcp_deployment_tool_states",
    "catalog_mcp_resource_scopes",
    "catalog_mcp_status_history",
    "catalog_mcp_sync_log",
    "catalog_mcp_deployment_findings",
    "catalog_mcp_policy_history",
    "catalog_mcp_audit_events",
    "catalog_mcp_server_versions",
    "argus_alert_rule",
    "argus_lineage_alert",
    "argus_alert_notification",
]

HIVE_CONFIG = """
mcp:
  transport: streamable-http
  host: 0.0.0.0
  port: 8792
  path: /mcp
api:
  enabled: true
  path: /api/v1
hiveserver2:
  host: hs2.example.com
  port: 10000
  auth: none
  username: ""
  doas_user: null
  limits:
    max_rows: 200
    max_cells: 4000
metastore:
  uris: [thrift://hms1.example.com:9083]
  auth: none
  username: mcp
  allow_superuser_ugi: false
  tls: { enabled: true, ca_cert: null, require_verify: false }
allow:
  partitions: false
  properties: false
  acid: false
  dml: true
  ddl: false
  maintenance: true
  destructive: false
"""

KAFKA_CONFIG = """
kafka:
  bootstrap_servers: [kafka-1.example.com:9093]
  security_protocol: SASL_SSL
  sasl: { mechanism: SCRAM-SHA-512, username: svc-mcp }
  peek:
    enabled: true
    deny_topics: ["__*", "*pii*"]
    redact_fields: [password]
  policy:
    protected_topics: ["__*", "*-changelog"]
    mutable_topics: ["app-*"]
  allow:
    topics: true
    offsets: false
    destructive: false
connect:
  allow:
    control: false
"""


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [am.Base.metadata.tables[t] for t in _TABLES]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: am.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


def _forge_rest_transport(tools: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/manifest":  # manifest 미지원 서버 → 도구 REST 폴백
            return httpx.Response(404)
        assert request.url.path == "/api/v1/tools"
        assert request.headers.get("authorization") == "Bearer forge-token"
        return httpx.Response(200, json={"tools": tools})

    return httpx.MockTransport(handler)


def _mcp_transport(sse_for: set[str] = frozenset()) -> httpx.MockTransport:
    """표준 MCP Streamable HTTP 서버 흉내 (initialize 는 세션 ID 발급, 일부 메서드는 SSE 로 응답)."""
    pages = {
        "tools/list": [
            {"tools": [
                {"name": "search_issues", "description": "이슈 검색", "inputSchema": {"type": "object"},
                 "annotations": {"readOnlyHint": True}},
                {"name": "create_issue", "description": "이슈 생성", "inputSchema": {"type": "object"},
                 "annotations": {"readOnlyHint": False, "destructiveHint": False}},
            ], "nextCursor": "p2"},
            {"tools": [
                {"name": "delete_repo", "description": "저장소 삭제", "inputSchema": {"type": "object"},
                 "annotations": {"destructiveHint": True}},
                {"name": "mystery", "description": "annotation 없음", "inputSchema": {"type": "object"}},
            ]},
        ],
        "resources/list": [{"resources": [{"uri": "repo://readme", "name": "README", "mimeType": "text/markdown"}]}],
        "resources/templates/list": [{"resourceTemplates": [{"uriTemplate": "repo://{owner}/{name}", "name": "repo"}]}],
        "prompts/list": [{"prompts": [{"name": "triage", "description": "이슈 분류",
                                       "arguments": [{"name": "issue", "required": True}]}]}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(204)
        body = json.loads(request.content)
        method = body.get("method")
        if "id" not in body:  # notification
            return httpx.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "github", "version": "1.4.0"},
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "instructions": "GitHub 도구",
            }
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result},
                                  headers={"Mcp-Session-Id": "sess-1"})
        assert request.headers.get("mcp-session-id") == "sess-1"
        assert request.headers.get("mcp-protocol-version") == "2025-06-18"
        cursor = (body.get("params") or {}).get("cursor")
        result = pages[method][1 if cursor == "p2" else 0]
        msg = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        if method in sse_for:
            text = f"event: message\ndata: {json.dumps(msg)}\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=msg)

    return httpx.MockTransport(handler)


async def _run() -> None:
    factory = await _make_session()
    async with factory() as db:
        # ------------------------------------------------------------------
        # Forge 시드 → 정의·그룹·알려진 도구
        # ------------------------------------------------------------------
        seeded = await service.seed_forge(db, by="admin")
        assert len(seeded.servers) == len(FORGE_BY_NAME)
        hive = await service.get_server_detail(db, "hive-mcp")
        assert hive.framework == "argus-forge" and hive.provider_type == "internal"
        assert {"read", "dml", "destructive", "unclassified"} <= {g.key for g in hive.groups}
        seeded_tools = {t.name: t for t in hive.tools}
        assert seeded_tools["execute"].group_key == "dml" and seeded_tools["execute"].source == "forge_seed"
        assert seeded_tools["drop_partitions"].risk == "critical"
        # 재실행은 중복 생성하지 않음
        again = await service.seed_forge(db)
        assert all(i.action == "updated" for i in again.servers)

        # ------------------------------------------------------------------
        # 배포 + 설정 가져오기
        # ------------------------------------------------------------------
        await service.create_deployment(
            db,
            s.DeploymentCreate(name="hive-mcp-prod", server_name="hive-mcp", environment="PROD", status="active",
                               endpoint="https://hive-mcp.example.com:8792/mcp", auth_method="bearer"),
            created_by="alice",
        )
        try:
            await service.create_deployment(db, s.DeploymentCreate(name="hive-mcp-prod", server_name="hive-mcp"))
            raise AssertionError("duplicate deployment should conflict")
        except service.ConflictError:
            pass

        res = await service.import_config(db, "hive-mcp-prod", s.ConfigImportRequest(content=HIVE_CONFIG))
        assert res.ok and res.diff["groups"]["dml"] == [None, True]
        dep = await service.get_deployment_detail(db, "hive-mcp-prod")
        assert dep.service_principal == "mcp"
        assert dep.target_endpoint == "hs2.example.com"
        assert dep.limits["hiveserver2.limits.max_rows"] == 200
        assert dep.rest_api_base == "https://hive-mcp.example.com:8792/api/v1"
        assert any("TLS" in w for w in dep.security_warnings)
        assert any("0.0.0.0" in w for w in dep.security_warnings)
        assert sorted(dep.enabled_mutating_groups) == ["dml", "maintenance"]

        eff = await service.get_effective(db, "hive-mcp-prod")
        groups = {g.key: g for g in eff.groups}
        assert groups["dml"].observed_enabled is True and groups["dml"].observed_basis == "config"
        assert groups["destructive"].observed_enabled is False
        tools = {t.name: t for t in eff.tools}
        assert tools["execute"].exposed is True and tools["drop_partitions"].exposed is False

        # ------------------------------------------------------------------
        # Forge 도구 REST 수집 (자격증명 Bearer)
        # ------------------------------------------------------------------
        cred = await service.put_credential(db, "hive-mcp-prod", s.CredentialPut(secret="forge-token"))
        assert cred.masked == "fo****en"
        row = (await db.execute(select(mm.McpDeploymentCredential))).scalars().one()
        assert "forge-token" not in row.secret_encrypted
        forge_tools = [
            {"name": "list_tables", "description": "테이블 목록", "mutating": False, "inputSchema": {"type": "object"}},
            {"name": "execute", "description": "SQL 실행", "mutating": True, "inputSchema": {"type": "object"}},
            {"name": "analyze_table", "description": "통계", "mutating": True, "inputSchema": {"type": "object"}},
        ]
        res = await service.collect_deployment(
            db, "hive-mcp-prod", s.CollectRequest(), by="alice", transport=_forge_rest_transport(forge_tools)
        )
        assert res.ok and res.source == "forge_rest", res
        assert "list_tables" in res.diff["tools"]["added"]
        assert "drop_partitions" in res.diff["tools"]["not_exposed"]
        hive = await service.get_server_detail(db, "hive-mcp")
        by_name = {t.name: t for t in hive.tools}
        assert by_name["list_tables"].group_key == "read" and by_name["list_tables"].risk == "low"
        assert by_name["execute"].source == "forge_rest" and by_name["execute"].group_key == "dml"
        eff = await service.get_effective(db, "hive-mcp-prod")
        assert {g.key: g.observed_basis for g in eff.groups}["dml"] == "inferred"
        logs = await service.list_sync_log(db, deployment_name="hive-mcp-prod")
        assert [entry.kind for entry in logs][:2] == ["collect", "config_import"]

        # dry_run 은 반영하지 않음
        res = await service.collect_deployment(
            db, "hive-mcp-prod", s.CollectRequest(dry_run=True),
            transport=_forge_rest_transport(forge_tools + [{"name": "new_tool", "mutating": False}]),
        )
        assert res.ok and "new_tool" in res.diff["tools"]["added"]
        names = {t.name for t in (await service.get_server_detail(db, "hive-mcp")).tools}
        assert "new_tool" not in names

        # ------------------------------------------------------------------
        # 리소스 범위 (Kafka) + 시뮬레이터
        # ------------------------------------------------------------------
        await service.create_deployment(db, s.DeploymentCreate(name="kafka-mcp-dev", server_name="kafka-mcp",
                                                               endpoint="http://kafka-mcp.example.com:8794/mcp"))
        await service.import_config(db, "kafka-mcp-dev", s.ConfigImportRequest(content=KAFKA_CONFIG))
        kdep = await service.get_deployment_detail(db, "kafka-mcp-dev")
        assert kdep.service_principal == "svc-mcp" and kdep.principal_auth == "SASL_SSL"
        assert kdep.enabled_mutating_groups == ["topics"]

        async def sim(resource: str, action: str) -> str:
            r = await service.simulate(db, "kafka-mcp-dev",
                                       s.SimulateRequest(resource_type="topic", resource=resource, action=action))
            return r.decision

        assert await sim("customer-pii", "read") == "deny"
        assert await sim("orders", "read") == "unknown"
        assert await sim("app-orders", "write") == "allow"
        assert await sim("orders", "write") == "deny"          # mutable_topics 목록에 없음
        assert await sim("app-x-changelog", "write") == "deny"  # protect 우선
        assert await sim("app-orders", "delete") == "allow"     # delete 에 write 규칙 적용

        # ------------------------------------------------------------------
        # 표준 MCP 서버 (다른 개발자가 만든 서버) — Streamable HTTP 수집
        # ------------------------------------------------------------------
        await service.create_server(db, s.McpServerCreate(name="github-mcp", provider_type="vendor",
                                                          vendor="GitHub"), created_by="bob")
        await service.create_deployment(db, s.DeploymentCreate(
            name="github-mcp-ext", server_name="github-mcp", endpoint="https://api.example.com/mcp/",
            deployment_scope="external", status="active"), created_by="bob")
        res = await service.collect_deployment(db, "github-mcp-ext", s.CollectRequest(),
                                               transport=_mcp_transport(sse_for={"tools/list"}))
        assert res.ok and res.source == "mcp", res
        gh = await service.get_server_detail(db, "github-mcp")
        groups_of = {t.name: t.group_key for t in gh.tools}
        assert groups_of == {"search_issues": "read", "create_issue": "write", "delete_repo": "destructive",
                             "mystery": "unclassified"}
        assert gh.server_info["version"] == "1.4.0" and gh.protocol_version == "2025-06-18"
        assert {r.uri for r in gh.resources} == {"repo://readme", "repo://{owner}/{name}"}
        assert gh.prompts[0].name == "triage"
        gdep = await service.get_deployment_detail(db, "github-mcp-ext")
        assert gdep.deployed_version == "1.4.0" and gdep.max_exposed_risk == "critical"

        # 수동 분류 확정
        mystery = next(t for t in gh.tools if t.name == "mystery")
        upd = await service.update_tool(db, "github-mcp", mystery.id, s.ToolUpdate(group_key="read", risk="low"))
        assert upd.group_key == "read" and upd.risk_confirmed and upd.mutating is False

        # stdio 배포는 수집하지 않음
        await service.create_deployment(db, s.DeploymentCreate(name="github-mcp-local", server_name="github-mcp",
                                                               transport="stdio", deployment_scope="local"))
        res = await service.collect_deployment(db, "github-mcp-local", s.CollectRequest())
        assert not res.ok and "stdio" in res.message

        # ------------------------------------------------------------------
        # server.json 내보내기 → 가져오기 (Argus 전용 정보 왕복)
        # ------------------------------------------------------------------
        await service.update_server(db, "github-mcp", s.McpServerUpdate(
            owner_email="sec@example.com", registry_name="io.github.github/github-mcp-server",
            description="GitHub 공식 MCP"))
        doc = await service.export_server_json(db, "github-mcp")
        assert doc["name"] == "io.github.github/github-mcp-server"
        assert doc["remotes"] == [{"type": "streamable-http", "url": "https://api.example.com/mcp/"}]
        argus = doc["_meta"][ARGUS_META_NAMESPACE]
        assert argus["owner_email"] == "sec@example.com" and argus["name"] == "github-mcp"
        assert "owner_email" not in doc  # 표준 최상위에는 Argus 필드 없음
        assert any(t["name"] == "delete_repo" and t["risk"] == "critical" for t in argus["tools"])
        parsed = interop.parse_server_json(doc)[0]
        assert parsed["name"] == "github-mcp" and parsed["owner_email"] == "sec@example.com"

        res = await service.import_documents(db, s.ImportRequest(format="server_json", content={
            "name": "io.github.acme/weather", "description": "날씨", "version": "1.0.0",
            "packages": [{"registryType": "npm", "identifier": "@acme/weather", "transport": {"type": "stdio"}}],
        }))
        assert [(i.name, i.action) for i in res.items] == [("io.github.acme.weather", "created")]

        # ------------------------------------------------------------------
        # mcp.json 가져오기 — 비밀값(env 값·헤더 값)은 저장하지 않음
        # ------------------------------------------------------------------
        mcp_json = {"mcpServers": {
            "notion": {"type": "http", "url": "https://mcp.notion.example.com/mcp",
                       "headers": {"Authorization": "Bearer SECRET"}},
            "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
                   "env": {"API_KEY": "SECRET"}},
            "nifi": {"command": "uv", "args": ["run", "nifi-mcp", "--transport", "stdio"]},
        }}
        res = await service.import_documents(db, s.ImportRequest(format="mcp_json", content=json.dumps(mcp_json),
                                                                 environment="STG"))
        actions = {(i.kind, i.name): i.action for i in res.items}
        assert actions[("server", "notion")] == "created"
        assert actions[("deployment", "notion-mcp-stg")] == "created"
        assert actions[("server", "nifi-mcp")] == "exists"  # Forge 서버로 인식
        fs = await service.get_server_detail(db, "fs")
        assert fs.launch_config == {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
                                    "env_keys": ["API_KEY"]}
        notion_dep = await service.get_deployment_detail(db, "notion-mcp-stg")
        assert notion_dep.auth_config == {"header_names": ["Authorization"]}
        assert "SECRET" not in json.dumps(fs.model_dump(mode="json")) + json.dumps(notion_dep.model_dump(mode="json"))

        # ------------------------------------------------------------------
        # AI Agent 연계
        # ------------------------------------------------------------------
        await agent_service.create_agent(db, agent_schemas.AIAgentCreate(name="data.analyst"))
        link = await agent_service.add_mcp_server(db, "data.analyst", agent_schemas.AIAgentMcpServerCreate(
            mcp_deployment_name="hive-mcp-prod", allowed_groups=["read"]))
        assert link.registered and link.mcp_server_name == "hive-mcp" and link.url.startswith("https://hive-mcp")
        assert link.effective_tool_count == 1  # list_tables 만 (read 그룹, 노출됨)
        try:
            await agent_service.add_mcp_server(db, "data.analyst", agent_schemas.AIAgentMcpServerCreate(
                mcp_deployment_name="hive-mcp-prod", allowed_groups=["nope"]))
            raise AssertionError("unknown group should fail")
        except ValueError:
            pass
        legacy = await agent_service.add_mcp_server(db, "data.analyst", agent_schemas.AIAgentMcpServerCreate(
            name="Slack MCP", url="https://slack.example.com/mcp"))
        assert legacy.registered is False

        detail = await agent_service.get_agent_detail(db, "data.analyst")
        assert {m.registered for m in detail.mcp_servers} == {True, False}

        bundle = await agent_service.get_policy_bundle(db, "data.analyst")
        mcp_tools = [t for t in bundle.allowed_tools if t.mcp_deployment == "hive-mcp-prod"]
        assert [t.name for t in mcp_tools] == ["list_tables"] and mcp_tools[0].risk == "low"
        assert {m.registered for m in bundle.allowed_mcp_servers} == {True, False}

        # 허용 그룹 확장 → dml·maintenance 도구 포함, 에이전트 정책 번들 버전도 바뀐다(집행기 변경 감지)
        v_before = bundle.policy_version
        upd = await agent_service.update_mcp_server(db, "data.analyst", link.id, agent_schemas.AIAgentMcpServerUpdate(
            allowed_groups=["read", "dml", "maintenance"]))
        assert upd.effective_tool_count == 3
        assert (await agent_service.get_policy_bundle(db, "data.analyst")).policy_version != v_before

        # blocked 배포는 정책에서 제외
        await service.update_deployment(db, "hive-mcp-prod", s.DeploymentUpdate(status="blocked", status_note="점검"))
        bundle = await agent_service.get_policy_bundle(db, "data.analyst")
        assert not [t for t in bundle.allowed_tools if t.mcp_deployment]
        hist = await service.list_deployment_status_history(db, "hive-mcp-prod")
        assert hist[0].to_status == "blocked" and hist[0].note == "점검"

        agents = await service.list_deployment_agents(db, "hive-mcp-prod")
        assert [a.agent_name for a in agents] == ["data.analyst"]
        try:
            await service.retire_deployment(db, "hive-mcp-prod")
            raise AssertionError("in-use deployment should not retire")
        except service.ConflictError:
            pass

        # 미등록 MCP → 배포 연결
        unreg = await service.list_unregistered(db)
        assert unreg[0].name == "Slack MCP" and unreg[0].agents == ["data.analyst"]
        assert await service.link_unregistered(db, "notion-mcp-stg", unreg[0].link_ids) == 1
        assert await service.list_unregistered(db) == []

        # 서버 retire 는 살아있는 배포가 있으면 거부
        try:
            await service.retire_server(db, "github-mcp")
            raise AssertionError("server with deployments should not retire")
        except service.ConflictError:
            pass

        stats = await service.get_stats(db)
        assert stats.server_count >= 14 and stats.deployments_with_mutating >= 2
        assert stats.deployments_with_warnings >= 1

        listing = await service.list_deployments(db, mutating_enabled=True)
        assert {d.name for d in listing.items} >= {"hive-mcp-prod", "kafka-mcp-dev"}


KAFKA_MANIFEST = {
    "server": {"name": "kafka-mcp", "version": "2.1.0", "instance_id": "inst-7"},
    "target": {"platform": "kafka", "endpoint": "kafka-1.example.com:9093", "principal": "svc-mcp",
               "auth": "SASL_SSL"},
    "groups": [
        {"key": "peek", "enabled": True}, {"key": "topics", "enabled": True},
        {"key": "offsets", "enabled": False}, {"key": "destructive", "enabled": False},
    ],
    "tools": [
        {"name": "list_topics", "group": "read", "mutating": False, "exposed": True, "description": "토픽 목록"},
        {"name": "peek_messages", "group": "peek", "mutating": False, "exposed": True, "description": "메시지 조회"},
        {"name": "create_topic", "group": "topics", "mutating": True, "exposed": True, "description": "토픽 생성",
         "resource_types": ["topic"]},
        {"name": "reset_offsets", "group": "offsets", "mutating": True, "exposed": False},
        {"name": "delete_topic", "group": "destructive", "mutating": True, "exposed": False},
    ],
    "resource_scopes": [
        {"config_key": "kafka.policy.mutable_topics", "resource_type": "topic", "action": "write",
         "effect": "allow", "patterns": ["app-*"]},
        {"config_key": "kafka.policy.protected_topics", "resource_type": "topic", "action": "write",
         "effect": "protect", "patterns": ["__*"]},
    ],
    "limits": {"kafka.peek.max_messages": 500},
    "security_warnings": ["TLS certificate verification is disabled"],
}


def _manifest_transport(manifest: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/manifest"
        return httpx.Response(200, json=manifest)

    return httpx.MockTransport(handler)


def _ts(minute: int) -> str:
    """Forge Audit 형식(%z = +0900) 타임스탬프 — 감사 위반 7일 창 안쪽."""
    kst = timezone(timedelta(hours=9))
    base = (datetime.now(timezone.utc) - timedelta(hours=1)).astimezone(kst).replace(second=0, microsecond=0)
    return (base + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%S%z")


def _health_transport(status: int) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json={"status": "down"}))


async def _run_phase2() -> None:
    factory = await _make_session()
    async with factory() as db:
        await service.seed_forge(db, by="admin")
        await service.create_deployment(db, s.DeploymentCreate(
            name="kafka-mcp-prod", server_name="kafka-mcp", environment="PROD", status="active",
            owner_email="ops@example.com", endpoint="http://kafka-mcp.example.com:8794/mcp"), created_by="alice")

        # 알림 규칙: PROD 의 medium 이상 드리프트, 보안 경고
        await alert_service.create_rule(db, alert_schemas.AlertRuleCreate(
            rule_name="MCP PROD 드리프트", scope_type="ALL", trigger_type="MCP_POLICY_DRIFT",
            trigger_config=json.dumps({"min_severity": "medium", "environments": ["PROD"]}),
            subscribers="secops"))
        await alert_service.create_rule(db, alert_schemas.AlertRuleCreate(
            rule_name="MCP 보안 경고", scope_type="ALL", trigger_type="MCP_SECURITY_WARNING"))
        await db.commit()

        # ------------------------------------------------------------------
        # manifest 수집 (auto → manifest) — 정의·노출·범위·limits·보안 경고, 버전 기록
        # ------------------------------------------------------------------
        res = await service.collect_deployment(db, "kafka-mcp-prod", s.CollectRequest(), by="alice",
                                               transport=_manifest_transport(KAFKA_MANIFEST))
        assert res.ok and res.source == "manifest", res
        dep = await service.get_deployment_detail(db, "kafka-mcp-prod")
        assert dep.deployed_version == "2.1.0" and dep.instance_id == "inst-7"
        assert dep.observed_source == "manifest" and dep.limits == {"kafka.peek.max_messages": 500}
        assert sorted(dep.enabled_mutating_groups) == ["topics"]
        eff = await service.get_effective(db, "kafka-mcp-prod")
        groups = {g.key: g for g in eff.groups}
        assert groups["offsets"].observed_enabled is False and groups["offsets"].observed_basis == "manifest"
        tools = {t.name: t for t in eff.tools}
        assert tools["create_topic"].exposed is True and tools["delete_topic"].exposed is False
        kafka = await service._get_server(db, "kafka-mcp")
        vers = await versions.list_versions(db, kafka.id)
        assert len(vers) == 1 and vers[0].reported_version == "2.1.0" and vers[0].diff["initial"]
        assert "create_topic" in vers[0].diff["tools"]["added"]

        # 정책 없음 → 보안 경고·위험도 미확정만
        findings = {f.kind: f for f in (await policy.list_findings(db, deployment_name="kafka-mcp-prod",
                                                                   status="unresolved")).items}
        assert set(findings) == {"security_warning", "new_tool_unreviewed"}, set(findings)
        assert findings["security_warning"].alert_id is not None
        sec_alerts = await alert_service.list_alerts(db, entity_type="mcp_deployment",
                                                     alert_type="MCP_SECURITY_WARNING")
        assert sec_alerts.total == 1
        a = sec_alerts.items[0]
        assert a.source_dataset_id is None and a.entity_ref == "kafka-mcp-prod" and a.severity == "BREAKING"

        # 같은 정의 재수집 → 새 버전 없음
        await service.collect_deployment(db, "kafka-mcp-prod", s.CollectRequest(),
                                         transport=_manifest_transport(KAFKA_MANIFEST))
        assert await versions.version_count(db, kafka.id) == 1

        # ------------------------------------------------------------------
        # 승인 정책 저장 → finding
        # ------------------------------------------------------------------
        try:
            await service.update_deployment(db, "kafka-mcp-prod", s.DeploymentUpdate(policy_mode="enforce"))
            raise AssertionError("policy_mode 는 정책 API 로만 변경")
        except ValueError:
            pass
        pol = await policy.save_policy(db, "kafka-mcp-prod", s.PolicyUpdate(
            groups={"topics": False, "peek": True},
            tools={"delete_topic": s.PolicyToolIn(blocked=True)},
            scopes=[
                s.ResourceScopeCreate(resource_type="topic", action="write", effect="allow", patterns=["app-orders"]),
                s.ResourceScopeCreate(resource_type="topic", action="write", effect="protect",
                                      patterns=["__*", "*-changelog"]),
            ],
            limits={"kafka.peek.max_messages": 100},
            approved_version="2.0.0",
            note="최초 승인",
        ), by="alice")
        assert pol.policy_version and pol.policy_version.endswith("#1")
        assert {g.key: g.approved_enabled for g in pol.groups}["topics"] is False
        assert len(pol.approved_scopes) == 2 and all(sc.layer == "approved" for sc in pol.approved_scopes)

        findings = {f.kind: f for f in (await policy.list_findings(db, deployment_name="kafka-mcp-prod",
                                                                   status="unresolved")).items}
        assert {"group_enabled_unapproved", "scope_wider_than_approved", "limit_exceeds_approved",
                "version_mismatch", "security_warning"} <= set(findings), set(findings)
        scope_subjects = {f.subject for f in (await policy.list_findings(
            db, deployment_name="kafka-mcp-prod", kind="scope_wider_than_approved")).items}
        assert scope_subjects == {"topic:write:protect", "topic:allow:wider"}, scope_subjects
        drift = await alert_service.list_alerts(db, alert_type="MCP_POLICY_DRIFT")
        # medium 이상만: 첫 수집의 new_tool_unreviewed(medium) + group(high)·scope(high×2)·limit(medium)
        # — version_mismatch(low)는 규칙 min_severity 로 제외
        assert drift.total == 5, [x.change_summary for x in drift.items]
        assert not any("버전" in x.change_summary for x in drift.items)
        assert all("[MCP PROD]" in x.change_summary for x in drift.items)

        # 확인(ack) 처리
        gf = findings["group_enabled_unapproved"]
        acked = await policy.update_finding(db, "kafka-mcp-prod", gf.id, s.FindingUpdate(status="acknowledged",
                                                                                         note="변경 요청 중"), by="bob")
        assert acked.status == "acknowledged" and acked.acknowledged_by == "bob"

        # 관측값 채택(그룹) → 그룹 finding 자동 해소, 이력 2건
        await policy.adopt_observed(db, "kafka-mcp-prod", s.AdoptObservedRequest(
            groups=True, scopes=False, limits=False, version=False), by="alice")
        unresolved = {f.kind for f in (await policy.list_findings(db, deployment_name="kafka-mcp-prod",
                                                                  status="unresolved")).items}
        assert "group_enabled_unapproved" not in unresolved
        hist = await policy.list_policy_history(db, "kafka-mcp-prod")
        assert len(hist) == 2 and hist[0].snapshot["groups"]["topics"] is True
        assert hist[1].snapshot["blocked_tools"] == ["delete_topic"]

        # 위험도 확정 → new_tool_unreviewed 해소
        kafka_detail = await service.get_server_detail(db, "kafka-mcp")
        for t in kafka_detail.tools:
            if t.mutating and not t.risk_confirmed:
                await service.update_tool(db, "kafka-mcp", t.id, s.ToolUpdate(risk=t.risk or "high"))
        unresolved = {f.kind for f in (await policy.list_findings(db, deployment_name="kafka-mcp-prod",
                                                                  status="unresolved")).items}
        assert "new_tool_unreviewed" not in unresolved

        # 차단 도구가 노출되면 finding
        m2 = json.loads(json.dumps(KAFKA_MANIFEST))
        m2["server"]["version"] = "2.0.0"
        m2["groups"][3]["enabled"] = True
        m2["tools"][4]["exposed"] = True
        m2["tools"][2]["description"] = "토픽 생성 (변경된 설명)"
        res = await service.import_manifest(db, deployment_name="kafka-mcp-prod",
                                            req=s.ManifestImportRequest(content=json.dumps(m2)), by="alice")
        assert res.ok and res.source == "manifest"
        unresolved = {f.kind: f for f in (await policy.list_findings(db, deployment_name="kafka-mcp-prod",
                                                                     status="unresolved")).items}
        assert "tool_exposed_blocked" in unresolved and unresolved["tool_exposed_blocked"].subject == "delete_topic"
        assert "version_mismatch" not in unresolved  # 2.0.0 = 승인 버전
        vers = await versions.list_versions(db, kafka.id)
        assert len(vers) == 2 and vers[0].diff["tools"]["changed"] == ["create_topic"]

        # 정적 manifest(서버 대상) — 정의만 갱신, 배포 관측값은 그대로
        static = {"server": {"version": "2.2.0"}, "tools": [
            {k: v for k, v in t.items() if k != "exposed"} for t in m2["tools"]
        ] + [{"name": "describe_topic", "group": "read", "mutating": False}]}
        res = await service.import_manifest(db, server_name="kafka-mcp", req=s.ManifestImportRequest(content=static))
        assert res.ok and res.exposed_count == 0
        dep = await service.get_deployment_detail(db, "kafka-mcp-prod")
        assert dep.deployed_version == "2.0.0"
        assert (await versions.list_versions(db, kafka.id))[0].diff["tools"]["added"] == ["describe_topic"]

        # ------------------------------------------------------------------
        # 계층별 시뮬레이터 (observed: app-* 허용 / approved: app-orders 만 허용)
        # ------------------------------------------------------------------
        async def sim(resource: str, layer: str) -> str:
            r = await service.simulate(db, "kafka-mcp-prod", s.SimulateRequest(
                resource_type="topic", resource=resource, action="write", layer=layer))
            return r.decision

        assert await sim("app-payments", "observed") == "allow"
        assert await sim("app-payments", "approved") == "deny"
        assert await sim("app-payments", "combined") == "deny"
        assert await sim("app-orders", "combined") == "allow"
        r = await service.simulate(db, "kafka-mcp-prod", s.SimulateRequest(resource_type="topic",
                                                                           resource="app-x-changelog",
                                                                           action="write"))
        assert r.decision == "deny" and r.by_layer["observed"]["decision"] == "allow"

        # ------------------------------------------------------------------
        # 감사 이벤트 — 수집 토큰, JSONL, 중복 제거, 판정
        # ------------------------------------------------------------------
        dep_row = await service._get_deployment(db, "kafka-mcp-prod")
        tok = await audit.issue_token(db, dep_row, by="alice")
        assert tok.token.startswith("mcpi_") and audit.verify_token(dep_row, tok.token)
        assert not audit.verify_token(dep_row, "mcpi_wrong")
        assert dep_row.ingest_token_hash != tok.token  # 원문 저장 안 함
        lines = [
            {"ts": _ts(0), "session": "s1", "tool": "create_topic", "user": "agent-a",
             "args": {"topic": "app-orders", "partitions": 3}, "ok": True},
            {"ts": _ts(1), "session": "s1", "tool": "create_topic", "user": "agent-a",
             "args": {"topic": "payments", "sasl_password": "x"}, "ok": False, "error": "tool failed"},
            {"ts": _ts(2), "session": "s1", "tool": "delete_topic", "user": "agent-b",
             "args": {"topic": "app-orders"}, "ok": True},
            {"ts": _ts(3), "session": "s2", "tool": "custom_tool", "user": "agent-b",
             "args": {}, "ok": True},
        ]
        jsonl = "\n".join(json.dumps(x) for x in lines) + "\nnot-json\n"
        r = await audit.ingest(db, dep_row, s.AuditIngestRequest(jsonl=jsonl), via="token")
        assert (r.received, r.inserted, r.invalid, r.violations) == (4, 4, 1, 2), r
        r = await audit.ingest(db, dep_row, s.AuditIngestRequest(events=lines[:2]), via="upload")
        assert r.inserted == 0 and r.duplicates == 2
        evs = {e.tool_name + ":" + str((e.args or {}).get("topic")): e
               for e in (await audit.list_events(db, dep_row)).items}
        assert evs["create_topic:app-orders"].policy_decision == "allow"
        assert evs["create_topic:payments"].policy_decision == "deny"
        assert evs["create_topic:payments"].args["sasl_password"] == "***"
        assert evs["delete_topic:app-orders"].policy_decision == "blocked"
        assert evs["create_topic:app-orders"].group_key == "topics"
        assert evs["create_topic:app-orders"].resources == [{"type": "topic", "value": "app-orders"}]
        assert (await audit.list_events(db, dep_row, decision="violation")).total == 2
        audit_findings = {f.subject: f for f in (await policy.list_findings(
            db, deployment_name="kafka-mcp-prod", kind="audit_policy_violation")).items}
        assert audit_findings["delete_topic:blocked"].severity == "critical"
        assert audit_findings["create_topic:deny"].severity == "high"

        # 보존 기간 밖 이벤트는 받지 않음
        old = {**lines[0], "ts": "2001-01-01T00:00:00Z"}
        r = await audit.ingest(db, dep_row, s.AuditIngestRequest(events=[old]), via="token")
        assert r.inserted == 0 and r.invalid == 1

        # ------------------------------------------------------------------
        # 헬스 체크 연속 실패 → unhealthy, 성공 시 해소
        # ------------------------------------------------------------------
        for _ in range(3):
            await service.health_check(db, "kafka-mcp-prod", transport=_health_transport(503))
        unresolved = {f.kind for f in (await policy.list_findings(db, deployment_name="kafka-mcp-prod",
                                                                  status="unresolved")).items}
        assert "unhealthy" in unresolved
        detail = await service.get_deployment_detail(db, "kafka-mcp-prod")
        assert detail.health_fail_count == 3 and detail.open_finding_count >= 1 and detail.has_ingest_token

        # ------------------------------------------------------------------
        # 통제 현황
        # ------------------------------------------------------------------
        ov = await service.get_control_overview(db)
        assert ov.environments == ["PROD"]
        row = next(x for x in ov.matrix if x.server == "kafka-mcp")
        cell = row.cells["PROD"][0]
        assert cell.deployment == "kafka-mcp-prod" and cell.policy_set and cell.open_findings >= 1
        assert ov.coverage.deployments == 1 and ov.coverage.with_policy == 1 and ov.coverage.with_ingest_token == 1
        assert ov.risky and ov.risky[0].deployment == "kafka-mcp-prod"
        assert len(ov.trend) == 14 and sum(p.opened for p in ov.trend) >= 1
        assert len(ov.recent_calls) == 4

        # 토큰 폐기
        await audit.revoke_token(db, dep_row)
        assert not audit.verify_token(dep_row, tok.token)


def test_audit_resource_extraction() -> None:
    assert audit.extract_resources({"topic": "a"}, ["topic"]) == [{"type": "topic", "value": "a"}]
    assert audit.extract_resources({"group_id": "g", "topics": ["t1", "t2"]}, ["consumer_group", "topic"]) == [
        {"type": "consumer_group", "value": "g"}, {"type": "topic", "value": "t1"}, {"type": "topic", "value": "t2"}]
    assert audit.extract_resources({"database": "db", "table": "t"}, ["table"]) == [{"type": "table", "value": "db.t"}]
    assert audit.extract_resources({"topic": "***"}, ["topic"]) == []
    assert audit._parse_ts("2026-10-21T11:15:30+0900").utcoffset().total_seconds() == 9 * 3600


def test_mcp_phase2() -> None:
    asyncio.run(_run_phase2())


def test_normalize_classify() -> None:
    assert normalize.classify_annotations({"readOnlyHint": True}) == "read"
    assert normalize.classify_annotations({"destructiveHint": False}) == "write"
    assert normalize.classify_annotations({"destructiveHint": True}) == "destructive"
    assert normalize.classify_annotations(None) == "unclassified"
    assert normalize.classify_annotations(None, mutating=False) == "read"


def test_mcp_catalog_lifecycle() -> None:
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Phase 3 — 정책 번들(pull·ETag·304)·enforce 적용 상태·에이전트별 제한·서버 거부 감사 이벤트
# ---------------------------------------------------------------------------


def _bundle_app(factory):
    from fastapi import FastAPI

    from app.core.database import get_session
    from app.mcp_servers.router import deployments_router

    app = FastAPI()
    app.include_router(deployments_router, prefix="/api/v1")

    async def _session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = _session
    return app


async def _run_phase3() -> None:
    from app.mcp_servers import bundle as bundle_mod

    factory = await _make_session()
    async with factory() as db:
        await service.seed_forge(db, by="admin")
        await service.create_deployment(db, s.DeploymentCreate(
            name="kafka-mcp-prod", server_name="kafka-mcp", environment="PROD", status="active",
            owner_email="ops@example.com", endpoint="http://kafka-mcp.example.com:8794/mcp"), created_by="alice")
        res = await service.collect_deployment(db, "kafka-mcp-prod", s.CollectRequest(),
                                               transport=_manifest_transport(KAFKA_MANIFEST))
        assert res.ok and res.source == "manifest"
        await policy.save_policy(db, "kafka-mcp-prod", s.PolicyUpdate(
            groups={"topics": False},
            tools={"delete_topic": s.PolicyToolIn(blocked=True), "create_topic": s.PolicyToolIn(requires_approval=True)},
            scopes=[s.ResourceScopeCreate(resource_type="topic", action="write", effect="protect", patterns=["__*"])],
            limits={"kafka.peek.max_messages": 100},
        ), by="alice")

        # enforce 는 서비스 토큰이 있어야 한다
        try:
            await policy.save_policy(db, "kafka-mcp-prod", s.PolicyUpdate(policy_mode="enforce"), by="alice")
            raise AssertionError("enforce without service token")
        except ValueError as e:
            assert "서비스 토큰" in str(e)
        await db.rollback()
        dep = await service._get_deployment(db, "kafka-mcp-prod")
        tok = (await audit.issue_token(db, dep, by="alice")).token
        pol = await policy.save_policy(db, "kafka-mcp-prod", s.PolicyUpdate(policy_mode="enforce", note="집행 시작"),
                                       by="alice")
        assert pol.policy_mode == "enforce" and pol.has_service_token and pol.pull_state == "never"

        # 에이전트 연결 → 번들 agents (허용 그룹 + 에이전트 리소스 범위)
        await agent_service.create_agent(db, agent_schemas.AIAgentCreate(name="cs.bot"))
        link = await agent_service.add_mcp_server(db, "cs.bot", agent_schemas.AIAgentMcpServerCreate(
            mcp_deployment_name="kafka-mcp-prod", allowed_groups=["read", "peek"]))
        # 에이전트별 리소스 범위 — API 로 목록 교체
        assert await service.put_agent_scopes(db, "kafka-mcp-prod", 99999, []) is None
        try:
            await service.put_agent_scopes(db, "kafka-mcp-prod", link.id, [s.ResourceScopeCreate(
                resource_type="topic", effect="deny", patterns=["(bad"], match="regex")])
            raise AssertionError("bad regex accepted")
        except ValueError as e:
            assert "정규식" in str(e)
        await service.put_agent_scopes(db, "kafka-mcp-prod", link.id, [
            s.ResourceScopeCreate(resource_type="topic", action="read", effect="allow", patterns=["tmp-*"])])
        saved = await service.put_agent_scopes(db, "kafka-mcp-prod", link.id, [
            s.ResourceScopeCreate(resource_type="topic", action="read", effect="deny", patterns=["secret-*"])])
        assert [(x.effect, x.patterns, x.layer, x.agent_link_id) for x in saved] == [
            ("deny", ["secret-*"], "approved", link.id)]
        # 인스턴스 승인 범위 저장은 에이전트 범위를 지우지 않고, 인스턴스 범위 목록에도 섞이지 않는다
        pol = await policy.save_policy(db, "kafka-mcp-prod", s.PolicyUpdate(scopes=[
            s.ResourceScopeCreate(resource_type="topic", action="write", effect="protect", patterns=["__*"])]),
            by="alice")
        assert all(x.agent_link_id is None for x in pol.approved_scopes)
        links = await service.list_deployment_agents(db, "kafka-mcp-prod")
        assert links[0].agent_urn and [x.patterns for x in links[0].resource_scopes] == [["secret-*"]]
        # 시뮬레이터: 에이전트 범위까지 적용하면 secret-* 읽기 거부
        sim = s.SimulateRequest(resource_type="topic", resource="secret-pay", action="read")
        assert (await service.simulate(db, "kafka-mcp-prod", sim)).decision == "unknown"
        r = await service.simulate(db, "kafka-mcp-prod", sim.model_copy(update={"agent_link_id": link.id}))
        assert r.decision == "deny" and r.reason.startswith("에이전트 범위") and set(r.by_layer) == {
            "observed", "approved", "agent"}

        b = await bundle_mod.build_bundle(db, dep)
        assert b["policy_mode"] == "enforce" and b["status"] == "active" and b["deployment"] == "kafka-mcp-prod"
        assert b["groups"]["topics"] is False and b["blocked_tools"] == ["delete_topic"]
        assert b["approval_required_tools"] == ["create_topic"]
        assert b["resource_scopes"] == [{"resource_type": "topic", "action": "write", "effect": "protect",
                                         "patterns": ["__*"], "match": "fnmatch"}]
        assert b["limits"] == {"kafka.peek.max_messages": 100}
        agent = b["agents"]["cs.bot"]
        assert agent["allowed_groups"] == ["read", "peek"] and agent["allowed_tools"] is None
        assert agent["urn"] and agent["resource_scopes"][0]["patterns"] == ["secret-*"]

        # 유예 시간 안에는 finding 없음 → 지나면 policy_not_applied(never)
        kinds = {f.kind for f in (await policy.list_findings(db, deployment_name="kafka-mcp-prod")).items}
        assert "policy_not_applied" not in kinds
        dep.policy_updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await policy.evaluate_findings(db, dep)
        await db.commit()
        nf = (await policy.list_findings(db, deployment_name="kafka-mcp-prod", kind="policy_not_applied")).items
        assert len(nf) == 1 and nf[0].detail["state"] == "never" and nf[0].severity == "high"

    # ------------------------------------------------------------------
    # HTTP: 서비스 토큰 pull · ETag · 304 · pull 기록 → finding 자동 해소
    # ------------------------------------------------------------------
    transport = httpx.ASGITransport(app=_bundle_app(factory))
    async with httpx.AsyncClient(transport=transport, base_url="http://catalog") as client:
        url = "/api/v1/mcp-deployments/kafka-mcp-prod/policy-bundle"
        assert (await client.get(url, headers={"X-Argus-Ingest-Token": "mcpi_wrong"})).status_code == 401
        r = await client.get(url, headers={"Authorization": f"Bearer {tok}", "X-Argus-Instance-Id": "inst-9"})
        assert r.status_code == 200, r.text
        etag = r.headers["etag"]
        assert r.json()["agents"]["cs.bot"]["allowed_groups"] == ["read", "peek"]
        r304 = await client.get(url, headers={"X-Argus-Ingest-Token": tok, "If-None-Match": etag})
        assert r304.status_code == 304 and r304.headers["etag"] == etag

    async with factory() as db:
        dep = await service._get_deployment(db, "kafka-mcp-prod")
        assert dep.policy_pull_instance == "inst-9" and dep.policy_pulled_version.startswith(dep.policy_version)
        pol = await policy.get_policy(db, "kafka-mcp-prod")
        assert pol.pull_state == "applied" and pol.agent_count == 1 and pol.bundle_etag == etag
        nf = (await policy.list_findings(db, deployment_name="kafka-mcp-prod", kind="policy_not_applied")).items
        assert nf[0].status == "resolved"
        ov = await service.get_control_overview(db)
        assert ov.coverage.enforce == 1 and ov.coverage.enforce_applied == 1

        # 에이전트 허용 범위가 바뀌면 policy_version 은 그대로여도 ETag 가 바뀌어 outdated
        link_row = (await db.execute(select(am.AIAgentMcpServer))).scalars().first()
        await agent_service.update_mcp_server(db, "cs.bot", link_row.id, agent_schemas.AIAgentMcpServerUpdate(
            allowed_groups=["read"]))
        dep = await service._get_deployment(db, "kafka-mcp-prod")
        etag2 = bundle_mod.bundle_etag(await bundle_mod.build_bundle(db, dep))
        assert etag2 != etag and bundle_mod.pull_state(dep, etag2) == "outdated"

        # 오래 안 받아가면 stale finding 재오픈
        dep.policy_pulled_at = datetime.now(timezone.utc) - timedelta(hours=2)
        await policy.evaluate_findings(db, dep)
        await db.commit()
        nf = (await policy.list_findings(db, deployment_name="kafka-mcp-prod", kind="policy_not_applied")).items
        assert nf[0].status == "open" and nf[0].detail["state"] == "stale"

        # 서버가 거부한 호출(decision=denied)은 집행 결과 — 위반 finding 아님, agent 기록
        lines = [
            {"ts": _ts(0), "session": "s1", "tool": "delete_topic", "user": "u1", "agent": "cs.bot",
             "args": {"topic": "app-orders"}, "ok": False, "error": "tool failed", "decision": "denied",
             "reason": "blocked tool"},
            {"ts": _ts(1), "session": "s1", "tool": "create_topic", "user": "u1",
             "args": {"topic": "__consumer_offsets"}, "ok": True},
        ]
        r = await audit.ingest(db, dep, s.AuditIngestRequest(events=lines), via="token")
        assert r.inserted == 2 and r.violations == 1, r
        evs = {e.tool_name: e for e in (await audit.list_events(db, dep)).items}
        assert evs["delete_topic"].server_decision == "denied" and evs["delete_topic"].agent_name == "cs.bot"
        assert evs["delete_topic"].policy_decision == "blocked" and "서버가 거부" in evs["delete_topic"].policy_reason
        assert (await audit.list_events(db, dep, decision="enforced")).total == 1
        assert (await audit.list_events(db, dep, decision="violation")).total == 1
        subjects = {f.subject for f in (await policy.list_findings(
            db, deployment_name="kafka-mcp-prod", kind="audit_policy_violation")).items}
        assert subjects == {"create_topic:deny"}, subjects

        # 배포 차단 → 번들 status=blocked
        await service.update_deployment(db, "kafka-mcp-prod", s.DeploymentUpdate(status="blocked", status_note="사고"))
        dep = await service._get_deployment(db, "kafka-mcp-prod")
        assert (await bundle_mod.build_bundle(db, dep))["status"] == "blocked"


def test_mcp_phase3() -> None:
    asyncio.run(_run_phase3())


async def _run_search() -> None:
    from app.embedding import service as embedding_service
    from app.search import service as search_service

    assert "mcp_server" in embedding_service.ENTITY_TYPES
    factory = await _make_session()
    async with factory() as db:
        await service.seed_forge(db, by="admin")
        await service.create_deployment(db, s.DeploymentCreate(
            name="kafka-mcp-prod", server_name="kafka-mcp", environment="PROD", status="active"), created_by="alice")
        kafka = await service._get_server(db, "kafka-mcp")

        # 임베딩 원문: 이름·그룹·도구 시그니처·배포 이름
        text = await embedding_service.build_entity_source_text(db, "mcp_server", kafka.id)
        assert text.startswith("kafka-mcp") and "tools: " in text and "delete_topic" in text
        assert "tool groups: " in text and "kafka-mcp-prod (PROD)" in text

        # 키워드: 서버 이름·도구 이름 모두 매칭 (임베딩 제공자 없으면 키워드 폴백)
        grouped = await search_service.hybrid_search_entities(db, "delete_topic", ["mcp_server"])
        assert [eid for eid, _, _ in grouped["mcp_server"]] == [kafka.id]
        ids = await search_service._keyword_search_entity_ids(db, "kafka", "mcp_server")
        assert kafka.id in ids
        payload = await search_service.build_entity_result(db, "mcp_server", kafka.id)
        assert payload["name"] == "kafka-mcp" and payload["extra"]["status"] == "active"

        # retire 된 서버는 검색에서 빠진다
        await service.retire_deployment(db, "kafka-mcp-prod")
        assert await service.retire_server(db, "kafka-mcp")
        assert kafka.id not in await search_service._keyword_search_entity_ids(db, "kafka", "mcp_server")
        assert await search_service.build_entity_result(db, "mcp_server", kafka.id) is None


def test_mcp_search() -> None:
    asyncio.run(_run_search())


if __name__ == "__main__":
    test_normalize_classify()
    test_mcp_catalog_lifecycle()
    test_audit_resource_extraction()
    test_mcp_phase2()
    test_mcp_phase3()
    test_mcp_search()
    print("OK")
