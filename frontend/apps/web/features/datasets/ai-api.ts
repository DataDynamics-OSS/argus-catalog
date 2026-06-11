/**
 * AI metadata generation API client for datasets.
 */

import { authFetch } from "@/features/auth/auth-fetch"

const BASE = "/api/v1/ai"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type GenerateRequest = {
  apply?: boolean
  force?: boolean
  language?: string
}

export type DescriptionResult = {
  dataset_id: number
  description: string
  confidence: number
  applied: boolean
  skipped?: boolean
  reason?: string
  log_id?: number
}

export type SummaryResult = {
  dataset_id: number
  summary: string
  confidence: number
  applied: boolean
  skipped?: boolean
  reason?: string
  log_id?: number
}

export type ColumnDescriptionItem = {
  field_path: string
  description: string
  confidence: number
  had_existing: boolean
  log_id?: number
}

export type ColumnsResult = {
  dataset_id: number
  columns: ColumnDescriptionItem[]
  total_generated: number
  applied: boolean
  skipped?: boolean
  reason?: string
}

export type TagSuggestionResult = {
  dataset_id: number
  suggested_tags: string[]
  new_tags: { name: string; description: string }[]
  applied_tags: string[]
  created_tags: string[]
  applied: boolean
  log_id?: number
}

export type PIIColumnItem = {
  name: string
  pii_type: string
  confidence: number
  reason: string
}

export type PIIResult = {
  dataset_id: number
  pii_columns: PIIColumnItem[]
  applied: boolean
  log_id?: number
}

export type GenerateAllResult = {
  dataset_id: number
  description: DescriptionResult
  columns: ColumnsResult
  tags: TagSuggestionResult
  pii: PIIResult
}

export type SuggestionItem = {
  id: number
  entity_type: string
  entity_id: number
  field_name: string | null
  generation_type: string
  generated_text: string
  provider: string
  model: string
  created_at: string | null
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function generateDescription(
  datasetId: number,
  req: GenerateRequest = {},
): Promise<DescriptionResult> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/describe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error(`설명 생성 실패: ${res.status}`)
  return res.json()
}

export async function generateSummary(
  datasetId: number,
  req: GenerateRequest = {},
): Promise<SummaryResult> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/summarize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error(`요약 생성 실패: ${res.status}`)
  return res.json()
}

export async function generateColumns(
  datasetId: number,
  req: GenerateRequest = {},
): Promise<ColumnsResult> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/describe-columns`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error(`컬럼 생성 실패: ${res.status}`)
  return res.json()
}

export async function suggestTags(
  datasetId: number,
  req: GenerateRequest = {},
): Promise<TagSuggestionResult> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/suggest-tags`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error(`태그 추천 실패: ${res.status}`)
  return res.json()
}

export async function detectPII(
  datasetId: number,
  req: GenerateRequest = {},
): Promise<PIIResult> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/detect-pii`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error(`PII 탐지 실패: ${res.status}`)
  return res.json()
}

export async function generateAll(
  datasetId: number,
  req: GenerateRequest = {},
): Promise<GenerateAllResult> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/generate-all`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error(`일괄 생성 실패: ${res.status}`)
  return res.json()
}

export async function getSuggestions(datasetId: number): Promise<SuggestionItem[]> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/suggestions`)
  if (!res.ok) throw new Error(`추천 조회 실패: ${res.status}`)
  return res.json()
}

export async function applySuggestion(
  suggestionId: number,
): Promise<{ id: number; applied: boolean }> {
  const res = await authFetch(`${BASE}/suggestions/${suggestionId}/apply`, { method: "POST" })
  if (!res.ok) throw new Error(`추천 적용 실패: ${res.status}`)
  return res.json()
}

export async function applySuggestions(
  suggestionIds: number[],
): Promise<{ applied_ids: number[]; count: number }> {
  const res = await authFetch(`${BASE}/suggestions/apply-batch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ suggestion_ids: suggestionIds }),
  })
  if (!res.ok) throw new Error(`제안 일괄 적용 실패: ${res.status}`)
  return res.json()
}

export async function rejectSuggestion(
  suggestionId: number,
): Promise<{ id: number; rejected: boolean }> {
  const res = await authFetch(`${BASE}/suggestions/${suggestionId}/reject`, { method: "POST" })
  if (!res.ok) throw new Error(`추천 거부 실패: ${res.status}`)
  return res.json()
}

export type AiStatus = {
  enabled: boolean
  provider: string | null
  model: string | null
}

/** LLM(AI 메타데이터 생성) 활성화 상태 — AI 메뉴 활성/비활성 판단용. */
export async function fetchAiStatus(): Promise<AiStatus> {
  const res = await authFetch(`${BASE}/status`)
  if (!res.ok) throw new Error(`AI 상태 조회 실패: ${res.status}`)
  return res.json()
}

