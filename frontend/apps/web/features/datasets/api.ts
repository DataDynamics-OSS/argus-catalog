/**
 * Dataset API client.
 */

import type { DatasetDetail, DatasetSummary, Datasource, SchemaField } from "./data/schema"
import { authFetch, throwOnError } from "@/features/auth/auth-fetch" // Added for SSO AUTH

const BASE = "/api/v1/catalog"

type DatasetListParams = {
  search?: string
  datasource?: string
  origin?: string
  tag?: string
  status?: string
  orgId?: number
  systemId?: number
  categoryId?: number
  taxonomyId?: number
  uncategorized?: boolean
  /** 태그 id 쉼표 목록 */
  tagIds?: string
  /** 용어집 용어 id */
  termId?: number
  page?: number
  pageSize?: number
  /** 카드 보기용 필드(컬럼 미리보기)도 받는다 */
  card?: boolean
}

export type PaginatedDatasets = {
  items: DatasetSummary[]
  total: number
  page: number
  page_size: number
}

export async function fetchDatasets(
  params?: DatasetListParams
): Promise<PaginatedDatasets> {
  const query = new URLSearchParams()
  if (params?.search) query.set("search", params.search)
  if (params?.datasource) query.set("datasource", params.datasource)
  if (params?.origin) query.set("origin", params.origin)
  if (params?.tag) query.set("tag", params.tag)
  if (params?.status) query.set("status", params.status)
  if (params?.orgId) query.set("org_id", String(params.orgId))
  if (params?.systemId) query.set("system_id", String(params.systemId))
  if (params?.categoryId) query.set("category_id", String(params.categoryId))
  if (params?.taxonomyId) query.set("taxonomy_id", String(params.taxonomyId))
  if (params?.uncategorized) query.set("uncategorized", "true")
  if (params?.tagIds) query.set("tag_id", params.tagIds)
  if (params?.termId) query.set("term_id", String(params.termId))
  query.set("page", String(params?.page ?? 1))
  query.set("page_size", String(params?.pageSize ?? 20))
  if (params?.card) query.set("card", "true")

  const res = await authFetch(`${BASE}/datasets?${query.toString()}`)
  if (!res.ok) throw new Error(`데이터셋을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function fetchDataset(id: number): Promise<DatasetDetail> {
  const res = await authFetch(`${BASE}/datasets/${id}`)
  if (!res.ok) {
    // 서버 안내(detail)를 살린다 — 404 는 "데이터셋을 찾을 수 없습니다." (DEF-082)
    const err = await res.json().catch(() => ({}))
    const detail = typeof err?.detail === "string" ? err.detail : null
    throw new Error(detail || (res.status === 404 ? "데이터셋을 찾을 수 없습니다."
      : `데이터셋을 불러오지 못했습니다. (HTTP ${res.status})`))
  }
  return res.json()
}

export async function createDataset(payload: {
  name: string
  display_name?: string
  datasource_id: number
  summary?: string
  description?: string
  origin?: string
  qualified_name?: string
  schema_fields?: {
    field_path: string
    display_name?: string
    field_type: string
    native_type?: string
    description?: string
    nullable?: string
    ordinal?: number
  }[]
  tags?: number[]
  owners?: { owner_name: string; owner_type: string }[]
}): Promise<DatasetDetail> {
  const res = await authFetch(`${BASE}/datasets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `데이터셋을 생성하지 못했습니다. (HTTP ${res.status})`)
  }
  return res.json()
}

export type DatasetMetadataUpdate = {
  ingestion_frequency?: string | null
  ingestion_time?: string | null
  ingestion_day?: string | null
  ingestion_timezone?: string | null
  ingestion_cron?: string | null
  ingestion_mode?: string | null
  update_type?: string | null
  freshness_sla?: string | null
  last_ingested_at?: string | null
  retention_days?: number | null
  purge_days?: number | null
  data_category?: string | null
  data_format?: string | null
  compression?: string | null
  encoding?: string | null
  row_count?: number | null
  byte_size?: number | null
  file_count?: number | null
  sensitivity?: string | null
  contains_pii?: boolean | null
  pii_fields?: string[] | null
  compliance_tags?: string[] | null
  tier?: string | null
  certification?: string | null
  steward?: string | null
  quality_status?: string | null
  show_quality_score?: boolean | null
  note?: string | null
} & AiReadyUpdate

