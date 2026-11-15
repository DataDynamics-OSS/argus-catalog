"use client"

import { InfoTip, Tip } from "@/components/tip"
import { Fragment, useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { ChevronDown, ChevronRight, FileBarChart, RefreshCw, Square } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { StatusBadge } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { datasetHref } from "@/features/datasets/links"
import { fmtDuration, fmtTime } from "@/features/datasources/sync-format"
import { usePermissions } from "@/features/permissions/use-permissions"
import {
  ACTIVE_QUALITY_STATUSES,
  cancelQualityJobRun,
  fetchQualityJobRuns,
  fetchQualityJobs,
  fetchQualityRuns,
  loadQualityIndicators,
  type QualityIndicator,
  type QualityJob,
  type QualityJobRun,
  type QualityRun,
} from "@/features/quality/api"
import { EmptyRow, StaleNote } from "@/features/quality/components/quality-list-state"
import { QualityRunDetailDialog, ScoreBar } from "@/features/quality/components/quality-run-detail"
import {
  describeMeasured,
  fmtErrorRate,
  jobRunElapsedSeconds,
  QUALITY_JOB_RUN_STATUS_LABEL,
  QUALITY_JOB_RUN_STATUS_TONE,
  QUALITY_STATUS_LABEL,
  QUALITY_STATUS_TONE,
  QUALITY_TRIGGER_LABEL,
  isProfileRun,
  qualityElapsedSeconds,
} from "@/features/quality/format"
import { qualityJobRunsHref, qualityReportHref } from "@/features/quality/links"
import { useLatest, usePolling } from "@/features/quality/use-polling"
import { errorMessage, toast, notifyError } from "@/lib/notify"

const POLL_MS = 5000
const PAGE = 30
const MAX_ROWS = 200
const CHILD_LIMIT = 200
const ALL = "all"

/** 작업 실행 한 건 아래의 데이터셋 실행과, 진단을 시작하지 못한 데이터셋. */
function JobRunChildren({ jobRun, onOpenRun }: { jobRun: QualityJobRun; onOpenRun: (runId: number) => void }) {
  const [runs, setRuns] = useState<QualityRun[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setRuns(await fetchQualityRuns({ jobRunId: jobRun.id, limit: CHILD_LIMIT }))
      setError(null)
    } catch (e) {
      setError(errorMessage(e) ?? String(e))
    }
  }, [jobRun.id])

  // 작업 실행의 집계가 바뀔 때마다(진행 중) 다시 읽는다
  useEffect(() => {
    void load()
  }, [load, jobRun.status, jobRun.runs_succeeded, jobRun.runs_failed, jobRun.profiles_succeeded, jobRun.profiles_failed])

  if (runs == null) {
    return error ? (
      <p className="flex items-center gap-2 px-3 py-2 text-xs text-destructive">
        데이터셋 실행을 불러오지 못했습니다: {error}
        <Button variant="outline" size="sm" className="h-6 px-2 text-xs" onClick={() => void load()}>다시 시도</Button>
      </p>
    ) : <p className="px-3 py-2 text-xs text-muted-foreground">불러오는 중…</p>
  }
  // 오류율이 높은 것부터, 그다음 문제가 있는 것
  const sorted = [...runs].sort((a, b) => (b.error_rate ?? -1) - (a.error_rate ?? -1) || a.id - b.id)
  return (
    <div className="grid gap-2 px-3 py-2">
      <StaleNote error={error} show />
      {jobRun.message && <p className="text-xs">{jobRun.message}</p>}
      {sorted.length > 0 && (
        <table className="w-full text-xs">
          <thead className="text-left text-muted-foreground">
            <tr>
              <th className="py-1 pr-3 font-normal">데이터셋</th>
              <th className="py-1 pr-3 font-normal">상태</th>
              <th className="py-1 pr-3 font-normal">소요</th>
              <th className="py-1 pr-3 text-right font-normal">규칙</th>
              <th className="py-1 pr-3 text-right font-normal">진단 건수</th>
              <th className="py-1 pr-3 text-right font-normal">오류 건수</th>
              <th className="py-1 text-right font-normal">오류율</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.id} className="cursor-pointer border-t hover:bg-muted/40" onClick={() => onOpenRun(r.id)}
                title="규칙별 결과 보기">
                <td className="py-1 pr-3">
                  <span className="font-medium">{r.dataset_name ?? `데이터셋 #${r.dataset_id}`}</span>
                  <span className="ml-1 font-mono">{r.engine_table}</span>
                  {isProfileRun(r) && <Badge variant="outline" className="ml-1">프로파일</Badge>}
                </td>
                <td className="py-1 pr-3">
                  <StatusBadge tone={QUALITY_STATUS_TONE[r.status]}>{QUALITY_STATUS_LABEL[r.status]}</StatusBadge>
                  {r.error_message && r.status !== "SUCCEEDED" && (
                    <span className="ml-1 text-destructive" title={r.error_message}>
                      {r.error_message.length > 40 ? `${r.error_message.slice(0, 40)}…` : r.error_message}
                    </span>
                  )}
                </td>
                <td className="py-1 pr-3">{r.started_at ? fmtDuration(qualityElapsedSeconds(r)) : ""}</td>
                <td className="py-1 pr-3 text-right">
                  {isProfileRun(r) ? "" : ACTIVE_QUALITY_STATUSES.has(r.status) ? "-" : r.rules_evaluated}
                </td>
                <td className="py-1 pr-3 text-right">{r.checked_count?.toLocaleString() ?? ""}</td>
                <td className="py-1 pr-3 text-right">{r.checked_count != null ? (r.error_count ?? 0).toLocaleString() : ""}</td>
                <td className={`py-1 text-right ${r.error_rate ? "text-destructive" : ""}`}>
                  {r.checked_count != null ? fmtErrorRate(r.error_rate) : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {runs.length === CHILD_LIMIT && (
        <p className="text-xs">데이터셋 실행이 많아 앞의 {CHILD_LIMIT}개만 보여 줍니다.</p>
      )}
      {jobRun.skipped_total > 0 && (
        <div className="grid gap-1">
          <p className="text-xs font-medium">진단을 시작하지 못한 데이터셋 {jobRun.skipped_total}개</p>
          <ul className="grid gap-0.5 text-xs">
            {jobRun.skipped.map((s) => (
              <li key={s.dataset_id}>
                <span className="font-medium">{s.name ?? `데이터셋 #${s.dataset_id}`}</span>
                <span className="ml-1">{s.reason}</span>
              </li>
            ))}
          </ul>
          {jobRun.skipped_total > jobRun.skipped.length && (
            <p className="text-xs">나머지 {jobRun.skipped_total - jobRun.skipped.length}개는 목록에 남기지 않았습니다.</p>
          )}
        </div>
      )}
      {jobRun.no_rules > 0 && (
        <p className="text-xs">
          대상 가운데 {jobRun.no_rules.toLocaleString()}개 데이터셋은 고른 진단 기준의 활성 규칙이 없어 진단하지 않았습니다.
        </p>
      )}
    </div>
  )
}

/** 진행 칸 — 실행 현황과 같은 모양: 실행 중이면 쿼리 진행 막대, 끝났으면 상태 배지. */
function JobRunProgressCell({ run }: { run: QualityJobRun }) {
  if (run.status === "RUNNING") {
    const finished = run.runs_succeeded + run.runs_failed
    const pct = run.progress_total ? Math.min(100, Math.round((run.progress_done / run.progress_total) * 100)) : null
    if (pct === null) {
      return <span >{run.runs_total > 1 ? `데이터셋 ${finished}/${run.runs_total}` : "대기"}</span>
    }
    return (
      <div className="grid gap-0.5">
        <div className="h-2 w-40 overflow-hidden rounded bg-muted">
          <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
        </div>
        <span className="max-w-64 truncate text-[11px]" title={run.current_target ?? undefined}>
          쿼리 {run.progress_done}/{run.progress_total}
          {run.runs_total > 1 ? ` · 데이터셋 ${finished}/${run.runs_total}` : ""}
          {run.current_target ? ` · ${run.current_target}` : ""}
        </span>
      </div>
    )
  }
  return (
    <div className="grid gap-0.5">
      <span><StatusBadge tone={QUALITY_JOB_RUN_STATUS_TONE[run.status]}>{QUALITY_JOB_RUN_STATUS_LABEL[run.status]}</StatusBadge></span>
      {run.message && <span className="max-w-64 truncate" title={run.message}>{run.message}</span>}
      {(run.runs_total > 1 || run.skipped_total > 0) && run.status !== "SKIPPED" && (
        <span >
          데이터셋 성공 {run.runs_succeeded}
          {run.runs_failed > 0 && <span className="text-destructive"> · 실패 {run.runs_failed}</span>}
          {run.skipped_total > 0 && <span className="text-destructive"> · 시작 못 함 {run.skipped_total}</span>}
        </span>
      )}
      {(run.profiles_total ?? 0) > 0 && run.status !== "SKIPPED" && (
        <span >
          프로파일 성공 {run.profiles_succeeded ?? 0}/{run.profiles_total}
          {(run.profiles_failed ?? 0) > 0 && <span className="text-destructive"> · 실패 {run.profiles_failed}</span>}
        </span>
      )}
    </div>
  )
}

/** 데이터 품질 > 작업 이력 탭 — 작업 실행 → 데이터셋 실행 → 규칙 결과. */
export function QualityJobRunsTab() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const { canUseFeature } = usePermissions()
  // 관리 › 데이터 품질 메뉴의 실행 · 취소 · 다시 실행은 품질 관리 권한(quality.manage)으로 — 서버와 같은 기준
  const canRun = canUseFeature("quality.manage")
  // ?job=<작업 id> 로 들어오면 그 작업의 이력만
  const jobParam = params.get("job")
  const jobId = jobParam && /^\d+$/.test(jobParam) ? jobParam : ALL

  const [jobs, setJobs] = useState<QualityJob[]>([])
  const [indicators, setIndicators] = useState<QualityIndicator[]>([])
  const [rows, setRows] = useState<QualityJobRun[]>([])
  const [more, setMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<Set<number>>(new Set())
  const [detailId, setDetailId] = useState<number | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [, setTick] = useState(0)

  // 보이는 줄 수 — 새로고침·취소 뒤에도 "더 보기" 로 불러온 만큼 다시 읽는다
  const shown = useRef(PAGE)
  const begin = useLatest()
  const load = useCallback(async (opts: { quiet?: boolean; more?: boolean } = {}) => {
    const current = begin()
    if (!opts.quiet) setLoading(true)
    const want = Math.min(MAX_ROWS, opts.more ? shown.current + PAGE : shown.current)
    try {
      const page = await fetchQualityJobRuns({ jobId: jobId === ALL ? undefined : Number(jobId), limit: want })
      if (!current()) return // 그 사이 작업을 바꿨다 — 늦게 온 응답은 버린다
      shown.current = Math.max(PAGE, want)
      setRows(page)
      setMore(page.length === want && want < MAX_ROWS)
      setError(null)
    } catch (e) {
      if (!current()) return
      const message = errorMessage(e) ?? String(e)
      setError(message)
      if (!opts.quiet) notifyError(e, "작업 실행 이력을 불러오지 못했습니다.")
    } finally {
      if (current()) setLoading(false)
    }
  }, [jobId, begin])

  useEffect(() => {
    shown.current = PAGE
    setRows([])
    setError(null)
    void load()
  }, [load])

  useEffect(() => {
    fetchQualityJobs().then(setJobs).catch(() => setJobs([]))
    loadQualityIndicators().then(setIndicators).catch(() => setIndicators([]))
  }, [])

  // 실행 중인 작업이 있으면 끝날 때까지 다시 읽는다. 한 번 실패해도 다음 차례에 다시 읽는다
  const hasRunning = rows.some((r) => r.status === "RUNNING")
  usePolling(hasRunning, () => load({ quiet: true }), POLL_MS)

  useEffect(() => {
    if (!hasRunning) return
    const t = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [hasRunning])

  const toggle = (id: number) => {
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const cancel = async (row: QualityJobRun) => {
    setBusy(row.id)
    try {
      await cancelQualityJobRun(row.id)
      toast.success("중지를 요청했습니다.")
    } catch (e) {
      notifyError(e, "작업 실행을 중지하지 못했습니다.")
    } finally {
      setBusy(null)
      await load()
    }
  }

  return (
    <div className="flex flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={jobId}
          onValueChange={(v) => router.replace(v === ALL ? `${pathname}?tab=history` : qualityJobRunsHref(Number(v)), { scroll: false })}>
          <SelectTrigger className="h-8 w-64"><SelectValue placeholder="품질 진단 작업" /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>전체 품질 진단 작업</SelectItem>
            {jobs.map((j) => <SelectItem key={j.id} value={String(j.id)}>{j.name}</SelectItem>)}
          </SelectContent>
        </Select>
        <span className="text-xs">행을 누르면 그 실행의 상세(개요 · 규칙별 결과 · 쿼리)가 열립니다.</span>
        <Tip label="새로고침"><Button aria-label="새로고침" variant="outline" size="sm" className="ml-auto" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
        </Button></Tip>
      </div>

      <StaleNote error={error} show={rows.length > 0} />
      <div className="overflow-auto rounded border">
        <table className="w-full text-sm">
          {/* 열은 실행 현황과 같다 — 작업 · 시작 · 진행 · 경과 · 규칙 · 진단/오류 건수 · 오류율 · 품질 점수 */}
          <thead className="bg-muted text-left text-xs">
            <tr>
              <th className="px-3 py-2">품질 진단 작업</th>
              <th className="px-3 py-2">시작</th>
              <th className="px-3 py-2">진행</th>
              <th className="px-3 py-2">경과</th>
              <th className="px-3 py-2">규칙</th>
              <th className="px-3 py-2">진단/오류 건수</th>
              <th className="px-3 py-2">오류율</th>
              <th className="px-3 py-2"><InfoTip tip="통과한 규칙 수 ÷ 판정한 규칙 수 × 100 입니다. 판정하지 못한(미평가) 규칙은 빼고 계산합니다.">품질 점수</InfoTip></th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <EmptyRow colSpan={9} loading={loading} error={error} onRetry={() => void load()}>
                작업 이력이 없습니다. 품질 진단 작업 탭에서 작업을 등록하거나 &quot;지금 실행&quot; 하면 여기에 쌓입니다.
              </EmptyRow>
            ) : rows.map((r) => {
              // 데이터셋 실행이 하나뿐이면 행을 누를 때 그 실행의 상세를 바로 연다. 아니면(예전 방식의 여러 데이터셋·건너뜀) 펼친다
              const single = r.run_id != null && r.skipped_total === 0
              const expanded = !single && open.has(r.id)
              const active = r.status === "RUNNING"
              return (
                <Fragment key={r.id}>
                  <tr className="cursor-pointer border-t hover:bg-muted/40"
                    title={single ? "실행 상세 보기" : "데이터셋별 결과 펼치기"}
                    onClick={() => (single ? setDetailId(r.run_id) : toggle(r.id))}>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-1 font-medium">
                        {!single && (expanded
                          ? <ChevronDown className="size-4 text-muted-foreground" />
                          : <ChevronRight className="size-4 text-muted-foreground" />)}
                        {r.job_name ?? `작업 #${r.job_id}`}
                      </div>
                      <div className="text-xs">
                        #{r.id}
                        {r.dataset_id != null ? (
                          <>
                            {" · "}
                            <Link href={datasetHref(r.dataset_id, "quality")} className="hover:underline"
                              onClick={(e) => e.stopPropagation()} title="데이터셋 품질 탭에서 열기">
                              {r.dataset_name ?? `데이터셋 #${r.dataset_id}`}
                            </Link>
                            {r.engine_table && <> · <span className="font-mono">{r.engine_table}</span></>}
                          </>
                        ) : r.status !== "SKIPPED" && ` · 데이터셋 ${r.targets_total.toLocaleString()}개`}
                      </div>
                      <div className="max-w-96 truncate text-xs" title={describeMeasured(r, indicators)}>
                        {describeMeasured(r, indicators)}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <div>{r.trigger_type === "MANUAL" ? (r.requested_by ?? "수동") : QUALITY_TRIGGER_LABEL[r.trigger_type] ?? r.trigger_type}</div>
                      <div >{fmtTime(r.started_at)}</div>
                    </td>
                    <td className="px-3 py-2 text-xs"><JobRunProgressCell run={r} /></td>
                    <td className="px-3 py-2 text-xs">
                      {r.status === "SKIPPED" ? "" : fmtDuration(jobRunElapsedSeconds(r))}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {r.runs_total === 0 ? "" : active ? `-/${r.rules_total}` : `${r.rules_evaluated}/${r.rules_total}`}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {r.checked_count != null ? `${r.checked_count.toLocaleString()} / ${(r.error_count ?? 0).toLocaleString()}` : ""}
                    </td>
                    <td className={`px-3 py-2 text-xs ${r.error_rate ? "text-destructive" : ""}`}>
                      {r.checked_count != null ? fmtErrorRate(r.error_rate) : ""}
                    </td>
                    <td className="px-3 py-2 text-xs">{r.score != null && !active && <ScoreBar score={r.score} compact />}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                      {canRun && r.status === "RUNNING" && !r.cancel_requested_by && (
                        <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={busy === r.id}
                          onClick={() => void cancel(r)}>
                          <Square className="mr-1 size-3" /> 중지
                        </Button>
                      )}
                      {r.status === "RUNNING" && r.cancel_requested_by && (
                        <span className="text-xs">중지 요청됨</span>
                      )}
                      {r.status !== "RUNNING" && r.status !== "SKIPPED" && (
                        <Button asChild variant="ghost" size="sm" className="h-7 px-2 text-xs">
                          <Link href={qualityReportHref(r.job_id, r.id)} title="이 작업 실행의 리포트">
                            <FileBarChart className="mr-1 size-3" /> 리포트
                          </Link>
                        </Button>
                      )}
                    </td>
                  </tr>
                  {expanded && (
                    <tr className="border-t bg-muted/20">
                      <td colSpan={9}>
                        {r.status === "SKIPPED"
                          ? <p className="px-3 py-2 text-xs">{r.message}</p>
                          : <JobRunChildren jobRun={r} onOpenRun={setDetailId} />}
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      {more && (
        <Button variant="outline" size="sm" className="self-center" disabled={loading}
          onClick={() => void load({ more: true })}>
          더 보기
        </Button>
      )}

      <QualityRunDetailDialog runId={detailId} onOpenChange={(o) => !o && setDetailId(null)} />
    </div>
  )
}
