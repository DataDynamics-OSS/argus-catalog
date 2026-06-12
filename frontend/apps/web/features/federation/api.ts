import { authFetch, throwOnError } from "@/features/auth/auth-fetch"

const BASE = "/api/v1/federation"

// ---------------------------------------------------------------------------
// 타입 (백엔드 app/federation/schemas.py 와 대응)
// ---------------------------------------------------------------------------

export type FederationMode = "LIVE" | "HARVEST" | "HYBRID"
export type FederationStatus = "ACTIVE" | "PAUSED"

export type FederatedInstance = {
  id: number
  instance_key: string
  name: string
  base_url: string
  has_auth_token: boolean
  mode: FederationMode
  sync_interval_sec: number
  status: FederationStatus
  description: string | null
  display_fields: string[] | null
  created_at: string
  updated_at: string
}

export type FederatedInstanceCreate = {
  instance_key: string
  name: string
  base_url: string
  auth_token?: string | null
  mode?: FederationMode
  sync_interval_sec?: number
  description?: string | null
  display_fields?: string[] | null
}

export type FederatedInstanceUpdate = Partial<{
  name: string
  base_url: string
  auth_token: string | null
  mode: FederationMode
  sync_interval_sec: number
  status: FederationStatus
  description: string | null
  display_fields: string[] | null
}>

// ---------------------------------------------------------------------------
// Capability — 노출자 advertise / 노출자 정책 / 소비자 선택
// ---------------------------------------------------------------------------

export type CapabilityItem = {
  key: string
  label: string
  group: string
  group_label: string
}

export type CapabilityGroup = { group: string; label: string }

export type CapabilitiesResponse = {
  items: CapabilityItem[]
  groups: CapabilityGroup[]
  exposed: string[]
}

export type ExportPolicy = CapabilitiesResponse

export type InstanceHealth = {
  instance_key: string
  reachable: boolean
  status_code: number | null
  version: string | null
  latency_ms: number | null
  error: string | null
}

export type FederationSyncResult = {
  instance_key: string
  status: "SUCCESS" | "FAILED"
  datasets_seen: number
  datasets_upserted: number
  datasets_embedded: number
  datasets_pruned: number
  error: string | null
}

export type FederationSyncRun = {
  id: number
  instance_id: number
  status: string
  datasets_total: number
  datasets_seen: number
  datasets_upserted: number
  datasets_embedded: number
  datasets_pruned: number
  // 단계별 진행률(가중 % 계산용)
  phase: string
  phase_done: number
  phase_total: number
  error: string | null
  started_at: string
  finished_at: string | null
}

export type FederatedSearchHit = {
  urn: string
  name: string
  datasource_name: string | null
  datasource_type: string | null
  description: string | null
  origin: string | null
  score: number
  match_type: string
  source_instance_key: string | null
  source_instance_name: string | null
  source_base_url: string | null
}

export type FederatedSearchResponse = {
  query: string
  items: FederatedSearchHit[]
  total: number
  instances_queried: number
  instances_failed: string[]
}

export type FederatedLineageNode = {
  urn: string
  name: string | null
  datasource_name: string | null
  datasource_type: string | null
  source_instance_key: string | null
  source_instance_name: string | null
  unresolved: boolean
}

export type FederatedLineageEdge = {
  source_urn: string
  target_urn: string
  relation_type: string
  reported_by: string | null
}

export type FederatedLineageGraph = {
  root_urn: string
  depth: number
  nodes: FederatedLineageNode[]
  edges: FederatedLineageEdge[]
}

// 원격 드릴다운(peer 실시간 조회) 상세 페이로드 — peer export/dataset 응답을 감싼다.
export type FederatedDatasetSchemaField = {
  field_path: string
  display_name?: string | null
  field_type?: string | null
  native_type?: string | null
  nullable?: string | null
  is_primary_key?: string | null
  is_unique?: string | null
  is_indexed?: string | null
  is_partition_key?: string | null
  is_distribution_key?: string | null
  description?: string | null
  pii_type?: string | null
  ordinal?: number | null
}

