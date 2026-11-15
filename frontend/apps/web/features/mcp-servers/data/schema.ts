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

import type { StatusTone } from "@workspace/ui/components/status-badge"

import { LIFECYCLE_LABEL } from "@/lib/lifecycle"
import { RISK_LABEL, RISK_LEVELS } from "@/lib/risk"

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
// 상태 · 위험도 이름과 톤은 AI 에이전트와 공용(`lib/lifecycle.ts` · `lib/risk.ts`, TODO-107)
export const STATUS_LABEL: Record<string, string> = LIFECYCLE_LABEL

export const RISK_OPTIONS = RISK_LEVELS
export { RISK_LABEL }

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
/** 인증 방식 · 자격 종류 이름(TODO-113) — 예전엔 코드 그대로 보였다 */
export const AUTH_METHOD_LABEL: Record<string, string> = {
  none: "없음",
  bearer: "Bearer 토큰",
  api_key: "API 키",
  basic: "Basic 인증",
  oauth2: "OAuth2",
}
export const authMethodLabel = (m: string | null | undefined) => (m ? (AUTH_METHOD_LABEL[m] ?? m) : "-")
export const PROVIDER_TYPES = ["internal", "vendor", "community"] as const
export const PROVIDER_LABEL: Record<string, string> = {
  internal: "사내", vendor: "벤더", community: "커뮤니티",
}
export const FRAMEWORK_LABEL: Record<string, string> = {
  "argus-forge": "Argus Forge", generic: "일반 MCP",
}

