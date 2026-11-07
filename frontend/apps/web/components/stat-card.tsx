"use client"

import { Card, CardContent } from "@workspace/ui/components/card"

/**
 * 목록(그리드) 상단 지표 카드 — MCP Server · AI Agent 목록이 함께 쓴다.
 * `tone="warn"` 이면 아이콘을 경고색으로 표시한다. `onClick` 을 주면 눌러서 해당 목록 조건으로 이동한다.
 */
export function StatCard({ icon: Icon, label, value, hint, tone, onClick }: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: number | string
  hint?: string
  tone?: "warn"
  onClick?: () => void
}) {
  return (
    <Card
      className={onClick ? "cursor-pointer py-3 transition-colors hover:bg-muted/40" : "py-3"}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => (e.key === "Enter" || e.key === " ") && onClick() : undefined}
    >
      <CardContent className="flex items-center gap-3 px-4">
        <Icon className={tone === "warn" ? "h-5 w-5 text-amber-500" : "h-5 w-5 text-muted-foreground"} />
        <div className="min-w-0">
          <div className="text-xs text-muted-foreground">{label}</div>
          <div className="text-lg font-semibold leading-tight">{value}</div>
          {hint && <div className="truncate text-[11px] text-muted-foreground" title={hint}>{hint}</div>}
        </div>
      </CardContent>
    </Card>
  )
}
