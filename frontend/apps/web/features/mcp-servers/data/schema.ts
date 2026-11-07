/**
 * MCP Server 카탈로그 — 응답 타입 · 표시 상수 · 필드 출처(origin) 맵.
 *
 * 필드 출처는 백엔드 `app/mcp_servers/origins.py` 와 동일하다
 * (`GET /api/v1/mcp-servers/field-origins` 로도 조회 가능).
 *   - mcp           : MCP 서버가 프로토콜로 보고한 값 (initialize, tools/list ...)
 *   - registry      : MCP Registry server.json 표준 메타데이터
 *   - server_config : 서버 설정에서 수집한 값 (Forge config.yaml 등, MCP 표준 밖)
 *   - argus         : Argus Catalog 에서만 관리하는 정보 → UI 에서 "Argus" 배지로 표시
 */

export type Origin = "mcp" | "registry" | "server_config" | "argus"

export const ORIGIN_LABELS: Record<Origin, string> = {
  mcp: "MCP 서버 보고",
  registry: "MCP Registry 표준",
  server_config: "서버 설정 수집",
  argus: "Argus 전용",
}

export const ORIGIN_DESCRIPTIONS: Record<Origin, string> = {
  mcp: "MCP 서버가 프로토콜(initialize·tools/list 등)로 보고한 값입니다. 서버가 바뀌면 재수집 시 갱신됩니다.",
  registry: "MCP Registry server.json 표준 메타데이터입니다. 다른 MCP 카탈로그와 그대로 호환됩니다.",
  server_config: "서버 설정(예: Argus Forge config.yaml)에서 수집한 값입니다. MCP 표준에는 없습니다.",
  argus: "Argus Catalog 에서만 관리하는 정보입니다. MCP 서버에는 존재하지 않으며, server.json 내보내기 시 _meta[\"io.datadynamics.argus/catalog\"] 아래에만 포함됩니다.",
}

export const ARGUS_META_NAMESPACE = "io.datadynamics.argus/catalog"

// 행 단위 source → 출처
export const ROW_SOURCE_ORIGINS: Record<string, Origin> = {
  mcp: "mcp",
  forge_rest: "mcp",
  registry: "registry",
  config: "server_config",
  manifest: "server_config",
  forge_seed: "argus",
  manual: "argus",
  adopted: "argus",
}

export const ROW_SOURCE_LABELS: Record<string, string> = {
  mcp: "MCP 수집",
  forge_rest: "Forge REST 수집",
  registry: "Registry 가져오기",
  config: "설정 가져오기",
  manifest: "manifest",
  forge_seed: "Forge 기본 정의",
  manual: "수동 등록",
  adopted: "관측값 채택",
}

// ---------------------------------------------------------------------------
// 표시 상수
// ---------------------------------------------------------------------------

export const STATUS_OPTIONS = ["draft", "staging", "active", "blocked", "deprecated", "retired"] as const
export const STATUS_LABEL: Record<string, string> = {
  draft: "초안", staging: "스테이징", active: "활성",
  blocked: "차단", deprecated: "사용 중단", retired: "폐기",
}
export const STATUS_VARIANTS: Record<string, string> = {
  draft: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  staging: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  blocked: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  deprecated: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
  retired: "bg-zinc-200 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
}

export const RISK_OPTIONS = ["low", "medium", "high", "critical"] as const
export const RISK_LABEL: Record<string, string> = {
  low: "낮음", medium: "중간", high: "높음", critical: "치명",
}
export const RISK_VARIANTS: Record<string, string> = {
  low: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  medium: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  high: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
  critical: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
}

export const HEALTH_VARIANTS: Record<string, string> = {
  healthy: "bg-emerald-500",
  degraded: "bg-amber-500",
  down: "bg-red-500",
  unknown: "bg-zinc-400",
}
export const HEALTH_LABEL: Record<string, string> = {
  healthy: "정상", degraded: "저하", down: "중단", unknown: "미확인",
}

