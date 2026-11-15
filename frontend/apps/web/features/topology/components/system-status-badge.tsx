import { StatusBadge } from "@workspace/ui/components/status-badge"

import { systemStatus } from "@/features/topology/system-status"

/** 시스템 상태 배지 — "운영 중" · "사용 안 함" · "폐기 예정" */
export function SystemStatusBadge({
  status,
  size,
  className,
}: {
  status: string | null | undefined
  size?: "default" | "sm"
  className?: string
}) {
  return <StatusBadge {...systemStatus(status)} size={size} className={className} />
}
