"use client"

import { useRouter } from "next/navigation"
import { Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { ChartTooltipBox } from "@/components/charts/chart-tooltip"
import { AXIS_TICK, BAR_RADIUS_END, HOVER_CURSOR, SERIES } from "@/components/charts/chart-style"

export type TopBarItem = {
  /** 전체 이름 (툴팁) */
  name: string
  /** 축 라벨용 짧은 이름 — 없으면 name */
  label?: string | null
  value: number
  /** 막대 끝 · 툴팁에 보일 값 (예: "1.2 GB", "34회") */
  display: string
  /** 막대를 누르면 갈 곳 — 없으면 누를 수 없음 */
  href?: string
}

const ROW = 30
const LABEL_MAX = 14

const clip = (s: string) => (s.length > LABEL_MAX ? `${s.slice(0, LABEL_MAX - 1)}…` : s)

/** 축 라벨을 한 줄로 — 기본 눈금은 공백에서 줄을 바꿔 두 줄이 된다. 넘치는 글자는 ``clip`` 이 이미 줄임표로 자름 */
function SingleLineTick({ x, y, payload }: { x?: number | string; y?: number | string; payload?: { value?: string } }) {
  return (
    <text x={x} y={y} dx={-6} dy={4} textAnchor="end" fontSize={AXIS_TICK.fontSize} fill={AXIS_TICK.fill}>
      {payload?.value}
    </text>
  )
}

/**
 * 상위 N개 가로 막대 — 값은 막대 끝에 바로 적어 축 없이 읽는다. 값이 0 인 항목은 제외.
 * 높이는 막대 수에 맞춘다(라벨이 빠지지 않게). 계열이 하나라 색은 기본 1번(바꿀 이유가 있을 때만 ``color``).
 */
export function TopBarCard({
  title, items, color = SERIES[0], empty, limit = 5, className,
}: {
  title: string
  items: TopBarItem[]
  color?: string
  empty: string
  limit?: number
  className?: string
}) {
  const router = useRouter()
  const data = items
    .filter((i) => i.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, limit)
    .map((i) => ({ ...i, axis: clip(i.label || i.name) }))
  const clickable = data.some((d) => d.href)

  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <p className="mt-0.5 text-xs">
          상위 {limit}개{clickable ? " · 막대를 누르면 상세로 이동" : ""}
        </p>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="flex h-[160px] items-center justify-center text-center text-sm text-muted-foreground">{empty}</p>
        ) : (
          <ResponsiveContainer width="100%" height={data.length * ROW + 8}>
            <BarChart data={data} layout="vertical" margin={{ top: 4, right: 64, bottom: 4, left: 0 }} barCategoryGap={6}>
              <XAxis type="number" hide domain={[0, "dataMax"]} />
              <YAxis type="category" dataKey="axis" tickLine={false} axisLine={false} width={112} tick={SingleLineTick} />
              <Tooltip
                cursor={HOVER_CURSOR}
                isAnimationActive={false}
                content={({ active, payload }) => {
                  const d = payload?.[0]?.payload as (TopBarItem & { axis: string }) | undefined
                  if (!active || !d) return null
                  return <ChartTooltipBox title={d.name} rows={[{ key: "v", color, name: title, value: d.display }]} />
                }}
              />
              <Bar
                dataKey="value" fill={color} radius={BAR_RADIUS_END} maxBarSize={20} isAnimationActive={false}
                cursor={clickable ? "pointer" : undefined}
                onClick={(entry) => {
                  const href = (entry as unknown as { payload?: TopBarItem })?.payload?.href
                  if (href) router.push(href)
                }}
              >
                <LabelList dataKey="display" position="right" className="fill-foreground text-[11px]" />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}