export const ENVIRONMENTS = ["DEV", "STG", "PROD"] as const
export const TRANSPORTS = ["streamable_http", "sse", "stdio"] as const
export const AUTH_METHODS = ["none", "bearer", "api_key", "basic", "oauth2"] as const
export const PROVIDER_TYPES = ["internal", "vendor", "community"] as const
export const PROVIDER_LABEL: Record<string, string> = {
  internal: "사내", vendor: "벤더", community: "커뮤니티",
}
export const FRAMEWORK_LABEL: Record<string, string> = {
  "argus-forge": "Argus Forge", generic: "일반 MCP",
}

export const SCOPE_EFFECT_VARIANTS: Record<string, string> = {
  allow: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  deny: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  protect: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
  mask: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
}

// ---------------------------------------------------------------------------
// 서버 정의
// ---------------------------------------------------------------------------

export type McpServerSummary = {
  id: number
  name: string
  urn: string
  display_name: string | null
  title: string | null
  description: string | null
  status: string
  version: string
  owner_email: string | null
  department: string | null
  category: string | null
  tags: string[] | null
  provider_type: string
  vendor: string | null
  framework: string
  target_platform: string | null
  registry_name: string | null
  supported_transports: string[] | null
  last_synced_at: string | null
  created_at: string | null
  updated_at: string | null
  created_by: string | null
  deployment_count: number
  tool_count: number
  mutating_tool_count: number
}

export type McpToolGroup = {
  id: number
  key: string
  label: string | null
  description: string | null
  config_key: string | null
  default_enabled: boolean
  default_risk: string
  mutating: boolean
  sort_order: number
  tool_count: number
}

export type McpTool = {
  id: number
  name: string
  title: string | null
  description: string | null
  input_schema: Record<string, unknown> | null
  output_schema: Record<string, unknown> | null
  annotations: Record<string, unknown> | null
  meta: Record<string, unknown> | null
  group_key: string | null
  mutating: boolean
  risk: string | null
  risk_confirmed: boolean
  requires_approval: boolean
  resource_types: string[] | null
  notes: string | null
  source: string
  removed_at: string | null
  updated_at: string | null
}

export type McpResource = {
  id: number
  uri: string
  is_template: boolean
  name: string | null
  title: string | null
  description: string | null
  mime_type: string | null
  source: string
  removed_at: string | null
}

export type McpPrompt = {
  id: number
  name: string
  title: string | null
  description: string | null
  arguments: Record<string, unknown>[] | null
  source: string
  removed_at: string | null
}

export type McpServerDetail = McpServerSummary & {
  website_url: string | null
  repository_url: string | null
  license: string | null
  packages: Record<string, unknown>[] | null
  remotes: Record<string, unknown>[] | null
  source_repo: string | null
  source_path: string | null
  package_checksum: string | null
  oci_artifact_ref: string | null
  launch_config: Record<string, unknown> | null
  server_info: Record<string, unknown> | null
  protocol_version: string | null
  capabilities: Record<string, unknown> | null
  instructions: string | null
  capability_hash: string | null
  updated_by: string | null
  groups: McpToolGroup[]
  tools: McpTool[]
  resources: McpResource[]
  prompts: McpPrompt[]
  deployments: McpDeploymentSummary[]
}

// ---------------------------------------------------------------------------
// 배포
// ---------------------------------------------------------------------------

export type McpDeploymentSummary = {
  id: number
  name: string
  urn: string
  server_name: string | null
  display_name: string | null
  description: string | null
  environment: string
  status: string
  owner_email: string | null
  department: string | null
  tags: string[] | null
  transport: string
  endpoint: string | null
  auth_method: string
  deployment_scope: string
  target_endpoint: string | null
  service_principal: string | null
  policy_mode: string
  health_status: string
  last_health_check_at: string | null
  last_observed_at: string | null
  observed_source: string | null
  deployed_version: string | null
  created_at: string | null
  updated_at: string | null
  created_by: string | null
  enabled_mutating_groups: string[]
  exposed_tool_count: number
  max_exposed_risk: string | null
  warning_count: number
  agent_count: number
  policy_version: string | null
  open_finding_count: number
  max_finding_severity: string | null
}

