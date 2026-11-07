"use client"

import { useRouter } from "next/navigation"

import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Highchart } from "@/components/charts/highchart"

export type TopBarItem = {
  /** 전체 이름 (툴팁) */
  name: string
  /** 축 라벨용 짧은 이름 — 없으면 name */
  label?: string | null
  value: number
  /** 툴팁에 보일 값 (예: "1.2 GB", "34회") */
  display: string
  href: string
}

/** 상위 N개 모델 가로 막대 차트 — 막대를 누르면 모델 상세로 이동한다. 값이 0 인 항목은 제외. */
export function TopBarCard({
  title, items, color, empty, limit = 5, axisFormat, className,
}: {
  title: string
  items: TopBarItem[]
  color: string
  empty: string
  limit?: number
  /** 값 축 라벨 포맷 (예: 바이트 → "2G") */
  axisFormat?: (v: number) => string
  className?: string
}) {
  const router = useRouter()
  const data = items.filter((i) => i.value > 0).sort((a, b) => b.value - a.value).slice(0, limit)

  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <p className="mt-0.5 text-xs text-muted-foreground">상위 {limit}개 · 막대를 누르면 상세로 이동</p>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="flex h-[200px] items-center justify-center text-center text-sm text-muted-foreground">{empty}</p>
        ) : (
          <Highchart
            height={200}
            options={{
              chart: { type: "bar", style: { fontFamily: "inherit" }, spacing: [4, 8, 4, 4] },
              title: { text: undefined },
              legend: { enabled: false },
              xAxis: {
                categories: data.map((d) => d.label || d.name),
                labels: { style: { fontSize: "11px" } },
              },
              yAxis: {
                title: { text: undefined },
                allowDecimals: false,
                labels: {
                  style: { fontSize: "11px" },
                  ...(axisFormat ? { formatter() { return axisFormat(this.value as number) } } : {}),
                },
              },
              tooltip: {
                formatter() {
                  const d = data[this.index]
                  return d ? `<b>${d.name}</b><br/>${d.display}` : ""
                },
              },
              plotOptions: {
                bar: {
                  borderWidth: 0, borderRadius: 2, cursor: "pointer",
                  point: { events: { click() { const d = data[this.index]; if (d) router.push(d.href) } } },
                },
              },
              series: [{ type: "bar", name: title, data: data.map((d) => d.value), color }],
            }}
          />
        )}
      </CardContent>
    </Card>
  )
}
