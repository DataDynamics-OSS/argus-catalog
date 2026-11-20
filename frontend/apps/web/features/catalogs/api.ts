import { authFetch, throwOnError } from "@/features/auth/auth-fetch"
import type { DatasetSourceMap, SourceCheckResult } from "@/features/datasets/data/schema"

// 카탈로그 엔티티 API (design/catalog-entity-design.md) — /api/v1/catalog/catalogs …
const BASE = "/api/v1/catalog"

export type CatalogConnectionRole = "SYNC" | "QUERY"

export type CatalogConnection = {
  datasource_id: number
  datasource_key: string
  datasource_name: string
  datasource_type: string
  role: CatalogConnectionRole
  remote_name: string | null
}

export type Catalog = {
  id: number
  catalog_key: string
  name: string
  kind: string | null
  source_type: "EXTERNAL" | "ARGUS"
  auto_created: boolean
  summary: string | null
  description: string | null
  owner: string | null
  status: string
  dataset_count: number
  connections: CatalogConnection[]
  /** 소속 시스템 · 조직 — 수집 데이터 소스의 시스템. 직접 등록 카탈로그는 카탈로그 화면에서 지정(DEF-CAT-001) */
  system_id?: number | null
  system_name?: string | null
  org_name?: string | null
  created_at?: string | null
  updated_at?: string | null
}

/** 카탈로그 수동 등록 데이터 소스 유형(TODO-CAT-001) — ARGUS 카탈로그의 SYNC 연결로만 나온다. */
export const MANUAL_DATASOURCE_TYPE = "manual"

/** 사용자에게 보일 연결 — 수동 등록 데이터 소스(카탈로그 내부)는 뺀다. */
export function visibleConnections(c: Catalog): CatalogConnection[] {
  return c.connections.filter((x) => x.datasource_type !== MANUAL_DATASOURCE_TYPE)
}

/** 메타데이터를 수집하는 데이터 소스(SYNC) — ARGUS 카탈로그는 없음. */
export function syncConnection(c: Catalog): CatalogConnection | undefined {
  return visibleConnections(c).find((x) => x.role === "SYNC")
}

export type EngineCatalogEntry = {
  remote_name: string
  connector: string | null
  connected_catalog_id: number | null
  suggested_catalog_ids: number[]
}

export type EngineCatalogs = {
  supported: boolean
  message: string | null
  catalogs: EngineCatalogEntry[]
}

export async function fetchCatalogs(): Promise<Catalog[]> {
  const res = await authFetch(`${BASE}/catalogs`)
  if (!res.ok) await throwOnError(res, "카탈로그 목록을 불러오지 못했습니다.")
  return res.json()
}

export async function fetchDatasourceCatalogs(datasourceId: number): Promise<Catalog[]> {
  const res = await authFetch(`${BASE}/datasources/${datasourceId}/catalogs`)
  if (!res.ok) await throwOnError(res, "연결된 카탈로그를 불러오지 못했습니다.")
  return res.json()
}

export async function fetchCatalog(id: number): Promise<Catalog> {
  const res = await authFetch(`${BASE}/catalogs/${id}`)
  if (!res.ok) await throwOnError(res, "카탈로그를 불러오지 못했습니다.")
  return res.json()
}

export type CatalogPayload = {
  name: string
  kind?: string | null
  /** 화면에서 직접 등록하면 ARGUS — 데이터셋을 직접 등록하는 카탈로그(TODO-CAT-001) */
  source_type?: "EXTERNAL" | "ARGUS"
  summary?: string | null
  description?: string | null
  owner?: string | null
  /** 직접 등록(ARGUS) 카탈로그의 소속 시스템 — 수동 데이터셋의 소관기관(DEF-CAT-001) */
  system_id?: number | null
}