export type McpDeploymentDetail = McpDeploymentSummary & {
  rest_api_base: string | null
  auth_config: Record<string, unknown> | null
  host: string | null
  datasource_id: number | null
  datasource_name: string | null
  principal_auth: string | null
  limits: Record<string, unknown> | null
  security_warnings: string[] | null
  server_info: Record<string, unknown> | null
  protocol_version: string | null
  collect_interval_minutes: number | null
  latency_ms: number | null
  instance_id: string | null
  observed_hash: string | null
  server_framework: string | null
  has_credential: boolean
  updated_by: string | null
  // Phase 2
  approved_limits: Record<string, unknown> | null
  approved_version: string | null
  policy_updated_at: string | null
  policy_updated_by: string | null
  audit_retention_days: number
  health_fail_count: number
  has_ingest_token: boolean
  ingest_token_hint: string | null
  ingest_token_created_at: string | null
}

export type McpCredential = {
  kind: string
  header_name: string | null
  username: string | null
  masked: string
  updated_at: string | null
  updated_by: string | null
}

export type McpResourceScope = {
  id: number
  layer: string
  agent_link_id: number | null
  resource_type: string
  action: string
  effect: string
  patterns: string[]
  match: string
  config_key: string | null
  description: string | null
  source: string
}

export type McpEffectiveTool = {
  id: number
  name: string
  group_key: string | null
  risk: string | null
  mutating: boolean
  exposed: boolean | null
  blocked: boolean
  requires_approval: boolean
  resource_types: string[] | null
  source: string
}

export type McpEffectiveGroup = {
  id: number
  key: string
  label: string | null
  config_key: string | null
  risk: string
  mutating: boolean
  observed_enabled: boolean | null
  observed_basis: string | null
  approved_enabled: boolean | null
  tool_count: number
  exposed_count: number
}

export type McpEffectiveState = {
  deployment: string
  server: string
  framework: string
  observed_source: string | null
  last_observed_at: string | null
  target_endpoint: string | null
  service_principal: string | null
  principal_auth: string | null
  limits: Record<string, unknown> | null
  security_warnings: string[]
  groups: McpEffectiveGroup[]
  tools: McpEffectiveTool[]
  scopes: McpResourceScope[]
}

export type McpSimulateLayer = "observed" | "approved" | "combined"

export type McpSimulateResult = {
  decision: "allow" | "deny" | "unknown"
  reason: string
  matched: McpResourceScope[]
  by_layer: (Record<"observed" | "approved", { decision: string; reason: string }> & {
    agent?: { decision: string; reason: string }
  }) | null
}

export type McpCollectResult = {
  ok: boolean
  source: string | null
  message: string | null
  dry_run: boolean
  diff: Record<string, unknown> | null
  tool_count: number
  exposed_count: number
}

export type McpImportItem = {
  kind: "server" | "deployment"
  name: string
  action: "created" | "exists" | "updated" | "error"
  message: string | null
}

export type McpImportResult = { dry_run: boolean; items: McpImportItem[] }

export type McpSyncLog = {
  id: number
  deployment_id: number | null
  kind: string
  source: string | null
  ok: boolean
  message: string | null
  diff: Record<string, unknown> | null
  duration_ms: number | null
  triggered_by: string | null
  created_at: string | null
}

export type McpStatusHistory = {
  id: number
  from_status: string | null
  to_status: string
  note: string | null
  changed_by: string | null
  changed_at: string | null
}

export type McpDeploymentAgent = {
  link_id: number
  agent_name: string
  agent_display_name: string | null
  agent_status: string | null
  allowed_groups: string[] | null
  allowed_tools: string[] | null
  effective_tool_count: number
  agent_urn: string | null
  /** 에이전트별 리소스 범위 — 인스턴스 범위 위에 추가 제한 (정책 번들 agents.*.resource_scopes) */
  resource_scopes: McpResourceScope[]
}

