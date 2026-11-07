/**
 * AI Agent 카탈로그 API client.
 * 백엔드 `/api/v1/ai-agents` 와 통신한다.
 */

import { authFetch, throwOnError } from "@/features/auth/auth-fetch"
import type { AIAgentSummary } from "./data/schema"

const BASE = "/api/v1/ai-agents"

/** 목록 질의 — 백엔드 `GET /ai-agents` 질의와 같은 이름(저장된 보기의 query 와도 같다). */
export type AgentListQuery = {
  search?: string
  status?: string
  framework?: string
  category?: string
  department?: string
  /** 소유자 이메일. "me" 는 로그인 사용자 */
  owner?: string
  /** 태그(쉼표, 모두 포함) */
  tag?: string
  risk?: "low" | "medium" | "high" | "critical"
  managed_by?: string
  /** attention(확인 필요) 또는 ok/missing/unknown/inactive */
  owner_state?: string
  incomplete?: boolean
  /** updated(기본)/name/usage/last_invoked/completeness/risk, "-" 접두는 반대 방향 */
  sort?: string
}

export const LIST_QUERY_KEYS: (keyof AgentListQuery)[] = [
  "search", "status", "framework", "category", "department", "owner", "tag", "risk", "managed_by",
  "owner_state", "incomplete", "sort",
]

/** 빈 값을 뺀 질의(저장·비교용). */
export function cleanQuery(q: AgentListQuery): AgentListQuery {
  const out: Record<string, unknown> = {}
  for (const k of LIST_QUERY_KEYS) {
    const v = q[k]
    if (v === undefined || v === null || v === "" || v === false) continue
    out[k] = typeof v === "string" ? v.trim() : v
  }
  return out as AgentListQuery
}

export type PaginatedAIAgents = {
  items: AIAgentSummary[]
  total: number
  page: number
  page_size: number
}

export async function fetchAIAgents(
  params?: AgentListQuery & { page?: number; pageSize?: number },
): Promise<PaginatedAIAgents> {
  const query = new URLSearchParams()
  const q = cleanQuery(params ?? {})
  for (const [k, v] of Object.entries(q)) query.set(k, String(v))
  query.set("page", String(params?.page ?? 1))
  query.set("page_size", String(params?.pageSize ?? 20))

  const res = await authFetch(`${BASE}?${query.toString()}`)
  if (!res.ok) await throwOnError(res, "AI 에이전트 목록을 불러오지 못했습니다.")
  return res.json()
}

export type AIAgentStats = {
  total_agents: number
  active_agents: number
  by_status: { name: string; count: number }[]
  by_framework: { name: string; count: number }[]
  by_category: { name: string; count: number }[]
  multi_agent_count: number
  hitl_required_count: number
  total_invocations: number
  invocations_24h: number
  errors_24h: number
  risky_tool_agents: number
  unregistered_mcp_count: number
  unregistered_agent_count: number
  /** 책임자 확인 필요(소유자 없음·사용자 목록에 없음·비활성) */
  owner_attention_count: number
  /** 정보 완성도 100% 미만 */
  incomplete_count: number
  by_department: DepartmentQuality[]
}

export type DepartmentQuality = {
  department: string | null
  agents: number
  avg_completeness: number
  missing_required: number
  owner_attention: number
}

