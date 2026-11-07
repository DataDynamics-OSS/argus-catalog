/**
 * API Catalog API client. 백엔드 /api/v1/apis 와 통신.
 */

import { authFetch, throwOnError } from "@/features/auth/auth-fetch"

const BASE = "/api/v1/apis"

/** HTTP 상태 코드를 함께 담는 오류 — 화면에서 404·403 등을 구분해 안내할 때 사용. */
export class ApiRequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
    this.name = "ApiRequestError"
  }
}

/** owner_email 과 일치하는 등록 사용자 정보(백엔드 조인). 미지정·미등록 이메일이면 null. */
export type ApiOwnerInfo = {
  email: string
  username: string
  first_name: string | null
  last_name: string | null
  organization: string | null
  department: string | null
}

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
  owner: ApiOwnerInfo | null
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

/** 현재 사용자가 이 API 에 할 수 있는 작업(버튼 노출 판단용). 실제 통제는 백엔드 가드. */
export type ApiPermissions = {
  /** 개요 편집·상태 변경·삭제·엔드포인트 추가/수정/삭제 — 생성자 본인 또는 관리자 */
  can_edit: boolean
  /** 자격증명·리니지 추가/삭제 — 관리자 */
  can_admin: boolean
}

export type ApiDetail = ApiSummary & {
  permissions: ApiPermissions
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
  by_category: { name: string; count: number }[]
  total_endpoints: number
  certified_apis: number
  unowned_apis: number
  open_alert_apis: number
}
export type ApiStatusHistory = {
  id: number
  from_status: string | null
  to_status: string
  note: string | null
  changed_by: string | null
  changed_at: string
}

/** 목록 정렬 — updated: 최근 수정순 · created: 최근 등록순 · name: 이름순 */
export type ApiSort = "updated" | "created" | "name"

/** 목록 검색 조건 — 빈 문자열/false 는 조건 없음. */
export type ApiListFilters = {
  search: string
  status: string
  category: string
  protocol: string
  certification: string
  /** "unowned": 소유자 미지정만 */
  owner: "" | "unowned"
  /** 확인되지 않은 Breaking 변경 알림이 있는 API 만 */
  alert: boolean
  sort: ApiSort
}

type ListParams = Partial<ApiListFilters> & { page?: number; pageSize?: number }

export async function fetchApis(params?: ListParams): Promise<PaginatedApis> {
  const q = new URLSearchParams()
  if (params?.search) q.set("search", params.search)
  if (params?.status) q.set("status", params.status)
  if (params?.category) q.set("category", params.category)
  if (params?.protocol) q.set("protocol", params.protocol)
  if (params?.certification) q.set("certification", params.certification)
  if (params?.owner) q.set("owner", params.owner)
  if (params?.alert) q.set("has_open_alert", "true")
  if (params?.sort && params.sort !== "updated") q.set("sort", params.sort)
  q.set("page", String(params?.page ?? 1))
  q.set("page_size", String(params?.pageSize ?? 20))
  const res = await authFetch(`${BASE}?${q.toString()}`)
  if (!res.ok) await throwOnError(res, "API 목록을 불러오지 못했습니다.")
  return res.json()
}

export async function fetchApiStats(): Promise<ApiStats> {
  const res = await authFetch(`${BASE}/stats`)
  if (!res.ok) throw new Error(`API 통계를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function fetchApiDetail(name: string): Promise<ApiDetail> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`)
  if (res.status === 404) throw new ApiRequestError(`'${name}' API를 찾을 수 없습니다. 삭제되었거나 이름이 바뀌었을 수 있습니다.`, 404)
  if (!res.ok) await throwOnError(res, "API 정보를 불러오지 못했습니다.")
  return res.json()
}

/** API 식별자 사용 가능 여부(중복 사전 확인). 최종 판단은 등록 시점. */
export async function checkApiName(name: string): Promise<boolean> {
  const res = await authFetch(`${BASE}/name-available?name=${encodeURIComponent(name)}`)
  if (!res.ok) throw new Error(`API 식별자를 확인하지 못했습니다. (HTTP ${res.status})`)
  return (await res.json()).available as boolean
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
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(errorDetailText(e.detail) || `엔드포인트를 추가하지 못했습니다. (HTTP ${res.status})`) }
  return res.json()
}

