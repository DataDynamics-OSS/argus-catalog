# SPDX-License-Identifier: Apache-2.0
"""MCP Server 카탈로그 요청/응답 스키마.

필드 출처(MCP 표준 / Registry / 서버 설정 / Argus 전용)는 ``origins.py`` 참고.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["draft", "staging", "active", "blocked", "deprecated", "retired"]
Risk = Literal["low", "medium", "high", "critical"]
Transport = Literal["stdio", "streamable_http", "sse"]
Environment = Literal["DEV", "STG", "PROD"]
ProviderType = Literal["internal", "vendor", "community"]
Framework = Literal["argus-forge", "generic"]
AuthMethod = Literal["none", "bearer", "api_key", "basic", "oauth2"]
DeploymentScope = Literal["shared", "external", "local"]
PolicyMode = Literal["observe", "enforce"]
ScopeAction = Literal["read", "write", "delete", "any"]
ScopeEffect = Literal["allow", "deny", "protect", "mask"]

# 서버·배포 이름 규칙 — 소문자·숫자로 시작, 이후 소문자·숫자·. _ - (URN·URL 경로에 그대로 쓰임)
_NAME = r"^[a-z0-9][a-z0-9._-]*$"


# ---------------------------------------------------------------------------
# 서버 정의
# ---------------------------------------------------------------------------


class McpServerBase(BaseModel):
    """서버 정의 생성·수정 공통 필드(모두 선택)."""

    display_name: str | None = None
    description: str | None = None
    status: Status | None = None
    version: str | None = Field(None, max_length=50)
    owner_email: str | None = None
    department: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    provider_type: ProviderType | None = None
    vendor: str | None = None
    framework: Framework | None = None
    target_platform: str | None = None
    source_repo: str | None = None
    source_path: str | None = None
    registry_name: str | None = None
    title: str | None = None
    website_url: str | None = None
    repository_url: str | None = None
    license: str | None = None
    packages: list[dict[str, Any]] | None = None
    remotes: list[dict[str, Any]] | None = None
    package_checksum: str | None = None
    oci_artifact_ref: str | None = None
    supported_transports: list[Transport] | None = None
    launch_config: dict[str, Any] | None = None


class McpServerCreate(McpServerBase):
    name: str = Field(..., min_length=1, max_length=255, pattern=_NAME)


class McpServerUpdate(McpServerBase):
    # 상태 변경 시 이력(catalog_mcp_status_history.note)에 남길 메모
    status_note: str | None = None


class McpServerSummary(BaseModel):
    id: int
    name: str
    urn: str
    display_name: str | None = None
    title: str | None = None
    description: str | None = None
    status: str
    version: str
    owner_email: str | None = None
    department: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    provider_type: str
    vendor: str | None = None
    framework: str
    target_platform: str | None = None
    registry_name: str | None = None
    supported_transports: list[str] | None = None
    last_synced_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: str | None = None
    # 집계
    deployment_count: int = 0
    tool_count: int = 0
    mutating_tool_count: int = 0

    model_config = {"from_attributes": True}


class ToolGroupResponse(BaseModel):
    id: int
    key: str
    label: str | None = None
    description: str | None = None
    config_key: str | None = None
    default_enabled: bool
    default_risk: str
    mutating: bool
    sort_order: int = 0
    tool_count: int = 0

    model_config = {"from_attributes": True}


class ToolResponse(BaseModel):
    id: int
    name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    group_key: str | None = None
    mutating: bool
    risk: str | None = None
    risk_confirmed: bool = False
    requires_approval: bool = False
    resource_types: list[str] | None = None
    notes: str | None = None
    source: str
    removed_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ToolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    group_key: str | None = None
    risk: Risk | None = None
    requires_approval: bool = False
    resource_types: list[str] | None = None


class ToolUpdate(BaseModel):
    group_key: str | None = None
    risk: Risk | None = None
    risk_confirmed: bool | None = None
    requires_approval: bool | None = None
    resource_types: list[str] | None = None
    notes: str | None = None


class ResourceResponse(BaseModel):
    id: int
    uri: str
    is_template: bool
    name: str | None = None
    title: str | None = None
    description: str | None = None
    mime_type: str | None = None
    source: str
    removed_at: datetime | None = None

    model_config = {"from_attributes": True}


class ResourceCreate(BaseModel):
    uri: str = Field(..., min_length=1, max_length=1000)
    is_template: bool = False
    name: str | None = None
    description: str | None = None
    mime_type: str | None = None


class PromptResponse(BaseModel):
    id: int
    name: str
    title: str | None = None
    description: str | None = None
    arguments: list[dict[str, Any]] | None = None
    source: str
    removed_at: datetime | None = None

    model_config = {"from_attributes": True}


class PromptCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    description: str | None = None
    arguments: list[dict[str, Any]] | None = None


class McpServerDetail(McpServerSummary):
    website_url: str | None = None
    repository_url: str | None = None
    license: str | None = None
    packages: list[dict[str, Any]] | None = None
    remotes: list[dict[str, Any]] | None = None
    source_repo: str | None = None
    source_path: str | None = None
    package_checksum: str | None = None
    oci_artifact_ref: str | None = None
    launch_config: dict[str, Any] | None = None
    server_info: dict[str, Any] | None = None
    protocol_version: str | None = None
    capabilities: dict[str, Any] | None = None
    instructions: str | None = None
    capability_hash: str | None = None
    updated_by: str | None = None
    groups: list[ToolGroupResponse] = []
    tools: list[ToolResponse] = []
    resources: list[ResourceResponse] = []
    prompts: list[PromptResponse] = []
    deployments: list[DeploymentSummary] = []


class PaginatedMcpServers(BaseModel):
    items: list[McpServerSummary]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# 배포
# ---------------------------------------------------------------------------


class DeploymentBase(BaseModel):
    """배포 생성·수정 공통 필드(모두 선택)."""

    display_name: str | None = None
    description: str | None = None
    environment: Environment | None = None
    status: Status | None = None
    owner_email: str | None = None
    department: str | None = None
    tags: list[str] | None = None
    transport: Transport | None = None
    endpoint: str | None = Field(None, max_length=1000)
    rest_api_base: str | None = Field(None, max_length=1000)
    auth_method: AuthMethod | None = None
    auth_config: dict[str, Any] | None = None
    deployment_scope: DeploymentScope | None = None
    host: str | None = None
    target_endpoint: str | None = None
    datasource_id: int | None = None
    service_principal: str | None = None
    principal_auth: str | None = None
    policy_mode: PolicyMode | None = None
    # 주기 수집 간격(분). 0/None = 주기 수집 끔
    collect_interval_minutes: int | None = Field(None, ge=0)


class DeploymentCreate(DeploymentBase):
    name: str = Field(..., min_length=1, max_length=255, pattern=_NAME)
    server_name: str


class DeploymentUpdate(DeploymentBase):
    status_note: str | None = None
    audit_retention_days: int | None = Field(None, ge=1, le=3650)


class DeploymentSummary(BaseModel):
    id: int
    name: str
    urn: str
    server_name: str | None = None
    display_name: str | None = None
    description: str | None = None
    environment: str
    status: str
    owner_email: str | None = None
    department: str | None = None
    tags: list[str] | None = None
    transport: str
    endpoint: str | None = None
    auth_method: str
    deployment_scope: str
    target_endpoint: str | None = None
    service_principal: str | None = None
    policy_mode: str
    health_status: str
    last_health_check_at: datetime | None = None
    last_observed_at: datetime | None = None
    observed_source: str | None = None
    deployed_version: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: str | None = None
    policy_version: str | None = None
    # 집계
    enabled_mutating_groups: list[str] = []
    exposed_tool_count: int = 0
    max_exposed_risk: str | None = None
    warning_count: int = 0
    agent_count: int = 0
    open_finding_count: int = 0
    max_finding_severity: str | None = None

    model_config = {"from_attributes": True}


class DeploymentDetail(DeploymentSummary):
    rest_api_base: str | None = None
    auth_config: dict[str, Any] | None = None
    host: str | None = None
    datasource_id: int | None = None
    datasource_name: str | None = None
    principal_auth: str | None = None
    limits: dict[str, Any] | None = None
    security_warnings: list[str] | None = None
    server_info: dict[str, Any] | None = None
    protocol_version: str | None = None
    collect_interval_minutes: int | None = None
    latency_ms: int | None = None
    instance_id: str | None = None
    observed_hash: str | None = None
    server_framework: str | None = None
    has_credential: bool = False
    updated_by: str | None = None
    # Phase 2
    approved_limits: dict[str, Any] | None = None
    approved_version: str | None = None
    policy_updated_at: datetime | None = None
    policy_updated_by: str | None = None
    audit_retention_days: int = 180
    health_fail_count: int = 0
    has_ingest_token: bool = False
    ingest_token_hint: str | None = None
    ingest_token_created_at: datetime | None = None


class PaginatedDeployments(BaseModel):
    items: list[DeploymentSummary]
    total: int
    page: int
    page_size: int


class CredentialPut(BaseModel):
    """배포 접속 자격증명 저장 요청 — ``secret`` 은 Fernet 으로 암호화해 저장하고 응답에는 마스킹만 준다."""

    kind: Literal["bearer", "api_key", "basic"] = "bearer"
    secret: str = Field(..., min_length=1)
    header_name: str | None = None
    username: str | None = None


class CredentialResponse(BaseModel):
    kind: str
    header_name: str | None = None
    username: str | None = None
    masked: str
    updated_at: datetime | None = None
    updated_by: str | None = None


class ResourceScopeResponse(BaseModel):
    id: int
    layer: str
    agent_link_id: int | None = None
    resource_type: str
    action: str
    effect: str
    patterns: list[str]
    match: str
    config_key: str | None = None
    description: str | None = None
    source: str

    model_config = {"from_attributes": True}


class ResourceScopeCreate(BaseModel):
    resource_type: str = Field(..., min_length=1, max_length=50)
    action: ScopeAction = "any"
    effect: ScopeEffect
    patterns: list[str] = Field(..., min_length=1)
    match: Literal["fnmatch", "regex", "exact"] = "fnmatch"
    description: str | None = None


class EffectiveTool(BaseModel):
    id: int
    name: str
    group_key: str | None = None
    risk: str | None = None
    mutating: bool
    exposed: bool | None = None
    blocked: bool = False
    requires_approval: bool = False
    resource_types: list[str] | None = None
    source: str


class EffectiveGroup(BaseModel):
    id: int
    key: str
    label: str | None = None
    config_key: str | None = None
    risk: str
    mutating: bool
    observed_enabled: bool | None = None
    observed_basis: str | None = None
    approved_enabled: bool | None = None
    tool_count: int = 0
    exposed_count: int = 0


class EffectiveState(BaseModel):
    deployment: str
    server: str
    framework: str
    observed_source: str | None = None
    last_observed_at: datetime | None = None
    target_endpoint: str | None = None
    service_principal: str | None = None
    principal_auth: str | None = None
    limits: dict[str, Any] | None = None
    security_warnings: list[str] = []
    groups: list[EffectiveGroup] = []
    tools: list[EffectiveTool] = []
    scopes: list[ResourceScopeResponse] = []


class SimulateRequest(BaseModel):
    resource_type: str
    resource: str
    action: ScopeAction = "read"
    # observed: 서버 설정 규칙만 / approved: 승인 정책만 / combined: 둘 다 적용(더 좁은 쪽)
    layer: Literal["observed", "approved", "combined"] = "combined"
    # combined 에서 이 에이전트 연결(catalog_ai_agent_mcp_servers.id)의 범위까지 적용 (Phase 3)
    agent_link_id: int | None = None


class SimulateResult(BaseModel):
    decision: Literal["allow", "deny", "unknown"]
    reason: str
    matched: list[ResourceScopeResponse] = []
    # combined 일 때 계층별 판정 {"observed": {...}, "approved": {...}, "agent": {...}}
    by_layer: dict[str, dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# 수집 / 가져오기 / 이력
# ---------------------------------------------------------------------------


class CollectRequest(BaseModel):
    # auto: Forge 면 manifest → (없으면) 도구 REST, 아니면 MCP 프로토콜
    source: Literal["auto", "mcp", "forge_rest", "manifest"] = "auto"
    dry_run: bool = False


class CollectResult(BaseModel):
    ok: bool
    source: str | None = None
    message: str | None = None
    dry_run: bool = False
    diff: dict[str, Any] | None = None
    tool_count: int = 0
    exposed_count: int = 0


class ConfigImportRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2_000_000)
    dry_run: bool = False
    # 설정의 mcp.host/port 로 endpoint 가 비어 있을 때 채울지
    fill_endpoint: bool = False


class ImportRequest(BaseModel):
    format: Literal["server_json", "mcp_json"]
    content: Any
    dry_run: bool = False
    environment: Environment = "DEV"


class ImportItem(BaseModel):
    kind: Literal["server", "deployment"]
    name: str
    action: Literal["created", "exists", "updated", "error"]
    message: str | None = None


class ImportResult(BaseModel):
    dry_run: bool
    items: list[ImportItem]


class ForgeSeedResult(BaseModel):
    servers: list[ImportItem]


class SyncLogResponse(BaseModel):
    id: int
    deployment_id: int | None = None
    kind: str
    source: str | None = None
    ok: bool
    message: str | None = None
    diff: dict[str, Any] | None = None
    duration_ms: int | None = None
    triggered_by: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class StatusHistoryResponse(BaseModel):
    id: int
    from_status: str | None = None
    to_status: str
    note: str | None = None
    changed_by: str | None = None
    changed_at: datetime | None = None

    model_config = {"from_attributes": True}


class DeploymentAgentLink(BaseModel):
    link_id: int
    agent_name: str
    agent_display_name: str | None = None
    agent_status: str | None = None
    allowed_groups: list[str] | None = None
    allowed_tools: list[str] | None = None
    effective_tool_count: int = 0
    agent_urn: str | None = None
    # 에이전트별 리소스 범위 — 인스턴스 범위 위에 추가 제한 (정책 번들 agents.*.resource_scopes)
    resource_scopes: list[ResourceScopeResponse] = []


class AgentScopesUpdate(BaseModel):
    """에이전트별 리소스 범위 — 목록 전체 교체."""

    scopes: list[ResourceScopeCreate] = []


class NameCount(BaseModel):
    name: str
    count: int


class McpStats(BaseModel):
    server_count: int
    deployment_count: int
    tool_count: int
    unregistered_agent_mcp_count: int
    deployments_with_mutating: int
    deployments_with_warnings: int
    servers_by_status: list[NameCount]
    servers_by_provider: list[NameCount]
    deployments_by_environment: list[NameCount]
    deployments_by_health: list[NameCount]


class UnregisteredMcp(BaseModel):
    name: str
    url: str | None = None
    auth_method: str | None = None
    agent_count: int
    agents: list[str]
    link_ids: list[int]


# ---------------------------------------------------------------------------
# Phase 2 — 승인 정책
# ---------------------------------------------------------------------------


class PolicyGroup(BaseModel):
    key: str
    label: str | None = None
    config_key: str | None = None
    mutating: bool
    risk: str
    observed_enabled: bool | None = None
    observed_basis: str | None = None
    approved_enabled: bool | None = None


class PolicyTool(BaseModel):
    id: int
    name: str
    group_key: str | None = None
    risk: str | None = None
    risk_confirmed: bool = False
    mutating: bool
    observed_exposed: bool | None = None
    blocked: bool = False
    requires_approval: bool | None = None
    tool_requires_approval: bool = False


class DeploymentPolicy(BaseModel):
    deployment: str
    policy_mode: str
    policy_version: str | None = None
    policy_updated_at: datetime | None = None
    policy_updated_by: str | None = None
    approved_version: str | None = None
    deployed_version: str | None = None
    approved_limits: dict[str, Any] | None = None
    observed_limits: dict[str, Any] | None = None
    groups: list[PolicyGroup] = []
    tools: list[PolicyTool] = []
    approved_scopes: list[ResourceScopeResponse] = []
    observed_scopes: list[ResourceScopeResponse] = []
    # --- Phase 3: 정책 번들 집행 상태 ---
    has_service_token: bool = False
    bundle_etag: str | None = None
    policy_pulled_at: datetime | None = None
    policy_pulled_version: str | None = None
    policy_pull_instance: str | None = None
    # never / stale / outdated / applied
    pull_state: str | None = None
    agent_count: int = 0


class PolicyToolIn(BaseModel):
    blocked: bool = False
    # None = 도구 정의 값을 따름
    requires_approval: bool | None = None


class PolicyUpdate(BaseModel):
    """승인 정책 부분 저장 — 전달한 항목만 바꾼다.

    groups: {그룹키: true/false/null(미승인)} / tools: {도구명: {...}} 은 **전달한 키만** 갱신,
    scopes 는 승인 계층 규칙 **전체 교체**, limits 는 전체 교체(null = 제한 없음).
    """

    groups: dict[str, bool | None] | None = None
    tools: dict[str, PolicyToolIn] | None = None
    scopes: list[ResourceScopeCreate] | None = None
    limits: dict[str, Any] | None = None
    approved_version: str | None = None
    policy_mode: PolicyMode | None = None
    note: str | None = Field(None, max_length=2000)


class AdoptObservedRequest(BaseModel):
    """관측값을 승인 정책 기준선으로 채택."""

    groups: bool = True
    scopes: bool = True
    limits: bool = True
    version: bool = True
    note: str | None = None


class PolicyHistoryResponse(BaseModel):
    id: int
    policy_version: str
    snapshot: dict[str, Any]
    note: str | None = None
    changed_by: str | None = None
    changed_at: datetime | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Phase 2 — finding
# ---------------------------------------------------------------------------


FindingStatus = Literal["open", "acknowledged", "resolved"]


class FindingResponse(BaseModel):
    id: int
    deployment_name: str | None = None
    server_name: str | None = None
    environment: str | None = None
    kind: str
    subject: str | None = None
    severity: str
    title: str
    detail: dict[str, Any] | None = None
    status: str
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    resolved_at: datetime | None = None
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    ack_note: str | None = None
    # 마지막으로 발행된 알림 (argus_lineage_alert.id)
    alert_id: int | None = None

    model_config = {"from_attributes": True}


class FindingUpdate(BaseModel):
    # acknowledged: 확인(알고 있음) / open: 확인 취소
    status: Literal["open", "acknowledged"]
    note: str | None = Field(None, max_length=2000)


class PaginatedFindings(BaseModel):
    items: list[FindingResponse]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Phase 2 — manifest / 감사 / 버전
# ---------------------------------------------------------------------------


class ManifestImportRequest(BaseModel):
    # manifest JSON (객체 또는 문자열)
    content: Any
    dry_run: bool = False


class AuditIngestRequest(BaseModel):
    """감사 이벤트 — ``events`` (JSON 배열) 또는 ``jsonl`` (Forge Audit 파일 내용)."""

    events: list[dict[str, Any]] | None = None
    jsonl: str | None = Field(None, max_length=20_000_000)


class AuditIngestResult(BaseModel):
    received: int
    inserted: int
    duplicates: int
    invalid: int
    violations: int
    purged: int = 0


class AuditEventResponse(BaseModel):
    id: int
    deployment_name: str | None = None
    ts: datetime
    session_id: str | None = None
    tool_name: str
    group_key: str | None = None
    risk: str | None = None
    user_name: str | None = None
    args: dict[str, Any] | None = None
    ok: bool
    error: str | None = None
    result_id: str | None = None
    resources: list[dict[str, Any]] | None = None
    agent_name: str | None = None
    # 서버가 enforce 로 거부했으면 "denied"
    server_decision: str | None = None
    policy_decision: str | None = None
    policy_reason: str | None = None
    received_via: str | None = None
    received_at: datetime | None = None

    model_config = {"from_attributes": True}


class PaginatedAuditEvents(BaseModel):
    items: list[AuditEventResponse]
    total: int
    page: int
    page_size: int


class IngestTokenResponse(BaseModel):
    # 발급 직후에만 원문을 돌려준다
    token: str | None = None
    hint: str | None = None
    created_at: datetime | None = None
    ingest_url: str | None = None


class ServerVersionResponse(BaseModel):
    id: int
    capability_hash: str
    reported_version: str | None = None
    tool_count: int
    diff: dict[str, Any] | None = None
    source: str | None = None
    deployment_name: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None


class ServerVersionDetail(ServerVersionResponse):
    snapshot: dict[str, Any]


# ---------------------------------------------------------------------------
# Phase 2 — 통제 현황
# ---------------------------------------------------------------------------


class ControlCell(BaseModel):
    deployment: str
    display_name: str | None = None
    status: str
    health_status: str
    policy_mode: str
    policy_set: bool
    enabled_mutating_groups: list[str] = []
    open_findings: int = 0
    max_finding_severity: str | None = None


class ControlRow(BaseModel):
    server: str
    server_display_name: str | None = None
    target_platform: str | None = None
    cells: dict[str, list[ControlCell]]


class RiskyDeployment(BaseModel):
    deployment: str
    server_name: str | None = None
    environment: str
    high_risk_exposed: int
    exposed_tool_count: int
    max_exposed_risk: str | None = None
    enabled_mutating_groups: list[str] = []
    open_findings: int = 0
    policy_set: bool = False


class TrendPoint(BaseModel):
    date: str
    opened: int = 0
    resolved: int = 0


class PolicyCoverage(BaseModel):
    deployments: int
    with_policy: int
    enforce: int
    # enforce 배포 중 최신 번들을 받아간 배포 수
    enforce_applied: int = 0
    with_ingest_token: int


class ControlOverview(BaseModel):
    environments: list[str]
    matrix: list[ControlRow]
    risky: list[RiskyDeployment]
    findings_by_kind: list[NameCount]
    findings_by_severity: list[NameCount]
    trend: list[TrendPoint]
    recent_calls: list[AuditEventResponse]
    coverage: PolicyCoverage


McpServerDetail.model_rebuild()
