"use client"

import { createContext, useContext, type ReactNode } from "react"

import {
  Tooltip,
  TooltipContent,

  TooltipTrigger,
} from "@workspace/ui/components/tooltip"
import { cn } from "@workspace/ui/lib/utils"

import { InfoTip } from "@/components/tip"

import { StatusBadge as ToneBadge } from "@workspace/ui/components/status-badge"

import { RiskBadge as SharedRiskBadge } from "@/components/risk-badge"
import { LIFECYCLE_STATUS } from "@/lib/lifecycle"

import {
  DECISION_LABEL,
  DECISION_TONE,
  FINDING_STATUS_LABEL,
  FINDING_STATUS_TONE,
  HEALTH_LABEL,
  HEALTH_VARIANTS,
  ORIGIN_DESCRIPTIONS,
  ORIGIN_LABELS,
  ROW_SOURCE_LABELS,
  ROW_SOURCE_ORIGINS,
  SCOPE_EFFECT_TONE,
  type Origin,
} from "../data/schema"

// 출처 배지 — 특히 "Argus" 는 MCP 표준에 없는 Argus Catalog 전용 관리 정보임을 나타낸다.
const ORIGIN_SHORT: Record<Origin, string> = {
  argus: "Argus",
  mcp: "MCP",
  registry: "Registry",
  server_config: "설정",
}

// 출처마다 다른 색 — 상태 톤(초록 정상 · 주황 주의 · 빨강 실패 · 파랑 진행)과 헷갈리지 않는 범주 구별 색을 연한 칠로 쓴다.
// (TODO-107 에서는 출처를 무채색 테두리로 두었으나, 필드가 어디서 왔는지 한눈에 구별되도록 색을 나눔 — TODO-107 의 리니지 DB 종류 배지와 같은 예외)
const ORIGIN_STYLE: Record<Origin, string> = {
  argus: "border-violet-300 bg-violet-50 text-violet-700 dark:border-violet-800 dark:bg-violet-950/50 dark:text-violet-300",
  mcp: "border-sky-300 bg-sky-50 text-sky-700 dark:border-sky-800 dark:bg-sky-950/50 dark:text-sky-300",
  registry: "border-teal-300 bg-teal-50 text-teal-700 dark:border-teal-800 dark:bg-teal-950/50 dark:text-teal-300",
  server_config: "border-orange-300 bg-orange-50 text-orange-700 dark:border-orange-800 dark:bg-orange-950/50 dark:text-orange-300",
}


export function OriginBadge({ origin, className }: { origin?: Origin | null; className?: string }) {
  if (!origin) return null
  return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            aria-label={ORIGIN_LABELS[origin]}
            className={cn(
              // 출처별 색(ORIGIN_STYLE) + 이름(Argus · MCP · Registry · 설정). 필드 이름 옆에 붙는 꼬리표라 상태 배지보다 작게 둔다.
              "inline-flex h-4 shrink-0 cursor-help items-center rounded border px-1 text-[11px] font-medium leading-none",
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
  )
}

/** 행 단위 source(manual/mcp/forge_rest/...) 배지 — 출처 색상 + 구체 경로 툴팁. */
export function RowSourceBadge({ source, className }: { source: string; className?: string }) {
  const origin = ROW_SOURCE_ORIGINS[source]
  if (!origin) return <span className="text-xs">{source}</span>
  return (
    <span className="inline-flex items-center gap-1" title={ROW_SOURCE_LABELS[source] ?? source}>
      <OriginBadge origin={origin} className={className} />
      <span className="text-[11px]">{ROW_SOURCE_LABELS[source] ?? source}</span>
    </span>
  )
}

/** 상세 화면 상단 범례. */
export function OriginLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
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

/** 서버 · 배포 상태 — AI 에이전트와 같은 수명 주기 맵(`lib/lifecycle.ts`). */
export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const meta = LIFECYCLE_STATUS[status] ?? { label: status, tone: "neutral" as const }
  return <ToneBadge {...meta} className={className} />
}

/** 위험도 — 공용 `components/risk-badge.tsx`. 값이 없으면 "미분류". */
export function RiskBadge({ risk, className }: { risk: string | null | undefined; className?: string }) {
  return <SharedRiskBadge risk={risk} empty="미분류" className={className} />
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
  return <ToneBadge tone={SCOPE_EFFECT_TONE[effect] ?? "neutral"} className="font-mono">{effect}</ToneBadge>
}

/** 감사 이벤트 승인 정책 판정 (allow / deny / blocked / unknown). */
export function DecisionBadge({ decision }: { decision: string | null | undefined }) {
  if (!decision) return <span className="text-xs text-muted-foreground">-</span>
  return <ToneBadge tone={DECISION_TONE[decision] ?? "neutral"} label={DECISION_LABEL[decision] ?? decision} />
}

/** finding 상태 (열림 / 확인됨 / 해소). */
export function FindingStatusBadge({ status }: { status: string }) {
  return <ToneBadge tone={FINDING_STATUS_TONE[status] ?? "neutral"} label={FINDING_STATUS_LABEL[status] ?? status} />
}

/** 라벨 + 출처 배지 + 값 한 줄. */
// FieldTable 안의 FieldRow 는 표 행 모양(라벨 칸 음영, 칸 사이 선)으로 그린다
const FieldTableContext = createContext(false)

/** 읽기 전용 항목 표 — 데이터 소스 상세의 정보 표(InfoTable)와 같은 모양. 안에 FieldRow 를 둔다(TODO-127) */
export function FieldTable({ children }: { children: ReactNode }) {
  return (
    <FieldTableContext.Provider value={true}>
      <div className="divide-y overflow-hidden rounded-md border text-sm">{children}</div>
    </FieldTableContext.Provider>
  )
}

export function FieldRow({
  label,
  origin,
  help,
  children,
}: {
  label: string
  origin?: Origin | null
  /** 항목 설명 — 있으면 이름에 점선 밑줄, 마우스를 올리면 툴팁(데이터셋 상세와 같은 InfoTip) */
  help?: ReactNode
  children: ReactNode
}) {
  const inTable = useContext(FieldTableContext)
  const name = help ? <InfoTip tip={help} side="right">{label}</InfoTip> : <span>{label}</span>
  if (inTable) {
    return (
      <div className="grid grid-cols-[minmax(120px,180px)_1fr]">
        <div className="flex items-center gap-1.5 bg-muted/40 px-3 py-2 font-medium">
          {name}
          <OriginBadge origin={origin} />
        </div>
        <div className="min-w-0 break-words px-3 py-2">
          {children ?? <span className="text-muted-foreground">-</span>}
        </div>
      </div>
    )
  }
  return (
    <div className="grid grid-cols-[160px_1fr] items-start gap-2 py-1.5 text-sm">
      <div className="flex items-center gap-1.5">
        {name}
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
