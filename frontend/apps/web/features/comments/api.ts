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
  /** 오류 신고·개선 요청 처리 (TODO-096 4단계) — 데이터셋의 최상위 bug·suggestion 댓글만 값이 있다 */
  status?: ReportStatus | null
  due_at?: string | null
  resolved_at?: string | null
  resolution?: string | null
  handled_by?: string | null
  overdue?: boolean
  replies: CommentData[]
}

export type ReportStatus = "OPEN" | "IN_PROGRESS" | "RESOLVED" | "REJECTED"

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
  /** 이 댓글이 있는 쪽을 받는다(page 무시) — 신고 · 알림 링크(TODO-151). 응답의 page 가 실제 쪽. */
  focus?: number | null,
): Promise<PaginatedComments> {
  const params = new URLSearchParams({
    entity_type: entityType,
    entity_id: entityId,
    page: String(page),
    page_size: String(pageSize),
  })
  if (focus != null) params.set("focus", String(focus))
  const res = await authFetch(`${BASE}?${params}`)
  if (!res.ok) throw new Error(`댓글을 불러오지 못했습니다. (HTTP ${res.status})`)
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
    throw new Error(err.detail || `댓글을 작성하지 못했습니다. (HTTP ${res.status})`)
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
  if (!res.ok) await throwOnError(res, "댓글을 수정하지 못했습니다.")
  return res.json()
}

export async function deleteComment(commentId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${commentId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "댓글을 삭제하지 못했습니다.")
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

// ---------------------------------------------------------------------------
// 오류 신고·개선 요청 처리 (TODO-096 4단계)
// ---------------------------------------------------------------------------

/** 처리 상태 변경 — 데이터셋 소유자(생성자)·관리자. 소명(REJECTED)은 사유 필수. */
export async function updateReportStatus(
  commentId: number, body: { status: ReportStatus; resolution?: string | null },
): Promise<CommentData> {
  const res = await authFetch(`${BASE}/${commentId}/status`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) await throwOnError(res, "처리 상태를 바꾸지 못했습니다.")
  return res.json()
}

export type ReportState = "open" | "overdue" | "closed" | "all"

export type ReportItem = {
  id: number
  entity_type: string
  entity_id: string
  entity_name: string | null
  entity_path: string | null
  category: "bug" | "suggestion"
  content_plain: string | null
  author_name: string
  created_at: string
  status: ReportStatus
  due_at: string | null
  resolved_at: string | null
  resolution: string | null
  handled_by: string | null
  overdue: boolean
  reply_count: number
  /** 현재 사용자가 처리할 수 있는지 — 데이터셋 소유자 · 관리자(서버 판정, 기능 권한은 화면이 따로 본다) */
  can_handle: boolean
}

/** 신고 목록 범위 — owned: 내가 소유자인 데이터셋, reported: 내가 낸 신고 */
export type ReportMine = "owned" | "reported"

export type ReportPage = {
  items: ReportItem[]
  total: number
  page: number
  page_size: number
  counts: Record<ReportState, number>
}

export async function fetchReports(params: {
  state: ReportState
  category?: "bug" | "suggestion" | null
  datasetId?: number | null
  mine?: ReportMine | null
  page?: number
  pageSize?: number
}): Promise<ReportPage> {
  const sp = new URLSearchParams({ state: params.state, page: String(params.page ?? 1), page_size: String(params.pageSize ?? 20) })
  if (params.category) sp.set("category", params.category)
  if (params.mine) sp.set("mine", params.mine)
  if (params.datasetId) sp.set("dataset_id", String(params.datasetId))
  const res = await authFetch(`${BASE}/reports?${sp}`)
  if (!res.ok) await throwOnError(res, "오류 신고 목록을 불러오지 못했습니다.")
  return res.json()
}