export async function fetchAIAgentStats(): Promise<AIAgentStats> {
  const res = await authFetch(`${BASE}/stats`)
  if (!res.ok) throw new Error(`AI 에이전트 통계를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

// ---------------------------------------------------------------------------
// Detail
// ---------------------------------------------------------------------------

export type AIAgentTool = {
  id: number
  name: string
  description: string | null
  tool_schema: Record<string, unknown> | null
  risk: "low" | "medium" | "high" | "critical" | null
  requires_approval: boolean
}

export type AIAgentMcpServer = {
  id: number
  name: string
  url: string | null
  auth_method: string | null
  description: string | null
  // MCP Server 카탈로그 연계 (registered=false 면 직접 입력한 "미등록 MCP")
  mcp_deployment_id: number | null
  mcp_deployment_name: string | null
  mcp_deployment_urn: string | null
  mcp_server_name: string | null
  deployment_status: string | null
  deployment_environment: string | null
  registered: boolean
  allowed_groups: string[] | null
  allowed_tools: string[] | null
  effective_tool_count: number | null
}

export type AIAgentLineageItem = {
  id: number
  target_type: string
  target_ref: string
  relation: string
  description: string | null
}

export type AIAgentVersionItem = {
  id: number
  version: string
  source: string | null
  system_prompt: string | null
  changelog: string | null
  status: string
  created_at: string
  created_by: string | null
}

export type AIAgentDetail = {
  id: number
  name: string
  urn: string
  display_name: string | null
  description: string | null
  version: string
  status: string
  owner_email: string | null
  department: string | null
  category: string | null
  base_model: string | null
  base_model_ref: number | null
  model_provider: string | null
  framework: string | null
  execution_policy: string | null
  max_steps: number | null
  memory_type: string | null
  is_multi_agent: boolean
  endpoint: string | null
  protocol: string | null
  streaming: boolean
  invocation_method: string | null
  auth_method: string | null
  pii_handling: string | null
  data_residency: string | null
  budget_limit: number | null
  hitl_required: boolean
  audit_log_ref: string | null
  latency_p50: number | null
  latency_p95: number | null
  error_rate: number | null
  avg_token_usage: number | null
  cost_per_call: number | null
  reputation_score: number | null
  capabilities: string[] | null
  input_schema: Record<string, unknown> | null
  output_schema: Record<string, unknown> | null
  supported_languages: string[] | null
  use_cases: string[] | null
  limitations: string[] | null
  inference_params: Record<string, unknown> | null
  guardrails: Record<string, unknown> | null
  rag_config: Record<string, unknown> | null
  network_allowlist: string[] | null
  dlp_policies: string[] | null
  hitl_config: Record<string, unknown> | null
  sub_agents: Record<string, unknown>[] | null
  tags: string[] | null
  usage_count: number
  last_invoked_at: string | null
  created_at: string
  updated_at: string
  created_by: string | null
  updated_by: string | null
  /** 관리 방식 — ui(화면) / manifest(정의 파일) / discovered(발견 후 등록) */
  managed_by: string
  source_ref: string | null
  last_applied_at: string | null
  last_applied_by: string | null
  /** 분류와 책임(조회 때 계산) */
  owner_state?: string | null
  completeness?: number | null
  missing_fields?: string[]
  max_risk?: string | null
  tools: AIAgentTool[]
  mcp_servers: AIAgentMcpServer[]
  lineage: AIAgentLineageItem[]
  versions: AIAgentVersionItem[]
}

export async function fetchAIAgentDetail(name: string): Promise<AIAgentDetail> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`)
  if (!res.ok) throw new Error(`AI 에이전트 정보를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export type AIAgentCreatePayload = {
  name: string
  display_name?: string
  description?: string
  status?: string
  owner_email?: string
  department?: string
  category?: string
  base_model?: string
  base_model_ref?: number
  model_provider?: string
  framework?: string
  execution_policy?: string
}

export async function createAIAgent(
  payload: AIAgentCreatePayload,
): Promise<AIAgentSummary> {
  const res = await authFetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  // 422 검증 오류(detail 배열)도 읽을 수 있는 사유로 — 직접 err.detail 을 쓰면 "[object Object]"
  if (!res.ok) await throwOnError(res, "AI 에이전트를 생성하지 못했습니다.")
  return res.json()
}

// 부분 갱신 — 전달한 필드만 반영(PATCH /ai-agents/{name}). AIAgentUpdate 와 동일 키.
export type AIAgentUpdatePayload = Partial<{
  display_name: string | null
  description: string | null
  status: string
  version: string
  category: string | null
  owner_email: string | null
  department: string | null
  base_model: string | null
  model_provider: string | null
  framework: string | null
  execution_policy: string | null
  max_steps: number | null
  memory_type: string | null
  is_multi_agent: boolean
  endpoint: string | null
  protocol: string | null
  streaming: boolean
  invocation_method: string | null
  auth_method: string | null
  tags: string[] | null
  capabilities: string[] | null
  supported_languages: string[] | null
  use_cases: string[] | null
  limitations: string[] | null
  input_schema: Record<string, unknown> | null
  output_schema: Record<string, unknown> | null
  rag_config: Record<string, unknown> | null
  network_allowlist: string[] | null
  pii_handling: string | null
  data_residency: string | null
  budget_limit: number | null
  hitl_required: boolean
  audit_log_ref: string | null
  dlp_policies: string[] | null
  guardrails: Record<string, unknown> | null
  hitl_config: Record<string, unknown> | null
}>

export async function updateAIAgent(
  name: string,
  payload: AIAgentUpdatePayload,
): Promise<AIAgentSummary> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  // 422 검증 오류(detail 배열)도 읽을 수 있는 사유로 — 직접 err.detail 을 쓰면 "[object Object]"
  if (!res.ok) await throwOnError(res, "AI 에이전트를 수정하지 못했습니다.")
  return res.json()
}

export async function deleteAIAgent(name: string): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`, {
    method: "DELETE",
  })
  if (!res.ok) await throwOnError(res, "AI 에이전트를 삭제하지 못했습니다.")
}