/** AI-Ready 항목 (TODO-096). null 을 보내면 지운다. */
export type AiReadyUpdate = {
  legal_basis?: string | null
  publisher?: string | null
  landing_page?: string | null
  contact_email?: string | null
  data_language?: string | null
  persistent_id?: string | null
  temporal_start?: string | null
  temporal_end?: string | null
  spatial_coverage?: string | null
  data_version?: string | null
  version_notes?: string | null
  license?: string | null
  license_url?: string | null
  rights?: string | null
  allowed_uses?: string[] | null
  contains_synthetic?: boolean | null
  contains_machine_annotation?: boolean | null
  data_biases?: string | null
  known_limitations?: string | null
  missing_data_info?: string | null
  missing_value_notation?: string | null
  privacy_measures?: string | null
}

export type AiReadyCode = { code: string; label: string }
export type AiReadyDefaults = {
  /** 설정 › AI-Ready 의 기관 기본값 — 데이터셋에 값이 없을 때 쓴다 */
  defaults: {
    publisher: string | null
    data_language: string
    license: string | null
    license_url: string | null
    contact_email: string | null
  }
  licenses: AiReadyCode[]
  allowed_uses: AiReadyCode[]
}

export async function fetchAiReadyDefaults(): Promise<AiReadyDefaults> {
  const res = await authFetch(`${BASE}/ai-ready/defaults`)
  if (!res.ok) await throwOnError(res, "AI-Ready 기본값을 불러오지 못했습니다.")
  return res.json()
}

export type AiReadySuggestions = {
  profile: { id: number; row_count: number; profiled_at: string | null } | null
  temporal: { temporal_start: string; temporal_end: string; column: string } | null
  missing: { missing_data_info: string; missing_ratio: number; missing_columns: number } | null
}

/** 최신 프로파일에서 계산한 시간범위·결측 정보 제안 (저장하지 않음) */
export async function fetchAiReadySuggestions(datasetId: number): Promise<AiReadySuggestions> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/ai-ready/suggestions`)
  if (!res.ok) await throwOnError(res, "프로파일에서 제안을 만들지 못했습니다.")
  return res.json()
}

// ---------------------------------------------------------------------------
// AI-Ready 점검 (TODO-096 2단계) — 가이드라인 부록 4 체크리스트
// ---------------------------------------------------------------------------

export type AiReadyStatus = "PASS" | "FAIL" | "NA"
export type AiReadyLevel = "REQUIRED" | "RECOMMENDED"
export type AiReadyRate = { passed: number; total: number; rate: number | null }

export type AiReadyItem = {
  key: string
  level: AiReadyLevel
  /** 최종 판정 — 수동 판정이 있으면 그것, 없으면 자동 판정. null = 판정 필요 */
  status: AiReadyStatus | null
  source: "AUTO" | "MANUAL" | null
  auto_status: AiReadyStatus | null
  evidence: string
  group: string
  text: string
  /** AUTO(카탈로그 정보로 계산) · MANUAL(담당자 판정) · EXTERNAL(제공 플랫폼의 일) */
  mode: "AUTO" | "MANUAL" | "EXTERNAL"
  hint: string
  judgment: { status: AiReadyStatus; note: string | null; judged_by: string | null; judged_at: string | null } | null
}

export type AiReadyMinCheck = { key: string; label: string; met: boolean | null; detail: string }

export type AiReadyCheck = {
  dataset_id: number
  required: AiReadyRate
  recommended: AiReadyRate
  counts: { PASS: number; FAIL: number; NA: number; UNKNOWN: number }
  min_requirements: { met: boolean; checks: AiReadyMinCheck[] }
  items: AiReadyItem[]
  profiled_at: string | null
}

export async function fetchAiReadyCheck(datasetId: number): Promise<AiReadyCheck> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/ai-ready/check`)
  if (!res.ok) await throwOnError(res, "AI-Ready 점검 결과를 불러오지 못했습니다.")
  return res.json()
}

