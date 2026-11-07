# SPDX-License-Identifier: Apache-2.0
"""AI 에이전트 정의 파일(``agent.yaml``) 해석 · 일괄 반영.

설계: design/ai-agent-bulk-registration-design.md

정의 파일 한 문서가 에이전트 하나다. ``apply_manifests`` 는 여러 문서를 받아 에이전트마다 독립적으로
생성 · 갱신 · 변경 없음 · 실패를 판단하고, 결과를 돌려준다. 같은 내용을 다시 반영해도 결과가 같다(멱등).

반영 규칙
- 정의 파일이 그 에이전트의 기준이다. 파일에 없는 기본 항목(설명·호출 주소 등)은 비운다.
  단 ``status``·``version`` 을 생략하면 현재 값을 유지한다(화면에서 바꾼 상태를 의도치 않게 되돌리지 않도록).
- 하위 항목(``tools``·``mcpServers``·``lineage``)은 키가 있으면 "이 목록이 전부"로 맞추고(없는 항목 삭제),
  키를 생략하면 기존 항목을 건드리지 않는다.
- ``version`` 이 바뀌거나 새로 만들 때 버전 기록을 하나 남긴다.
- 실제 DB 값과 비교해 변경 여부를 판단하므로, 화면에서 고친 값도 다음 반영 때 정의 파일 기준으로 돌아간다.

권한
- 새 에이전트: 로그인 사용자(``POST /apply`` 는 ``agents.register`` 기능 권한 필요 — 라우터에서 검사).
- 기존 에이전트: 관리자, 등록자(``created_by``), 소유자(``owner_email`` = 로그인 사용자 이메일).
  화면·개별 API(``router._assert_agent_editor``)·일괄 변경과 같은 기준(``_can_edit``)이며, 이 권한이면
  도구·도구 서버·계보 같은 하위 항목도 바꿀 수 있다.
- ``blocked`` 로 등록하거나, ``blocked`` 로 바꾸거나 ``blocked`` 에서 푸는 것은 관리자만.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.models import (
    AIAgent,
    AIAgentLineage,
    AIAgentMcpServer,
    AIAgentStatusHistory,
    AIAgentTool,
    AIAgentVersion,
)
from app.agents.schemas import BUDGET_LIMIT_MAX, AgentStatus

logger = logging.getLogger(__name__)

API_VERSION = "argus.catalog/v1"
MAX_DOCUMENTS = 200
# 영문 소문자·숫자로 시작, 이후 소문자·숫자·점·하이픈·밑줄, 전체 1~255자 (discovery 는 대문자도 허용)
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,254}$")
_RISKS = {"low", "medium", "high", "critical"}


class ManifestError(ValueError):
    """요청 전체를 받을 수 없는 오류(형식 오류·문서 수 초과) — HTTP 400."""


# ---------------------------------------------------------------------------
# 정의 파일 형식
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    # 처음 보는 항목은 거절한다(오타가 조용히 무시되지 않도록). 키는 camelCase 로 받는다.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _Metadata(_Strict):
    name: str
    display_name: str | None = Field(None, alias="displayName", max_length=255)
    owner: str = Field(..., min_length=3, max_length=200)
    department: str = Field(..., min_length=1, max_length=200)
    category: str = Field(..., min_length=1, max_length=100)
    tags: list[str] | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_PATTERN.match(v):
            raise ValueError("name 은 영문 소문자·숫자로 시작하고 영문 소문자·숫자·점·하이픈·밑줄만 쓸 수 있습니다(최대 255자).")
        return v


class _Model(_Strict):
    name: str | None = Field(None, max_length=255)
    provider: str | None = Field(None, max_length=100)
    registry_ref: str | None = Field(None, alias="registryRef")


class _Runtime(_Strict):
    framework: str | None = Field(None, max_length=100)
    execution_policy: str | None = Field(None, alias="executionPolicy", max_length=50)
    max_steps: int | None = Field(None, alias="maxSteps", ge=1)
    memory_type: str | None = Field(None, alias="memoryType", max_length=30)
    multi_agent: bool = Field(False, alias="multiAgent")
    sub_agents: list[dict[str, Any]] | None = Field(None, alias="subAgents")


class _Interface(_Strict):
    endpoint: str | None = Field(None, max_length=1000)
    protocol: str | None = Field(None, max_length=30)
    invocation_method: str | None = Field(None, alias="invocationMethod", max_length=30)
    streaming: bool = False
    auth_method: str | None = Field(None, alias="authMethod", max_length=50)


class _Governance(_Strict):
    pii_handling: str | None = Field(None, alias="piiHandling", max_length=30)
    data_residency: str | None = Field(None, alias="dataResidency", max_length=100)
    budget_limit: float | None = Field(None, alias="budgetLimit", ge=0, lt=BUDGET_LIMIT_MAX)
    hitl_required: bool = Field(False, alias="hitlRequired")
    hitl_config: dict[str, Any] | None = Field(None, alias="hitlConfig")
    audit_log_ref: str | None = Field(None, alias="auditLogRef", max_length=500)
    network_allowlist: list[str] | None = Field(None, alias="networkAllowlist")
    dlp_policies: list[str] | None = Field(None, alias="dlpPolicies")
    guardrails: dict[str, Any] | None = None


class _Tool(_Strict):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    risk: str | None = None
    requires_approval: bool = Field(False, alias="requiresApproval")
    tool_schema: dict[str, Any] | None = Field(None, alias="schema")

    @field_validator("risk")
    @classmethod
    def _risk(cls, v: str | None) -> str | None:
        if v is not None and v not in _RISKS:
            raise ValueError(f"risk 는 {', '.join(sorted(_RISKS))} 중 하나여야 합니다.")
        return v


class _McpServer(_Strict):
    deployment: str | None = None  # 카탈로그 도구 서버 배포 이름
    name: str | None = Field(None, max_length=200)  # 배포 없이 직접 입력(미등록 연결)
    url: str | None = None
    auth_method: str | None = Field(None, alias="authMethod")
    description: str | None = None
    allowed_groups: list[str] | None = Field(None, alias="allowedGroups")
    allowed_tools: list[str] | None = Field(None, alias="allowedTools")

    def key(self) -> str:
        """기존 연결과 맞춰 볼 키 — 배포 연결은 ``dep:<배포명>``, 직접 입력은 ``name:<이름>``."""
        return f"dep:{self.deployment}" if self.deployment else f"name:{self.name}"


class _Lineage(_Strict):
    target_type: str = Field("agent", alias="targetType", max_length=20)
    target_ref: str = Field(..., alias="targetRef", min_length=1, max_length=500)
    relation: str = Field("depends_on", max_length=20)
    description: str | None = None


class _Release(_Strict):
    system_prompt: str | None = Field(None, alias="systemPrompt")
    changelog: str | None = None
    source: str | None = Field(None, max_length=1000)


class _Spec(_Strict):
    description: str | None = None
    version: str | None = Field(None, min_length=1, max_length=50)
    status: AgentStatus | None = None
    model: _Model | None = None
    runtime: _Runtime | None = None
    interface: _Interface | None = None
    governance: _Governance | None = None
    capabilities: list[str] | None = None
    supported_languages: list[str] | None = Field(None, alias="supportedLanguages")
    use_cases: list[str] | None = Field(None, alias="useCases")
    limitations: list[str] | None = None
    input_schema: dict[str, Any] | None = Field(None, alias="inputSchema")
    output_schema: dict[str, Any] | None = Field(None, alias="outputSchema")
    inference_params: dict[str, Any] | None = Field(None, alias="inferenceParams")
    rag_config: dict[str, Any] | None = Field(None, alias="ragConfig")
    tools: list[_Tool] | None = None
    mcp_servers: list[_McpServer] | None = Field(None, alias="mcpServers")
    lineage: list[_Lineage] | None = None
    release: _Release | None = None


class AgentManifest(_Strict):
    """정의 파일 한 문서(``apiVersion``·``kind: Agent``·``metadata``·``spec``)."""

    api_version: str = Field(API_VERSION, alias="apiVersion")
    kind: str = "Agent"
    metadata: _Metadata
    spec: _Spec = Field(default_factory=_Spec)

    @field_validator("api_version")
    @classmethod
    def _api(cls, v: str) -> str:
        if v != API_VERSION:
            raise ValueError(f"apiVersion 은 '{API_VERSION}' 이어야 합니다.")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v != "Agent":
            raise ValueError("kind 는 'Agent' 이어야 합니다.")
        return v

    def scalar_fields(self) -> dict[str, Any]:
        """``catalog_ai_agents`` 기본 컬럼으로 옮길 값(하위 항목·상태·버전 제외)."""
        m, s = self.metadata, self.spec
        model = s.model or _Model()
        rt = s.runtime or _Runtime()
        itf = s.interface or _Interface()
        gov = s.governance or _Governance()
        return {
            "display_name": m.display_name,
            "owner_email": m.owner,
            "department": m.department,
            "category": m.category,
            "tags": m.tags,
            "description": s.description,
            "base_model": model.name,
            "model_provider": model.provider,
            "framework": rt.framework,
            "execution_policy": rt.execution_policy,
            "max_steps": rt.max_steps,
            "memory_type": rt.memory_type,
            "is_multi_agent": rt.multi_agent,
            "sub_agents": rt.sub_agents,
            "endpoint": itf.endpoint,
            "protocol": itf.protocol,
            "invocation_method": itf.invocation_method,
            "streaming": itf.streaming,
            "auth_method": itf.auth_method,
            "pii_handling": gov.pii_handling,
            "data_residency": gov.data_residency,
            "budget_limit": gov.budget_limit,
            "hitl_required": gov.hitl_required,
            "hitl_config": gov.hitl_config,
            "audit_log_ref": gov.audit_log_ref,
            "network_allowlist": gov.network_allowlist,
            "dlp_policies": gov.dlp_policies,
            "guardrails": gov.guardrails,
            "capabilities": s.capabilities,
            "supported_languages": s.supported_languages,
            "use_cases": s.use_cases,
            "limitations": s.limitations,
            "input_schema": s.input_schema,
            "output_schema": s.output_schema,
            "inference_params": s.inference_params,
            "rag_config": s.rag_config,
        }


def parse_documents(body: str | bytes | dict | list) -> list[Any]:
    """요청 본문을 문서 목록으로. YAML 원문(여러 문서 ``---``) 또는 JSON ``{"agents": [...]}``/배열.

    해석 실패·빈 목록·``MAX_DOCUMENTS`` 초과는 ``ManifestError``. 각 문서의 형식 검사는 ``apply_manifests`` 에서 한다.
    """
    if isinstance(body, (str, bytes)):
        try:
            docs = [d for d in yaml.safe_load_all(body) if d is not None]
        except yaml.YAMLError as e:
            raise ManifestError(f"정의 파일을 해석할 수 없습니다: {e}") from e
    elif isinstance(body, dict):
        docs = body.get("agents")
        if not isinstance(docs, list):
            raise ManifestError("JSON 본문은 {\"agents\": [ ... ]} 형식이어야 합니다.")
    else:
        docs = body
    if not docs:
        raise ManifestError("반영할 에이전트 정의가 없습니다.")
    if len(docs) > MAX_DOCUMENTS:
        raise ManifestError(f"한 번에 최대 {MAX_DOCUMENTS}개까지 반영할 수 있습니다(받은 수: {len(docs)}).")
    logger.debug("정의 파일 문서 해석: %d건", len(docs))
    return docs


# ---------------------------------------------------------------------------
# 반영
# ---------------------------------------------------------------------------


def _norm(v: Any) -> Any:
    """비교용 정규화 — Decimal/float·JSON 순서 차이로 '변경'이 잡히지 않게."""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, ensure_ascii=False)
    if isinstance(v, bool):
        return v
    try:
        from decimal import Decimal

        if isinstance(v, (int, float, Decimal)):
            return float(v)
    except Exception:  # pragma: no cover
        pass
    return v


def _short(v: Any) -> str:
    """변경 내역 표시용 짧은 문자열(None 은 ∅, 60자 초과는 말줄임)."""
    s ="∅" if v is None else (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
    return s if len(s) <= 60 else s[:57] + "..."


def _can_edit(agent: AIAgent, user) -> bool:
    """에이전트 쓰기 권한 — 관리자, 등록자(created_by), 소유자(owner_email, 대소문자 무시)."""
    if user.is_admin:
        return True
    if agent.created_by and agent.created_by == user.username:
        return True
    return bool(agent.owner_email and user.email and agent.owner_email.lower() == user.email.lower())


async def _resolve_model_ref(session: AsyncSession, name: str | None) -> int | None:
    """``model.registryRef`` (등록 모델 이름) → ``catalog_registered_models.id``. 삭제됐거나 없으면 ValueError."""
    if not name:
        return None
    from app.models.models import RegisteredModel

    mid = (await session.execute(
        select(RegisteredModel.id).where(RegisteredModel.name == name, RegisteredModel.status != "deleted")
    )).scalar_one_or_none()
    if mid is None:
        raise ValueError(f"model.registryRef: MLflow 모델 '{name}' 을(를) 찾을 수 없습니다.")
    return mid


async def _sync_tools(session, agent: AIAgent, desired: list[_Tool], changes: list[str]) -> bool:
    """도구 목록을 ``desired`` 와 같게 맞춘다(이름 기준 추가·수정·삭제). 바뀌었으면 True(정책 버전 갱신용)."""
    names =[t.name for t in desired]
    if len(names) != len(set(names)):
        raise ValueError("tools: 같은 이름의 도구가 두 번 이상 있습니다.")
    rows = (await session.execute(select(AIAgentTool).where(AIAgentTool.agent_id == agent.id))).scalars().all()
    current = {r.name: r for r in rows}
    changed = False
    for name, row in current.items():
        if name not in names:
            await session.delete(row)
            changes.append(f"tools: -{name}")
            changed = True
    for t in desired:
        want = {"description": t.description, "risk": t.risk, "requires_approval": t.requires_approval,
                "tool_schema": t.tool_schema}
        row = current.get(t.name)
        if row is None:
            session.add(AIAgentTool(agent_id=agent.id, name=t.name, **want))
            changes.append(f"tools: +{t.name}")
            changed = True
            continue
        diff = [k for k, v in want.items() if _norm(getattr(row, k)) != _norm(v)]
        if diff:
            for k in diff:
                setattr(row, k, want[k])
            changes.append(f"tools: ~{t.name} ({', '.join(diff)})")
            changed = True
    return changed


async def _sync_mcp(session, agent: AIAgent, desired: list[_McpServer], changes: list[str]) -> bool:
    """MCP 연결을 ``desired`` 와 같게 맞춘다(``_McpServer.key`` 기준). 바뀌었으면 True.

    배포 연결은 배포를 찾아 허용 범위를 검사한 뒤 주소·인증 방식을 배포 값으로 채운다. 배포가 없거나
    키가 중복되면 ValueError.
    """
    from app.mcp_servers import service as mcp_service

    keys = []
    resolved: dict[str, Any] = {}
    for m in desired:
        if not m.deployment and not (m.name or "").strip():
            raise ValueError("mcpServers: deployment 또는 name 중 하나는 필요합니다.")
        keys.append(m.key())
        if m.deployment:
            r = await mcp_service.resolve_deployment(session, m.deployment)
            if not r:
                raise ValueError(f"mcpServers: 도구 서버 배포 '{m.deployment}' 을(를) 찾을 수 없습니다.")
            await mcp_service.validate_agent_scope(session, r[0], m.allowed_groups, m.allowed_tools)
            resolved[m.key()] = r
    if len(keys) != len(set(keys)):
        raise ValueError("mcpServers: 같은 배포(또는 이름)가 두 번 이상 있습니다.")

    rows = (await session.execute(
        select(AIAgentMcpServer).where(AIAgentMcpServer.agent_id == agent.id)
    )).scalars().all()
    from app.mcp_servers.models import McpDeployment

    dep_names = {}
    dep_ids = [r.mcp_deployment_id for r in rows if r.mcp_deployment_id]
    if dep_ids:
        dep_names = dict((await session.execute(
            select(McpDeployment.id, McpDeployment.name).where(McpDeployment.id.in_(dep_ids))
        )).all())

    def row_key(r: AIAgentMcpServer) -> str:
        return f"dep:{dep_names.get(r.mcp_deployment_id)}" if r.mcp_deployment_id else f"name:{r.name}"

    current = {}
    changed = False
    for r in rows:
        k = row_key(r)
        if k in current or k not in keys:  # 중복 행·선언에 없는 행 정리
            await session.delete(r)
            changes.append(f"mcpServers: -{k.split(':', 1)[1]}")
            changed = True
        else:
            current[k] = r

    for m in desired:
        k = m.key()
        if m.deployment:
            dep, server = resolved[k]
            want = {"name": m.name or dep.display_name or dep.name, "url": dep.endpoint, "auth_method": dep.auth_method,
                    "description": m.description or dep.description or server.description,
                    "mcp_deployment_id": dep.id, "allowed_groups": m.allowed_groups, "allowed_tools": m.allowed_tools}
        else:
            want = {"name": m.name, "url": m.url, "auth_method": m.auth_method, "description": m.description,
                    "mcp_deployment_id": None, "allowed_groups": m.allowed_groups, "allowed_tools": m.allowed_tools}
        row = current.get(k)
        label = k.split(":", 1)[1]
        if row is None:
            session.add(AIAgentMcpServer(agent_id=agent.id, **want))
            changes.append(f"mcpServers: +{label}")
            changed = True
            continue
        diff = [f for f, v in want.items() if _norm(getattr(row, f)) != _norm(v)]
        if diff:
            for f in diff:
                setattr(row, f, want[f])
            changes.append(f"mcpServers: ~{label} ({', '.join(diff)})")
            changed = True
    return changed


async def _sync_lineage(session, agent: AIAgent, desired: list[_Lineage], changes: list[str]) -> None:
    """계보를 ``desired`` 와 같게 맞춘다((대상 유형, 대상, 관계) 기준). 정책 번들과 무관해 반환값 없음."""
    keys =[(x.target_type, x.target_ref, x.relation) for x in desired]
    if len(keys) != len(set(keys)):
        raise ValueError("lineage: 같은 대상·관계가 두 번 이상 있습니다.")
    rows = (await session.execute(select(AIAgentLineage).where(AIAgentLineage.agent_id == agent.id))).scalars().all()
    current = {}
    for r in rows:
        k = (r.target_type, r.target_ref, r.relation)
        if k in current or k not in keys:
            await session.delete(r)
            changes.append(f"lineage: -{r.target_ref}({r.relation})")
        else:
            current[k] = r
    for x, k in zip(desired, keys):
        row = current.get(k)
        if row is None:
            session.add(AIAgentLineage(agent_id=agent.id, target_type=x.target_type, target_ref=x.target_ref,
                                       relation=x.relation, description=x.description))
            changes.append(f"lineage: +{x.target_ref}({x.relation})")
        elif _norm(row.description) != _norm(x.description):
            row.description = x.description
            changes.append(f"lineage: ~{x.target_ref}({x.relation})")


async def _apply_one(session: AsyncSession, man: AgentManifest, user, source_ref: str | None) -> dict:
    """문서 하나를 반영한다(커밋·롤백은 호출 측). 결과 ``{name, action, changes, agent_id}``.

    권한 위반은 ``PermissionError``, 값 문제(모델·배포 없음, 중복 등)는 ``ValueError``.
    """
    name = man.metadata.name
    changes: list[str] = []
    agent = (await session.execute(select(AIAgent).where(AIAgent.name == name))).scalars().first()
    created = agent is None
    want_status = man.spec.status.value if man.spec.status else None

    scalars = man.scalar_fields()
    scalars["base_model_ref"] = await _resolve_model_ref(session, (man.spec.model or _Model()).registry_ref)

    if created:
        if want_status == AgentStatus.BLOCKED.value and not user.is_admin:
            raise PermissionError("blocked 상태로 등록하는 것은 관리자만 할 수 있습니다.")
        # 서버 기본값 컬럼까지 모두 채워 만든다(flush 후 미로딩 속성 접근 방지)
        agent = AIAgent(name=name, urn=f"{name}.agent", status=want_status or AgentStatus.DRAFT.value,
                        version=man.spec.version or "0.1.0", created_by=user.username, updated_by=user.username,
                        usage_count=0, managed_by="manifest", source_ref=source_ref, **scalars)
        session.add(agent)
        await session.flush()
        from app.agents import discovery

        await discovery.mark_registered(session, name, agent.id, user.username)
        changes.append("created")
    else:
        if not _can_edit(agent, user):
            raise PermissionError("이 에이전트의 소유자·등록자 또는 관리자만 반영할 수 있습니다.")
        if want_status and want_status != agent.status and AgentStatus.BLOCKED.value in (want_status, agent.status) \
                and not user.is_admin:
            raise PermissionError("blocked 상태로 바꾸거나 해제하는 것은 관리자만 할 수 있습니다.")
        # 기본 항목
        for field, value in scalars.items():
            if _norm(getattr(agent, field)) != _norm(value):
                changes.append(f"{field}: {_short(getattr(agent, field))} → {_short(value)}")
                setattr(agent, field, value)

    # 상태
    if want_status and want_status != agent.status:
        changes.append(f"status: {agent.status} → {want_status}")
        session.add(AIAgentStatusHistory(agent_id=agent.id, from_status=agent.status, to_status=want_status,
                                         changed_by=user.username, note="정의 파일 반영"))
        agent.status = want_status

    # 버전 기록
    rel = man.spec.release or _Release()
    want_version = man.spec.version or agent.version
    if created or want_version != agent.version:
        if not created:
            changes.append(f"version: {agent.version} → {want_version}")
        exists = (await session.execute(select(AIAgentVersion.id).where(
            AIAgentVersion.agent_id == agent.id, AIAgentVersion.version == want_version))).first()
        if not exists:
            session.add(AIAgentVersion(agent_id=agent.id, version=want_version, source=rel.source,
                                       system_prompt=rel.system_prompt, changelog=rel.changelog,
                                       created_by=user.username))
        agent.version = want_version

    # 하위 항목 (키가 있을 때만)
    policy_changed = False
    if man.spec.tools is not None:
        policy_changed |= await _sync_tools(session, agent, man.spec.tools, changes)
    if man.spec.mcp_servers is not None:
        policy_changed |= await _sync_mcp(session, agent, man.spec.mcp_servers, changes)
    if man.spec.lineage is not None:
        await _sync_lineage(session, agent, man.spec.lineage, changes)

    # 관리 방식 (변경 여부와 무관한 기록 항목은 비교 대상에서 뺀다)
    if agent.managed_by != "manifest":
        if not created:
            changes.append(f"managed_by: {agent.managed_by} → manifest")
        agent.managed_by = "manifest"
    if source_ref and agent.source_ref != source_ref:
        if not created:
            changes.append(f"source_ref: {_short(agent.source_ref)} → {_short(source_ref)}")
        agent.source_ref = source_ref

    now = _dt.datetime.now(_dt.timezone.utc)
    if changes:
        agent.updated_by = user.username
        agent.last_applied_at = now
        agent.last_applied_by = user.username
        if policy_changed:
            agent.updated_at = now  # 정책 번들 policy_version 갱신(실행 계층 변경 감지)
    action = "created" if created else ("updated" if changes else "unchanged")
    logger.debug("정의 파일 반영 판단: %s → %s (변경 %d건)", name, action, len(changes))
    return {"name": name, "action": action, "changes": [] if created else changes, "agent_id": agent.id}


def _drop_empty(d: dict) -> dict:
    """None·빈 묶음을 뺀다(내보낸 파일을 짧게)."""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            v = _drop_empty(v)
            if not v:
                continue
        if v is None:
            continue
        out[k] = v
    return out


def _num(v: Any) -> Any:
    """Decimal → int(정수값) 또는 float — YAML 로 내보낼 수 있게."""
    from decimal import Decimal

    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


async def export_manifest(session: AsyncSession, name: str) -> dict | None:
    """에이전트의 현재 내용을 정의 파일 문서(dict)로. 없으면 None.

    내보낸 문서를 그대로 반영하면 '변경 없음'이 된다(왕복 보장). 필수 항목(owner·department·category)은 값이 없어도
    키를 남겨(null) 반영 전에 채워야 함을 드러낸다. 버전 기록의 지시문은 내보내지 않는다.
    """
    from app.mcp_servers.models import McpDeployment
    from app.models.models import RegisteredModel

    a = (await session.execute(select(AIAgent).where(AIAgent.name == name))).scalars().first()
    if a is None:
        return None
    registry_ref = None
    if a.base_model_ref:
        registry_ref = (await session.execute(
            select(RegisteredModel.name).where(RegisteredModel.id == a.base_model_ref)
        )).scalar_one_or_none()
    tools = (await session.execute(
        select(AIAgentTool).where(AIAgentTool.agent_id == a.id).order_by(AIAgentTool.id)
    )).scalars().all()
    mcps = (await session.execute(
        select(AIAgentMcpServer).where(AIAgentMcpServer.agent_id == a.id).order_by(AIAgentMcpServer.id)
    )).scalars().all()
    dep_names = {}
    dep_ids = [m.mcp_deployment_id for m in mcps if m.mcp_deployment_id]
    if dep_ids:
        dep_names = dict((await session.execute(
            select(McpDeployment.id, McpDeployment.name).where(McpDeployment.id.in_(dep_ids))
        )).all())
    lineage = (await session.execute(
        select(AIAgentLineage).where(AIAgentLineage.agent_id == a.id).order_by(AIAgentLineage.id)
    )).scalars().all()

    def mcp_doc(m: AIAgentMcpServer) -> dict:
        if m.mcp_deployment_id:
            dep = dep_names.get(m.mcp_deployment_id)
            return _drop_empty({"deployment": dep, "name": m.name if m.name != dep else None,
                                "description": m.description, "allowedGroups": m.allowed_groups,
                                "allowedTools": m.allowed_tools})
        return _drop_empty({"name": m.name, "url": m.url, "authMethod": m.auth_method, "description": m.description,
                            "allowedGroups": m.allowed_groups, "allowedTools": m.allowed_tools})

    spec = _drop_empty({
        "description": a.description,
        "version": a.version,
        "status": a.status,
        "model": {"name": a.base_model, "provider": a.model_provider, "registryRef": registry_ref},
        "runtime": {"framework": a.framework, "executionPolicy": a.execution_policy, "maxSteps": a.max_steps,
                    "memoryType": a.memory_type, "multiAgent": a.is_multi_agent or None, "subAgents": a.sub_agents},
        "interface": {"endpoint": a.endpoint, "protocol": a.protocol, "invocationMethod": a.invocation_method,
                      "streaming": a.streaming or None, "authMethod": a.auth_method},
        "governance": {"piiHandling": a.pii_handling, "dataResidency": a.data_residency,
                       "budgetLimit": _num(a.budget_limit), "hitlRequired": a.hitl_required or None,
                       "hitlConfig": a.hitl_config, "auditLogRef": a.audit_log_ref,
                       "networkAllowlist": a.network_allowlist, "dlpPolicies": a.dlp_policies,
                       "guardrails": a.guardrails},
        "capabilities": a.capabilities,
        "supportedLanguages": a.supported_languages,
        "useCases": a.use_cases,
        "limitations": a.limitations,
        "inputSchema": a.input_schema,
        "outputSchema": a.output_schema,
        "inferenceParams": a.inference_params,
        "ragConfig": a.rag_config,
    })
    # 하위 항목은 비어 있어도 키를 남긴다 — 반영 시 "이 목록이 전부"로 맞춰져야 왕복이 성립한다
    spec["tools"] = [_drop_empty({"name": t.name, "description": t.description, "risk": t.risk,
                                  "requiresApproval": t.requires_approval or None, "schema": t.tool_schema})
                     for t in tools]
    spec["mcpServers"] = [mcp_doc(m) for m in mcps]
    spec["lineage"] = [_drop_empty({"targetType": x.target_type, "targetRef": x.target_ref, "relation": x.relation,
                                    "description": x.description}) for x in lineage]
    metadata = {"name": a.name, "displayName": a.display_name, "owner": a.owner_email,
                "department": a.department, "category": a.category, "tags": a.tags}
    metadata = {k: v for k, v in metadata.items() if v is not None or k in ("owner", "department", "category")}
    return {"apiVersion": API_VERSION, "kind": "Agent", "metadata": metadata, "spec": spec}


def dump_yaml(doc: dict) -> str:
    """문서를 YAML 로(한글 그대로, 키 순서 유지, 블록 형식)."""
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False)


async def apply_manifests(
    session: AsyncSession, docs: list[Any], user, *, dry_run: bool = False, source_ref: str | None = None,
) -> dict:
    """정의 문서 목록을 반영한다. 에이전트마다 독립 트랜잭션 — 하나가 실패해도 나머지는 반영된다.

    ``dry_run`` 이면 같은 처리를 한 뒤 되돌려(rollback) '무엇이 바뀔지'만 돌려준다.
    """
    results: list[dict] = []
    seen: set[str] = set()
    touched: list[int] = []
    for i, doc in enumerate(docs, start=1):
        label = (doc.get("metadata") or {}).get("name") if isinstance(doc, dict) else None
        label = label if isinstance(label, str) and label else f"#{i}"
        try:
            if not isinstance(doc, dict):
                raise ValueError("문서가 객체 형식이 아닙니다.")
            man = AgentManifest.model_validate(doc)
            if man.metadata.name in seen:
                raise ValueError("같은 요청 안에 같은 이름의 에이전트가 두 번 이상 있습니다.")
            seen.add(man.metadata.name)
            res = await _apply_one(session, man, user, source_ref)
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
                if res["action"] != "unchanged":
                    touched.append(res["agent_id"])
            res.pop("agent_id", None)
            results.append(res)
        except ValidationError as e:
            await session.rollback()
            msgs = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
            logger.debug("정의 파일 형식 오류: %s (%d건)", label, len(msgs))
            results.append({"name": label, "action": "failed", "error": "; ".join(msgs)})
        except PermissionError as e:
            await session.rollback()
            logger.warning("정의 파일 반영 권한 없음: %s (요청자 %s) — %s", label, user.username, e)
            results.append({"name": label, "action": "failed", "error": str(e)})
        except ValueError as e:
            await session.rollback()
            logger.debug("정의 파일 반영 실패(값 오류): %s — %s", label, e)
            results.append({"name": label, "action": "failed", "error": str(e)})
        except Exception as e:  # DB 오류 등 — 이 에이전트만 실패 처리
            await session.rollback()
            logger.exception("정의 파일 반영 실패: %s", label)
            results.append({"name": label, "action": "failed", "error": f"내부 오류: {type(e).__name__}"})

    summary = {k: sum(1 for r in results if r["action"] == k) for k in ("created", "updated", "unchanged", "failed")}
    logger.info("정의 파일 반영%s: %s (요청자 %s, source=%s)", "(미리 보기)" if dry_run else "", summary,
                user.username, source_ref)

    if touched:
        from app.embedding.service import embed_entity_background

        for aid in touched:
            await embed_entity_background("ai_agent", aid)
    return {"dry_run": dry_run, "summary": summary, "results": results}


async def prune_manifests(
    session: AsyncSession, source_prefix: str, keep: list[str], user, *, dry_run: bool = False,
) -> dict:
    """정의 파일에서 빠진 에이전트를 ``deprecated`` 로 바꾼다(삭제하지 않음).

    대상: ``managed_by='manifest'`` 이고 ``source_ref`` 가 ``source_prefix`` 로 시작하며 ``keep`` 에 없는 에이전트 중
    아직 ``deprecated``·``retired`` 가 아닌 것. 권한이 없는 에이전트는 건너뛰고 결과에 남긴다.
    """
    if not source_prefix or not source_prefix.strip():
        raise ManifestError("source_prefix 는 비어 있을 수 없습니다(전체 에이전트를 정리하는 사고 방지).")
    keep_set = set(keep)
    rows = (await session.execute(
        select(AIAgent).where(AIAgent.managed_by == "manifest", AIAgent.source_ref.startswith(source_prefix))
        .order_by(AIAgent.name)
    )).scalars().all()
    results = []
    for a in rows:
        if a.name in keep_set or a.status in (AgentStatus.DEPRECATED.value, AgentStatus.RETIRED.value):
            continue
        if not _can_edit(a, user):
            logger.warning("정의 파일 정리 권한 없음 — 건너뜀: %s (요청자 %s)", a.name, user.username)
            results.append({"name": a.name, "action": "skipped", "error": "권한 없음"})
            continue
        results.append({"name": a.name, "action": "deprecated", "from_status": a.status})
        if not dry_run:
            session.add(AIAgentStatusHistory(agent_id=a.id, from_status=a.status, to_status="deprecated",
                                             changed_by=user.username, note="정의 파일에서 제외됨"))
            a.status = AgentStatus.DEPRECATED.value
            a.last_applied_at = _dt.datetime.now(_dt.timezone.utc)
            a.last_applied_by = user.username
    if dry_run:
        await session.rollback()
    else:
        await session.commit()
    logger.info("정의 파일 정리%s: prefix=%s 사용 중단 %d건", "(미리 보기)" if dry_run else "", source_prefix,
                sum(1 for r in results if r["action"] == "deprecated"))
    return {"dry_run": dry_run, "results": results}