// ---------------------------------------------------------------------------
// Phase 2 — sub-resource management
// ---------------------------------------------------------------------------

export async function addAIAgentTool(
  name: string,
  payload: {
    name: string
    description?: string
    tool_schema?: Record<string, unknown> | null
    risk?: string | null
    requires_approval?: boolean
  },
): Promise<AIAgentTool> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/tools`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "도구를 등록하지 못했습니다.")
  return res.json()
}

export async function deleteAIAgentTool(name: string, toolId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/tools/${toolId}`, {
    method: "DELETE",
  })
  if (!res.ok) await throwOnError(res, "도구를 삭제하지 못했습니다.")
}

export async function addAIAgentMcpServer(
  name: string,
  payload: {
    name?: string
    url?: string
    auth_method?: string
    description?: string
    // 카탈로그 배포 선택 시 name/url/auth 는 배포 값으로 채워진다.
    mcp_deployment_name?: string
    allowed_groups?: string[] | null
    allowed_tools?: string[] | null
  },
): Promise<AIAgentMcpServer> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/mcp-servers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "MCP 서버를 등록하지 못했습니다.")
  return res.json()
}

// 연결된 MCP 의 허용 범위 수정 / 미등록 연결을 카탈로그 배포로 승격.
export async function updateAIAgentMcpServer(
  name: string,
  mcpId: number,
  payload: {
    mcp_deployment_name?: string | null
    allowed_groups?: string[] | null
    allowed_tools?: string[] | null
    description?: string | null
  },
): Promise<AIAgentMcpServer> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/mcp-servers/${mcpId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "MCP 연결을 수정하지 못했습니다.")
  return res.json()
}

export async function deleteAIAgentMcpServer(name: string, mcpId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/mcp-servers/${mcpId}`, {
    method: "DELETE",
  })
  if (!res.ok) await throwOnError(res, "MCP 서버를 삭제하지 못했습니다.")
}

export async function addAIAgentLineage(
  name: string,
  payload: { target_type: string; target_ref: string; relation: string; description?: string },
): Promise<AIAgentLineageItem> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/lineage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "리니지를 등록하지 못했습니다.")
  return res.json()
}

export async function deleteAIAgentLineage(name: string, lineageId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/lineage/${lineageId}`, {
    method: "DELETE",
  })
  if (!res.ok) await throwOnError(res, "리니지를 삭제하지 못했습니다.")
}

export async function addAIAgentVersion(
  name: string,
  payload: { version: string; source?: string; system_prompt?: string; changelog?: string },
): Promise<AIAgentVersionItem> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/versions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  // 422 검증 오류(detail 배열)도 읽을 수 있는 사유로 — 직접 err.detail 을 쓰면 "[object Object]"
  if (!res.ok) await throwOnError(res, "버전을 생성하지 못했습니다.")
  return res.json()
}

// ---------------------------------------------------------------------------
// Phase 2 — evaluation
// ---------------------------------------------------------------------------

export type AIAgentEval = {
  id: number
  version: string | null
  eval_type: string
  metric_key: string
  metric_value: number
  dataset_ref: string | null
  passed: boolean | null
  notes: string | null
  evaluated_at: string
  created_by: string | null
}

export async function fetchAIAgentEvals(name: string): Promise<AIAgentEval[]> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/evals`)
  if (!res.ok) throw new Error(`평가 결과를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function addAIAgentEval(
  name: string,
  payload: {
    eval_type: string
    metric_key: string
    metric_value: number
    version?: string
    dataset_ref?: string
    passed?: boolean
    notes?: string
  },
): Promise<AIAgentEval> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/evals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "평가 결과를 등록하지 못했습니다.")
  return res.json()
}

// ---------------------------------------------------------------------------
// Phase 2 — metering & agent card
// ---------------------------------------------------------------------------

export type AIAgentMetering = {
  total_invocations: number
  success_count: number
  error_count: number
  success_rate: number
  error_rate: number
  avg_latency_ms: number | null
  latency_p50: number | null
  latency_p95: number | null
  total_input_tokens: number
  total_output_tokens: number
  total_cost: number
  reputation_score: number | null
  daily_invocations: { date: string; count: number }[]
  by_consumer: { name: string; count: number }[]
}

