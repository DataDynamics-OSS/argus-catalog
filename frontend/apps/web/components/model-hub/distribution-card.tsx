"use client"

import Link from "next/link"
import { Cell, Pie, PieChart, Tooltip } from "recharts"

import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { ChartTooltipBox } from "@/components/charts/chart-tooltip"

export type DistributionItem = {
  key: string
  label: string
  count: number
  color: string
  /** 범례 줄을 누르면 갈 곳(예: 그 조건으로 거른 목록) */
  href?: string
}

const SIZE = 160

/** 구성 비율(상태·출처 등) — 도넛 차트 + 개수·비율 범례. 개수가 0 인 항목은 숨긴다. */
export function DistributionCard({
  title, items, empty = "데이터가 없습니다.", unit = "개", className,
}: { title: string; items: DistributionItem[]; empty?: string; unit?: string; className?: string }) {
  const data = items.filter((i) => i.count > 0)
  const total = data.reduce((s, i) => s + i.count, 0)
  const pct = (n: number) => Math.round((n / total) * 100)

  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <p className="mt-0.5 text-xs">총 {total.toLocaleString()}{unit}</p>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="flex h-[200px] items-center justify-center text-center text-sm text-muted-foreground">{empty}</p>
        ) : (
          <div className="flex items-center gap-4">
            {/* 조각 사이는 배경색 2px 간격 — 테두리를 그리지 않는다 */}
            <PieChart width={SIZE} height={SIZE} className="shrink-0" accessibilityLayer>
              <Pie
                data={data} dataKey="count" nameKey="label"
                innerRadius={SIZE * 0.3} outerRadius={SIZE / 2 - 2}
                stroke="var(--card)" strokeWidth={2} isAnimationActive={false}
              >
                {data.map((i) => <Cell key={i.key} fill={i.color} />)}
              </Pie>
              <Tooltip
                isAnimationActive={false}
                content={({ active, payload }) => {
                  const i = payload?.[0]?.payload as DistributionItem | undefined
                  if (!active || !i) return null
                  return (
                    <ChartTooltipBox rows={[{
                      key: i.key, color: i.color, name: i.label,
                      value: `${i.count.toLocaleString()}${unit} (${pct(i.count)}%)`,
                    }]} />
                  )
                }}
              />
            </PieChart>
            <ul className="min-w-0 flex-1 space-y-1">
              {data.map((i) => {
                const row = (
                  <>
                    <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: i.color }} />
                    <span className="min-w-0 flex-1 truncate" title={i.label}>{i.label}</span>
                    <span className="shrink-0 tabular-nums font-medium">{i.count.toLocaleString()}</span>
                    <span className="w-9 shrink-0 text-right text-xs tabular-nums">{pct(i.count)}%</span>
                  </>
                )
                return (
                  <li key={i.key}>
                    {i.href ? (
                      <Link href={i.href} className="-mx-1 flex items-center gap-2 rounded px-1 py-0.5 text-sm hover:bg-muted">
                        {row}
                      </Link>
                    ) : (
                      <div className="flex items-center gap-2 py-0.5 text-sm">{row}</div>
                    )}
                  </li>
                )
              })}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
