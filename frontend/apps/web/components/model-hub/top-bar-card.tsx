"use client"

import { useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { ChartTooltipBox } from "@/components/charts/chart-tooltip"
import { AXIS_TICK, BAR_RADIUS_END, HOVER_CURSOR, SERIES } from "@/components/charts/chart-style"
import { tooltipLeft } from "@/components/charts/tooltip-position"

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
  // 툴팁 가로 위치는 직접 정한다 — 그림 영역(라벨 112 · 값 64 를 뺀 나머지)이 툴팁보다 좁은 카드에서
  // Recharts 가 뒤집을 자리를 못 찾아 카드 밖으로 내보내 잘리기 때문(DEF-178)
  const boxRef = useRef<HTMLDivElement>(null)
  const tipRef = useRef<HTMLDivElement>(null)
  const [tipX, setTipX] = useState<number | undefined>(undefined)
  const placeTooltip = (e: React.MouseEvent<HTMLDivElement>) => {
    const box = boxRef.current?.getBoundingClientRect()
    if (!box) return
    // 아직 그려지기 전이면 툴팁 최대 폭(max-w-64)으로 잡는다
    setTipX(Math.round(tooltipLeft(e.clientX - box.left, tipRef.current?.offsetWidth || 256, box.width)))
  }

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
          <div ref={boxRef} onMouseMove={placeTooltip}>
          <ResponsiveContainer width="100%" height={data.length * ROW + 8}>
            <BarChart data={data} layout="vertical" margin={{ top: 4, right: 64, bottom: 4, left: 0 }} barCategoryGap={6}>
              <XAxis type="number" hide domain={[0, "dataMax"]} />
              <YAxis type="category" dataKey="axis" tickLine={false} axisLine={false} width={112} tick={SingleLineTick} />
              <Tooltip
                cursor={HOVER_CURSOR}
                isAnimationActive={false}
                position={{ x: tipX }}
                content={({ active, payload }) => {
                  const d = payload?.[0]?.payload as (TopBarItem & { axis: string }) | undefined
                  if (!active || !d) return null
                  return (
                    <div ref={tipRef}>
                      <ChartTooltipBox title={d.name} rows={[{ key: "v", color, name: title, value: d.display }]} />
                    </div>
                  )
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
          </div>
        )}
      </CardContent>
    </Card>
  )
}