export async function fetchAIAgentMetering(name: string): Promise<AIAgentMetering> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/metering`)
  if (!res.ok) throw new Error(`미터링 정보를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export type AIAgentCard = {
  name: string
  display_name: string | null
  description: string | null
  version: string
  url: string | null
  protocol: string | null
  auth_method: string | null
  provider: { organization: string | null; contact: string | null }
  capabilities: string[]
  supported_languages: string[]
  skills: { name: string; description: string | null }[]
  streaming: boolean
}

export async function fetchAIAgentCard(name: string): Promise<AIAgentCard> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/card`)
  if (!res.ok) throw new Error(`에이전트 카드를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

// ---------------------------------------------------------------------------
// Phase 3 — execution-plane integration interface (read-only views)
// ---------------------------------------------------------------------------

export type AIAgentPolicyBundle = {
  name: string
  urn: string
  version: string
  status: string
  policy_version: string
  max_steps: number | null
  budget_limit: number | null
  network_allowlist: string[]
  allowed_tools: { name: string; description: string | null; schema: Record<string, unknown> | null }[]
  allowed_mcp_servers: { name: string; url: string | null; auth_method: string | null }[]
  pii_handling: string | null
  data_residency: string | null
  dlp_policies: string[]
  hitl_required: boolean
  hitl_config: Record<string, unknown> | null
  guardrails: Record<string, unknown> | null
  auth_method: string | null
  protocol: string | null
  endpoint: string | null
}

export async function fetchAIAgentPolicy(name: string): Promise<AIAgentPolicyBundle> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/policy`)
  if (!res.ok) throw new Error(`정책 번들을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export type AIAgentHookEvent = {
  id: number
  occurred_at: string
  stage: string
  decision: string
  action_type: string | null
  target: string | null
  policy_ref: string | null
  reason: string | null
  session_id: string | null
  consumer: string | null
  metadata: Record<string, unknown> | null
}

export async function fetchAIAgentHookEvents(name: string): Promise<AIAgentHookEvent[]> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/hook-events?limit=100`)
  if (!res.ok) throw new Error(`집행 이벤트를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export type AIAgentStatusHistory = {
  id: number
  from_status: string | null
  to_status: string
  note: string | null
  changed_by: string | null
  changed_at: string
}

export async function fetchAIAgentStatusHistory(name: string): Promise<AIAgentStatusHistory[]> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/status-history`)
  if (!res.ok) throw new Error(`상태 이력을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

// ---------------------------------------------------------------------------
// 정의 파일(agent.yaml) 일괄 반영 · 내보내기 — design/ai-agent-bulk-registration-design.md
// ---------------------------------------------------------------------------

export type ApplyAction = "created" | "updated" | "unchanged" | "failed"

export type ApplyResult = {
  dry_run: boolean
  summary: Record<ApplyAction, number>
  results: { name: string; action: ApplyAction; changes?: string[]; error?: string }[]
}

/** 정의 파일 반영. `yaml` 은 원문(여러 문서 ---), `agents` 는 문서 객체 목록(표 형식 파일 변환 결과). */
export async function applyAgentManifests(
  body: { yaml: string } | { agents: Record<string, unknown>[] },
  dryRun: boolean,
): Promise<ApplyResult> {
  const init: RequestInit =
    "yaml" in body
      ? { method: "POST", headers: { "Content-Type": "application/yaml" }, body: body.yaml }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ agents: body.agents }) }
  const res = await authFetch(`${BASE}/apply?dry_run=${dryRun}`, init)
  if (!res.ok) await throwOnError(res, "정의 파일을 반영하지 못했습니다.")
  return res.json()
}

