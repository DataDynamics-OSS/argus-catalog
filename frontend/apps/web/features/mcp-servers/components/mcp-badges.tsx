"use client"

import type { ReactNode } from "react"

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@workspace/ui/components/tooltip"
import { cn } from "@workspace/ui/lib/utils"

import {
  DECISION_LABEL,
  DECISION_VARIANTS,
  FINDING_STATUS_LABEL,
  HEALTH_LABEL,
  HEALTH_VARIANTS,
  ORIGIN_DESCRIPTIONS,
  ORIGIN_LABELS,
  RISK_LABEL,
  RISK_VARIANTS,
  ROW_SOURCE_LABELS,
  ROW_SOURCE_ORIGINS,
  SCOPE_EFFECT_VARIANTS,
  STATUS_LABEL,
  STATUS_VARIANTS,
  type Origin,
} from "../data/schema"

// 출처 배지 — 특히 "Argus" 는 MCP 표준에 없는 Argus Catalog 전용 관리 정보임을 나타낸다.
const ORIGIN_SHORT: Record<Origin, string> = {
  argus: "Argus",
  mcp: "MCP",
  registry: "Registry",
  server_config: "설정",
}

const ORIGIN_STYLE: Record<Origin, string> = {
  argus: "border-violet-300 bg-violet-50 text-violet-700 dark:border-violet-700 dark:bg-violet-950/50 dark:text-violet-300",
  mcp: "border-sky-300 bg-sky-50 text-sky-700 dark:border-sky-700 dark:bg-sky-950/50 dark:text-sky-300",
  registry: "border-teal-300 bg-teal-50 text-teal-700 dark:border-teal-700 dark:bg-teal-950/50 dark:text-teal-300",
  server_config: "border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-700 dark:bg-amber-950/50 dark:text-amber-300",
}

export function OriginBadge({ origin, className }: { origin?: Origin | null; className?: string }) {
  if (!origin) return null
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            aria-label={ORIGIN_LABELS[origin]}
            className={cn(
              "inline-flex h-4 shrink-0 cursor-help items-center rounded border px-1 text-[10px] font-medium leading-none",
              ORIGIN_STYLE[origin],
              className,
            )}
          >
            {ORIGIN_SHORT[origin]}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs whitespace-pre-wrap">
          <div className="font-medium">{ORIGIN_LABELS[origin]}</div>
          <div className="mt-0.5 opacity-90">{ORIGIN_DESCRIPTIONS[origin]}</div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

/** 행 단위 source(manual/mcp/forge_rest/...) 배지 — 출처 색상 + 구체 경로 툴팁. */
export function RowSourceBadge({ source, className }: { source: string; className?: string }) {
  const origin = ROW_SOURCE_ORIGINS[source]
  if (!origin) return <span className="text-xs text-muted-foreground">{source}</span>
  return (
    <span className="inline-flex items-center gap-1" title={ROW_SOURCE_LABELS[source] ?? source}>
      <OriginBadge origin={origin} className={className} />
      <span className="text-[11px] text-muted-foreground">{ROW_SOURCE_LABELS[source] ?? source}</span>
    </span>
  )
}

/** 상세 화면 상단 범례. */
export function OriginLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      <span>필드 출처:</span>
      {(Object.keys(ORIGIN_LABELS) as Origin[]).map((o) => (
        <span key={o} className="inline-flex items-center gap-1">
          <OriginBadge origin={o} />
          {ORIGIN_LABELS[o]}
        </span>
      ))}
    </div>
  )
}

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        STATUS_VARIANTS[status] ?? STATUS_VARIANTS.draft,
        className,
      )}
    >
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

export function RiskBadge({ risk, className }: { risk: string | null | undefined; className?: string }) {
  if (!risk) return <span className="text-xs text-muted-foreground">미분류</span>
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium",
        RISK_VARIANTS[risk] ?? "bg-muted text-muted-foreground",
        className,
      )}
    >
      {RISK_LABEL[risk] ?? risk}
    </span>
  )
}

export function HealthDot({ status, withLabel = true }: { status: string; withLabel?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs" title={HEALTH_LABEL[status] ?? status}>
      <span className={cn("inline-block h-2 w-2 rounded-full", HEALTH_VARIANTS[status] ?? HEALTH_VARIANTS.unknown)} />
      {withLabel && (HEALTH_LABEL[status] ?? status)}
    </span>
  )
}

export function EffectBadge({ effect }: { effect: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium",
        SCOPE_EFFECT_VARIANTS[effect] ?? "bg-muted text-muted-foreground",
      )}
    >
      {effect}
    </span>
  )
}

/** 감사 이벤트 승인 정책 판정 (allow / deny / blocked / unknown). */
export function DecisionBadge({ decision }: { decision: string | null | undefined }) {
  if (!decision) return <span className="text-xs text-muted-foreground">-</span>
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium",
        DECISION_VARIANTS[decision] ?? "bg-muted text-muted-foreground",
      )}
    >
      {DECISION_LABEL[decision] ?? decision}
    </span>
  )
}

/** finding 상태 (열림 / 확인됨 / 해소). */
export function FindingStatusBadge({ status }: { status: string }) {
  const tone =
    status === "open" ? "bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-300 dark:border-red-800"
      : status === "acknowledged" ? "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800"
        : "bg-zinc-50 text-zinc-500 border-zinc-200 dark:bg-zinc-900 dark:text-zinc-400 dark:border-zinc-700"
  return (
    <span className={cn("inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium", tone)}>
      {FINDING_STATUS_LABEL[status] ?? status}
    </span>
  )
}

/** 라벨 + 출처 배지 + 값 한 줄. */
export function FieldRow({
  label,
  origin,
  children,
}: {
  label: string
  origin?: Origin | null
  children: ReactNode
}) {
  return (
    <div className="grid grid-cols-[160px_1fr] items-start gap-2 py-1.5 text-sm">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        <span>{label}</span>
        <OriginBadge origin={origin} />
      </div>
      <div className="min-w-0 break-words">{children ?? <span className="text-muted-foreground">-</span>}</div>
    </div>
  )
}

export function Dash() {
  return <span className="text-muted-foreground">-</span>
}

export function fmtDate(v: string | null | undefined): string {
  if (!v) return "-"
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString("ko-KR", { hour12: false })
}
