import { StatusBadge, type StatusMeta } from "@workspace/ui/components/status-badge"

// 초기 설정(Keycloak · OCI 레지스트리) 단계 결과 — 예전에는 OK · CREATED · SKIP · ERROR 코드를 그대로 보였다(TODO-107).
const INIT_STEP_STATUS: Record<string, StatusMeta> = {
  ok: { label: "확인", tone: "info" },
  created: { label: "생성", tone: "success" },
  skip: { label: "건너뜀", tone: "muted" },
  error: { label: "오류", tone: "danger" },
}

export function InitStepBadge({ status }: { status: string }) {
  return <StatusBadge {...(INIT_STEP_STATUS[status] ?? INIT_STEP_STATUS.error!)} />
}