export async function updateCatalog(
  id: number,
  payload: {
    name?: string; summary?: string | null; description?: string | null; owner?: string | null
    /** 보내면 바꾼다 — null 은 해제. 직접 등록 카탈로그만(DEF-CAT-001) */
    system_id?: number | null
  },
): Promise<Catalog> {
  const res = await authFetch(`${BASE}/catalogs/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "카탈로그를 저장하지 못했습니다.")
  return res.json()
}

export async function deleteCatalog(id: number): Promise<void> {
  const res = await authFetch(`${BASE}/catalogs/${id}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "카탈로그를 삭제하지 못했습니다.")
}

export async function createCatalog(payload: CatalogPayload): Promise<Catalog> {
  const res = await authFetch(`${BASE}/catalogs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "카탈로그를 만들지 못했습니다.")
  return res.json()
}

export async function addCatalogConnection(
  catalogId: number,
  payload: { datasource_id: number; role: CatalogConnectionRole; remote_name?: string | null },
): Promise<Catalog> {
  const res = await authFetch(`${BASE}/catalogs/${catalogId}/connections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "카탈로그에 연결하지 못했습니다.")
  return res.json()
}

export async function removeCatalogConnection(catalogId: number, datasourceId: number): Promise<Catalog> {
  const res = await authFetch(`${BASE}/catalogs/${catalogId}/connections/${datasourceId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "카탈로그 연결을 해제하지 못했습니다.")
  return res.json()
}

export async function discoverEngineCatalogs(datasourceId: number): Promise<EngineCatalogs> {
  const res = await authFetch(`${BASE}/datasources/${datasourceId}/engine-catalogs`)
  if (!res.ok) await throwOnError(res, "엔진 카탈로그를 불러오지 못했습니다.")
  return res.json()
}

// ---------------------------------------------------------------------------
// 수동 데이터셋의 원천 매핑 (TODO-CAT-001, design/catalog-registration-design.md 5장)
// ---------------------------------------------------------------------------

export type SourceMapRequest = { datasource_id: number; remote_path: string; skip_check?: boolean }

export type SourceMapConflict = {
  kind: "SYNCED_DATASET" | "MANUAL_MAPPING"
  dataset_id: number
  dataset_name: string
  urn: string
}

export type SourceMapPreview = {
  datasource_id: number
  datasource_name: string
  remote_path: string
  conflict: SourceMapConflict | null
  check: SourceCheckResult | null
}

export async function checkSourceMap(datasetId: number, req: SourceMapRequest): Promise<SourceMapPreview> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/source-map/check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) await throwOnError(res, "원천을 확인하지 못했습니다.")
  return res.json()
}

export async function saveSourceMap(datasetId: number, req: SourceMapRequest): Promise<DatasetSourceMap> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/source-map`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) await throwOnError(res, "원천 매핑을 저장하지 못했습니다.")
  return res.json()
}

export async function deleteSourceMap(datasetId: number): Promise<void> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/source-map`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "원천 매핑을 해제하지 못했습니다.")
}

export async function recheckSourceMap(datasetId: number): Promise<DatasetSourceMap> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/source-map/recheck`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "원천을 다시 확인하지 못했습니다.")
  return res.json()
}

export type ImportSchemaResult = { added: number; updated: number; check_status: string | null }

export async function importSourceSchema(datasetId: number): Promise<ImportSchemaResult> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/source-map/import-schema`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "원천 스키마를 가져오지 못했습니다.")
  return res.json()
}

/** 매핑 상태 표시 — 목록 source_map_status · 상세 check_status 공용. */
export const SOURCE_MAP_STATUS_LABEL: Record<string, { label: string; tone: "ok" | "warn" | "muted" | "error" }> = {
  UNMAPPED: { label: "매핑 없음", tone: "muted" },
  UNCHECKED: { label: "확인 안 함", tone: "muted" },
  MATCH: { label: "일치", tone: "ok" },
  DIFF: { label: "스키마 다름", tone: "warn" },
  NOT_FOUND: { label: "원천에 없음", tone: "error" },
  UNREACHABLE: { label: "확인 불가", tone: "warn" },
  UNSUPPORTED: { label: "비교 미지원", tone: "muted" },
}
