/**
 * AI Agent 카탈로그 API client.
 * 백엔드 `/api/v1/ai-agents` 와 통신한다.
 */

import { authFetch } from "@/features/auth/auth-fetch"
import type { AIAgentSummary } from "./data/schema"

const BASE = "/api/v1/ai-agents"

type AIAgentListParams = {
  search?: string
  status?: string
  framework?: string
  category?: string
  page?: number
  pageSize?: number
}

export type PaginatedAIAgents = {
  items: AIAgentSummary[]
  total: number
  page: number
  page_size: number
}

export async function fetchAIAgents(
  params?: AIAgentListParams,
): Promise<PaginatedAIAgents> {
  const query = new URLSearchParams()
  if (params?.search) query.set("search", params.search)
  if (params?.status) query.set("status", params.status)
  if (params?.framework) query.set("framework", params.framework)
  if (params?.category) query.set("category", params.category)
  query.set("page", String(params?.page ?? 1))
  query.set("page_size", String(params?.pageSize ?? 20))

  const res = await authFetch(`${BASE}?${query.toString()}`)
  if (!res.ok) throw new Error(`AI Agent 조회 실패: ${res.status}`)
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
}

export async function fetchAIAgentStats(): Promise<AIAgentStats> {
  const res = await authFetch(`${BASE}/stats`)
  if (!res.ok) throw new Error(`통계 조회 실패: ${res.status}`)
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
  tools: AIAgentTool[]
  mcp_servers: AIAgentMcpServer[]
  lineage: AIAgentLineageItem[]
  versions: AIAgentVersionItem[]
}

export async function fetchAIAgentDetail(name: string): Promise<AIAgentDetail> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`)
  if (!res.ok) throw new Error(`AI Agent 상세 조회 실패: ${res.status}`)
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
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `AI Agent 생성 실패: ${res.status}`)
  }
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
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `AI Agent 수정 실패: ${res.status}`)
  }
  return res.json()
}

export async function deleteAIAgent(name: string): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`, {
    method: "DELETE",
  })
  if (!res.ok) throw new Error(`AI Agent 삭제 실패: ${res.status}`)
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
  if (!res.ok) throw new Error(`도구 등록 실패: ${res.status}`)
  return res.json()
}

export async function deleteAIAgentTool(name: string, toolId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/tools/${toolId}`, {
    method: "DELETE",
  })
  if (!res.ok) throw new Error(`도구 삭제 실패: ${res.status}`)
}

export async function addAIAgentMcpServer(
  name: string,
  payload: { name: string; url?: string; auth_method?: string; description?: string },
): Promise<AIAgentMcpServer> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/mcp-servers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`MCP 서버 등록 실패: ${res.status}`)
  return res.json()
}

export async function deleteAIAgentMcpServer(name: string, mcpId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/mcp-servers/${mcpId}`, {
    method: "DELETE",
  })
  if (!res.ok) throw new Error(`MCP 서버 삭제 실패: ${res.status}`)
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
  if (!res.ok) throw new Error(`리니지 등록 실패: ${res.status}`)
  return res.json()
}

export async function deleteAIAgentLineage(name: string, lineageId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/lineage/${lineageId}`, {
    method: "DELETE",
  })
  if (!res.ok) throw new Error(`리니지 삭제 실패: ${res.status}`)
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
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `버전 생성 실패: ${res.status}`)
  }
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
  if (!res.ok) throw new Error(`평가 조회 실패: ${res.status}`)
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
  if (!res.ok) throw new Error(`평가 등록 실패: ${res.status}`)
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
  if (!res.ok) throw new Error(`미터링 조회 실패: ${res.status}`)
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
  if (!res.ok) throw new Error(`에이전트 카드 조회 실패: ${res.status}`)
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
  if (!res.ok) throw new Error(`정책 번들 조회 실패: ${res.status}`)
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
  if (!res.ok) throw new Error(`집행 이벤트 조회 실패: ${res.status}`)
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
  if (!res.ok) throw new Error(`상태 이력 조회 실패: ${res.status}`)
  return res.json()
}
