import type { ChangeRequest, CRStatus, Decision } from "./api"

/**
 * 결재 단계를 화면에 어떻게 보일지 정하는 순수 함수 (CR 상세 결재선·목록 결재 진행도).
 *
 * CR 이 반려·취소·결재 기한 초과(워크플로우가 REJECTED 로 끝냄) 등으로 끝나면 남은 단계는
 * DB 에 PENDING 그대로 남는다. 예전에는 그 단계를 결재 대기(주황 시계)로 그려 아직 결재가
 * 진행 중인 것처럼 보였다 — 끝난 CR 의 PENDING 단계는 "진행 안 함"(SKIPPED, 회색)으로 보인다.
 */

/** 더 이상 결재가 진행되지 않는 CR 상태 — 이 상태의 PENDING 단계는 처리되지 않는다. */
export const TERMINAL_CR_STATUSES: ReadonlySet<CRStatus> = new Set<CRStatus>([
  "REJECTED",
  "CANCELLED",
  "APPLIED",
  "ROLLED_BACK",
  "CLOSED",
])

export type StepDisplayState = Exclude<Decision, "PENDING"> | "PENDING" | "SKIPPED"

/** 단계 하나의 표시 상태 — 끝난 CR 의 PENDING 단계는 SKIPPED. 작성 중(DRAFT)·결재 중은 PENDING 그대로. */
export function stepDisplayState(crStatus: CRStatus, decision: Decision): StepDisplayState {
  if (decision === "PENDING" && TERMINAL_CR_STATUSES.has(crStatus)) return "SKIPPED"
  return decision
}

export interface ApprovalProgress {
  /** 표시 문구 — "반려", "1/2", "1/2 (중단)", 결재선 없으면 "—" */
  text: string
  tone: "rejected" | "stopped" | "normal"
}

/**
 * 목록의 결재 진행도 — 처리(승인·위임) 단계/전체 단계.
 * - 반려 단계가 있거나 CR 이 반려(기한 초과 포함)로 끝났으면 "반려"
 * - 그 밖에 끝난 CR 에 진행 안 함 단계가 남았으면(취소 등) "n/전체 (중단)"
 * 위임은 워크플로우가 승인처럼 다음 단계로 넘기므로 처리된 단계로 센다.
 */
export function approvalProgress(cr: Pick<ChangeRequest, "status" | "approval_steps">): ApprovalProgress {
  const total = cr.approval_steps.length
  if (total === 0) return { text: "—", tone: "normal" }
  if (cr.status === "REJECTED" || cr.approval_steps.some((s) => s.decision === "REJECTED")) {
    return { text: "반려", tone: "rejected" }
  }
  const done = cr.approval_steps.filter((s) => s.decision === "APPROVED" || s.decision === "DELEGATED").length
  const skipped = cr.approval_steps.some((s) => stepDisplayState(cr.status, s.decision) === "SKIPPED")
  return skipped ? { text: `${done}/${total} (중단)`, tone: "stopped" } : { text: `${done}/${total}`, tone: "normal" }
}
