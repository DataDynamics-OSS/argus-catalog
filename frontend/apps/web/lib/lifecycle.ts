// 등록 자산의 수명 주기 상태 — MCP 서버 · 배포와 AI 에이전트가 같은 여섯 값을 같은 이름 · 톤으로(TODO-107).
import type { StatusMeta } from "@workspace/ui/components/status-badge"

export const LIFECYCLE_STATUSES = ["draft", "staging", "active", "blocked", "deprecated", "retired"] as const

/** 스테이징은 환경 · 모델 단계의 스테이징과 같은 주의 색. */
export const LIFECYCLE_STATUS: Record<string, StatusMeta> = {
  draft: { label: "초안", tone: "neutral" },
  staging: { label: "스테이징", tone: "warning" },
  active: { label: "활성", tone: "success" },
  blocked: { label: "차단", tone: "danger" },
  deprecated: { label: "사용 중단", tone: "warning" },
  retired: { label: "폐기", tone: "muted" },
}

export const LIFECYCLE_LABEL: Record<string, string> = Object.fromEntries(
  Object.entries(LIFECYCLE_STATUS).map(([k, v]) => [k, v.label])
)
