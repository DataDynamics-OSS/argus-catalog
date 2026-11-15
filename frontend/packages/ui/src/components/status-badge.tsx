import * as React from "react"

import { Badge, type BadgeVariant } from "@workspace/ui/components/badge"

/**
 * 상태 배지의 의미 톤(TODO-107). 색은 "무엇의 상태인지"가 아니라 "어떤 의미인지"로 고른다.
 *
 * - `neutral` 초안 · 대기 · 취소 · 해당 없음 — 테두리만
 * - `info`    진행 중 · 검토 중
 * - `success` 정상 · 완료 · 운영 중
 * - `warning` 주의 · 일부 실패 · 곧 중단
 * - `danger`  실패 · 차단 · 치명
 * - `muted`   퇴역 · 보관 · 삭제 · 무시 — 무채색 칠
 */
export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger" | "muted"

/** 도메인 상태 맵의 한 값 — `Record<상태 코드, StatusMeta>` 로 이름과 톤을 한 곳에서 정한다. */
export type StatusMeta = { label: string; tone: StatusTone }

const TONE_VARIANT: Record<StatusTone, BadgeVariant> = {
  neutral: "outline",
  info: "info",
  success: "success",
  warning: "warning",
  danger: "destructive",
  muted: "muted",
}

/** 맵에서 상태를 찾는다. 모르는 값은 코드를 그대로 이름으로, 톤은 `neutral`(또는 `fallback`). */
export function statusMeta(
  map: Readonly<Record<string, StatusMeta>>,
  value: string | null | undefined,
  fallback: StatusTone = "neutral"
): StatusMeta {
  if (value == null || value === "") return { label: "-", tone: fallback }
  return map[value] ?? { label: value, tone: fallback }
}

type StatusBadgeProps = Omit<React.ComponentProps<"span">, "children"> & {
  tone: StatusTone
  /** 기본 · 촘촘한 곳(트리 · 좁은 셀)용 `sm` 두 가지만 둔다. 글자 크기를 className 으로 덮어쓰지 않는다. */
  size?: "default" | "sm"
  /** 앞에 붙는 아이콘(lucide 등) */
  icon?: React.ComponentType<{ className?: string }>
  /** 표시 이름 — 없으면 children */
  label?: React.ReactNode
  children?: React.ReactNode
}

/**
 * 상태 · 심각도 · 위험도 배지. 표 · 카드 · 상세가 같은 상태를 같은 모양 · 색으로 그리도록 모양과 크기를 고정한다.
 * `className` 은 배치(여백 등)에만 쓴다.
 *
 * ```tsx
 * <StatusBadge {...statusMeta(DATASET_STATUS, row.status)} />
 * <StatusBadge tone="danger" icon={XCircle}>실패</StatusBadge>
 * ```
 */
function StatusBadge({ tone, size = "default", icon: Icon, label, children, className, ...props }: StatusBadgeProps) {
  return (
    <Badge
      data-slot="status-badge"
      data-tone={tone}
      variant={TONE_VARIANT[tone]}
      size={size}
      className={className}
      {...props}
    >
      {Icon && <Icon />}
      {label ?? children}
    </Badge>
  )
}

export { StatusBadge }
