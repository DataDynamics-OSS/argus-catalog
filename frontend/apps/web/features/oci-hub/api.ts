/**
 * OCI Model Hub API client.
 *
 * 쓰기(생성·수정·삭제·README·태그·리니지)는 모델 소유자·생성자 또는 관리자,
 * HuggingFace 가져오기는 관리자. 실패하면 서버 detail 을 담은 Error 를 던진다.
 */

import { authFetch, throwOnError } from "@/features/auth/auth-fetch" // Added for SSO AUTH

const BASE = "/api/v1/oci-models"
const enc = encodeURIComponent

/** 모델 라이프사이클 상태 — approved·production 지정은 관리자만 */
export const OCI_STATUSES = ["draft", "review", "approved", "production", "deprecated", "archived"] as const
export const OCI_ADMIN_ONLY_STATUSES = ["approved", "production"]
export const OCI_STATUS_LABELS: Record<string, string> = {
  draft: "초안", review: "검토", approved: "승인", production: "운영",
  deprecated: "사용 중단", archived: "보관",
  // 버전 상태
  ready: "준비됨", importing: "가져오는 중", failed: "실패",
}

export type OciTag = { id: number; name: string; color: string }

export type OciModelSummary = {
  id: number
  name: string
  display_name: string | null
  description: string | null
  task: string | null
  framework: string | null
  language: string | null
  license: string | null
  source_type: string | null
  owner: string | null
  created_by: string | null
  /** 최신(준비된) 버전 번호 — 0 이면 버전 없음 */
  version_count: number
  /** 준비된 모든 버전의 저장 용량 합 */
  total_size: number
  download_count: number
  status: string
  tags: OciTag[]
  created_at: string
  updated_at: string
}

export type OciLineage = {
  id: number
  source_type: string
  source_id: string
  source_name: string | null
  relation_type: string
  description: string | null
  created_at: string
}

export type OciModelDetail = OciModelSummary & {
  readme: string | null
  source_id: string | null
  source_revision: string | null
  bucket: string | null
  storage_prefix: string | null
  lineage: OciLineage[]
}

export type PaginatedOciModels = {
  items: OciModelSummary[]
  total: number
  page: number
  page_size: number
}

export type OciModelVersion = {
  id: number
  model_id: number
  version: number
  manifest: string | null
  content_digest: string | null
  file_count: number
  total_size: number
  extra_metadata: Record<string, unknown> | null
  /** ready | importing | failed */
  status: string
  created_at: string
}

export type OciFacets = {
  tasks: string[]
  frameworks: string[]
  languages: string[]
  statuses: string[]
}

export type OciListParams = {
  search?: string
  task?: string
  framework?: string
  language?: string
  status?: string
  page?: number
  pageSize?: number
}

async function json<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) await throwOnError(res, fallback)
  return res.json() as Promise<T>
}

