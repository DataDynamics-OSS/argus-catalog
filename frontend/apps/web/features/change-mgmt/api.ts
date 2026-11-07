import { authFetch, throwOnError } from "@/features/auth/auth-fetch"

const BASE = "/api/v1/changes"

export type ChangeType = "BREAKING" | "NON_BREAKING" | "ADDITIVE" | "COSMETIC"
export type Priority = "EMERGENCY" | "HIGH" | "NORMAL" | "LOW"
export type CRStatus =
  | "DRAFT"
  | "SUBMITTED"
  | "APPROVING"
  | "APPROVED"
  | "REJECTED"
  | "SCHEDULED"
  | "APPLIED"
  | "ROLLED_BACK"
  | "CANCELLED"
  | "CLOSED"
export type Decision = "APPROVED" | "REJECTED" | "DELEGATED" | "PENDING"
/** 결재자가 보낼 수 있는 결정값 — PENDING 은 상태값이라 서버가 422 로 거부한다. */
export type DecisionInput = Exclude<Decision, "PENDING">

export interface ApprovalStep {
  id: number
  step_order: number
  approver: string
  role?: string | null
  decision: Decision
  comment?: string | null
  decided_at?: string | null
  delegated_to?: string | null
  due_at?: string | null
}

export interface ChangeRequest {
  id: number
  cr_code: string
  title: string
  description?: string | null
  dataset_id: number
  change_type: ChangeType
  priority: Priority
  status: CRStatus
  schema_before?: string | null
  schema_after?: string | null
  impact_report?: string | null
  rollback_plan: string
  business_justification: string
  scheduled_at?: string | null
  applied_at?: string | null
  workflow_id?: string | null
  requested_by: string
  created_at: string
  updated_at: string
  approval_steps: ApprovalStep[]
  referrers: Referrer[]
}

export type ReferrerChannel = "EMAIL" | "SLACK" | "MATTERMOST"

export interface Referrer {
  id?: number
  name?: string | null
  email?: string | null
  channel: ReferrerChannel
  slack_target?: string | null
  created_at?: string
}

export interface CreateChangeRequestInput {
  title: string
  description?: string
  dataset_id: number
  change_type: ChangeType
  priority?: Priority
  schema_before?: string
  schema_after?: string
  rollback_plan: string
  business_justification: string
  scheduled_at?: string
  approval_chain: { step_order: number; approver: string; role?: string }[]
  referrers?: { name?: string; email?: string; channel: ReferrerChannel; slack_target?: string }[]
}

// 요청자는 서버가 인증 사용자에서 도출한다 (requested_by 파라미터 제거)
export async function createChangeRequest(
  payload: CreateChangeRequestInput,
): Promise<ChangeRequest> {
  const res = await authFetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  // 결재선 검증 422 는 detail 이 배열 — 사람이 읽을 메시지로(throwOnError)
  if (!res.ok) await throwOnError(res, "변경 요청을 생성하지 못했습니다.")
  return res.json()
}

export async function listChangeRequests(params?: {
  status?: CRStatus
  dataset_id?: number
}): Promise<ChangeRequest[]> {
  const search = new URLSearchParams()
  if (params?.status) search.set("status", params.status)
  if (params?.dataset_id) search.set("dataset_id", String(params.dataset_id))
  const qs = search.toString() ? `?${search.toString()}` : ""
  const res = await authFetch(`${BASE}${qs}`)
  if (!res.ok) throw new Error(`변경 요청 목록을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function getChangeRequest(crId: number): Promise<ChangeRequest> {
  const res = await authFetch(`${BASE}/${crId}`)
  if (!res.ok) throw new Error(`변경 요청을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function submitChangeRequest(
  crId: number,
  notifyChannels: string[] = ["EMAIL", "IN_APP"],
): Promise<{ cr_id: number; cr_code: string; workflow_id: string; status: CRStatus }> {
  const res = await authFetch(`${BASE}/${crId}/submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(notifyChannels),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `변경 요청을 제출하지 못했습니다. (HTTP ${res.status})`)
  }
  return res.json()
}

// 결재자·대상 단계는 서버가 인증 사용자/현재 차례에서 결정한다.
// 클라이언트는 결정(승인/반려)과 코멘트만 보낸다.
export async function submitDecision(
  crId: number,
  payload: {
    decision: DecisionInput
    comment?: string
    delegated_to?: string
  },
): Promise<void> {
  const res = await authFetch(`${BASE}/${crId}/decisions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `결재를 처리하지 못했습니다. (HTTP ${res.status})`)
  }
}

// 결재 인박스 — 현재 사용자가 지금 결재할 차례인 CR 목록
export async function listMyInbox(): Promise<ChangeRequest[]> {
  const res = await authFetch(`${BASE}/inbox`)
  if (!res.ok) throw new Error(`결재 대기 목록을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

// 사이드바 뱃지용 — 내 결재 대기 건수 (실패 시 0 으로 폴백)
export async function getMyInboxCount(): Promise<number> {
  try {
    const res = await authFetch(`${BASE}/inbox/count`)
    if (!res.ok) return 0
    const data = (await res.json()) as { count?: number }
    return data.count ?? 0
  } catch {
    return 0
  }
}

export async function cancelChangeRequest(crId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${crId}/cancel`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "변경 요청을 취소하지 못했습니다.")
}

export interface NotificationLog {
  id: number
  consumer_id?: number | null
  referrer_id?: number | null
  recipient?: string | null
  channel: string
  stage: string
  status: string
  sent_at?: string | null
  acked_at?: string | null
  ack_comment?: string | null
}

export interface Consumer {
  id: number
  dataset_id: number
  consumer_name: string
  criticality?: string | null
}

/** 데이터셋 소비자 목록 — 알림 이력의 소비자 이름 표시용(공개 조회). */
export async function listConsumers(datasetId: number): Promise<Consumer[]> {
  const res = await authFetch(`${BASE}/consumers/${datasetId}`)
  if (!res.ok) throw new Error(`소비자 목록을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function listNotifications(crId: number): Promise<NotificationLog[]> {
  const res = await authFetch(`${BASE}/${crId}/notifications`)
  if (!res.ok) throw new Error(`알림 이력을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}