/** 현재 에이전트를 정의 파일(YAML 원문)로 내보내기. */
export async function fetchAgentManifest(name: string): Promise<string> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/manifest`)
  if (!res.ok) await throwOnError(res, "정의 파일을 내보내지 못했습니다.")
  return res.text()
}

export type CardImportResult = {
  source_url: string
  name: string
  exists: boolean
  /** 비어 있는 필수 항목(owner·department·category) — 채워야 반영된다 */
  missing: string[]
  notes: string[]
  document: Record<string, unknown>
  yaml: string
}

/** 외부 에이전트 소개 정보(에이전트 카드)를 읽어 정의 내용 미리 보기로(저장하지 않음). */
export async function importAgentCard(url: string): Promise<CardImportResult> {
  const res = await authFetch(`${BASE}/import/card`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  })
  if (!res.ok) await throwOnError(res, "소개 정보를 불러오지 못했습니다.")
  return res.json()
}

// ---------------------------------------------------------------------------
// 미등록 에이전트 발견
// ---------------------------------------------------------------------------

export type DiscoveryStatus = "new" | "ignored" | "registered"

export type AIAgentDiscovery = {
  id: number
  name: string
  first_seen_at: string
  last_seen_at: string
  seen_count: number
  invocation_count: number
  hook_event_count: number
  reported_by: string | null
  last_consumer: string | null
  status: DiscoveryStatus
  agent_id: number | null
  agent_name: string | null
  status_changed_by: string | null
  status_changed_at: string | null
}

export type PaginatedDiscoveries = {
  items: AIAgentDiscovery[]
  total: number
  page: number
  page_size: number
}

export async function fetchDiscoveries(params: {
  status?: DiscoveryStatus | ""
  search?: string
  page?: number
  pageSize?: number
}): Promise<PaginatedDiscoveries> {
  const q = new URLSearchParams()
  q.set("status", params.status ?? "new")
  if (params.search) q.set("search", params.search)
  q.set("page", String(params.page ?? 1))
  q.set("page_size", String(params.pageSize ?? 20))
  const res = await authFetch(`${BASE}/discoveries?${q.toString()}`)
  if (!res.ok) await throwOnError(res, "미등록 에이전트 목록을 불러오지 못했습니다.")
  return res.json()
}

/** 발견 기록 등록 — 새로 만들 때 소유자 이메일·부서·분류 필수(소유자·부서는 비우면 로그인 사용자 값). */
export async function registerDiscovery(
  id: number,
  body: { owner_email?: string; department?: string; category?: string } = {},
): Promise<{ discovery: AIAgentDiscovery; agent: AIAgentSummary }> {
  const res = await authFetch(`${BASE}/discoveries/${id}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) await throwOnError(res, "미등록 에이전트를 등록하지 못했습니다.")
  return res.json()
}

export async function setDiscoveryIgnored(id: number, ignored: boolean): Promise<AIAgentDiscovery> {
  const res = await authFetch(`${BASE}/discoveries/${id}/${ignored ? "ignore" : "reopen"}`, { method: "POST" })
  if (!res.ok) await throwOnError(res, ignored ? "미등록 에이전트를 무시 처리하지 못했습니다." : "무시 처리를 되돌리지 못했습니다.")
  return res.json()
}

// ---------------------------------------------------------------------------
// 분류와 책임 — 저장된 보기 · 일괄 변경 (design/ai-agent-ownership-design.md)
// ---------------------------------------------------------------------------

export type AgentView = {
  id: string
  name: string
  kind: "builtin" | "personal" | "shared"
  owner_name: string | null
  editable: boolean
  query: AgentListQuery
}

export async function fetchAgentViews(): Promise<AgentView[]> {
  const res = await authFetch(`${BASE}/views`)
  if (!res.ok) await throwOnError(res, "저장된 보기를 불러오지 못했습니다.")
  return res.json()
}

export async function saveAgentView(
  body: { name: string; shared: boolean; query: AgentListQuery },
  id?: string,
): Promise<AgentView> {
  const res = await authFetch(id ? `${BASE}/views/${id}` : `${BASE}/views`, {
    method: id ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, query: cleanQuery(body.query) }),
  })
  if (!res.ok) await throwOnError(res, "보기를 저장하지 못했습니다.")
  return res.json()
}

export async function deleteAgentView(id: string): Promise<void> {
  const res = await authFetch(`${BASE}/views/${id}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "보기를 삭제하지 못했습니다.")
}

export type BulkSet = {
  status?: string
  owner?: string
  department?: string
  category?: string
  add_tags?: string[]
  remove_tags?: string[]
}

export type BulkResult = {
  dry_run: boolean
  summary: { updated: number; unchanged: number; failed: number }
  results: { name: string; action: "updated" | "unchanged" | "failed"; changes?: string[]; error?: string; note?: string }[]
}

export async function bulkUpdateAgents(names: string[], set: BulkSet, dryRun: boolean): Promise<BulkResult> {
  const res = await authFetch(`${BASE}/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ names, set, dry_run: dryRun }),
  })
  if (!res.ok) await throwOnError(res, "AI 에이전트를 일괄 변경하지 못했습니다.")
  return res.json()
}
