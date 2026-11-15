// 심각도 이름 · 톤 — 알림과 품질 규칙이 같은 코드(BREAKING · WARNING · INFO)를 같은 이름 · 색으로(TODO-107 · TODO-109).
// BREAKING 은 위험도와 같은 "치명"(예전 알림은 "심각").
import type { StatusTone } from "@workspace/ui/components/status-badge"

export const SEVERITY_LABEL: Record<string, string> = {
  BREAKING: "치명",
  WARNING: "경고",
  INFO: "정보",
}

export const SEVERITY_TONE: Record<string, StatusTone> = {
  BREAKING: "danger",
  WARNING: "warning",
  INFO: "neutral",
}

export const severityTone = (severity: string | null | undefined): StatusTone => SEVERITY_TONE[severity ?? ""] ?? "neutral"
