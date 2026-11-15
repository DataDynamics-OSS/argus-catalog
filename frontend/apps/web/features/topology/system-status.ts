// 시스템 상태 — 이름과 톤을 한 곳에서 정한다(TODO-103, TODO-107). 백엔드 schemas.SystemStatus 와 같은 세 값.
// 시스템 상세 · 트리 · 시스템 추가 대화상자가 함께 쓴다.
import { statusMeta, type StatusMeta } from "@workspace/ui/components/status-badge"

export const SYSTEM_STATUSES = ["ACTIVE", "INACTIVE", "DEPRECATED"] as const
export type SystemStatus = (typeof SYSTEM_STATUSES)[number]

export const SYSTEM_STATUS_META: Record<SystemStatus, StatusMeta> = {
  ACTIVE: { label: "운영 중", tone: "success" },
  INACTIVE: { label: "사용 안 함", tone: "muted" },
  DEPRECATED: { label: "폐기 예정", tone: "warning" },
}

/** 이름 · 톤 — 모르는 값은 코드 그대로, 무채색 */
export function systemStatus(status: string | null | undefined): StatusMeta {
  return statusMeta(SYSTEM_STATUS_META, status, "muted")
}

/** "운영 중" — 모르는 값은 코드 그대로 */
export function systemStatusLabel(status: string | null | undefined): string {
  return systemStatus(status).label
}
