import type { DatasetSummary, Tag } from "@/features/datasets/data/schema"
import { authFetch, throwOnError } from "@/features/auth/auth-fetch"

/** 태그를 쓰는 데이터셋 외 항목 — OCI 모델, TAG 범위 알림 규칙. */
export type TagUsageRef = { id: number; name: string; is_active?: boolean | null }

export type TagUsage = {
  tag: Tag
  datasets: DatasetSummary[]
  total_datasets: number
  oci_models: TagUsageRef[]
  alert_rules: TagUsageRef[]
}

export type TagPayload = { name: string; description?: string | null; color: string }

const BASE = "/api/v1/catalog"

export async function fetchTags(): Promise<Tag[]> {
  const res = await authFetch(`${BASE}/tags`)
  if (!res.ok) await throwOnError(res, "태그를 불러오지 못했습니다")
  return res.json()
}

export async function createTag(payload: TagPayload): Promise<Tag> {
  const res = await authFetch(`${BASE}/tags`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "태그를 생성하지 못했습니다.")
  return res.json()
}

export async function updateTag(tagId: number, payload: Partial<TagPayload>): Promise<Tag> {
  const res = await authFetch(`${BASE}/tags/${tagId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "태그를 수정하지 못했습니다.")
  return res.json()
}

export async function fetchTagUsage(tagId: number): Promise<TagUsage> {
  const res = await authFetch(`${BASE}/tags/${tagId}/usage`)
  if (!res.ok) await throwOnError(res, "태그 사용 현황을 불러오지 못했습니다.")
  return res.json()
}

export async function deleteTag(tagId: number): Promise<void> {
  const res = await authFetch(`${BASE}/tags/${tagId}`, { method: "DELETE" })
  // 차단(알림 규칙이 감시 중 등)·권한 등 백엔드 안내(detail)를 그대로 전달.
  if (!res.ok) await throwOnError(res, "태그를 삭제하지 못했습니다.")
}
