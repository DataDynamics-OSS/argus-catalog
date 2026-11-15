import { describe, expect, it } from "vitest"

import { statusMeta } from "@workspace/ui/components/status-badge"

import { STATUS_TONE as ALERT_STATUS_TONE } from "@/features/alerts/labels"
import { apiStatus } from "@/features/apis/components/apis-constants"
import { STATUS_TONE as CR_STATUS_TONE } from "@/features/change-mgmt/labels"
import { datasetStatus } from "@/features/datasets/status"
import { LIFECYCLE_STATUS } from "@/lib/lifecycle"
import { RISK_LABEL, riskMeta } from "@/lib/risk"
import { SEVERITY_LABEL as ALERT_SEVERITY_LABEL } from "@/features/alerts/labels"
import { SEVERITY_LABEL, severityTone } from "@/lib/severity"
import { RISK_LABEL as MCP_RISK_LABEL, STATUS_LABEL as MCP_STATUS_LABEL } from "@/features/mcp-servers/data/schema"
import { AGENT_STATUS_LABEL, RISK_LABEL as AGENT_RISK_LABEL } from "@/features/ai-agents/data/schema"
import { datasetQualityStatus } from "@/features/datasets/status"
import { notificationStatus } from "@/features/change-mgmt/notification-labels"
import { STATUS_TONE as SYNC_RUN_TONE } from "@/features/datasources/sync-format"
import { instanceStatus, modeLabel, syncStatus } from "@/features/federation/status"
import { QUALITY_JOB_RUN_STATUS_TONE, QUALITY_RUN_STATUS, QUALITY_STATUS_TONE, checkResult } from "@/features/quality/format"
import { HOOK_DECISION } from "@/features/ai-agents/data/schema"
import { PRIORITY_TONE } from "@/features/change-mgmt/labels"
import { systemStatus } from "@/features/topology/system-status"

describe("statusMeta (TODO-107)", () => {
  const map = { OK: { label: "정상", tone: "success" as const } }
  it("맵에 있으면 이름 · 톤", () => {
    expect(statusMeta(map, "OK")).toEqual({ label: "정상", tone: "success" })
  })
  it("모르는 값은 코드 그대로, 톤은 neutral 또는 지정한 값", () => {
    expect(statusMeta(map, "WEIRD")).toEqual({ label: "WEIRD", tone: "neutral" })
    expect(statusMeta(map, "WEIRD", "muted")).toEqual({ label: "WEIRD", tone: "muted" })
  })
  it("빈 값은 -", () => {
    expect(statusMeta(map, null).label).toBe("-")
    expect(statusMeta(map, "").label).toBe("-")
  })
})

describe("실행 결과 톤이 화면마다 같다 (DEF-114)", () => {
  it("품질 진단 · 품질 작업 · 메타데이터 수집", () => {
    for (const tone of [QUALITY_STATUS_TONE, QUALITY_JOB_RUN_STATUS_TONE, SYNC_RUN_TONE]) {
      expect(tone.SUCCEEDED).toBe("success")
      expect(tone.PARTIAL).toBe("warning")
      expect(tone.FAILED).toBe("danger")
      expect(tone.RUNNING).toBe("info")
      expect(tone.CANCELLED).toBe("neutral")
    }
  })
  it("페더레이션은 한글 이름과 같은 톤", () => {
    expect(syncStatus("SUCCESS")).toEqual({ label: "성공", tone: "success" })
    expect(syncStatus("FAILED")).toEqual({ label: "실패", tone: "danger" })
    expect(instanceStatus("PAUSED")).toEqual({ label: "일시 중지", tone: "neutral" })
  })
})

describe("엔티티 상태 톤", () => {
  it("데이터셋 — 삭제됨은 danger (DEF-113)", () => {
    expect(datasetStatus("active").tone).toBe("success")
    expect(datasetStatus("deprecated").tone).toBe("warning")
    expect(datasetStatus("removed")).toEqual({ label: "삭제됨", tone: "danger" })
  })
  it("시스템 — 사용 안 함 · 모르는 값은 muted", () => {
    expect(systemStatus("INACTIVE").tone).toBe("muted")
    expect(systemStatus("RETIRED")).toEqual({ label: "RETIRED", tone: "muted" })
  })
})