/** 수동 판정 저장 — 자동 판정보다 우선한다. 저장 뒤 전체 점검 결과를 돌려준다. */
export async function saveAiReadyJudgment(
  datasetId: number, itemKey: string, body: { status: AiReadyStatus; note?: string | null },
): Promise<AiReadyCheck> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/ai-ready/check/${encodeURIComponent(itemKey)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) await throwOnError(res, "판정을 저장하지 못했습니다.")
  return res.json()
}

/** 수동 판정을 지워 자동 판정으로 되돌린다. */
export async function clearAiReadyJudgment(datasetId: number, itemKey: string): Promise<AiReadyCheck> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/ai-ready/check/${encodeURIComponent(itemKey)}`, {
    method: "DELETE",
  })
  if (!res.ok) await throwOnError(res, "수동 판정을 지우지 못했습니다.")
  return res.json()
}

// ---------------------------------------------------------------------------
// AI-Ready 내보내기 (TODO-096 3단계) — 배포본, DCAT · Croissant · 데이터 카드
// ---------------------------------------------------------------------------

export type DatasetDistribution = {
  id: number
  dataset_id: number
  title: string | null
  description: string | null
  format: string | null
  media_type: string | null
  /** 형식에서 추정한 MIME 까지 반영한 값 */
  effective_media_type: string | null
  access_url: string
  download_url: string | null
  byte_size: number | null
  checksum_algorithm: string | null
  checksum: string | null
  ordinal: number
  created_at: string | null
  updated_at: string | null
}

export type DistributionInput = {
  title?: string | null
  description?: string | null
  format?: string | null
  media_type?: string | null
  access_url: string
  download_url?: string | null
  byte_size?: number | null
  checksum_algorithm?: string | null
  checksum?: string | null
}

export async function fetchDistributions(datasetId: number): Promise<DatasetDistribution[]> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/distributions`)
  if (!res.ok) await throwOnError(res, "배포본을 불러오지 못했습니다.")
  return res.json()
}

export async function saveDistribution(
  datasetId: number, body: DistributionInput, distId?: number,
): Promise<DatasetDistribution> {
  const url = distId ? `${BASE}/datasets/${datasetId}/distributions/${distId}` : `${BASE}/datasets/${datasetId}/distributions`
  const res = await authFetch(url, {
    method: distId ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) await throwOnError(res, "배포본을 저장하지 못했습니다.")
  return res.json()
}

export async function deleteDistribution(datasetId: number, distId: number): Promise<void> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/distributions/${distId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "배포본을 지우지 못했습니다.")
}

export type AiReadyExportKind = "dcat" | "croissant" | "data-card"

/** 외부 연동 API 경로 — 다른 시스템이 부를 주소. 데이터셋 ID 판과 URN 판이 있다. */
export function aiReadyExportPath(datasetId: number, kind: AiReadyExportKind): string {
  return `/api/v1/external/datasets/${datasetId}/${kind}`
}

export async function fetchAiReadyExport(datasetId: number, kind: AiReadyExportKind): Promise<string> {
  const res = await authFetch(aiReadyExportPath(datasetId, kind))
  if (!res.ok) await throwOnError(res, "내보내기 결과를 만들지 못했습니다.")
  const text = await res.text()
  if (kind === "data-card") return text
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}

// 데이터 버전 이력 (TODO-096 4단계) — 데이터셋 '버전'·'버전 노트'를 저장할 때 서버가 남긴다.
export type DatasetVersionEntry = {
  id: number
  version: string
  notes: string | null
  created_by: string | null
  created_at: string | null
  updated_at: string | null
}

