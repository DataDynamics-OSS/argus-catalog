import { Shield, UserCheck, Users } from "lucide-react"

import type { StatusTone } from "@workspace/ui/components/status-badge"

import { type UserStatus } from "./schema"

/** 계정 상태 톤(TODO-107) — 비활성은 오류가 아니라 무채색. 잠김은 따로 주의 배지. */
export const USER_STATUS_TONE: Record<UserStatus, StatusTone> = {
  active: "success",
  inactive: "muted",
}

export const roles = [
  {
    label: "관리자",
    value: "argus-admin",
    icon: UserCheck,
  },
  {
    label: "슈퍼유저",
    value: "argus-superuser",
    icon: Shield,
  },
  {
    label: "사용자",
    value: "argus-user",
    icon: Users,
  },
] as const

// 상태 코드 → 한글 라벨 매핑. 테이블의 status 컬럼·필터에서 공통으로 사용.
export const statusLabels: Record<string, string> = {
  active: "활성",
  inactive: "비활성",
}
