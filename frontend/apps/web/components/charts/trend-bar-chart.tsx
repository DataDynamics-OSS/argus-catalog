"use client"

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import { ChartTooltipBox } from "./chart-tooltip"
import { AXIS_TICK, BAR_RADIUS_TOP, GRID, HOVER_CURSOR } from "./chart-style"

export type TrendSeriesDef = { key: string; name: string; color: string; data: number[]; unit?: string }

/**
 * 시간 축 막대 — 건수 추이(신규 데이터셋 · 다운로드 · 게시 등). 건수는 0 미만이 없어 곡선 대신 막대.
 * 계열이 둘 이상이면 위에 범례를 둔다. 툴팁 제목은 ``fullLabels``(없으면 축 라벨).
 */
export function TrendBarChart({ labels, fullLabels, series, height = 200 }: {
  labels: string[]
  fullLabels?: string[]
  series: TrendSeriesDef[]
  height?: number
}) {
  const rows = labels.map((label, i) => {
    const row: Record<string, string | number> = { label, full: fullLabels?.[i] ?? label }
    for (const s of series) row[s.key] = s.data[i] ?? 0
    return row
  })
  // 라벨이 겹치지 않게 대략 8개 안팎만 보인다
  const interval = Math.max(0, Math.ceil(labels.length / 8) - 1)

  return (
    <div className="space-y-2">
      {series.length > 1 && (
        <div className="flex flex-wrap items-center justify-end gap-3 text-xs">
          {series.map((s) => (
            <span key={s.key} className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: s.color }} />
              {s.name}
            </span>
          ))}
        </div>
      )}
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} barGap={2} barCategoryGap="20%">
          <CartesianGrid {...GRID} />
          <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: "var(--border)" }} interval={interval} />
          <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} allowDecimals={false} width={36} />
          <Tooltip
            cursor={HOVER_CURSOR}
            isAnimationActive={false}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const row = payload[0]?.payload as Record<string, string | number>
              return (
                <ChartTooltipBox
                  title={row.full}
                  rows={series.map((s) => ({
                    key: s.key, color: s.color, name: s.name,
                    value: `${Number(row[s.key] ?? 0).toLocaleString()}${s.unit ?? ""}`,
                  }))}
                />
              )
            }}
          />
          {series.map((s) => (
            <Bar key={s.key} dataKey={s.key} name={s.name} fill={s.color} radius={BAR_RADIUS_TOP} maxBarSize={28}
              isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