export async function fetchDatasetVersions(datasetId: number): Promise<DatasetVersionEntry[]> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/versions`)
  if (!res.ok) await throwOnError(res, "버전 이력을 불러오지 못했습니다.")
  return res.json()
}

export async function deleteDatasetVersion(datasetId: number, versionId: number): Promise<void> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/versions/${versionId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "버전 이력을 지우지 못했습니다.")
}

export type AiReadyItemDef = Pick<AiReadyItem, "key" | "group" | "level" | "text" | "mode" | "hint"> & {
  /** 표 · 배지용 짧은 이름(예: "필수 메타데이터") */
  short: string
}

export async function fetchAiReadyItems(): Promise<AiReadyItemDef[]> {
  const res = await authFetch(`${BASE}/ai-ready/items`)
  if (!res.ok) await throwOnError(res, "AI-Ready 점검 항목을 불러오지 못했습니다.")
  return res.json()
}

export type AiReadyOverviewRow = {
  dataset_id: number
  name: string
  display_name: string | null
  catalog_id: number | null
  /** 카탈로그 키 — 카탈로그가 없으면 데이터 소스 키 */
  catalog_key: string
  catalog_name: string | null
  /** DB(스키마) — URN 에서 나눈 값, 단계가 없으면 "" */
  db: string
  table: string
  /** db.dataset — 카탈로그 키는 난수가 섞여 넣지 않는다(카탈로그는 catalog_name 으로) */
  path: string
  required: AiReadyRate
  recommended: AiReadyRate
  counts: AiReadyCheck["counts"]
  min_requirements_met: boolean
  /** 적정이 아닌 필수 항목 키 (해당없음 제외) */
  unmet_required: string[]
}

export type AiReadyBreakdownGroup = {
  catalog_id: number | null
  catalog_name: string | null
  catalog_key: string
  /** 카탈로그를 골랐을 때만 — DB 별 묶음 */
  db: string | null
  datasets: number
  min_met: number
  required_rate: number | null
  recommended_rate: number | null
}

export type AiReadyOverview = {
  summary: {
    datasets: number
    required_rate: number | null
    recommended_rate: number | null
    min_met: number
    fully_required: number
  }
  /** 카탈로그별 평균 — 카탈로그를 고르면 그 카탈로그의 DB 별 */
  breakdown: { by: "catalog" | "db"; groups: AiReadyBreakdownGroup[] }
  items: AiReadyOverviewRow[]
  total: number
  page: number
  size: number
}

export type AiReadyScope = {
  /** null = 카탈로그가 없는 데이터셋 (목록 조건은 catalog_id=0) */
  catalog_id: number | null
  catalog_key: string | null
  name: string
  datasets: number
  dbs: { name: string; datasets: number }[]
}

/** 점검 목록의 범위 선택지 — 카탈로그와 그 아래 DB */
export async function fetchAiReadyScopes(): Promise<AiReadyScope[]> {
  const res = await authFetch(`${BASE}/ai-ready/scopes`)
  if (!res.ok) await throwOnError(res, "카탈로그·DB 목록을 불러오지 못했습니다.")
  return res.json()
}

export async function fetchAiReadyOverview(params: {
  q?: string
  /** 0 = 카탈로그 없음 */
  catalogId?: number
  /** catalogId 와 함께. "" = DB 단계 없음 */
  db?: string
  onlyUnmet?: boolean
  sort?: "required" | "recommended" | "name"
  /** 내림차순 — 기본은 오름차순(충족률 낮은 순 · 경로 가나다순) */
  desc?: boolean
  page?: number
  size?: number
}): Promise<AiReadyOverview> {
  const sp = new URLSearchParams()
  if (params.q) sp.set("q", params.q)
  if (params.catalogId !== undefined) sp.set("catalog_id", String(params.catalogId))
  if (params.db !== undefined) sp.set("db", params.db)
  if (params.onlyUnmet) sp.set("only_unmet", "true")
  if (params.sort) sp.set("sort", params.sort)
  if (params.desc) sp.set("desc", "true")
  sp.set("page", String(params.page ?? 1))
  sp.set("size", String(params.size ?? 20))
  const res = await authFetch(`${BASE}/ai-ready/overview?${sp}`)
  if (!res.ok) await throwOnError(res, "AI-Ready 충족률 목록을 불러오지 못했습니다.")
  return res.json()
}

export async function updateDataset(
  id: number,
  payload: {
    name?: string
    display_name?: string
    summary?: string
    description?: string
    origin?: string
    qualified_name?: string
    status?: string
  } & DatasetMetadataUpdate
): Promise<DatasetDetail> {
  const res = await authFetch(`${BASE}/datasets/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "데이터셋을 수정하지 못했습니다.")
  return res.json()
}

export async function deleteDataset(id: number): Promise<void> {
  const res = await authFetch(`${BASE}/datasets/${id}`, { method: "DELETE" })
  if (!res.ok) {
    // 백엔드 친절 메시지(권한·차단 등 detail)를 그대로 전달.
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `데이터셋을 삭제하지 못했습니다. (HTTP ${res.status})`)
  }
}

