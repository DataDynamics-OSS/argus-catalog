import { authFetch, throwOnError } from "@/features/auth/auth-fetch"
import type { Datasource } from "@/features/datasets/data/schema"
import { errorMessage } from "@/lib/notify"

const BASE = "/api/v1/catalog"

// 데이터 소스 응답에 소속 시스템 정보가 추가됨
export type DatasourceRow = Datasource & { system_id?: number | null; system_name?: string | null }

export type ConnectionTestResult = { ok: boolean; message: string; latency_ms?: number; unsupported?: boolean }

export async function fetchDatasources(): Promise<DatasourceRow[]> {
  const res = await authFetch(`${BASE}/datasources`)
  if (!res.ok) throw new Error(`Failed: ${res.status}`)
  return res.json()
}

export async function createDatasource(payload: {
  name: string
  type: string
  origin: string
}): Promise<Datasource> {
  const res = await authFetch(`${BASE}/datasources`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "데이터 소스를 생성하지 못했습니다.")
  return res.json()
}

export async function updateDatasource(id: number, payload: { name?: string; origin?: string }): Promise<Datasource> {
  const res = await authFetch(`${BASE}/datasources/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "데이터 소스를 수정하지 못했습니다.")
  return res.json()
}

export async function deleteDatasource(id: number, force = false): Promise<void> {
  const res = await authFetch(`${BASE}/datasources/${id}?force=${force}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "데이터 소스를 삭제하지 못했습니다.")
}

export type DeleteImpact = {
  dataset_count: number
  removed_dataset_count: number
  external_lineage_count: number
  alert_rule_count: number
  change_request_count: number
  model_count: number
  mcp_deployment_count: number
  /** 함께 지워지는 수집 실행 이력 수 */
  sync_run_count: number
  /** 대기·실행 중 수집 — 있으면 서버가 삭제를 거부한다(409) */
  active_sync_run_count: number
  /** 예약 수집이 켜져 있는지(설정도 함께 지워짐) */
  sync_scheduled: boolean
  federation_allowlisted: boolean
}
export const EMPTY_IMPACT: DeleteImpact = {
  dataset_count: 0,
  removed_dataset_count: 0,
  external_lineage_count: 0,
  alert_rule_count: 0,
  change_request_count: 0,
  model_count: 0,
  mcp_deployment_count: 0,
  sync_run_count: 0,
  active_sync_run_count: 0,
  sync_scheduled: false,
  federation_allowlisted: false,
}

export async function fetchDeleteImpact(id: number): Promise<DeleteImpact> {
  const res = await authFetch(`${BASE}/datasources/${id}/delete-impact`)
  if (!res.ok) await throwOnError(res, "삭제 영향을 불러오지 못했습니다.")
  return { ...EMPTY_IMPACT, ...(await res.json()) }
}

/**
 * 연결 테스트. 기존 데이터 소스면 datasourceId 를 넘겨, 비워 둔(마스킹된) 비밀번호를
 * 서버가 저장값으로 채워 테스트하게 한다. 네트워크 오류도 실패 결과로 돌려준다.
 */
export async function testDatasourceConnection(
  type: string,
  config: Record<string, unknown>,
  datasourceId?: number,
): Promise<ConnectionTestResult> {
  try {
    const res = await authFetch(`${BASE}/datasources/test-connection`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, config, datasource_id: datasourceId ?? null }),
    })
    if (!res.ok) await throwOnError(res, "연결을 테스트하지 못했습니다.")
    return await res.json()
  } catch (e) {
    return { ok: false, message: errorMessage(e) ?? String(e) }
  }
}

export type StoredConfig = {
  config: Record<string, unknown>
  /** 저장된 비밀값 키 — config 에는 빈 문자열로 마스킹되어 온다 */
  secretFields: string[]
}

export async function fetchDatasourceConfig(id: number): Promise<StoredConfig | null> {
  const res = await authFetch(`${BASE}/datasources/${id}/configuration`)
  if (res.status === 404) return null
  if (!res.ok) await throwOnError(res, "연결 설정을 불러오지 못했습니다.")
  const data = await res.json()
  return { config: data.config ?? {}, secretFields: data.secret_fields ?? [] }
}

export async function saveDatasourceConfig(id: number, config: Record<string, unknown>): Promise<void> {
  // 비밀값을 빈 문자열로 보내면 서버가 저장된 값을 유지한다(마스킹 왕복 안전).
  const res = await authFetch(`${BASE}/datasources/${id}/configuration`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config }),
  })
  if (!res.ok) await throwOnError(res, "연결 설정을 저장하지 못했습니다.")
}