describe("진한 단색을 쓰던 배지의 톤 (TODO-107)", () => {
  it("API 상태", () => {
    expect(apiStatus("published")).toEqual({ label: "게시", tone: "success" })
    expect(apiStatus("deprecated").tone).toBe("warning")
    expect(apiStatus("retired").tone).toBe("muted")
    expect(apiStatus("unknown")).toEqual({ label: "unknown", tone: "neutral" })
  })
  it("심각도 — 알림 · 품질 규칙 공용", () => {
    expect(severityTone("BREAKING")).toBe("danger")
    expect(severityTone("WARNING")).toBe("warning")
    expect(severityTone("INFO")).toBe("neutral")
    expect(severityTone(null)).toBe("neutral")
  })
  it("심각도 이름 — 알림 · 품질 규칙 모두 치명 · 경고 · 정보 (TODO-109)", () => {
    expect(SEVERITY_LABEL).toEqual({ BREAKING: "치명", WARNING: "경고", INFO: "정보" })
    expect(ALERT_SEVERITY_LABEL).toBe(SEVERITY_LABEL)
  })
  it("페더레이션 모드 이름 (TODO-109)", () => {
    expect([modeLabel("HYBRID"), modeLabel("HARVEST"), modeLabel("LIVE")]).toEqual(["혼합", "가져오기", "실시간"])
    expect(modeLabel("OTHER")).toBe("OTHER")
  })
  it("처리 상태 — 알림 미확인은 주의, 해결은 초록", () => {
    expect(ALERT_STATUS_TONE.OPEN).toBe("warning")
    expect(ALERT_STATUS_TONE.RESOLVED).toBe("success")
  })
  it("변경 요청 — 반려 빨강, 적용 초록, 취소 · 종료 무채색", () => {
    expect(CR_STATUS_TONE.REJECTED).toBe("danger")
    expect(CR_STATUS_TONE.APPLIED).toBe("success")
    expect(CR_STATUS_TONE.CLOSED).toBe("muted")
  })
})

describe("상태 · 위험도 맵 통합 (TODO-107 4단계)", () => {
  it("MCP 와 AI 에이전트가 같은 상태 이름 · 위험도 이름을 쓴다", () => {
    expect(MCP_STATUS_LABEL).toEqual(AGENT_STATUS_LABEL)
    expect(MCP_RISK_LABEL).toEqual(AGENT_RISK_LABEL)
    expect(RISK_LABEL).toEqual({ low: "낮음", medium: "보통", high: "높음", critical: "치명" })
  })
  it("수명 주기 톤", () => {
    expect(LIFECYCLE_STATUS.active!.tone).toBe("success")
    expect(LIFECYCLE_STATUS.blocked!.tone).toBe("danger")
    expect(LIFECYCLE_STATUS.retired!.tone).toBe("muted")
  })
  it("위험도 톤 — 낮음은 무채색, 높음 · 치명은 위험", () => {
    expect(riskMeta("low")).toEqual({ label: "낮음", tone: "neutral" })
    expect(riskMeta("medium").tone).toBe("warning")
    expect(riskMeta("critical")).toEqual({ label: "치명", tone: "danger" })
    expect(riskMeta("weird")).toEqual({ label: "weird", tone: "neutral" })
  })
  it("품질 상태 · 알림 발송 상태는 코드 대신 이름", () => {
    expect(datasetQualityStatus("BAD")).toEqual({ label: "불량", tone: "danger" })
    expect(datasetQualityStatus("WARN").label).toBe("주의")
    expect(notificationStatus("FAILED")).toEqual({ label: "실패", tone: "danger" })
    expect(notificationStatus("SKIPPED").tone).toBe("muted")
  })
})

describe("환경 표기 (TODO-107 5단계)", () => {
  it("MCP 배포의 STG 도 스테이징으로 — 코드는 받은 그대로 괄호에", async () => {
    const { originLabel, originMeta } = await import("@/features/datasets/origin")
    expect(originLabel("STG")).toBe("스테이징 (STG)")
    expect(originMeta("STG").bg).toBe(originMeta("STAGING").bg)
    expect(originLabel("PROD")).toBe("운영 (PROD)")
    expect(originLabel("QA")).toBe("QA")
  })
})

describe("상태성 배지 색 (DEF-116)", () => {
  it("심각도 경고는 주의 톤, 판정 통과는 초록", () => {
    expect(severityTone("WARNING")).toBe("warning")
    expect(checkResult(true)).toEqual({ label: "통과", tone: "success" })
    expect(checkResult(false)).toEqual({ label: "실패", tone: "danger" })
  })
  it("품질 실행 상태 맵", () => {
    expect(QUALITY_RUN_STATUS.FAILED).toEqual({ label: "실패", tone: "danger" })
    expect(QUALITY_RUN_STATUS.SUCCEEDED.tone).toBe("success")
  })
  it("훅 판정 · 변경 요청 우선순위", () => {
    expect(HOOK_DECISION.deny!.tone).toBe("danger")
    expect(HOOK_DECISION.allow!.tone).toBe("success")
    expect(PRIORITY_TONE.EMERGENCY).toBe("danger")
    expect(PRIORITY_TONE.HIGH).toBe("warning")
    expect(PRIORITY_TONE.LOW).toBe("neutral")
  })
})