/** 리소스 범위 효과 톤 — 값(allow · deny …)은 정책 파일 용어라 그대로 보인다. */
export const SCOPE_EFFECT_TONE: Record<string, StatusTone> = {
  allow: "success", deny: "danger", protect: "info", mask: "info",
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
/** 처리 상태 톤 — 알림 · 데이터 신고와 같은 기준(열림 주의 · 확인 진행 · 해소 초록). 심각도는 위험도 배지. */
export const FINDING_STATUS_TONE: Record<string, StatusTone> = {
  open: "warning", acknowledged: "info", resolved: "success",
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
export const DECISION_TONE: Record<string, StatusTone> = {
  allow: "success", deny: "danger", blocked: "danger", unknown: "neutral",
  violation: "danger", enforced: "warning",
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

/**
 * MCP 서버 상세의 항목 설명 — 항목 이름에 마우스를 올리면 툴팁으로 보인다(데이터셋 상세의 항목 설명과 같은 방식).
 * 키는 화면의 항목 이름. 출처(Argus · Registry · MCP)는 이름 옆 배지가 따로 알려 준다.
 */
export const SERVER_FIELD_HELP: Record<string, string> = {
  // 카탈로그 관리 정보 (Argus 전용 — MCP 표준에 없음)
  "이름": "카탈로그 안에서 서버 정의를 가리키는 고유 이름(영문 소문자·숫자·하이픈). 배포 · 정책 · 감사 기록이 이 이름으로 서버를 찾는다.",
  "표시명": "목록과 화면 제목에 보이는 사람이 읽는 이름. 비우면 Registry 제목이나 이름을 쓴다.",
  "상태": "카탈로그에서의 수명 주기 — 초안 · 검토 · 운영 · 사용 중단 · 퇴역. 퇴역한 서버 정의에는 새 배포를 등록할 수 없다.",
  "버전": "카탈로그가 관리하는 서버 정의의 버전. 실제로 떠 있는 서버가 보고한 버전은 배포 상세의 serverInfo · 배포 버전에 있다.",
  "제공자 유형": "서버를 누가 만들었는지 — 사내(직접 개발) · 벤더(상용 제품) · 커뮤니티(오픈 소스).",
  "벤더": "서버를 만든 회사나 단체.",
  "프레임워크": "서버를 만든 기반. Argus Forge 서버는 설정 파일 · 매니페스트로 도구 그룹과 설정을 수집하고, 일반 MCP 서버는 MCP 프로토콜(tools/list 등)로만 수집한다.",
  "대상 플랫폼": "이 MCP 서버가 다루는 데이터 플랫폼(예: Hive, Trino, Kafka).",
  "소유자": "서버 정의를 책임지는 담당자의 이메일.",
  "부서": "소유자가 속한 부서.",
  "카테고리": "목록에서 서버를 묶어 보는 분류.",
  "태그": "검색 · 필터에 쓰는 자유 키워드.",
  "지원 전송": "서버가 지원하는 MCP 전송 방식 — streamable_http(HTTP), sse(Server-Sent Events, 이전 방식), stdio(로컬 프로세스 표준 입출력).",
  "소스 저장소": "서버 소스 코드가 있는 저장소와 그 안의 경로.",
  "패키지 체크섬": "배포 산출물(패키지)의 해시. 실제로 배포된 파일이 등록한 것과 같은지 확인할 때 쓴다.",
  "OCI 아티팩트": "서버 이미지나 패키지가 올라간 OCI 레지스트리 주소(이미지 참조).",
  "최근 수집": "배포에서 도구 · 리소스 · 프롬프트 목록을 마지막으로 수집한 시각.",
  "등록": "서버 정의를 등록한 사용자와 시각.",
  "수정": "서버 정의를 마지막으로 수정한 사용자와 시각.",
  // MCP Registry 메타데이터 (server.json 표준)
  "Registry 이름": "MCP Registry 에서 서버를 가리키는 이름(역도메인 형식, 예: io.github.org/server). server.json 의 name.",
  "제목": "server.json 의 title — 사람이 읽는 서버 이름.",
  "설명": "server.json 의 description — 서버가 하는 일에 대한 설명.",
  "웹사이트": "서버 소개 · 문서 페이지 주소(websiteUrl).",
  "저장소": "server.json 의 repository — 소스 저장소 주소.",
  "라이선스": "서버 소프트웨어의 라이선스.",
  "packages": "로컬에서 설치 · 실행하는 방법 목록(npm · PyPI · OCI 등 패키지 레지스트리, 패키지 이름 · 버전, 실행 인자 · 환경 변수, 전송 방식).",
  "remotes": "이미 원격으로 떠 있는 서버에 붙는 방법 목록(전송 방식과 URL, 필요한 헤더).",
  // MCP 서버 보고값 (initialize 응답)
  "serverInfo": "MCP initialize 응답에서 서버가 스스로 밝힌 이름과 버전.",
  "프로토콜 버전": "서버와 합의한 MCP 프로토콜 버전(예: 2025-06-18).",
  "capabilities": "서버가 제공한다고 밝힌 기능 — tools · resources · prompts · logging 등과 그 세부 옵션(listChanged · subscribe 등).",
  "instructions": "서버가 클라이언트(AI 에이전트)에게 주는 사용 안내. 에이전트의 시스템 프롬프트에 들어가는 내용이다.",
  "capability 해시": "수집한 도구 · 리소스 · 프롬프트 정의를 정규화해 만든 지문. 값이 바뀌면 서버 기능이 바뀐 것이므로 새 버전 이력이 생긴다.",
  // 도구 (tools/list)
  "title": "도구의 사람이 읽는 이름(MCP 도구 정의의 title).",
  "description": "도구가 하는 일 — AI 에이전트가 도구를 고를 때 읽는 설명.",
  "annotations": "도구의 성격 힌트 — readOnlyHint(읽기만), destructiveHint(파괴적 변경), idempotentHint(반복해도 같음), openWorldHint(외부 세계와 상호작용) 등.",
  "inputSchema": "도구를 부를 때 넘기는 인자의 JSON Schema.",
  "outputSchema": "도구가 돌려주는 구조화된 결과의 JSON Schema(선언한 도구만).",
  "리소스 유형": "도구가 다루는 데이터 리소스 종류(예: table, topic). 정책의 리소스 범위 규칙과 연결된다.",
  "메모": "카탈로그 관리자가 남기는 도구 메모.",
  "사라진 시각": "마지막 수집에서 서버가 더 이상 이 도구를 보고하지 않은 시각.",
}

/** MCP 배포 상세의 항목 설명 — `SERVER_FIELD_HELP` 와 같은 방식(이름에 마우스를 올리면 툴팁). */
export const DEPLOYMENT_FIELD_HELP: Record<string, string> = {
  // 배포 관리 정보 (Argus 전용)
  "이름": "카탈로그 안에서 이 배포(실제로 떠 있는 서버 인스턴스)를 가리키는 고유 이름. 한 서버 정의에 환경별로 여러 배포가 있을 수 있다.",
  "표시명": "목록과 화면 제목에 보이는 사람이 읽는 이름.",
  "설명": "이 배포의 용도 · 운영 메모.",
  "환경": "배포가 속한 환경 — DEV(개발) · STG(검증) · PROD(운영).",
  "상태": "카탈로그에서의 수명 주기 — 초안 · 검토 · 운영 · 사용 중단 · 퇴역. 차단(blocked)된 배포는 정책 번들에서 빠진다.",
  "전송": "클라이언트가 이 배포에 붙는 MCP 전송 방식 — streamable_http(HTTP) · sse(이전 방식) · stdio(로컬 프로세스).",
  "MCP 엔드포인트": "AI 에이전트가 접속하는 MCP 주소(예: https://mcp.example.com:8792/mcp). streamable_http · sse 배포에 필요하다.",
  "도구 REST API": "Argus Forge 서버의 도구 REST · manifest 기준 경로(기본은 엔드포인트 호스트의 /api/v1). 도구 · 설정 수집에 쓴다.",
  "인증 방식": "에이전트가 MCP 엔드포인트에 붙을 때의 인증 — 없음 · Bearer 토큰 · API 키 · Basic · OAuth2. 비밀 값은 자격증명 탭에서 관리한다.",
  "배포 범위": "누가 운영하는 배포인지 — shared(사내 공용 서버) · external(외부 SaaS) · local(사용자 PC 에서 stdio 로 실행).",
  "호스트": "서버가 실행되는 호스트(운영 책임 확인용).",
  "연결 데이터 소스": "이 서버가 접속하는 대상 시스템이 카탈로그에 데이터 소스로 등록돼 있으면 그 연결.",
  "정책 모드": "관측(observe): 승인 정책과 실제 설정을 비교해 차이(finding)만 알린다 · 강제(enforce): 서버가 카탈로그의 정책 번들을 받아 도구 호출 직전에 집행한다.",
  "정책 버전": "승인 정책을 바꿀 때마다 새로 매기는 버전(바꾼 사람과 함께). enforce 서버는 이 값으로 새 정책 번들이 있는지 안다.",
  "승인 버전": "운영을 승인한 서버 버전. 서버가 보고한 배포 버전과 다르면 버전 불일치 finding 이 열린다.",
  "감사 이벤트 보존": "이 배포에서 받은 도구 호출 감사 이벤트를 보관하는 기간. 지나면 지운다.",
  "자동 수집 주기": "도구 · 리소스 · 프롬프트 목록과 설정을 자동으로 다시 수집하는 간격. 비어 있으면 수동 수집만 한다.",
  "헬스": "마지막 상태 점검 결과 — 응답 시간, 연속 실패 횟수, 점검 시각. 연속 실패가 기준을 넘으면 finding 이 열린다.",
  "최근 관측": "도구 목록 · 설정을 마지막으로 수집한 시각과 수집 경로(mcp: MCP 프로토콜, forge_rest: Forge 도구 REST, manifest: 매니페스트 가져오기).",
  "소유자 / 부서": "이 배포의 운영을 책임지는 담당자 이메일과 부서.",
  // 서버 설정 수집값 (MCP 표준 밖)
  "대상 시스템": "이 MCP 서버가 접속하는 데이터 시스템 주소(예: thrift://hms.example.com:9083).",
  "실행 계정": "서버가 대상 시스템에 접속할 때 쓰는 계정 — 예: Hive username, Impala doas_user, Kafka SASL 사용자, Kerberos principal. 에이전트가 할 수 있는 일은 이 계정의 권한을 넘지 못한다.",
  "대상 인증": "서버가 대상 시스템에 붙는 인증 방식(none · ldap · kerberos · sasl_scram 등).",
  "인스턴스 ID": "서버 프로세스가 /readyz 로 보고하는 실행 식별자. 값이 바뀌면 서버가 다시 시작된 것이다.",
  "제한(limits)": "서버 설정에서 수집한 실행 제한(예: hive.limits.max_rows — 한 번에 돌려주는 최대 행 수). 승인 정책의 제한과 비교된다.",
  // MCP 서버 보고값
  "배포 버전": "실제로 떠 있는 서버가 보고한 버전(serverInfo.version 또는 매니페스트의 서버 버전).",
  "프로토콜 버전": "서버와 합의한 MCP 프로토콜 버전(예: 2025-06-18).",
  "serverInfo": "MCP initialize 응답에서 서버가 스스로 밝힌 이름과 버전.",
}
