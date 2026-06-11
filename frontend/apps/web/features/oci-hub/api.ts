/**
 * OCI Model Hub API client.
 */

import { authFetch } from "@/features/auth/auth-fetch" // Added for SSO AUTH

const BASE = "/api/v1/oci-models"

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
  version_count: number
  total_size: number
  download_count: number
  status: string
  tags: { id: number; name: string; color: string }[]
  created_at: string
  updated_at: string
}

export type OciModelDetail = OciModelSummary & {
  readme: string | null
  source_id: string | null
  source_revision: string | null
  bucket: string | null
  storage_prefix: string | null
  lineage: {
    id: number
    source_type: string
    source_id: string
    source_name: string | null
    relation_type: string
    description: string | null
    created_at: string
  }[]
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
  status: string
  created_at: string
}

export async function fetchOciModels(params?: {
  search?: string
  task?: string
  framework?: string
  language?: string
  status?: string
  page?: number
  pageSize?: number
}): Promise<PaginatedOciModels> {
  const q = new URLSearchParams()
  if (params?.search) q.set("search", params.search)
  if (params?.task) q.set("task", params.task)
  if (params?.framework) q.set("framework", params.framework)
  if (params?.language) q.set("language", params.language)
  if (params?.status) q.set("status", params.status)
  q.set("page", String(params?.page ?? 1))
  q.set("page_size", String(params?.pageSize ?? 12))
  const res = await authFetch(`${BASE}?${q}`)
  if (!res.ok) throw new Error(`조회 실패: ${res.status}`)
  return res.json()
}

export async function fetchOciModel(name: string): Promise<OciModelDetail> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`)
  if (!res.ok) throw new Error(`조회 실패: ${res.status}`)
  return res.json()
}

export async function createOciModel(payload: {
  name: string
  display_name?: string
  description?: string
  task?: string
  framework?: string
  language?: string
  owner?: string
}): Promise<OciModelDetail> {
  const res = await authFetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `요청 실패: ${res.status}`)
  }
  return res.json()
}

export async function updateOciModel(
  name: string,
  payload: Record<string, string | null>,
): Promise<OciModelDetail> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`요청 실패: ${res.status}`)
  return res.json()
}

export async function deleteOciModel(name: string): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}`, { method: "DELETE" })
  if (!res.ok) throw new Error(`요청 실패: ${res.status}`)
}

export async function updateReadme(name: string, readme: string): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/readme`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ readme }),
  })
  if (!res.ok) throw new Error(`요청 실패: ${res.status}`)
}

export async function addTag(name: string, tagId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/tags/${tagId}`, { method: "POST" })
  if (!res.ok) throw new Error(`요청 실패: ${res.status}`)
}

export async function removeTag(name: string, tagId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/tags/${tagId}`, { method: "DELETE" })
  if (!res.ok) throw new Error(`요청 실패: ${res.status}`)
}

export async function fetchVersions(name: string): Promise<OciModelVersion[]> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/versions`)
  if (!res.ok) throw new Error(`요청 실패: ${res.status}`)
  return res.json()
}

export async function addLineage(name: string, payload: {
  source_type: string
  source_id: string
  source_name?: string
  relation_type: string
  description?: string
}): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/lineage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`요청 실패: ${res.status}`)
}

export async function removeLineage(name: string, lineageId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${encodeURIComponent(name)}/lineage/${lineageId}`, { method: "DELETE" })
  if (!res.ok) throw new Error(`요청 실패: ${res.status}`)
}

export async function importFromHuggingFace(payload: {
  hf_model_id: string
  name?: string
  description?: string
  owner?: string
  task?: string
  framework?: string
  language?: string
  revision?: string
}): Promise<{ name: string; version: number; file_count: number; total_size: number }> {
  const res = await authFetch(`${BASE}/import/huggingface`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `가져오기 실패: ${res.status}`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

export type OciHubStats = {
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
  const res = await authFetch(`${BASE}/stats`)
  if (!res.ok) throw new Error(`통계 조회 실패: ${res.status}`)
  return res.json()
}