export async function updateEndpoint(name: string, epId: number, payload: EndpointPayload): Promise<ApiEndpoint> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/endpoints/${epId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  })
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(errorDetailText(e.detail) || `엔드포인트를 수정하지 못했습니다. (HTTP ${res.status})`) }
  return res.json()
}

export async function deleteEndpoint(name: string, epId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/endpoints/${epId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "엔드포인트를 삭제하지 못했습니다.")
}

/**
 * 오류 응답 body 의 detail 을 사람이 읽을 문장으로 변환.
 * FastAPI 입력 검증(422)은 detail 이 [{loc, msg, ...}] 배열이라 그대로 Error 에 넣으면 "[object Object]" 가 된다.
 */
function errorDetailText(detail: unknown): string | null {
  if (typeof detail === "string") return detail || null
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => {
      if (!d || typeof d !== "object") return String(d)
      const { loc, msg } = d as { loc?: unknown[]; msg?: string }
      const field = Array.isArray(loc) ? loc.filter((p) => p !== "body").join(".") : ""
      return field ? `${field}: ${msg ?? ""}` : (msg ?? "")
    }).filter(Boolean)
    return msgs.length ? `입력값 오류 — ${msgs.join(" / ")}` : null
  }
  return null
}

/** 등록 전 스펙 미리보기 결과 — name: 등록 시 사용될 식별자(입력값 또는 자동 생성) */
export type SpecPreview = {
  format: string
  title: string | null
  description: string | null
  version: string
  base_url: string | null
  endpoint_count: number
  server_count: number
  security_count: number
  name: string
  name_auto: boolean
  name_available: boolean
}

/** 스펙을 저장하지 않고 파싱만 해 요약을 받는다. 파싱·URL 조회 실패 시 서버 메시지로 오류. */
export async function previewApiSpec(payload: { spec_text?: string; spec_url?: string; name?: string }): Promise<SpecPreview> {
  const res = await authFetch(`${BASE}/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(errorDetailText(err.detail) || `스펙 미리보기를 만들지 못했습니다. (HTTP ${res.status})`)
  }
  return res.json()
}

export async function createApi(payload: ApiCreatePayload): Promise<ApiSummary> {
  const res = await authFetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(errorDetailText(err.detail) || `API를 등록하지 못했습니다. (HTTP ${res.status})`)
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
  /** 상태 변경 사유 — status 와 함께 보내면 상태 변경 이력에 기록 */
  status_note: string | null
}>

export async function updateApi(name: string, payload: ApiUpdatePayload): Promise<ApiSummary> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(errorDetailText(err.detail) || `API를 수정하지 못했습니다. (HTTP ${res.status})`)
  }
  return res.json()
}

export async function deleteApi(name: string): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "API를 삭제하지 못했습니다.")
}

export async function uploadApiSpec(name: string, payload: { spec_text?: string; spec_url?: string }): Promise<ApiSummary> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/specs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(errorDetailText(err.detail) || `스펙을 업로드하지 못했습니다. (HTTP ${res.status})`)
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
  if (!res.ok) await throwOnError(res, "버전 비교 결과를 불러오지 못했습니다.")
  return res.json()
}

export async function fetchApiStatusHistory(name: string): Promise<ApiStatusHistory[]> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/status-history`)
  if (!res.ok) throw new Error(`상태 이력을 불러오지 못했습니다. (HTTP ${res.status})`)
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
  if (!res.ok) throw new Error(`알림을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function acknowledgeApiAlert(name: string, alertId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/alerts/${alertId}/ack`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "알림을 확인 처리하지 못했습니다.")
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
  if (!res.ok) await throwOnError(res, "린팅 결과를 불러오지 못했습니다.")
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
  /** 일별(사용자 시간대 날짜) 호출 수와 그중 오류 수 — 호출 없는 날은 빠져 있음 */
  daily: { date: string; count: number; errors: number }[]
  recent: {
    id: number; method: string; url: string; status_code: number
    ok: boolean; latency_ms: number; error: string | null
    called_by: string | null; created_at: string | null
  }[]
}

export type ApiUsageFilter = {
  /** 내 호출만 */
  mine?: boolean
  /** 특정 엔드포인트만(템플릿 메서드·경로) */
  endpoint?: { method: string; path: string } | null
}