export type McpStats = {
  server_count: number
  deployment_count: number
  tool_count: number
  unregistered_agent_mcp_count: number
  deployments_with_mutating: number
  deployments_with_warnings: number
  servers_by_status: { name: string; count: number }[]
  servers_by_provider: { name: string; count: number }[]
  deployments_by_environment: { name: string; count: number }[]
  deployments_by_health: { name: string; count: number }[]
}

export type McpUnregistered = {
  name: string
  url: string | null
  auth_method: string | null
  agent_count: number
  agents: string[]
  link_ids: number[]
}

export type Paginated<T> = { items: T[]; total: number; page: number; page_size: number }

// ---------------------------------------------------------------------------
// Phase 2 — 승인 정책 · finding · 감사 · 버전 · 통제 현황
// ---------------------------------------------------------------------------

export const POLICY_MODE_LABEL: Record<string, string> = {
  observe: "관측", enforce: "강제(enforce)",
}

/** 정책 번들 적용 상태 (enforce) */
export const PULL_STATE_LABEL: Record<string, string> = {
  never: "받아간 적 없음", stale: "오래 안 받아감", outdated: "이전 번들", applied: "최신 적용",
}

export type McpPolicyGroup = {
  key: string
  label: string | null
  config_key: string | null
  mutating: boolean
  risk: string
  observed_enabled: boolean | null
  observed_basis: string | null
  approved_enabled: boolean | null
}

export type McpPolicyTool = {
  id: number
  name: string
  group_key: string | null
  risk: string | null
  risk_confirmed: boolean
  mutating: boolean
  observed_exposed: boolean | null
  blocked: boolean
  requires_approval: boolean | null
  tool_requires_approval: boolean
}

export type McpDeploymentPolicy = {
  deployment: string
  policy_mode: string
  policy_version: string | null
  policy_updated_at: string | null
  policy_updated_by: string | null
  approved_version: string | null
  deployed_version: string | null
  approved_limits: Record<string, unknown> | null
  observed_limits: Record<string, unknown> | null
  groups: McpPolicyGroup[]
  tools: McpPolicyTool[]
  approved_scopes: McpResourceScope[]
  observed_scopes: McpResourceScope[]
  // Phase 3 — 정책 번들 집행 상태
  has_service_token: boolean
  bundle_etag: string | null
  policy_pulled_at: string | null
  policy_pulled_version: string | null
  policy_pull_instance: string | null
  pull_state: string | null
  agent_count: number
}

/** 서버가 pull 하는 정책 번들 (GET /mcp-deployments/{name}/policy-bundle) */
export type McpPolicyBundle = {
  bundle_version: number
  deployment: string
  policy_version: string | null
  policy_mode: string
  status: string
  groups: Record<string, boolean>
  blocked_tools: string[]
  approval_required_tools: string[]
  resource_scopes: { resource_type: string; action: string; effect: string; patterns: string[]; match: string }[]
  limits: Record<string, unknown>
  approved_version: string | null
  agents: Record<string, {
    urn: string
    status: string
    allowed_groups: string[] | null
    allowed_tools: string[] | null
    resource_scopes: McpPolicyBundle["resource_scopes"]
  }>
}

export type McpScopeInput = {
  resource_type: string
  action: string
  effect: string
  patterns: string[]
  match?: string
  description?: string | null
}

export type McpPolicyUpdate = Partial<{
  groups: Record<string, boolean | null>
  tools: Record<string, { blocked: boolean; requires_approval: boolean | null }>
  scopes: McpScopeInput[]
  limits: Record<string, unknown> | null
  approved_version: string | null
  policy_mode: string
  note: string | null
}>

export type McpPolicyHistory = {
  id: number
  policy_version: string
  snapshot: Record<string, unknown>
  note: string | null
  changed_by: string | null
  changed_at: string | null
}