export type FederatedDatasetMetadata = {
  name?: string
  description?: string | null
  origin?: string | null
  status?: string | null
  is_synced?: string | null
  urn?: string | null
  qualified_name?: string | null
  table_type?: string | null
  storage_format?: string | null
  datasource?: { name?: string; type?: string } | null
  schema?: FederatedDatasetSchemaField[]
  tags?: string[]
  owners?: { name: string; type: string }[]
  properties?: Record<string, unknown>
  ddl?: string | null
  glossary?: string[]
  // 확장 메타데이터(생명주기·물리·거버넌스·비즈니스·사용·품질·요약·비고) — 값 있는 항목만
  extended?: Record<string, unknown> | null
}

export type FederatedSample = {
  format?: string
  columns: string[]
  rows: (string | null)[][]
  row_count: number
}

export type FederatedDatasetDetail = {
  federated_urn?: string
  remote_urn?: string
  source_instance_key?: string | null
  source_instance_name?: string | null
  source_base_url?: string | null
  // 소비자(허브) 표시 선택 — null=전부 표시. (노출자 노출 ∩ 이 선택)만 렌더.
  display_fields?: string[] | null
  metadata?: FederatedDatasetMetadata
  // 프론트에서 오류 표시용으로 채우는 필드(백엔드 응답엔 없음)
  _error?: string
}

async function jsonOrThrow<T>(res: Response, action: string): Promise<T> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `${action} 실패: ${res.status}`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Peer 레지스트리
// ---------------------------------------------------------------------------

export async function listInstances(): Promise<FederatedInstance[]> {
  return jsonOrThrow(await authFetch(`${BASE}/instances`), "인스턴스 조회")
}

export async function createInstance(
  payload: FederatedInstanceCreate
): Promise<FederatedInstance> {
  const res = await authFetch(`${BASE}/instances`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  return jsonOrThrow(res, "인스턴스 등록")
}

export async function updateInstance(
  id: number,
  payload: FederatedInstanceUpdate
): Promise<FederatedInstance> {
  const res = await authFetch(`${BASE}/instances/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  return jsonOrThrow(res, "인스턴스 수정")
}

export async function deleteInstance(id: number): Promise<void> {
  const res = await authFetch(`${BASE}/instances/${id}`, { method: "DELETE" })
  if (!res.ok && res.status !== 204)
    await throwOnError(res, "인스턴스 삭제 실패")
}

export async function checkHealth(id: number): Promise<InstanceHealth> {
  return jsonOrThrow(
    await authFetch(`${BASE}/instances/${id}/health`),
    "상태 점검"
  )
}

export async function harvestInstance(
  id: number,
  full = false
): Promise<FederationSyncResult> {
  const res = await authFetch(`${BASE}/instances/${id}/harvest?full=${full}`, {
    method: "POST",
  })
  return jsonOrThrow(res, "가져오기")
}

export async function listSyncRuns(
  id: number,
  limit = 20
): Promise<FederationSyncRun[]> {
  return jsonOrThrow(
    await authFetch(`${BASE}/instances/${id}/sync-runs?limit=${limit}`),
    "동기화 이력 조회"
  )
}

// ---------------------------------------------------------------------------
// 연합 검색 / 드릴다운 / 리니지
// ---------------------------------------------------------------------------

export async function federatedSearch(
  q: string,
  opts?: { limit?: number; includeLocal?: boolean }
): Promise<FederatedSearchResponse> {
  const params = new URLSearchParams({ q })
  if (opts?.limit) params.set("limit", String(opts.limit))
  if (opts?.includeLocal === false) params.set("include_local", "false")
  return jsonOrThrow(await authFetch(`${BASE}/search?${params}`), "연합 검색")
}

export async function datasetDetail(
  urn: string
): Promise<FederatedDatasetDetail> {
  const params = new URLSearchParams({ urn })
  return jsonOrThrow(
    await authFetch(`${BASE}/datasets/detail?${params}`),
    "상세 조회"
  )
}

export type FederatedImportResult = { id: number; urn: string; name: string }

/** 페더레이션 데이터셋을 로컬 카탈로그로 승격(import). admin 전용. */
export async function importDataset(
  federatedUrn: string
): Promise<FederatedImportResult> {
  const res = await authFetch(`${BASE}/datasets/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ federated_urn: federatedUrn }),
  })
  return jsonOrThrow(res, "로컬로 가져오기")
}

