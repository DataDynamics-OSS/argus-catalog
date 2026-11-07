"use client"

import { Lock } from "lucide-react"
import { cn } from "@workspace/ui/lib/utils"


import type { ModelDetail, ModelStage, ModelVersionItem, VersionMetrics } from "../../api"

export const STAGE_LABEL: Record<ModelStage, string> = {
  NONE: "없음",
  STAGING: "스테이징",
  PRODUCTION: "운영",
  ARCHIVED: "보관",
}

const STAGE_CLASS: Record<ModelStage, string> = {
  NONE: "border-zinc-200 bg-zinc-50 text-zinc-500",
  STAGING: "border-amber-200 bg-amber-50 text-amber-700",
  PRODUCTION: "border-emerald-200 bg-emerald-50 text-emerald-700",
  ARCHIVED: "border-zinc-200 bg-zinc-100 text-zinc-500",
}

export function StageBadge({ stage, version, className }: { stage: ModelStage; version?: number; className?: string }) {
  return (
    <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium", STAGE_CLASS[stage], className)}>
      {STAGE_LABEL[stage]}{version !== undefined ? ` v${version}` : ""}
    </span>
  )
}

const STATUS_LABEL: Record<string, { label: string; cls: string }> = {
  READY: { label: "준비됨", cls: "bg-blue-500" },
  PENDING_REGISTRATION: { label: "등록 중", cls: "bg-zinc-400" },
  FAILED_REGISTRATION: { label: "등록 실패", cls: "bg-red-500" },
}

/** 버전 등록 상태 배지 — 준비됨(READY) 은 굳이 보여 주지 않는 곳이 많아 호출 측에서 고른다. */
export function VersionStatusBadge({ status }: { status: string | null }) {
  const s = STATUS_LABEL[status ?? ""] ?? { label: status ?? "알 수 없음", cls: "bg-orange-500" }
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold text-white ${s.cls}`}>
      {s.label}
    </span>
  )
}

/** MLflow 모델 파일 브라우저에서 해당 경로를 여는 주소. */
export function filesHref(path: string): string {
  return `/dashboard/mlflow-files?path=${encodeURIComponent(path)}`
}

/** 대표 flavor + 버전 표시 문자열 (예: "sklearn 1.5.2"). */
export function frameworkLabel(c: ModelDetail["catalog"]): string | null {
  if (!c?.flavor) return null
  return c.flavor_version ? `${c.flavor} ${c.flavor_version}` : c.flavor
}

/** 쓰기 권한이 없을 때 보여 주는 안내. */
export function ReadOnlyNote({ what }: { what: string }) {
  return (
    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <Lock className="h-3.5 w-3.5" />
      {what}은(는) 모델 소유자 또는 관리자만 변경할 수 있습니다.
    </p>
  )
}

/** 대표 버전 — 운영 스테이지 중 가장 높은 버전, 없으면 최신 버전. */
export function primaryVersion(versions: ModelVersionItem[]): ModelVersionItem | null {
  const sorted = [...versions].sort((a, b) => b.version - a.version)
  return sorted.find((v) => v.stage === "PRODUCTION") ?? sorted[0] ?? null
}

// 이름으로 "낮을수록 좋은" 지표를 추정한다 (loss·error·rmse·mae·mape·latency 등).
const LOWER_IS_BETTER = /(loss|error|err\b|rmse|mse|mae|mape|smape|latency|perplexity|logloss|wer|cer|_time|duration)/i

export function isLowerBetter(key: string): boolean {
  return LOWER_IS_BETTER.test(key)
}

export function formatMetric(v: number): string {
  if (Number.isInteger(v)) return v.toLocaleString()
  return Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(4)
}

/** 버전의 지표와 직전(지표가 있는) 버전 대비 변화. */
export function metricsWithDelta(all: VersionMetrics[], version: number) {
  const asc = [...all].sort((a, b) => a.version - b.version)
  const cur = asc.find((d) => d.version === version)
  if (!cur) return []
  return Object.keys(cur.metrics).sort().map((key) => {
    const value = cur.metrics[key]!
    const prev = asc.filter((d) => d.version < version && d.metrics[key] !== undefined).pop()?.metrics[key]
    const delta = prev === undefined ? undefined : value - prev
    const improved = delta !== undefined && delta !== 0 && (isLowerBetter(key) ? delta < 0 : delta > 0)
    return { key, value, delta, improved }
  })
}

/** "precision_at_10 0.2840 ▲0.0530" 형태의 작은 지표 칩. */
export function MetricChip({ name, value, delta, improved }: { name: string; value: number; delta?: number; improved?: boolean }) {
  return (
    <span className="inline-flex items-baseline gap-1 rounded-md border bg-muted/30 px-2 py-0.5 text-xs">
      <span className="text-muted-foreground">{name}</span>
      <span className="font-mono font-medium">{formatMetric(value)}</span>
      {delta !== undefined && delta !== 0 && (
        <span className={cn("font-mono text-[11px]", improved ? "text-emerald-600" : "text-red-600")}>
          {delta > 0 ? "▲" : "▼"}{formatMetric(Math.abs(delta))}
        </span>
      )}
    </span>
  )
}
