/**
 * ML Model Registry API client.
 */

import type { ModelSummary } from "./data/schema"
import { authFetch, throwOnError } from "@/features/auth/auth-fetch" // Added for SSO AUTH

const BASE = "/api/v1/models"

type ModelListParams = {
  search?: string
  status?: string
  python_version?: string
  sklearn_version?: string
  page?: number
  pageSize?: number
}

export type PaginatedModels = {
  items: ModelSummary[]
  total: number
  page: number
  page_size: number
}

export async function fetchModels(
  params?: ModelListParams,
): Promise<PaginatedModels> {
  const query = new URLSearchParams()
  if (params?.search) query.set("search", params.search)
  if (params?.status) query.set("status", params.status)
  if (params?.python_version) query.set("python_version", params.python_version)
  if (params?.sklearn_version) query.set("sklearn_version", params.sklearn_version)
  query.set("page", String(params?.page ?? 1))
  query.set("page_size", String(params?.pageSize ?? 20))

  const res = await authFetch(`${BASE}?${query.toString()}`)
  if (!res.ok) throw new Error(`모델 조회 실패: ${res.status}`)
  return res.json()
}

export type ModelStats = {
  total_models: number
  total_versions: number
  ready_models: number
  ready_versions: number
  pending_count: number
  failed_count: number
  total_download: number
  status_distribution: { status: string; count: number }[]
  model_sizes: { model_name: string; model_size_bytes: number }[]
  versions_per_model: { model_name: string; version_count: number }[]
  daily_download_1d: { date: string; count: number }[]
  daily_download_7d: { date: string; count: number }[]
  daily_download_30d: { date: string; count: number }[]
  download_by_model: Record<string, number>
  total_publish: number
  daily_publish_1d: { date: string; count: number }[]
  daily_publish_7d: { date: string; count: number }[]
  daily_publish_30d: { date: string; count: number }[]
}

export async function fetchModelStats(): Promise<ModelStats> {
  const res = await authFetch(`${BASE}/stats`)
  if (!res.ok) throw new Error(`통계 조회 실패: ${res.status}`)
  return res.json()
}

export async function createModel(payload: {
  name: string
  description?: string
  owner?: string
}): Promise<ModelSummary> {
  const res = await authFetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `모델 생성 실패: ${res.status}`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Model Detail
// ---------------------------------------------------------------------------

export type CatalogModelDetail = {
  predict_fn: string | null
  python_version: string | null
  serialization_format: string | null
  sklearn_version: string | null
  mlflow_version: string | null
  mlflow_model_id: string | null
  model_size_bytes: number | null
  utc_time_created: string | null
  requirements: string | null
  conda: string | null
  python_env: string | null
  source_type: string | null
}

export type ModelDetail = {
  id: number
  name: string
  urn: string
  description: string | null
  owner: string | null
  storage_type: string
  storage_location: string | null
  max_version_number: number
  status: string
  created_at: string
  updated_at: string
  latest_version_status: string | null
  catalog: CatalogModelDetail | null
  download_count: number
}

export type ModelVersionItem = {
  id: number
  model_id: number
  model_name: string
  version: number
  source: string | null
  run_id: string | null
  run_link: string | null
  description: string | null
  status: string
  status_message: string | null
  storage_location: string | null
  artifact_count: number
  artifact_size: number
  finished_at: string | null
  created_at: string
  updated_at: string
}

export type DownloadLogEntry = {
  downloaded_at: string
  version: number
  download_type: string
  client_ip: string | null
  user_agent: string | null
}

export type ModelDownloadStats = {
  total_download: number
  daily_download: { date: string; count: number }[]
  recent_logs: DownloadLogEntry[]
}

export async function fetchModelDetail(name: string): Promise<ModelDetail> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/detail`)
  if (!res.ok) throw new Error(`모델 상세 조회 실패: ${res.status}`)
  return res.json()
}

export async function fetchModelVersions(
  name: string, page: number = 1, pageSize: number = 20,
): Promise<{ items: ModelVersionItem[]; total: number; page: number; page_size: number }> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/versions?${params}`)
  if (!res.ok) throw new Error(`버전 조회 실패: ${res.status}`)
  return res.json()
}

export async function fetchModelDownloadStats(name: string): Promise<ModelDownloadStats> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/download`)
  if (!res.ok) throw new Error(`다운로드 통계 조회 실패: ${res.status}`)
  return res.json()
}

export async function deleteModel(name: string): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`, {
    method: "DELETE",
  })
  if (!res.ok) await throwOnError(res, "모델 삭제 실패")
}

export async function hardDeleteModels(
  names: string[],
): Promise<{ deleted: string[]; not_found: string[] }> {
  const res = await authFetch(`${BASE}/hard-delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ names }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `모델 일괄 삭제 실패: ${res.status}`)
  }
  return res.json()
}