export type DatasourceMetadata = {
  datasource: Datasource
  data_types: { id: number; type_name: string; type_category: string; description: string | null; ordinal: number }[]
  table_types: { id: number; type_name: string; display_name: string; description: string | null; is_default: string; ordinal: number }[]
  storage_formats: { id: number; format_name: string; display_name: string; description: string | null; is_default: string; ordinal: number }[]
  features: { id: number; feature_key: string; display_name: string; description: string | null; value_type: string; is_required: string; ordinal: number }[]
}

export async function fetchDatasourceMetadata(datasourceId: number): Promise<DatasourceMetadata> {
  const res = await authFetch(`${BASE}/datasources/${datasourceId}/metadata`)
  if (!res.ok) throw new Error(`데이터 소스 메타데이터를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function fetchDatasources(): Promise<Datasource[]> {
  const res = await authFetch(`${BASE}/datasources`)
  if (!res.ok) throw new Error(`데이터 소스를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function addDatasetTag(
  datasetId: number,
  tagId: number
): Promise<void> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/tags/${tagId}`, {
    method: "POST",
  })
  if (!res.ok)
    await throwOnError(res, "태그를 추가하지 못했습니다.")
}

export async function removeDatasetTag(
  datasetId: number,
  tagId: number
): Promise<void> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/tags/${tagId}`, {
    method: "DELETE",
  })
  if (!res.ok)
    await throwOnError(res, "태그를 제거하지 못했습니다.")
}

export async function addDatasetOwner(
  datasetId: number,
  payload: { owner_name: string; owner_type: string }
): Promise<void> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/owners`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok)
    await throwOnError(res, "소유자를 추가하지 못했습니다.")
}

export async function removeDatasetOwner(
  datasetId: number,
  ownerId: number
): Promise<void> {
  const res = await authFetch(
    `${BASE}/datasets/${datasetId}/owners/${ownerId}`,
    { method: "DELETE" }
  )
  if (!res.ok)
    await throwOnError(res, "소유자를 제거하지 못했습니다.")
}

export async function updateDatasetProperties(
  datasetId: number,
  properties: { key: string; value: string }[],
): Promise<{ id: number; property_key: string; property_value: string }[]> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/properties`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(properties),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `속성을 수정하지 못했습니다. (HTTP ${res.status})`)
  }
  return res.json()
}

export async function updateDatasetSchema(
  datasetId: number,
  fields: {
    field_path: string
    display_name?: string
    field_type: string
    native_type?: string
    description?: string
    nullable?: string
    ordinal?: number
  }[]
): Promise<SchemaField[]> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/schema`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  })
  if (!res.ok) await throwOnError(res, "스키마를 수정하지 못했습니다.")
  return res.json()
}

// ---------------------------------------------------------------------------
// Schema history
// ---------------------------------------------------------------------------

export type SchemaChangeEntry = {
  type: "ADD" | "MODIFY" | "DROP"
  field: string
  before: Record<string, string> | null
  after: Record<string, string> | null
}

export type SchemaSnapshot = {
  id: number
  dataset_id: number
  synced_at: string
  field_count: number
  change_summary: string | null
  changes: SchemaChangeEntry[]
}

export type PaginatedSchemaSnapshots = {
  items: SchemaSnapshot[]
  total: number
  page: number
  page_size: number
}

export async function fetchSchemaHistory(
  datasetId: number,
  page: number = 1,
  pageSize: number = 20,
): Promise<PaginatedSchemaSnapshots> {
  const res = await authFetch(
    `${BASE}/datasets/${datasetId}/schema/history?page=${page}&page_size=${pageSize}`,
  )
  if (!res.ok) throw new Error(`스키마 이력을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function addDatasetGlossaryTerm(
  datasetId: number,
  termId: number
): Promise<void> {
  const res = await authFetch(
    `${BASE}/datasets/${datasetId}/glossary/${termId}`,
    { method: "POST" }
  )
  if (!res.ok) await throwOnError(res, "용어를 추가하지 못했습니다.")
}

export async function removeDatasetGlossaryTerm(
  datasetId: number,
  termId: number
): Promise<void> {
  const res = await authFetch(
    `${BASE}/datasets/${datasetId}/glossary/${termId}`,
    { method: "DELETE" }
  )
  if (!res.ok)
    await throwOnError(res, "용어를 제거하지 못했습니다.")
}

