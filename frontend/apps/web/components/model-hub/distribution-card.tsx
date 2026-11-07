"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Highchart } from "@/components/charts/highchart"

export type DistributionItem = { key: string; label: string; count: number; color: string }

/** 구성 비율(상태·출처 등) — 도넛 차트 + 개수·비율 범례. 개수가 0 인 항목은 숨긴다. */
export function DistributionCard({
  title, items, empty = "데이터가 없습니다.", className,
}: { title: string; items: DistributionItem[]; empty?: string; className?: string }) {
  const data = items.filter((i) => i.count > 0)
  const total = data.reduce((s, i) => s + i.count, 0)

  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <p className="mt-0.5 text-xs text-muted-foreground">총 {total.toLocaleString()}개</p>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="flex h-[200px] items-center justify-center text-center text-sm text-muted-foreground">{empty}</p>
        ) : (
          <div className="flex items-center gap-4">
            <Highchart
              height={170}
              className="w-[170px] shrink-0"
              options={{
                chart: { type: "pie", style: { fontFamily: "inherit" }, spacing: [0, 0, 0, 0] },
                title: { text: undefined },
                legend: { enabled: false },
                tooltip: { pointFormat: "<b>{point.y}</b>개 ({point.percentage:.0f}%)" },
                plotOptions: { pie: { innerSize: "60%", dataLabels: { enabled: false }, borderWidth: 2 } },
                series: [{
                  type: "pie", name: title,
                  data: data.map((i) => ({ name: i.label, y: i.count, color: i.color })),
                }],
              }}
            />
            <ul className="min-w-0 flex-1 space-y-2">
              {data.map((i) => (
                <li key={i.key} className="flex items-center gap-2 text-sm">
                  <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: i.color }} />
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">{i.label}</span>
                  <span className="shrink-0 tabular-nums font-medium">{i.count.toLocaleString()}</span>
                  <span className="w-9 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                    {Math.round((i.count / total) * 100)}%
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
