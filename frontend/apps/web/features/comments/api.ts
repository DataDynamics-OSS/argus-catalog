/**
 * Comment API client.
 */

import { authFetch, throwOnError } from "@/features/auth/auth-fetch" // Added for SSO AUTH

const BASE = "/api/v1/comments"

export type CommentData = {
  id: number
  entity_type: string
  entity_id: string
  parent_id: number | null
  root_id: number | null
  depth: number
  content: string
  content_plain: string | null
  category: string
  author_name: string
  author_email: string | null
  author_avatar: string | null
  reply_count: number
  created_at: string
  updated_at: string
  is_deleted: boolean
  replies: CommentData[]
}

export type PaginatedComments = {
  items: CommentData[]
  total: number
  page: number
  page_size: number
}

export async function fetchComments(
  entityType: string,
  entityId: string,
  page: number = 1,
  pageSize: number = 10,
): Promise<PaginatedComments> {
  const params = new URLSearchParams({
    entity_type: entityType,
    entity_id: entityId,
    page: String(page),
    page_size: String(pageSize),
  })
  const res = await authFetch(`${BASE}?${params}`)
  if (!res.ok) throw new Error(`댓글 조회 실패: ${res.status}`)
  return res.json()
}

export async function createComment(payload: {
  entity_type: string
  entity_id: string
  parent_id?: number | null
  content: string
  content_plain?: string
  category: string
  author_name: string
}): Promise<CommentData> {
  const res = await authFetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `댓글 작성 실패: ${res.status}`)
  }
  return res.json()
}

export async function updateComment(
  commentId: number,
  payload: { content: string; content_plain?: string; category?: string },
): Promise<CommentData> {
  const res = await authFetch(`${BASE}/${commentId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "댓글 수정 실패")
  return res.json()
}

export async function deleteComment(commentId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${commentId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "댓글 삭제 실패")
}

export async function fetchCommentCount(
  entityType: string,
  entityId: string,
): Promise<number> {
  const params = new URLSearchParams({ entity_type: entityType, entity_id: entityId })
  const res = await authFetch(`${BASE}/count?${params}`)
  if (!res.ok) return 0
  const data = await res.json()
  return data.count ?? 0
}