// ---------------------------------------------------------------------------
// Sample data
// ---------------------------------------------------------------------------

export async function uploadSampleData(
  datasetId: number,
  file: File
): Promise<{ status: string; file: string; size: number }> {
  const form = new FormData()
  form.append("file", file)
  const res = await authFetch(`${BASE}/datasets/${datasetId}/sample`, {
    method: "POST",
    body: form,
  })
  if (!res.ok) await throwOnError(res, "샘플 데이터를 업로드하지 못했습니다.")
  return res.json()
}

export async function fetchSampleData(
  datasetId: number
): Promise<Response> {
  return authFetch(`${BASE}/datasets/${datasetId}/sample`)
}

export async function deleteSampleData(
  datasetId: number
): Promise<void> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/sample`, {
    method: "DELETE",
  })
  if (!res.ok) await throwOnError(res, "샘플 데이터를 삭제하지 못했습니다.")
}

export async function convertSampleToParquet(
  datasetId: number,
): Promise<{ status: string; rows: number; columns: number }> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/sample/convert-to-parquet`, {
    method: "POST",
  })
  if (!res.ok) await throwOnError(res, "Parquet으로 변환하지 못했습니다.")
  return res.json()
}

// ---------------------------------------------------------------------------
// Delimiter config
// ---------------------------------------------------------------------------

export type DelimiterConfig = {
  encoding: string
  line_delimiter: string
  delimiter: string
  delimiter_mode: string | null
  delimiter_input: string
  has_header: boolean
  quote_char: string
  custom_quote_char: string
  is_custom_quote: boolean
}

export async function saveDelimiterConfig(
  datasetId: number,
  config: DelimiterConfig
): Promise<void> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/sample/delimiter`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "구분자 설정을 저장하지 못했습니다.")
}

export async function fetchDelimiterConfig(
  datasetId: number
): Promise<Response> {
  return authFetch(`${BASE}/datasets/${datasetId}/sample/delimiter`)
}


// ---------------------------------------------------------------------------
// Semantic Search
// ---------------------------------------------------------------------------

export type SemanticSearchResult = {
  dataset: DatasetSummary
  score: number
  match_type: string
}

export type SemanticSearchResponse = {
  items: SemanticSearchResult[]
  total: number
  query: string
  provider: string | null
  model: string | null
}

export async function semanticSearch(
  q: string, limit: number = 20, threshold: number = 0.3,
): Promise<SemanticSearchResponse> {
  const params = new URLSearchParams({ q, limit: String(limit), threshold: String(threshold) })
  const res = await authFetch(`${BASE}/search/semantic?${params}`)
  if (!res.ok) throw new Error(`시맨틱 검색 결과를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function hybridSearch(
  q: string, limit: number = 20,
  keywordWeight: number = 0.3, semanticWeight: number = 0.7,
): Promise<SemanticSearchResponse> {
  const params = new URLSearchParams({
    q, limit: String(limit),
    keyword_weight: String(keywordWeight), semantic_weight: String(semanticWeight),
  })
  const res = await authFetch(`${BASE}/search/hybrid?${params}`)
  if (!res.ok) throw new Error(`하이브리드 검색 결과를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export type EntitySearchResult = {
  entity_type: "glossary_term" | "ai_agent" | "api" | "mcp_server"
  id: number
  name: string
  display_name: string | null
  description: string | null
  extra: Record<string, string | null>
  score: number
  match_type: string
}

export type UnifiedSearchResponse = {
  query: string
  provider: string | null
  model: string | null
  datasets: SemanticSearchResult[]
  glossary_terms: EntitySearchResult[]
  ai_agents: EntitySearchResult[]
  apis: EntitySearchResult[]
  mcp_servers?: EntitySearchResult[]
  total: number
}

export async function unifiedSearch(
  q: string, limit: number = 20, entityLimit: number = 5, threshold: number = 0.3,
): Promise<UnifiedSearchResponse> {
  const params = new URLSearchParams({
    q, limit: String(limit), entity_limit: String(entityLimit), threshold: String(threshold),
  })
  const res = await authFetch(`${BASE}/search/unified?${params}`)
  if (!res.ok) throw new Error(`통합 검색 결과를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}