// ---------------------------------------------------------------------------
// 수집 범위·실행 (design/datasource-sync-scope-design.md)
// ---------------------------------------------------------------------------

/** 수집 범위 항목 — tables 가 없으면 컨테이너 전체(새 테이블 포함), 있으면 그 테이블만(컨테이너 안 상대 이름). */
export type SyncScopeItem = { path: string[]; tables?: string[] }
/** 제외 패턴(glob, 대소문자 무시) — path 가 없으면 모든 컨테이너에서. */
export type SyncScopeExclude = { path?: string[]; table_pattern: string }
export type SyncScope = { include_all: boolean; include: SyncScopeItem[]; exclude?: SyncScopeExclude[] }

export type SyncSettings = {
  datasource_id: number
  scope: SyncScope | null
  executor: "BUILTIN" | "METADATA_SYNC" | null
  container_label: string
  supports_containers: boolean
  updated_by: string | null
  updated_at: string | null
  removed_outside?: number | null
  nested_containers?: boolean
  /** 쿼리 엔진인데 카탈로그에 '수집'(SYNC)으로 연결되지 않음 — 수집하면 409(DEF-068) */
  sync_catalog_missing?: boolean
  schedule_enabled?: boolean
  schedule_interval_minutes?: number | null
  next_scheduled_at?: string | null
}

export type SyncBrowseItem = {
  name: string
  path: string[]
  system: boolean
  expandable: boolean
  /** container: 범위로 고르는 컨테이너, group: 펼치기만(Trino 스키마), table: 테이블 */
  kind?: "container" | "group" | "table"
  /** table 일 때 — 범위에 넣을 컨테이너 경로와 그 안의 상대 이름 */
  container?: string[] | null
  table?: string | null
  collected: number
  removed: number
}
export type SyncBrowseResult = { path: string[]; items: SyncBrowseItem[]; total: number; next_cursor: number | null }
export type SyncScopePreview = { outside_count: number; outside_containers: string[]; in_scope_count: number }
export type SyncRunSummary = { active: number; failed_24h: number; succeeded_24h: number }
export type SyncServiceStatus = {
  reachable: boolean
  workers: number | null
  running: number | null
  queued: number | null
  version: string | null
  message: string | null
}

export type SyncRunStatus = "QUEUED" | "RUNNING" | "SUCCEEDED" | "PARTIAL" | "FAILED" | "CANCELLED"

export type SyncRun = {
  id: number
  datasource_id: number
  /** 데이터 소스의 외부 식별자(문자열 datasource_id) — 데이터셋 목록 필터 링크용 */
  datasource_key?: string | null
  datasource_name: string | null
  datasource_type: string | null
  trigger_type: string
  executor: string
  scope: SyncScope | null
  status: SyncRunStatus
  phase: string | null
  current_target: string | null
  progress_done: number
  progress_total: number | null
  created_count: number
  updated_count: number
  removed_count: number
  failed_count: number
  items_total: number
  error_message: string | null
  requested_by: string | null
  cancel_requested_by: string | null
  queued_at: string | null
  started_at: string | null
  finished_at: string | null
  heartbeat_at: string | null
  silent_seconds: number | null
}

export type SyncRunItem = { id: number; level: string; target: string | null; message: string; occurred_at: string | null }

export const ACTIVE_SYNC_STATUSES: ReadonlySet<SyncRunStatus> = new Set(["QUEUED", "RUNNING"])

export async function fetchSyncSettings(datasourceId: number): Promise<SyncSettings> {
  const res = await authFetch(`${BASE}/datasources/${datasourceId}/sync-scope`)
  if (!res.ok) await throwOnError(res, "수집 범위를 불러오지 못했습니다.")
  return res.json()
}

/** 예약 수집 켜기·끄기·주기(분, 15 ~ 10080). 저장된 수집 범위로 돈다. */
export async function saveSyncSchedule(
  datasourceId: number,
  enabled: boolean,
  intervalMinutes?: number | null,
): Promise<SyncSettings> {
  const res = await authFetch(`${BASE}/datasources/${datasourceId}/sync-schedule`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled, interval_minutes: intervalMinutes ?? null }),
  })
  if (!res.ok) await throwOnError(res, "예약을 저장하지 못했습니다.")
  return res.json()
}

