import type { ReactNode } from "react"

/** 차트 툴팁 상자 — 제목 한 줄과 "색 · 이름 · 값" 줄. 글자는 본문 색, 계열 색은 점으로만. */
export function ChartTooltipBox({ title, rows }: {
  title?: ReactNode
  rows: { key: string; color?: string; name: ReactNode; value: ReactNode }[]
}) {
  return (
    <div className="min-w-32 max-w-64 rounded-md border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      {title !== undefined && <div className="mb-1 font-medium">{title}</div>}
      <div className="space-y-0.5">
        {rows.map((r) => (
          <div key={r.key} className="flex items-center gap-2">
            {r.color && <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: r.color }} />}
            <span className="min-w-0 flex-1 truncate">{r.name}</span>
            <span className="shrink-0 font-medium tabular-nums">{r.value}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