export async function fetchApiUsage(name: string, days = 30, filter: ApiUsageFilter = {}): Promise<ApiUsage> {
  const q = new URLSearchParams({ days: String(days), tz_offset: String(-new Date().getTimezoneOffset()) })
  if (filter.mine) q.set("mine", "true")
  if (filter.endpoint) { q.set("endpoint_method", filter.endpoint.method); q.set("endpoint_path", filter.endpoint.path) }
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/usage?${q.toString()}`)
  if (!res.ok) await throwOnError(res, "사용량을 불러오지 못했습니다.")
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
  /** 데이터셋 대상의 상세 화면 id(URN·이름으로 찾은 경우) */
  target_id?: number | null
}

/** 다른 API 가 이 API 를 대상으로 등록한 관계(들어오는 관계) */
export type ApiIncomingLineage = ApiLineage & { source_api_name: string; source_display_name: string | null }

export async function fetchApiIncomingLineage(name: string): Promise<ApiIncomingLineage[]> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/lineage/incoming`)
  if (!res.ok) await throwOnError(res, "들어오는 리니지를 불러오지 못했습니다.")
  return res.json()
}

/** 리니지 대상 자동완성 항목 — ref 는 저장할 식별자(API·모델·에이전트=이름, 데이터셋=URN) */
export type LineageTargetOption = { ref: string; label: string; id?: number | null }

export async function searchLineageTargets(type: string, q: string, signal?: AbortSignal): Promise<LineageTargetOption[]> {
  const params = new URLSearchParams({ type, q })
  const res = await authFetch(`${BASE}/lineage-targets?${params.toString()}`, { signal })
  if (!res.ok) await throwOnError(res, "리니지 대상을 검색하지 못했습니다.")
  return res.json()
}

export async function fetchApiLineage(name: string): Promise<ApiLineage[]> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/lineage`)
  if (!res.ok) throw new Error(`리니지를 불러오지 못했습니다. (HTTP ${res.status})`)
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
    throw new Error(errorDetailText(err.detail) || `리니지를 추가하지 못했습니다. (HTTP ${res.status})`)
  }
  return res.json()
}

export async function deleteApiLineage(name: string, edgeId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/lineage/${edgeId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "리니지를 삭제하지 못했습니다.")
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
  if (!res.ok) throw new ApiRequestError(`자격증명을 불러오지 못했습니다. (HTTP ${res.status})`, res.status)
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
    throw new Error(errorDetailText(err.detail) || `자격증명을 등록하지 못했습니다. (HTTP ${res.status})`)
  }
  return res.json()
}

export async function deleteApiCredential(name: string, credId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/credentials/${credId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "자격증명을 삭제하지 못했습니다.")
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
  api_status: string | null
  api_protocol: string | null
  method: string
  path: string
  summary: string | null
  /** 현재 엔드포인트 목록에 없음(새 스펙에서 제거·경로 변경 등) */
  endpoint_missing: boolean
  created_at: string
}

export async function fetchAllFavorites(): Promise<ApiFavorite[]> {
  const res = await authFetch(`${BASE}/favorites`)
  if (!res.ok) await throwOnError(res, "즐겨찾기를 불러오지 못했습니다.")
  return res.json()
}

export async function fetchApiFavorites(name: string): Promise<ApiFavoriteRef[]> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/favorites`)
  if (!res.ok) throw new Error(`즐겨찾기를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function addApiFavorite(name: string, method: string, path: string): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/favorites`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ method, path }),
  })
  if (!res.ok) await throwOnError(res, "즐겨찾기에 추가하지 못했습니다.")
}

export async function removeApiFavorite(name: string, method: string, path: string): Promise<void> {
  const q = new URLSearchParams({ method, path })
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/favorites?${q.toString()}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "즐겨찾기를 해제하지 못했습니다.")
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
  if (!res.ok) throw new Error(`호출 이력을 불러오지 못했습니다. (HTTP ${res.status})`)
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
  if (!res.ok) {
    // 400 = 보안 정책(SSRF) 차단 — 서버가 차단 사유를 detail 로 돌려준다
    const err = await res.json().catch(() => ({}))
    throw new Error(typeof err.detail === "string" ? err.detail : `엔드포인트를 호출하지 못했습니다. (HTTP ${res.status})`)
  }
  return res.json()
}
