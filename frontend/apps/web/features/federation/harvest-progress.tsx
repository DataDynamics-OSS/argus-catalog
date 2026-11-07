"use client"

import { CheckCircle2, DownloadCloud, XCircle } from "lucide-react"

/** 가져오기 결과 요약 문구(관측·갱신·임베딩·정리 건수). */
export function runSummary(r: {
  datasets_seen: number
  datasets_upserted: number
  datasets_embedded: number
  datasets_pruned: number
}): string {
  return `관측 ${r.datasets_seen.toLocaleString()} · 갱신 ${r.datasets_upserted.toLocaleString()} · 임베딩 ${r.datasets_embedded.toLocaleString()} · 정리 ${r.datasets_pruned.toLocaleString()}`
}

export type HarvestProg = {
  phase: string // FETCH/EMBED/FINALIZE
  done: number // 현재 단계 진척 분자
  total: number // 현재 단계 진척 분모
  seen: number // 전체 관측 데이터셋 수(라벨용)
  // 완료/실패 결과(설정되면 진행 대신 결과 표시 — 팝업 대체)
  result?: { ok: boolean; text: string }
}

// 단계 순서와 가중치(합=1.0) — 전체 % 를 작업량에 비례해 가중 계산한다.
// SAMPLE 은 선택적(미사용 시 EMBED→FINALIZE 로 건너뜀 → 바가 앞으로 점프).
const PHASE_ORDER = ["FETCH", "EMBED", "SAMPLE", "FINALIZE"] as const
const PHASE_WEIGHT: Record<string, number> = {
  FETCH: 0.4,
  EMBED: 0.35,
  SAMPLE: 0.2,
  FINALIZE: 0.05,
}
const PHASE_LABEL: Record<string, string> = {
  FETCH: "가져오기",
  EMBED: "재임베딩",
  SAMPLE: "샘플",
  FINALIZE: "정리",
}

/** 현재 단계까지 누적 가중치 + 현재 단계 진척분을 합산한 전체 진행 %. */
function overallPct(prog: HarvestProg): number {
  const idx = PHASE_ORDER.indexOf(prog.phase as (typeof PHASE_ORDER)[number])
  if (idx < 0) return 0
  let base = 0
  for (let i = 0; i < idx; i += 1) {
    const ph = PHASE_ORDER[i]
    if (ph) base += (PHASE_WEIGHT[ph] ?? 0) * 100
  }
  const weight = PHASE_WEIGHT[prog.phase] ?? 0
  const frac = prog.total > 0 ? Math.min(1, prog.done / prog.total) : 0
  return Math.min(100, Math.round(base + weight * 100 * frac))
}

/**
 * 가져오기 진행 바 — FETCH→EMBED→FINALIZE 단계 가중 %.
 * 첫 페이지 전(total 미정)에는 비결정형 애니메이션, 이후 정확한 숫자 % 표시.
 */
export function HarvestProgress({ prog }: { prog: HarvestProg }) {
  // 완료/실패 결과 — 팝업 대신 진행 바 자리에 인라인 표시.
  if (prog.result) {
    const { ok, text } = prog.result
    return (
      <div
        className={`flex items-start gap-2 rounded-md border px-3 py-2 text-sm whitespace-normal ${
          ok
            ? "border-primary/30 bg-primary/5 text-foreground"
            : "border-destructive/40 bg-destructive/10 text-destructive"
        }`}
      >
        {ok ? (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        ) : (
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
        )}
        <span className="shrink-0 font-medium">
          {ok ? "가져오기 완료" : "가져오기 실패"}
        </span>
        {/* 표 셀은 nowrap 이라 truncate 로는 폭이 줄지 않는다 — 긴 오류는 두 줄까지 줄바꿈 */}
        <span
          className="line-clamp-2 min-w-0 break-all text-muted-foreground"
          title={text}
        >
          {text}
        </span>
      </div>
    )
  }
  // 단계 분모를 아직 모르는 준비 구간(첫 페이지 전)만 비결정형으로.
  const indeterminate = prog.phase === "FETCH" && prog.total === 0
  const pct = overallPct(prog)
  const label = PHASE_LABEL[prog.phase] ?? prog.phase
  return (
    <div className="flex items-center gap-3">
      <DownloadCloud className="h-4 w-4 shrink-0 animate-pulse text-muted-foreground" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <div className="h-2 flex-1 overflow-hidden rounded bg-muted">
            <div
              className={`h-full rounded bg-primary transition-all ${
                indeterminate ? "w-2/5 animate-pulse" : ""
              }`}
              style={indeterminate ? undefined : { width: `${pct}%` }}
            />
          </div>
          {!indeterminate && (
            <span className="w-10 shrink-0 text-right text-sm font-semibold tabular-nums">
              {pct}%
            </span>
          )}
        </div>
        <span className="block text-[11px] text-muted-foreground">
          {indeterminate
            ? "가져오는 중..."
            : `${label} ${prog.done.toLocaleString()} / ${prog.total.toLocaleString()}` +
              (prog.phase !== "FETCH"
                ? ` · 관측 ${prog.seen.toLocaleString()}`
                : "")}
        </span>
      </div>
    </div>
  )
}