export async function datasetSample(
  urn: string,
  limit = 100
): Promise<FederatedSample> {
  const params = new URLSearchParams({ urn, limit: String(limit) })
  return jsonOrThrow(
    await authFetch(`${BASE}/datasets/sample?${params}`),
    "샘플 조회"
  )
}

export async function datasetLineage(
  urn: string,
  depth = 1
): Promise<FederatedLineageGraph> {
  const params = new URLSearchParams({ urn, depth: String(depth) })
  return jsonOrThrow(
    await authFetch(`${BASE}/datasets/lineage?${params}`),
    "리니지 조회"
  )
}

// ---------------------------------------------------------------------------
// 인스턴스별 미러 데이터셋 탐색(browse) — 분류체계 트리
// ---------------------------------------------------------------------------

export type FederatedBrowseDataset = {
  federated_urn: string
  remote_urn: string
  name: string
  display_name: string | null
  summary: string | null
  description: string | null
  qualified_name: string | null
  origin: string | null
  field_count: number
  remote_created_at: string | null
  remote_updated_at: string | null
  harvested_at: string | null
}

export type FederatedBrowseDatasource = {
  datasource_name: string
  datasource_type: string | null
  dataset_count: number
  datasets: FederatedBrowseDataset[]
}

export type FederatedBrowseResponse = {
  instance_id: number
  instance_key: string
  instance_name: string
  total_datasets: number
  truncated: boolean
  datasources: FederatedBrowseDatasource[]
}

export async function browseInstanceDatasets(
  id: number,
  q?: string
): Promise<FederatedBrowseResponse> {
  const params = new URLSearchParams()
  if (q && q.trim()) params.set("q", q.trim())
  const qs = params.toString()
  return jsonOrThrow(
    await authFetch(`${BASE}/instances/${id}/datasets${qs ? `?${qs}` : ""}`),
    "데이터셋 탐색"
  )
}

// ---------------------------------------------------------------------------
// 관측성 (observability)
// ---------------------------------------------------------------------------

export type FederationStatsInstance = {
  id: number
  instance_key: string
  name: string
  mode: FederationMode
  status: FederationStatus
  mirror_datasets: number
  mirror_lineage: number
  last_sync_status: string | null
  last_sync_started_at: string | null
  last_sync_finished_at: string | null
  last_sync_seen: number | null
  last_sync_embedded: number | null
  last_error: string | null
  breaker_open: boolean
  breaker_failures: number
}

export type FederationStats = {
  total_instances: number
  active_instances: number
  total_mirror_datasets: number
  total_mirror_lineage: number
  instances: FederationStatsInstance[]
}

export async function fetchStats(): Promise<FederationStats> {
  return jsonOrThrow(await authFetch(`${BASE}/stats`), "관측 지표 조회")
}

// ---------------------------------------------------------------------------
// 노출자 정책 / capabilities (advertise·probe)
// ---------------------------------------------------------------------------

export async function getExportPolicy(): Promise<ExportPolicy> {
  return jsonOrThrow(await authFetch(`${BASE}/export-policy`), "노출 정책 조회")
}

export async function updateExportPolicy(exposed: string[]): Promise<ExportPolicy> {
  const res = await authFetch(`${BASE}/export-policy`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ exposed }),
  })
  return jsonOrThrow(res, "노출 정책 저장")
}

/** 등록 전 peer 의 노출 항목 사전 조회. */
export async function probeCapabilities(
  base_url: string,
  auth_token?: string | null
): Promise<CapabilitiesResponse> {
  const res = await authFetch(`${BASE}/probe-capabilities`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base_url, auth_token: auth_token || null }),
  })
  return jsonOrThrow(res, "노출 항목 조회")
}

/** 등록된 peer 의 노출 항목 조회(수정 다이얼로그용). */
export async function instanceCapabilities(
  id: number
): Promise<CapabilitiesResponse> {
  return jsonOrThrow(
    await authFetch(`${BASE}/instances/${id}/capabilities`),
    "노출 항목 조회"
  )
}
