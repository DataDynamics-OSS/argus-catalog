// 위험도 — MCP 도구 · finding 과 AI 에이전트 도구가 같은 네 단계(low · medium · high · critical)를 같은 이름 · 색으로(TODO-107).
// 예전에는 화면마다 이름(중간/보통, 치명/매우 높음)과 색(낮음이 초록 · 회색 · 초록 테두리)이 달랐다.
import type { StatusMeta, StatusTone } from "@workspace/ui/components/status-badge"

export const RISK_LEVELS = ["low", "medium", "high", "critical"] as const

export const RISK_LABEL: Record<string, string> = { low: "낮음", medium: "보통", high: "높음", critical: "치명" }

/** 낮음은 무채색(대부분이 낮음이라 조용하게), 보통 주의, 높음 · 치명 위험 — 치명은 배지에 아이콘을 더해 높음과 가른다. */
export const RISK_TONE: Record<string, StatusTone> = { low: "neutral", medium: "warning", high: "danger", critical: "danger" }

export function riskMeta(risk: string): StatusMeta {
  return { label: RISK_LABEL[risk] ?? risk, tone: RISK_TONE[risk] ?? "neutral" }
}
