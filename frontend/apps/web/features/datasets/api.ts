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
  page?: number
  pageSize?: number
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
  query.set("page", String(params?.page ?? 1))
  query.set("page_size", String(params?.pageSize ?? 20))

  const res = await authFetch(`${BASE}/datasets?${query.toString()}`)
  if (!res.ok) throw new Error(`데이터셋 조회 실패: ${res.status}`)
  return res.json()
}

export async function fetchDataset(id: number): Promise<DatasetDetail> {
  const res = await authFetch(`${BASE}/datasets/${id}`)
  if (!res.ok) throw new Error(`데이터셋 조회 실패: ${res.status}`)
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
    throw new Error(err.detail || `데이터셋 생성 실패: ${res.status}`)
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
  pii_fields?: string | null
  compliance_tags?: string | null
  tier?: string | null
  certification?: string | null
  steward?: string | null
  quality_status?: string | null
  show_quality_score?: boolean | null
  note?: string | null
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
  if (!res.ok) await throwOnError(res, "데이터셋 수정 실패")
  return res.json()
}

export async function deleteDataset(id: number): Promise<void> {
  const res = await authFetch(`${BASE}/datasets/${id}`, { method: "DELETE" })
  if (!res.ok) {
    // 백엔드 친절 메시지(권한·차단 등 detail)를 그대로 전달.
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `데이터셋 삭제 실패: ${res.status}`)
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
  if (!res.ok) throw new Error(`데이터 소스 메타데이터 조회 실패: ${res.status}`)
  return res.json()
}

export async function fetchDatasources(): Promise<Datasource[]> {
  const res = await authFetch(`${BASE}/datasources`)
  if (!res.ok) throw new Error(`데이터 소스 조회 실패: ${res.status}`)
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
    await throwOnError(res, "태그 추가 실패")
}

export async function removeDatasetTag(
  datasetId: number,
  tagId: number
): Promise<void> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/tags/${tagId}`, {
    method: "DELETE",
  })
  if (!res.ok)
    await throwOnError(res, "태그 제거 실패")
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
    await throwOnError(res, "소유자 추가 실패")
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
    await throwOnError(res, "소유자 제거 실패")
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
    throw new Error(err.detail || `속성 수정 실패: ${res.status}`)
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
  if (!res.ok) await throwOnError(res, "스키마 수정 실패")
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
  if (!res.ok) throw new Error(`스키마 이력 조회 실패: ${res.status}`)
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
  if (!res.ok) await throwOnError(res, "용어 추가 실패")
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
    await throwOnError(res, "용어 제거 실패")
}

// ---------------------------------------------------------------------------
// Sample data
// ---------------------------------------------------------------------------

export async function uploadSampleData(
  datasetId: number,
  file: File
): Promise<{ status: string; path: string; size: number }> {
  const form = new FormData()
  form.append("file", file)
  const res = await authFetch(`${BASE}/datasets/${datasetId}/sample`, {
    method: "POST",
    body: form,
  })
  if (!res.ok) await throwOnError(res, "샘플 데이터 업로드 실패")
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
  if (!res.ok) await throwOnError(res, "샘플 데이터 삭제 실패")
}

export async function convertSampleToParquet(
  datasetId: number,
): Promise<{ status: string; rows: number; columns: number }> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/sample/convert-to-parquet`, {
    method: "POST",
  })
  if (!res.ok) await throwOnError(res, "Parquet 변환 실패")
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
  if (!res.ok) await throwOnError(res, "구분자 설정 저장 실패")
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
  if (!res.ok) throw new Error(`시맨틱 검색 실패: ${res.status}`)
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
  if (!res.ok) throw new Error(`하이브리드 검색 실패: ${res.status}`)
  return res.json()
}

export type EntitySearchResult = {
  entity_type: "glossary_term" | "ai_agent" | "api"
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
  total: number
}

export async function unifiedSearch(
  q: string, limit: number = 20, entityLimit: number = 5, threshold: number = 0.3,
): Promise<UnifiedSearchResponse> {
  const params = new URLSearchParams({
    q, limit: String(limit), entity_limit: String(entityLimit), threshold: String(threshold),
  })
  const res = await authFetch(`${BASE}/search/unified?${params}`)
  if (!res.ok) throw new Error(`통합 검색 실패: ${res.status}`)
  return res.json()
}
