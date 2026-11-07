"use client"

import { useMemo } from "react"
import { BarChart3 } from "lucide-react"
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import { Badge } from "@workspace/ui/components/badge"
import { cn } from "@workspace/ui/lib/utils"
import { EmptyState } from "@/components/model-hub/detail-layout"

import type { ModelStage, ModelVersionItem, VersionMetrics } from "../../api"
import { formatMetric, isLowerBetter, StageBadge } from "./shared"

/** 성능 탭 — 버전별 지표 비교표(최신 버전 먼저, 최고값 강조, 직전 대비 변화) + 3개 버전 이상이면 추이 차트. */
export function PerformanceTab({ metrics, versions }: { metrics: VersionMetrics[]; versions: ModelVersionItem[] }) {
  const keys = useMemo(() => Array.from(new Set(metrics.flatMap((d) => Object.keys(d.metrics)))).sort(), [metrics])
  const ascending = useMemo(() => [...metrics].sort((a, b) => a.version - b.version), [metrics])
  const stageOf = useMemo(() => new Map<number, ModelStage>(versions.map((v) => [v.version, v.stage])), [versions])

  const best = useMemo(() => {
    const out: Record<string, number> = {}
    for (const k of keys) {
      const vals = metrics.map((d) => d.metrics[k]).filter((v): v is number => v !== undefined)
      if (vals.length) out[k] = isLowerBetter(k) ? Math.min(...vals) : Math.max(...vals)
    }
    return out
  }, [metrics, keys])

  if (metrics.length === 0) {
    return (
      <EmptyState
        icon={BarChart3}
        text={<>기록된 성능 지표가 없습니다.<br />학습 코드에서 <code className="text-xs">POST /api/v1/models/{"{이름}"}/versions/{"{버전}"}/metrics</code> 로 버전별 지표를 기록하세요.</>}
      />
    )
  }

  const prevOf = (version: number, key: string) =>
    ascending.filter((d) => d.version < version && d.metrics[key] !== undefined).pop()?.metrics[key]

  return (
    <div className="space-y-4">
      <div className="overflow-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="w-28 px-3 py-2 text-left font-medium">버전</th>
              {keys.map((k) => (
                <th key={k} className="px-3 py-2 text-right font-medium">
                  {k}
                  <span className="ml-1 text-[11px] font-normal text-muted-foreground">{isLowerBetter(k) ? "↓좋음" : "↑좋음"}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {[...ascending].reverse().map((d) => {
              const stage = stageOf.get(d.version)
              return (
                <tr key={d.version}>
                  <td className="px-3 py-2">
                    <span className="font-medium">v{d.version}</span>
                    {stage && stage !== "NONE" && <StageBadge stage={stage} className="ml-1.5" />}
                  </td>
                  {keys.map((k) => {
                    const v = d.metrics[k]
                    if (v === undefined) return <td key={k} className="px-3 py-2 text-right text-muted-foreground">—</td>
                    const prev = prevOf(d.version, k)
                    const delta = prev === undefined ? undefined : v - prev
                    const improved = delta !== undefined && delta !== 0 && (isLowerBetter(k) ? delta < 0 : delta > 0)
                    const isBest = metrics.length > 1 && v === best[k]
                    return (
                      <td key={k} className="px-3 py-2 text-right font-mono">
                        {isBest && <Badge variant="outline" className="mr-1.5 px-1 py-0 text-[10px]">최고</Badge>}
                        <span className={cn(isBest && "font-semibold text-emerald-700")}>{formatMetric(v)}</span>
                        {delta !== undefined && delta !== 0 && (
                          <span className={cn("ml-1.5 text-xs", improved ? "text-emerald-600" : "text-red-600")} title="직전 버전 대비">
                            {delta > 0 ? "▲" : "▼"}{formatMetric(Math.abs(delta))}
                          </span>
                        )}
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {ascending.length >= 3 && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {keys.map((k) => {
            const series = ascending.filter((d) => d.metrics[k] !== undefined).map((d) => ({ version: `v${d.version}`, value: d.metrics[k] }))
            if (series.length < 3) return null
            return (
              <div key={k} className="rounded-lg border p-4">
                <p className="mb-2 text-sm font-medium">{k} 추이</p>
                <ResponsiveContainer width="100%" height={160}>
                  <LineChart data={series} margin={{ left: 0, right: 10, top: 5, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="version" fontSize={11} />
                    <YAxis fontSize={11} width={48} domain={["auto", "auto"]} />
                    <Tooltip formatter={(val) => (typeof val === "number" ? formatMetric(val) : String(val))} />
                    <Line type="monotone" dataKey="value" name={k} stroke="#3b82f6" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
