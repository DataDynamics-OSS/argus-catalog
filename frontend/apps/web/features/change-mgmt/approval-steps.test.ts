import { describe, expect, it } from "vitest"

import type { ApprovalStep, CRStatus, Decision } from "./api"
import { approvalProgress, stepDisplayState } from "./approval-steps"

const step = (order: number, decision: Decision): ApprovalStep => ({
  id: order,
  step_order: order,
  approver: `u${order}`,
  decision,
})
const cr = (status: CRStatus, ...decisions: Decision[]) => ({
  status,
  approval_steps: decisions.map((d, i) => step(i + 1, d)),
})

describe("stepDisplayState", () => {
  it("끝난 CR 의 남은 PENDING 단계는 진행 안 함(SKIPPED)", () => {
    for (const s of ["REJECTED", "CANCELLED", "APPLIED", "ROLLED_BACK", "CLOSED"] as CRStatus[]) {
      expect(stepDisplayState(s, "PENDING")).toBe("SKIPPED")
    }
  })
  it("작성 중·결재 중인 CR 의 PENDING 은 대기 그대로", () => {
    for (const s of ["DRAFT", "SUBMITTED", "APPROVING"] as CRStatus[]) {
      expect(stepDisplayState(s, "PENDING")).toBe("PENDING")
    }
  })
  it("결정된 단계는 CR 상태와 무관하게 결정값", () => {
    expect(stepDisplayState("REJECTED", "REJECTED")).toBe("REJECTED")
    expect(stepDisplayState("CANCELLED", "APPROVED")).toBe("APPROVED")
    expect(stepDisplayState("APPLIED", "DELEGATED")).toBe("DELEGATED")
  })
})

describe("approvalProgress", () => {
  it("결재선 없음", () => {
    expect(approvalProgress(cr("APPLIED"))).toEqual({ text: "—", tone: "normal" })
  })
  it("결재 중 — 처리(승인·위임) 단계 수", () => {
    expect(approvalProgress(cr("APPROVING", "APPROVED", "DELEGATED", "PENDING"))).toEqual({ text: "2/3", tone: "normal" })
  })
  it("반려 단계 또는 반려 상태(기한 초과) — 반려", () => {
    expect(approvalProgress(cr("REJECTED", "APPROVED", "REJECTED", "PENDING")).text).toBe("반려")
    expect(approvalProgress(cr("REJECTED", "PENDING", "PENDING"))).toEqual({ text: "반려", tone: "rejected" })
  })
  it("취소로 남은 단계 — 중단", () => {
    expect(approvalProgress(cr("CANCELLED", "APPROVED", "PENDING"))).toEqual({ text: "1/2 (중단)", tone: "stopped" })
  })
  it("모두 승인된 적용 완료", () => {
    expect(approvalProgress(cr("APPLIED", "APPROVED", "APPROVED"))).toEqual({ text: "2/2", tone: "normal" })
  })
})