export async function saveSyncScope(
  datasourceId: number,
  scope: SyncScope | null,
  removeOutside = false,
): Promise<SyncSettings> {
  const res = await authFetch(`${BASE}/datasources/${datasourceId}/sync-scope`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope, remove_outside: removeOutside }),
  })
  if (!res.ok) await throwOnError(res, "수집 범위를 저장하지 못했습니다.")
  return res.json()
}

/** 수집 시작 — scope 를 주면 이번 한 번만 그 범위로(저장된 범위는 그대로). */
export async function startSyncRun(datasourceId: number, scope?: SyncScope): Promise<SyncRun> {
  const res = await authFetch(`${BASE}/datasources/${datasourceId}/sync-runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scope ? { scope } : {}),
  })
  if (!res.ok) await throwOnError(res, "수집을 시작하지 못했습니다.")
  return res.json()
}

export async function fetchDatasourceSyncRuns(datasourceId: number, limit = 10): Promise<SyncRun[]> {
  const res = await authFetch(`${BASE}/datasources/${datasourceId}/sync-runs?limit=${limit}`)
  if (!res.ok) await throwOnError(res, "수집 이력을 불러오지 못했습니다.")
  return res.json()
}

export async function previewSyncScope(datasourceId: number, scope: SyncScope | null): Promise<SyncScopePreview> {
  const res = await authFetch(`${BASE}/datasources/${datasourceId}/sync-scope/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  })
  if (!res.ok) await throwOnError(res, "범위 영향을 확인하지 못했습니다.")
  return res.json()
}

export async function browseDatasource(
  datasourceId: number,
  path: string[] = [],
  opts: { q?: string; cursor?: number } = {},
): Promise<SyncBrowseResult> {
  const qs = new URLSearchParams()
  for (const p of path) qs.append("path", p)
  if (opts.q) qs.set("q", opts.q)
  if (opts.cursor) qs.set("cursor", String(opts.cursor))
  const res = await authFetch(`${BASE}/datasources/${datasourceId}/browse?${qs}`)
  if (!res.ok) await throwOnError(res, "원본 목록을 불러오지 못했습니다.")
  return res.json()
}

export async function fetchSyncRuns(
  filters: { status?: string; datasourceId?: number; since?: string; cursor?: number; limit?: number } = {},
): Promise<SyncRun[]> {
  const qs = new URLSearchParams()
  if (filters.status) qs.set("status", filters.status)
  if (filters.datasourceId) qs.set("datasource_id", String(filters.datasourceId))
  if (filters.since) qs.set("since", filters.since)
  if (filters.cursor) qs.set("cursor", String(filters.cursor))
  qs.set("limit", String(filters.limit ?? 50))
  const res = await authFetch(`${BASE}/sync-runs?${qs}`)
  if (!res.ok) await throwOnError(res, "수집 실행 목록을 불러오지 못했습니다.")
  return res.json()
}

export async function fetchSyncRun(runId: number): Promise<SyncRun> {
  const res = await authFetch(`${BASE}/sync-runs/${runId}`)
  if (!res.ok) await throwOnError(res, "수집 실행을 불러오지 못했습니다.")
  return res.json()
}

export async function fetchSyncRunSummary(): Promise<SyncRunSummary> {
  const res = await authFetch(`${BASE}/sync-runs/summary`)
  if (!res.ok) await throwOnError(res, "수집 요약을 불러오지 못했습니다.")
  return res.json()
}

export async function fetchSyncServiceStatus(): Promise<SyncServiceStatus> {
  const res = await authFetch(`${BASE}/sync-service/status`)
  if (!res.ok) await throwOnError(res, "수집 서비스 상태를 불러오지 못했습니다.")
  return res.json()
}

export async function fetchSyncRunItems(
  runId: number,
  opts: { level?: string; q?: string; limit?: number } = {},
): Promise<SyncRunItem[]> {
  const qs = new URLSearchParams({ limit: String(opts.limit ?? 1000) })
  if (opts.level) qs.set("level", opts.level)
  if (opts.q) qs.set("q", opts.q)
  const res = await authFetch(`${BASE}/sync-runs/${runId}/items?${qs}`)
  if (!res.ok) await throwOnError(res, "실패 항목을 불러오지 못했습니다.")
  return res.json()
}

export async function cancelSyncRun(runId: number): Promise<SyncRun> {
  const res = await authFetch(`${BASE}/sync-runs/${runId}/cancel`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "수집을 취소하지 못했습니다.")
  return res.json()
}

export async function retrySyncRun(runId: number): Promise<SyncRun> {
  const res = await authFetch(`${BASE}/sync-runs/${runId}/retry`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "다시 실행하지 못했습니다.")
  return res.json()
}