export const FINDING_KIND_LABEL: Record<string, string> = {
  group_enabled_unapproved: "승인되지 않은 그룹 켜짐",
  tool_exposed_blocked: "차단 도구 노출",
  new_tool_unreviewed: "위험도 미확정 도구",
  scope_wider_than_approved: "리소스 범위가 승인보다 넓음",
  limit_exceeds_approved: "제한값 초과",
  version_mismatch: "승인 버전과 다름",
  security_warning: "보안 경고",
  unhealthy: "헬스 연속 실패",
  audit_policy_violation: "승인 정책 위반 호출",
  policy_not_applied: "enforce 정책 미적용",
}

export const FINDING_STATUS_LABEL: Record<string, string> = {
  open: "열림", acknowledged: "확인됨", resolved: "해소",
}

export type McpFinding = {
  id: number
  deployment_name: string | null
  server_name: string | null
  environment: string | null
  kind: string
  subject: string | null
  severity: string
  title: string
  detail: Record<string, unknown> | null
  status: string
  first_seen_at: string | null
  last_seen_at: string | null
  resolved_at: string | null
  acknowledged_by: string | null
  acknowledged_at: string | null
  ack_note: string | null
  alert_id: number | null
}

export const DECISION_LABEL: Record<string, string> = {
  allow: "허용", deny: "범위 밖", blocked: "차단 도구", unknown: "판정 불가",
  violation: "위반(서버 거부 제외)", enforced: "서버가 거부",
}
export const DECISION_VARIANTS: Record<string, string> = {
  allow: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  deny: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  blocked: "bg-red-200 text-red-900 dark:bg-red-900/60 dark:text-red-200",
  unknown: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
}

export type McpAuditEvent = {
  id: number
  deployment_name: string | null
  ts: string
  session_id: string | null
  tool_name: string
  group_key: string | null
  risk: string | null
  user_name: string | null
  args: Record<string, unknown> | null
  ok: boolean
  error: string | null
  result_id: string | null
  resources: { type: string; value: string }[] | null
  agent_name: string | null
  /** 서버가 enforce 로 거부했으면 "denied" */
  server_decision: string | null
  policy_decision: string | null
  policy_reason: string | null
  received_via: string | null
  received_at: string | null
}

export type McpAuditIngestResult = {
  received: number
  inserted: number
  duplicates: number
  invalid: number
  violations: number
  purged: number
}

export type McpIngestToken = {
  token: string | null
  hint: string | null
  created_at: string | null
  ingest_url: string | null
}

export type McpServerVersion = {
  id: number
  capability_hash: string
  reported_version: string | null
  tool_count: number
  diff: Record<string, unknown> | null
  source: string | null
  deployment_name: string | null
  created_by: string | null
  created_at: string | null
}

export type McpServerVersionDetail = McpServerVersion & { snapshot: Record<string, unknown> }

export type McpControlCell = {
  deployment: string
  display_name: string | null
  status: string
  health_status: string
  policy_mode: string
  policy_set: boolean
  enabled_mutating_groups: string[]
  open_findings: number
  max_finding_severity: string | null
}

export type McpControlOverview = {
  environments: string[]
  matrix: { server: string; server_display_name: string | null; target_platform: string | null;
    cells: Record<string, McpControlCell[]> }[]
  risky: {
    deployment: string
    server_name: string | null
    environment: string
    high_risk_exposed: number
    exposed_tool_count: number
    max_exposed_risk: string | null
    enabled_mutating_groups: string[]
    open_findings: number
    policy_set: boolean
  }[]
  findings_by_kind: { name: string; count: number }[]
  findings_by_severity: { name: string; count: number }[]
  trend: { date: string; opened: number; resolved: number }[]
  recent_calls: McpAuditEvent[]
  coverage: { deployments: number; with_policy: number; enforce: number; enforce_applied: number; with_ingest_token: number }
}
