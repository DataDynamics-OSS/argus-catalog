"use client"

import { useMemo, useState } from "react"

import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Highchart } from "@/components/charts/highchart"

import { SegmentedToggle } from "./segmented-toggle"

type Point = { date: string; count: number }
export type TrendSeries = { h24: Point[]; d7: Point[]; d30: Point[] }
type Period = "h24" | "d7" | "d30"

const PERIOD_LABELS: Record<Period, string> = { h24: "24시간", d7: "7일", d30: "30일" }
const pad = (n: number) => String(n).padStart(2, "0")

/**
 * 서버는 데이터가 있는 구간만 준다(시간: UTC "HH:00", 일: UTC "yyyy-MM-dd").
 * 빈 구간을 0 으로 채우고 라벨은 브라우저 시간대로 바꿔 추이가 끊기지 않게 한다.
 */
function buckets(period: Period): { key: string; label: string }[] {
  const now = new Date()
  if (period === "h24") {
    return Array.from({ length: 24 }, (_, i) => {
      const d = new Date(now.getTime() - (23 - i) * 3600_000)
      return { key: `${pad(d.getUTCHours())}:00`, label: `${pad(d.getHours())}시` }
    })
  }
  const days = period === "d7" ? 7 : 30
  return Array.from({ length: days }, (_, i) => {
    const d = new Date(now.getTime() - (days - 1 - i) * 86400_000)
    return {
      key: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`,
      label: `${d.getMonth() + 1}/${d.getDate()}`,
    }
  })
}

function fill(points: Point[], keys: { key: string }[]): number[] {
  const m = new Map(points.map((p) => [p.date, p.count]))
  return keys.map((k) => m.get(k.key) ?? 0)
}

/** 다운로드·게시 추이 — 기간(24시간/7일/30일)을 토글로 바꾸는 하나의 차트. */
export function ActivityTrendCard({
  downloads, publishes, publishLabel = "게시", className,
}: {
  downloads: TrendSeries
  publishes: TrendSeries
  publishLabel?: string
  className?: string
}) {
  const [period, setPeriod] = useState<Period>("d30")
  const keys = useMemo(() => buckets(period), [period])
  const dl = fill(downloads[period], keys)
  const pub = fill(publishes[period], keys)
  const empty = dl.every((v) => v === 0) && pub.every((v) => v === 0)
  const sum = (a: number[]) => a.reduce((x, y) => x + y, 0)

  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-2">
        <div>
          <CardTitle className="text-sm font-medium">활동 추이</CardTitle>
          <p className="mt-0.5 text-xs text-muted-foreground">
            최근 {PERIOD_LABELS[period]} · 다운로드 {sum(dl).toLocaleString()}회 · {publishLabel} {sum(pub).toLocaleString()}건
          </p>
        </div>
        <SegmentedToggle
          value={period}
          onChange={setPeriod}
          options={(Object.keys(PERIOD_LABELS) as Period[]).map((p) => ({ value: p, label: PERIOD_LABELS[p] }))}
        />
      </CardHeader>
      <CardContent>
        {empty ? (
          <p className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">
            최근 {PERIOD_LABELS[period]} 동안 다운로드·{publishLabel} 기록이 없습니다.
          </p>
        ) : (
          <Highchart
            height={220}
            options={{
              chart: { type: "column", style: { fontFamily: "inherit" }, spacing: [8, 4, 4, 4] },
              title: { text: undefined },
              legend: { enabled: true, align: "right", verticalAlign: "top", itemStyle: { fontSize: "11px", fontWeight: "normal" } },
              xAxis: {
                categories: keys.map((k) => k.label),
                labels: { style: { fontSize: "10px" }, step: period === "d30" ? 3 : period === "h24" ? 2 : 1 },
              },
              yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
              tooltip: { shared: true },
              plotOptions: { column: { borderWidth: 0, borderRadius: 2, groupPadding: 0.12, pointPadding: 0.04 } },
              series: [
                { type: "column", name: "다운로드", data: dl, color: "#10b981" },
                { type: "column", name: publishLabel, data: pub, color: "#8b5cf6" },
              ],
            }}
          />
        )}
      </CardContent>
    </Card>
  )
}
