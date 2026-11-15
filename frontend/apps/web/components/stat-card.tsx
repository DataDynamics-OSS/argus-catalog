"use client"

import { InfoTip } from "@/components/tip"
import { Card, CardContent } from "@workspace/ui/components/card"

import { interactiveCardClass } from "@/components/card-hover"

/**
 * 목록(그리드) 상단 지표 카드 — MCP Server · AI Agent 목록이 함께 쓴다.
 * `tone="warn"` 이면 아이콘을 경고색으로 표시한다. `onClick` 을 주면 눌러서 해당 목록 조건으로 이동한다.
 */
export function StatCard({ icon: Icon, label, value, hint, tip, tone, onClick }: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: number | string
  hint?: string
  /** 판정 기준 같은 긴 설명 — 이름에 점선 밑줄 툴팁으로(TODO-120) */
  tip?: string
  tone?: "warn"
  onClick?: () => void
}) {
  return (
    <Card
      className={onClick ? `cursor-pointer py-3 ${interactiveCardClass}` : "py-3"}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => (e.key === "Enter" || e.key === " ") && onClick() : undefined}
    >
      <CardContent className="flex items-center gap-3 px-4">
        <Icon className={tone === "warn" ? "h-5 w-5 text-amber-500" : "h-5 w-5 text-muted-foreground"} />
        <div className="min-w-0">
          <div className="text-xs"><InfoTip tip={tip}>{label}</InfoTip></div>
          <div className="text-lg font-semibold leading-tight">{value}</div>
          {hint && <div className="truncate text-[11px]" title={hint}>{hint}</div>}
        </div>
      </CardContent>
    </Card>
  )
}
