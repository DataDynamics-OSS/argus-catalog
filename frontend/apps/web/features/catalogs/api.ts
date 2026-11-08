import { authFetch, throwOnError } from "@/features/auth/auth-fetch"

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

export async function createCatalog(payload: { name: string; kind?: string | null }): Promise<Catalog> {
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
