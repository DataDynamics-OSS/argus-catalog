/**
 * API Catalog API client. 백엔드 /api/v1/apis 와 통신.
 */

import { authFetch } from "@/features/auth/auth-fetch"

const BASE = "/api/v1/apis"

export type ApiSummary = {
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
  protocol: string | null
  source: string | null
  spec_format: string | null
  base_url: string | null
  base_url_overridden: string | null
  contract_url: string | null
  certification: string | null
  tier: string | null
  tags: string[] | null
  endpoint_count: number
  created_at: string
  updated_at: string
}

export type ApiEndpoint = {
  id: number
  method: string
  path: string
  operation_id: string | null
  summary: string | null
  description: string | null
  tags: string[] | null
  parameters: Record<string, unknown>[] | null
  request_body: Record<string, unknown> | null
  responses: Record<string, unknown> | null
  security: unknown[] | null
  extra: Record<string, unknown> | null
}

export type ApiServer = { id: number; url: string; description: string | null; env: string | null }
export type ApiSecurityScheme = { id: number; scheme_name: string; type: string | null; config: Record<string, unknown> | null }
export type ApiSpec = { id: number; version: string; format: string | null; source_url: string | null; is_current: string | null; created_at: string; created_by: string | null }

export type ApiTagDef = { name: string; description: string }

export type ApiDetail = ApiSummary & {
  note: string | null
  contract_text: string | null
  raw_spec: string | null
  servers: ApiServer[]
  security_schemes: ApiSecurityScheme[]
  endpoints: ApiEndpoint[]
  specs: ApiSpec[]
  tag_defs: ApiTagDef[]
}

export type PaginatedApis = { items: ApiSummary[]; total: number; page: number; page_size: number }
export type ApiStats = {
  total_apis: number
  published_apis: number
  by_status: { name: string; count: number }[]
  by_protocol: { name: string; count: number }[]
  total_endpoints: number
}
export type ApiStatusHistory = {
  id: number
  from_status: string | null
  to_status: string
  note: string | null
  changed_by: string | null
  changed_at: string
}

type ListParams = { search?: string; status?: string; category?: string; page?: number; pageSize?: number }

export async function fetchApis(params?: ListParams): Promise<PaginatedApis> {
  const q = new URLSearchParams()
  if (params?.search) q.set("search", params.search)
  if (params?.status) q.set("status", params.status)
  if (params?.category) q.set("category", params.category)
  q.set("page", String(params?.page ?? 1))
  q.set("page_size", String(params?.pageSize ?? 20))
  const res = await authFetch(`${BASE}?${q.toString()}`)
  if (!res.ok) throw new Error(`API 목록 조회 실패: ${res.status}`)
  return res.json()
}

export async function fetchApiStats(): Promise<ApiStats> {
  const res = await authFetch(`${BASE}/stats`)
  if (!res.ok) throw new Error(`통계 조회 실패: ${res.status}`)
  return res.json()
}

export async function fetchApiDetail(name: string): Promise<ApiDetail> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`)
  if (!res.ok) throw new Error(`API 상세 조회 실패: ${res.status}`)
  return res.json()
}

export type ApiCreatePayload = {
  name?: string
  display_name?: string
  description?: string
  owner_email?: string
  department?: string
  category?: string
  tags?: string[]
  spec_text?: string
  spec_url?: string
  // 수동 등록용
  version?: string
  base_url?: string
  protocol?: string
  contract_text?: string
  contract_url?: string
}

export type EndpointPayload = {
  method: string
  path: string
  operation_id?: string | null
  summary?: string | null
  description?: string | null
  tags?: string[] | null
  parameters?: Record<string, unknown>[] | null
  request_body?: Record<string, unknown> | null
  responses?: Record<string, unknown> | null
  extra?: Record<string, unknown> | null
}

export async function createEndpoint(name: string, payload: EndpointPayload): Promise<ApiEndpoint> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/endpoints`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  })
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `엔드포인트 추가 실패: ${res.status}`) }
  return res.json()
}

export async function updateEndpoint(name: string, epId: number, payload: EndpointPayload): Promise<ApiEndpoint> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/endpoints/${epId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  })
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `엔드포인트 수정 실패: ${res.status}`) }
  return res.json()
}

