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
  flavor?: string
  /** true 면 소프트 삭제된 모델만 (관리자 전용) */
  deleted?: boolean
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
  if (params?.flavor) query.set("flavor", params.flavor)
  if (params?.deleted) query.set("deleted", "true")
  query.set("page", String(params?.page ?? 1))
  query.set("page_size", String(params?.pageSize ?? 20))

  const res = await authFetch(`${BASE}?${query.toString()}`)
  if (!res.ok) await throwOnError(res, "모델 목록을 불러오지 못했습니다.")
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
  /** 운영(PRODUCTION) 스테이지 버전이 있는 모델 수 */
  production_models: number
  /** 최근 30일 다운로드 수 */
  download_30d_total: number
}

export async function fetchModelStats(): Promise<ModelStats> {
  const res = await authFetch(`${BASE}/stats`)
  if (!res.ok) throw new Error(`모델 통계를 불러오지 못했습니다. (HTTP ${res.status})`)
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
  flavor: string | null
  flavor_version: string | null
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
  created_by: string | null
  /** MLflow 모델 파일 브라우저 기준 경로 (저장소가 브라우저 루트 밖이면 null) */
  files_path: string | null
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
  stage: ModelStage
  stage_changed_at: string | null
  stage_changed_by: string | null
  /** 로컬 버전 디렉터리에 파일이 있는지 (null = 확인 불가/원격 저장소) */
  files_present: boolean | null
  files_path: string | null
}

export type ModelStage = "NONE" | "STAGING" | "PRODUCTION" | "ARCHIVED"

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
  if (!res.ok) throw new Error(`모델 상세 정보를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function fetchModelVersions(
  name: string, page: number = 1, pageSize: number = 20,
): Promise<{ items: ModelVersionItem[]; total: number; page: number; page_size: number }> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/versions?${params}`)
  if (!res.ok) throw new Error(`모델 버전 목록을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function fetchModelDownloadStats(name: string): Promise<ModelDownloadStats> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/download`)
  if (!res.ok) throw new Error(`다운로드 통계를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function deleteModel(name: string): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`, {
    method: "DELETE",
  })
  if (!res.ok) await throwOnError(res, "모델을 삭제하지 못했습니다.")
}

/** 소프트 삭제된 모델 복원 (관리자) */
export async function restoreModel(name: string): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/restore`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "모델을 복원하지 못했습니다.")
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
    throw new Error(err.detail || `모델을 영구 삭제하지 못했습니다. (HTTP ${res.status})`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// 스테이지 · 메트릭 · 모델 카드 · 데이터셋 리니지
// (쓰기는 모델 소유자·생성자 또는 관리자 — 실패 시 서버 detail 을 그대로 던진다)
// ---------------------------------------------------------------------------

const m = (name: string) => `${BASE}/${encodeURIComponent(name)}`

export async function updateVersionStage(name: string, version: number, stage: ModelStage): Promise<void> {
  const res = await authFetch(`${m(name)}/versions/${version}/stage`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stage }),
  })
  if (!res.ok) await throwOnError(res, "버전 스테이지를 변경하지 못했습니다.")
}

export type VersionMetrics = { version: number; metrics: Record<string, number> }

export async function fetchModelMetrics(name: string): Promise<VersionMetrics[]> {
  const res = await authFetch(`${m(name)}/metrics`)
  if (!res.ok) await throwOnError(res, "메트릭을 불러오지 못했습니다.")
  return res.json()
}

export type ModelCard = {
  purpose: string | null
  performance: string | null
  limitations: string | null
  training_data: string | null
  framework: string | null
  license: string | null
  contact: string | null
  updated_at?: string | null
}

export async function fetchModelCard(name: string): Promise<ModelCard> {
  const res = await authFetch(`${m(name)}/card`)
  if (!res.ok) await throwOnError(res, "모델 카드를 불러오지 못했습니다.")
  return res.json()
}

export async function saveModelCard(name: string, card: Partial<ModelCard>): Promise<void> {
  const res = await authFetch(`${m(name)}/card`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(card),
  })
  if (!res.ok) await throwOnError(res, "모델 카드를 저장하지 못했습니다.")
}

export type LineageRelation = "TRAINING_DATA" | "EVALUATION_DATA" | "FEATURE_SOURCE"

export type ModelLineage = {
  id: number
  dataset_id: number
  dataset_name: string | null
  datasource_type: string | null
  model_version: number | null
  relation_type: LineageRelation
  description: string | null
}

export async function fetchModelLineage(name: string): Promise<ModelLineage[]> {
  const res = await authFetch(`${m(name)}/lineage`)
  if (!res.ok) await throwOnError(res, "리니지를 불러오지 못했습니다.")
  return res.json()
}

export async function addModelLineage(
  name: string,
  body: { dataset_id: number; model_version: number | null; relation_type: LineageRelation; description?: string },
): Promise<void> {
  const res = await authFetch(`${m(name)}/lineage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) await throwOnError(res, "리니지를 추가하지 못했습니다.")
}

export async function deleteModelLineage(name: string, lineageId: number): Promise<void> {
  const res = await authFetch(`${m(name)}/lineage/${lineageId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "리니지를 삭제하지 못했습니다.")
}