function send(url: string, method: string, body?: unknown): Promise<Response> {
  return authFetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export async function fetchOciModels(params?: OciListParams): Promise<PaginatedOciModels> {
  const q = new URLSearchParams()
  if (params?.search) q.set("search", params.search)
  if (params?.task) q.set("task", params.task)
  if (params?.framework) q.set("framework", params.framework)
  if (params?.language) q.set("language", params.language)
  if (params?.status) q.set("status", params.status)
  q.set("page", String(params?.page ?? 1))
  q.set("page_size", String(params?.pageSize ?? 12))
  return json(await authFetch(`${BASE}?${q}`), "모델 목록을 불러오지 못했습니다.")
}

export async function fetchOciFacets(): Promise<OciFacets> {
  return json(await authFetch(`${BASE}/facets`), "필터 목록을 불러오지 못했습니다.")
}

export async function fetchOciModel(name: string): Promise<OciModelDetail> {
  return json(await authFetch(`${BASE}/${enc(name)}`), "모델 정보를 불러오지 못했습니다.")
}

export type OciModelInput = {
  name?: string
  display_name?: string | null
  description?: string | null
  task?: string | null
  framework?: string | null
  language?: string | null
  license?: string | null
  owner?: string | null
  status?: string
}

export async function createOciModel(payload: OciModelInput & { name: string }): Promise<OciModelDetail> {
  return json(await send(BASE, "POST", payload), "모델을 등록하지 못했습니다.")
}

export async function updateOciModel(name: string, payload: OciModelInput): Promise<OciModelDetail> {
  return json(await send(`${BASE}/${enc(name)}`, "PATCH", payload), "모델을 수정하지 못했습니다.")
}

export async function deleteOciModel(name: string): Promise<void> {
  const res = await send(`${BASE}/${enc(name)}`, "DELETE")
  if (!res.ok) await throwOnError(res, "모델을 삭제하지 못했습니다.")
}

export async function updateReadme(name: string, readme: string): Promise<void> {
  const res = await send(`${BASE}/${enc(name)}/readme`, "PUT", { readme })
  if (!res.ok) await throwOnError(res, "README를 저장하지 못했습니다.")
}

export async function addTag(name: string, tagId: number): Promise<void> {
  const res = await send(`${BASE}/${enc(name)}/tags/${tagId}`, "POST")
  if (!res.ok) await throwOnError(res, "태그를 추가하지 못했습니다.")
}

export async function removeTag(name: string, tagId: number): Promise<void> {
  const res = await send(`${BASE}/${enc(name)}/tags/${tagId}`, "DELETE")
  if (!res.ok) await throwOnError(res, "태그를 제거하지 못했습니다.")
}

export async function fetchVersions(name: string): Promise<OciModelVersion[]> {
  return json(await authFetch(`${BASE}/${enc(name)}/versions`), "버전 목록을 불러오지 못했습니다.")
}

/** 버전 하나 삭제 — DB 기록과 저장소 파일을 지우고 모델 통계를 다시 계산한다. 가져오는 중이면 409. */
export async function deleteVersion(name: string, version: number): Promise<void> {
  const res = await send(`${BASE}/${enc(name)}/versions/${version}`, "DELETE")
  if (!res.ok) await throwOnError(res, `v${version} 을(를) 삭제하지 못했습니다.`)
}

export const LINEAGE_SOURCE_TYPES = ["dataset", "mlflow", "model", "external"] as const
export const LINEAGE_SOURCE_LABELS: Record<string, string> = {
  dataset: "데이터셋", mlflow: "MLflow 모델", model: "외부 모델", external: "외부 자원",
}
export const LINEAGE_RELATIONS = ["trained_on", "fine_tuned_from", "distilled_from", "derived_from", "converted_from", "produces"] as const
export const LINEAGE_RELATION_LABELS: Record<string, string> = {
  trained_on: "학습 데이터",
  fine_tuned_from: "파인튜닝 원본",
  distilled_from: "증류 원본",
  derived_from: "파생 원본",
  converted_from: "변환 원본",
  produces: "산출물",
}

export async function addLineage(name: string, payload: {
  source_type: string
  source_id: string
  source_name?: string
  relation_type: string
  description?: string
}): Promise<void> {
  const res = await send(`${BASE}/${enc(name)}/lineage`, "POST", payload)
  if (!res.ok) await throwOnError(res, "리니지를 추가하지 못했습니다.")
}

export async function removeLineage(name: string, lineageId: number): Promise<void> {
  const res = await send(`${BASE}/${enc(name)}/lineage/${lineageId}`, "DELETE")
  if (!res.ok) await throwOnError(res, "리니지를 삭제하지 못했습니다.")
}

/** HF 가져오기 접수 — 202 와 함께 status=importing 을 돌려주고 백그라운드에서 진행된다. */
export async function importFromHuggingFace(payload: {
  hf_model_id: string
  name?: string
  description?: string
  owner?: string
  task?: string
  framework?: string
  language?: string
  revision?: string
}): Promise<{ name: string; version: number; status: string }> {
  return json(await send(`${BASE}/import/huggingface`, "POST", payload), "HuggingFace 모델을 가져오지 못했습니다.")
}

export type CatalogTag = { id: number; name: string; color: string }

/** MLflow 등록 모델에서 파생된 OCI 모델 (MLflow 상세의 '배포 아티팩트' 표시용). */
export type LinkedOciModel = {
  name: string
  display_name: string | null
  status: string
  framework: string | null
  version_count: number
  relation_type: string
}

export async function fetchOciLinkedToMlflow(mlflowModel: string): Promise<LinkedOciModel[]> {
  return json(await authFetch(`${BASE}/linked?${new URLSearchParams({ mlflow_model: mlflowModel })}`), "연결된 OCI 모델을 불러오지 못했습니다.")
}

export async function fetchCatalogTags(): Promise<CatalogTag[]> {
  return json(await authFetch("/api/v1/catalog/tags"), "태그 목록을 불러오지 못했습니다.")
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

export type OciHubStats = {
  /** 상태별 모델 수 (draft/review/approved/production/…) */
  status_counts: Record<string, number>
  /** 모든 모델의 저장 용량 합 (bytes) */
  total_storage: number
  /** 최근 30일 다운로드 (다운로드 로그 기준) */
  download_30d_total: number
  total_models: number
  total_versions: number
  hf_count: number
  my_count: number
  total_downloads: number
  total_download: number
  total_publish: number
  source_distribution: { source: string; count: number }[]
  model_sizes: { model_name: string; total_size: number }[]
  top_downloads: { model_name: string; download_count: number }[]
  download_1d: { date: string; count: number }[]
  download_7d: { date: string; count: number }[]
  download_30d: { date: string; count: number }[]
  publish_1d: { date: string; count: number }[]
  publish_7d: { date: string; count: number }[]
  publish_30d: { date: string; count: number }[]
}

export async function fetchOciHubStats(): Promise<OciHubStats> {
  return json(await authFetch(`${BASE}/stats`), "통계를 불러오지 못했습니다.")
}