export async function deleteEndpoint(name: string, epId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/endpoints/${epId}`, { method: "DELETE" })
  if (!res.ok) throw new Error(`엔드포인트 삭제 실패: ${res.status}`)
}

export async function createApi(payload: ApiCreatePayload): Promise<ApiSummary> {
  const res = await authFetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `API 등록 실패: ${res.status}`)
  }
  return res.json()
}

export type ApiUpdatePayload = Partial<{
  display_name: string | null
  description: string | null
  version: string
  status: string
  owner_email: string | null
  department: string | null
  category: string | null
  protocol: string | null
  base_url: string | null
  certification: string | null
  tier: string | null
  tags: string[] | null
  note: string | null
  contract_text: string | null
  contract_url: string | null
}>

export async function updateApi(name: string, payload: ApiUpdatePayload): Promise<ApiSummary> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `API 수정 실패: ${res.status}`)
  }
  return res.json()
}

export async function deleteApi(name: string): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`, { method: "DELETE" })
  if (!res.ok) throw new Error(`API 삭제 실패: ${res.status}`)
}

export async function uploadApiSpec(name: string, payload: { spec_text?: string; spec_url?: string }): Promise<ApiSummary> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/specs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `스펙 업로드 실패: ${res.status}`)
  }
  return res.json()
}

export type ApiSpecDiff = {
  from_spec_id: number | null
  to_spec_id: number | null
  from_version: string | null
  to_version: string | null
  added: string[]
  removed: string[]
  changed: { key: string; breaking: boolean; items: { detail: string; breaking: boolean }[] }[]
  breaking: boolean
  breaking_count: number
  added_count: number
  removed_count: number
  changed_count: number
  message: string | null
}

export async function fetchApiDiff(name: string, from?: number, to?: number): Promise<ApiSpecDiff> {
  const q = new URLSearchParams()
  if (from != null) q.set("from", String(from))
  if (to != null) q.set("to", String(to))
  const qs = q.toString()
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/diff${qs ? `?${qs}` : ""}`)
  if (!res.ok) throw new Error(`버전 디프 조회 실패: ${res.status}`)
  return res.json()
}

export async function fetchApiStatusHistory(name: string): Promise<ApiStatusHistory[]> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/status-history`)
  if (!res.ok) throw new Error(`상태 이력 조회 실패: ${res.status}`)
  return res.json()
}

export type ApiAlert = {
  id: number
  api_id: number
  from_spec_id: number | null
  to_spec_id: number | null
  from_version: string | null
  to_version: string | null
  severity: string
  breaking_count: number
  summary: string
  detail: string | null
  status: string
  created_by: string | null
  created_at: string
  acknowledged_by: string | null
  acknowledged_at: string | null
}

export async function fetchApiAlerts(name: string, status?: string): Promise<ApiAlert[]> {
  const q = status ? `?status=${encodeURIComponent(status)}` : ""
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/alerts${q}`)
  if (!res.ok) throw new Error(`알림 조회 실패: ${res.status}`)
  return res.json()
}

export async function acknowledgeApiAlert(name: string, alertId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/alerts/${alertId}/ack`, { method: "POST" })
  if (!res.ok) throw new Error(`알림 확인 처리 실패: ${res.status}`)
}

export type ApiLintFinding = { rule: string; severity: string; message: string; location: string }
export type ApiLint = {
  spec_id: number | null
  version: string | null
  score: number
  error_count: number
  warning_count: number
  info_count: number
  findings: ApiLintFinding[]
}

export async function fetchApiLint(name: string, specId?: number): Promise<ApiLint> {
  const q = specId != null ? `?spec_id=${specId}` : ""
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/lint${q}`)
  if (!res.ok) throw new Error(`린팅 조회 실패: ${res.status}`)
  return res.json()
}

export type ApiUsage = {
  days: number
  total_calls: number
  success_calls: number
  error_calls: number
  success_rate: number
  avg_latency_ms: number
  p95_latency_ms: number
  by_status: { status: string; count: number }[]
  top_endpoints: { endpoint: string; count: number; avg_latency_ms: number }[]
  top_callers: { name: string; count: number }[]
  daily: { date: string; count: number }[]
  recent: {
    id: number; method: string; url: string; status_code: number
    ok: boolean; latency_ms: number; error: string | null
    called_by: string | null; created_at: string | null
  }[]
}

export async function fetchApiUsage(name: string, days = 30): Promise<ApiUsage> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/usage?days=${days}`)
  if (!res.ok) throw new Error(`사용량 조회 실패: ${res.status}`)
  return res.json()
}

