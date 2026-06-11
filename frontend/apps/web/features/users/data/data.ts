import { Shield, UserCheck, Users } from "lucide-react"

import { type UserStatus } from "./schema"

export const callTypes = new Map<UserStatus, string>([
  [
    "active",
    "bg-primary/10 text-primary border-primary/30",
  ],
  [
    "inactive",
    "bg-destructive/10 dark:bg-destructive/50 text-destructive dark:text-primary border-destructive/10",
  ],
])

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