export type ApiLineage = {
  id: number
  api_id: number
  relation: string        // provides / consumes / depends_on
  target_type: string     // api / dataset / model / agent / system
  target_ref: string
  target_label: string | null
  note: string | null
  created_by: string | null
  created_at: string
}

export async function fetchApiLineage(name: string): Promise<ApiLineage[]> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/lineage`)
  if (!res.ok) throw new Error(`리니지 조회 실패: ${res.status}`)
  return res.json()
}

export async function addApiLineage(
  name: string,
  payload: { relation: string; target_type: string; target_ref: string; target_label?: string; note?: string },
): Promise<ApiLineage> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/lineage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `리니지 추가 실패: ${res.status}`)
  }
  return res.json()
}

export async function deleteApiLineage(name: string, edgeId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/lineage/${edgeId}`, { method: "DELETE" })
  if (!res.ok) throw new Error(`리니지 삭제 실패: ${res.status}`)
}

export type ApiCredential = {
  id: number
  scheme_name: string | null
  label: string
  type: string
  created_by: string | null
  created_at: string
}

export async function fetchApiCredentials(name: string): Promise<ApiCredential[]> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/credentials`)
  if (!res.ok) throw new Error(`자격증명 조회 실패: ${res.status}`)
  return res.json()
}

export async function createApiCredential(
  name: string,
  payload: { scheme_name?: string; label: string; type: string; values: Record<string, unknown> },
): Promise<ApiCredential> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/credentials`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `자격증명 등록 실패: ${res.status}`)
  }
  return res.json()
}

export async function deleteApiCredential(name: string, credId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/credentials/${credId}`, { method: "DELETE" })
  if (!res.ok) throw new Error(`자격증명 삭제 실패: ${res.status}`)
}

export type InvokeResult = {
  status: number
  headers: Record<string, string>
  body: string
  latency_ms: number
  error: string | null
}

export type ApiFavoriteRef = { method: string; path: string }
export type ApiFavorite = {
  id: number
  api_id: number
  api_name: string | null
  api_display_name: string | null
  method: string
  path: string
  summary: string | null
  created_at: string
}

export async function fetchAllFavorites(): Promise<ApiFavorite[]> {
  const res = await authFetch(`${BASE}/favorites`)
  if (!res.ok) throw new Error(`즐겨찾기 조회 실패: ${res.status}`)
  return res.json()
}

export async function fetchApiFavorites(name: string): Promise<ApiFavoriteRef[]> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/favorites`)
  if (!res.ok) throw new Error(`즐겨찾기 조회 실패: ${res.status}`)
  return res.json()
}

export async function addApiFavorite(name: string, method: string, path: string): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/favorites`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ method, path }),
  })
  if (!res.ok) throw new Error(`즐겨찾기 추가 실패: ${res.status}`)
}

export async function removeApiFavorite(name: string, method: string, path: string): Promise<void> {
  const q = new URLSearchParams({ method, path })
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/favorites?${q.toString()}`, { method: "DELETE" })
  if (!res.ok) throw new Error(`즐겨찾기 삭제 실패: ${res.status}`)
}

export type ApiInvocationRecord = {
  id: number
  endpoint_method: string | null
  endpoint_path: string | null
  method: string
  url: string
  status_code: number
  ok: boolean
  latency_ms: number
  error: string | null
  called_by: string | null
  created_at: string
  request_input: { path_params?: Record<string, string>; query_params?: Record<string, string>; headers?: Record<string, string>; body?: string } | null
}

export async function fetchEndpointInvocations(name: string, method: string, path: string, limit = 20): Promise<ApiInvocationRecord[]> {
  const q = new URLSearchParams({ method, path, limit: String(limit) })
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/invocations?${q.toString()}`)
  if (!res.ok) throw new Error(`호출 이력 조회 실패: ${res.status}`)
  return res.json()
}

export async function invokeApi(payload: {
  method: string
  url: string
  headers?: Record<string, string>
  body?: unknown
  api_name?: string
  credential_id?: number
  endpoint_method?: string
  endpoint_path?: string
  path_params?: Record<string, string>
  query_params?: Record<string, string>
}): Promise<InvokeResult> {
  const res = await authFetch(`${BASE}/invoke`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`호출 실패: ${res.status}`)
  return res.json()
}
