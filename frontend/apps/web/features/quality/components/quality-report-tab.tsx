"use client"

import { useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { FileSpreadsheet, FileText } from "lucide-react"
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { AXIS_TICK, GRID, SERIES } from "@/components/charts/chart-style"

import { StatusBadge } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { fmtDuration, fmtTime } from "@/features/datasources/sync-format"
import {
  fetchQualityJobRunReport,
  fetchQualityJobRunReportPdf,
  fetchQualityJobRunReportXlsx,
  fetchQualityJobRuns,
  fetchQualityJobs,
  fetchQualityJobTrend,
  loadQualityIndicators,
  type QualityIndicator,
  type QualityJob,
  type QualityJobRun,
  type QualityJobRunReport,
  type QualityJobTrend,
  type QualityReportGroup,
} from "@/features/quality/api"
import { QualityRunDetailDialog } from "@/features/quality/components/quality-run-detail"
import {
  describeChange,
  describeMeasured,
  failedFirst,
  fmtErrorRate,
  jobRunElapsedSeconds,
  QUALITY_JOB_RUN_STATUS_LABEL,
  QUALITY_JOB_RUN_STATUS_TONE,
  QUALITY_STATUS_LABEL,
  QUALITY_STATUS_TONE,
  type QualityChange,
  checkResult,
  rateBarPercent,
} from "@/features/quality/format"
import { qualityReportHref } from "@/features/quality/links"
import { errorMessage, notifyError } from "@/lib/notify"
import { usePermissions } from "@/features/permissions/use-permissions"

const OVERALL = "overall"
const DATASET_ROWS = 20
const RULE_ROWS = 30

const CHANGE_CLASS: Record<QualityChange["trend"], string> = {
  worse: "text-destructive",
  better: "text-emerald-600 dark:text-emerald-400",
  same: "text-muted-foreground",
}
const CHANGE_WORD: Record<QualityChange["trend"], string> = { worse: "나빠짐", better: "좋아짐", same: "" }

/** 핵심 숫자 하나 — ``change`` 가 있으면 직전 회차 대비 증감을 화살표·숫자·말로 함께 적는다. */
function Tile({ label, value, sub, alert, change }: {
  label: string; value: string; sub?: string; alert?: boolean; change?: QualityChange | null
}) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-xs">{label}</div>
      <div className={`text-2xl font-semibold tabular-nums ${alert ? "text-destructive" : ""}`}>{value}</div>
      {change && (
        <div className={`text-xs tabular-nums ${CHANGE_CLASS[change.trend]}`}>
          직전 대비 {change.text}{CHANGE_WORD[change.trend] && ` (${CHANGE_WORD[change.trend]})`}
        </div>
      )}
      {sub && <div className="text-xs">{sub}</div>}
    </div>
  )
}

/** 안내 한 줄 — 불러오는 중·없음·실패. 실패는 다시 시도 단추를 함께 준다. */
function Notice({ children, onRetry, action }: {
  children: React.ReactNode; onRetry?: () => void; action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded border px-3 py-8 text-center text-sm">
      <p>{children}</p>
      {(onRetry || action) && (
        <div className="flex gap-2">
          {onRetry && <Button variant="outline" size="sm" onClick={onRetry}>다시 시도</Button>}
          {action}
        </div>
      )}
    </div>
  )
}

/** 영역 또는 지표의 합계 표 — 오류율은 숫자와 막대(가장 큰 값 기준)로 함께 보여 준다. */
function GroupTable({ title, groups, withArea, areaLabel = "영역", withDetail }: {
  title: string; groups: QualityReportGroup[]; withArea?: boolean; areaLabel?: string; withDetail?: boolean
}) {
  const max = Math.max(0, ...groups.map((g) => g.error_rate ?? 0))
  return (
    <div className="grid gap-1">
      <h4 className="text-sm font-semibold">{title}</h4>
      <div className="overflow-auto rounded border">
        <table className="w-full text-xs">
          <thead className="bg-muted text-left">
            <tr>
              {withArea && <th className="px-3 py-1.5">{areaLabel}</th>}
              {withDetail && <th className="px-3 py-1.5">세부 지표</th>}
              <th className="px-3 py-1.5">{withArea ? "진단 기준" : areaLabel}</th>
              <th className="px-3 py-1.5 text-right">규칙</th>
              <th className="px-3 py-1.5 text-right">실패한 규칙</th>
              <th className="px-3 py-1.5 text-right">진단 건수</th>
              <th className="px-3 py-1.5 text-right">오류 건수</th>
              <th className="w-56 px-3 py-1.5">오류율</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.key} className="border-t">
                {withArea && <td className="px-3 py-1.5">{g.area_name}</td>}
                {withDetail && <td className="px-3 py-1.5">{g.ai_ready_detail ?? "-"}</td>}
                <td className="px-3 py-1.5 font-medium">{g.name}</td>
                <td className="px-3 py-1.5 text-right">{g.rules}</td>
                <td className={`px-3 py-1.5 text-right ${g.failed_rules ? "text-destructive" : ""}`}>{g.failed_rules}</td>
                <td className="px-3 py-1.5 text-right">{g.checked_count?.toLocaleString() ?? "-"}</td>
                <td className="px-3 py-1.5 text-right">{g.error_count?.toLocaleString() ?? "-"}</td>
                <td className="px-3 py-1.5">
                  <div className="flex items-center gap-2">
                    <span className="w-16 text-right tabular-nums">{fmtErrorRate(g.error_rate)}</span>
                    <div className="h-2 flex-1 rounded bg-muted">
                      <div className="h-full rounded bg-destructive/70" style={{ width: `${rateBarPercent(g.error_rate, max)}%` }} />
                    </div>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** 회차별 오류율 — 한 번에 한 계열(전체 또는 지표 하나)만 그린다. */
function TrendChart({ trend, indicators, currentId }: {
  trend: QualityJobTrend; indicators: QualityIndicator[]; currentId: number | null
}) {
  const [series, setSeries] = useState(OVERALL)
  // 이 작업의 추이에 한 번이라도 나온 지표만 고르게 한다
  const available = useMemo(() => {
    const seen = new Set(trend.points.flatMap((p) => Object.keys(p.indicators)))
    return indicators.filter((i) => seen.has(i.key))
  }, [trend, indicators])
  useEffect(() => {
    if (series !== OVERALL && !available.some((i) => i.key === series)) setSeries(OVERALL)
  }, [available, series])

  const data = trend.points.map((p) => ({
    id: p.job_run_id,
    label: fmtTime(p.started_at),
    rate: series === OVERALL ? p.error_rate : p.indicators[series] ?? null,
    checked: p.checked_count,
    errors: p.error_count,
  }))
  const name = series === OVERALL ? "전체 오류율" : `${available.find((i) => i.key === series)?.name ?? series} 오류율`
  const measured = data.filter((d) => d.rate != null).length

  return (
    <div className="grid gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold">회차별 추이 — {name}</h4>
        <Select value={series} onValueChange={setSeries}>
          <SelectTrigger className="ml-auto h-8 w-44"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={OVERALL}>전체</SelectItem>
            {available.map((i) => <SelectItem key={i.key} value={i.key}>{i.area_name} · {i.name}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
      {measured < 2 ? (
        <p className="rounded border px-3 py-6 text-center text-xs">
          작업 실행이 두 번 이상 쌓이면 추이가 그려집니다.
        </p>
      ) : (
        // 툴팁은 차트 안에서만 그린다 — 오른쪽 끝 점에서 툴팁이 화면 밖으로 나가 가로 스크롤이 생기면 차트 너비가 바뀌어 다시 그려진다
        <div className="overflow-hidden rounded border p-3">
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={data} margin={{ left: 0, right: 12, top: 8, bottom: 0 }}>
              <CartesianGrid {...GRID} />
              <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} minTickGap={24} />
              <YAxis tick={AXIS_TICK} width={52} tickLine={false} axisLine={false} domain={[0, "auto"]}
                tickFormatter={(v: number) => `${v}%`} />
              <Tooltip
                isAnimationActive={false}
                allowEscapeViewBox={{ x: false, y: false }}
                wrapperStyle={{ maxWidth: 260, whiteSpace: "normal", fontSize: 13 }}
                formatter={(val) => [typeof val === "number" ? fmtErrorRate(val) : "-", name]}
                labelFormatter={(label, items) => {
                  const p = items?.[0]?.payload as { id?: number } | undefined
                  return `${label}${p?.id ? ` · 작업 실행 #${p.id}` : ""}`
                }}
              />
              <Line type="monotone" dataKey="rate" name={name} stroke={SERIES[0]} strokeWidth={2} connectNulls={false}
                isAnimationActive={false}
                dot={(props: { cx?: number; cy?: number; payload?: { id: number } }) => {
                  if (props.cx == null || props.cy == null) return <g key={props.payload?.id} />
                  const current = props.payload?.id === currentId
                  // 지금 보고 있는 회차는 크게 — 표의 숫자와 차트의 점을 잇는다
                  return (
                    <circle key={props.payload?.id} cx={props.cx} cy={props.cy} r={current ? 6 : 4}
                      fill={SERIES[0]} stroke="var(--background)" strokeWidth={2} />
                  )
                }}
              />
            </LineChart>
          </ResponsiveContainer>
          <p className="mt-1 text-xs">
            최근 {trend.points.length}회 · 큰 점이 지금 보고 있는 작업 실행입니다. 건너뛴 차례와 실행 중인 것은 뺍니다.
          </p>
        </div>
      )}
    </div>
  )
}

/** 데이터 품질 > 리포트 탭 — 작업 실행 한 번의 핵심 숫자(직전 회차 대비), 영역·진단 기준별, 규칙별 결과, 회차별 추이, 내려받기. */
export function QualityReportTab() {
  const params = useSearchParams()
  const router = useRouter()
  const num = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : null)
  const jobId = num(params.get("job"))
  const runParam = num(params.get("run"))

  // null 은 불러오는 중 — "없음" 과 구분한다
  const [jobs, setJobs] = useState<QualityJob[] | null>(null)
  const [jobsError, setJobsError] = useState<string | null>(null)
  const [indicators, setIndicators] = useState<QualityIndicator[]>([])
  const [jobRuns, setJobRuns] = useState<QualityJobRun[] | null>(null)
  const [runsError, setRunsError] = useState<string | null>(null)
  const [report, setReport] = useState<QualityJobRunReport | null>(null)
  const [reportError, setReportError] = useState<string | null>(null)
  const [trend, setTrend] = useState<QualityJobTrend | null>(null)
  const [retry, setRetry] = useState(0)
  const [downloading, setDownloading] = useState<"xlsx" | "pdf" | null>(null)
  // 보고서 내려받기(PDF · 엑셀)는 역할의 기능 권한 quality.export(서버와 같은 기준, TODO-122)
  const canExport = usePermissions().canUseFeature("quality.export")
  const [showAll, setShowAll] = useState(false)
  const [showAllRules, setShowAllRules] = useState(false)
  const [detailId, setDetailId] = useState<number | null>(null)
  // 같은 결과를 관리지침 진단 영역으로 볼지, AI-Ready 품질 지표(가이드라인 §2.3)로 볼지
  const [grouping, setGrouping] = useState<"guide" | "ai">("guide")

  useEffect(() => {
    let stale = false
    setJobsError(null)
    fetchQualityJobs()
      .then((rows) => !stale && setJobs(rows))
      .catch((e) => !stale && setJobsError(errorMessage(e) ?? String(e)))
    loadQualityIndicators().then((rows) => !stale && setIndicators(rows)).catch(() => undefined)
    return () => {
      stale = true
    }
  }, [retry])

  // 작업을 고르지 않았으면 마지막으로 실행한 작업(없으면 첫 작업)을 연다
  useEffect(() => {
    if (jobId != null || !jobs || jobs.length === 0) return
    const latest = [...jobs].sort((a, b) => (b.last_run?.id ?? 0) - (a.last_run?.id ?? 0))[0]
    if (latest) router.replace(qualityReportHref(latest.id), { scroll: false })
  }, [jobId, jobs, router])

  // 작업의 작업 실행 목록과 추이
  useEffect(() => {
    setJobRuns(null)
    setRunsError(null)
    setTrend(null)
    if (jobId == null) return
    let stale = false
    fetchQualityJobRuns({ jobId, limit: 100 })
      .then((rows) => !stale && setJobRuns(rows.filter((r) => r.status !== "SKIPPED")))
      .catch((e) => !stale && setRunsError(errorMessage(e) ?? String(e)))
    fetchQualityJobTrend(jobId).then((t) => !stale && setTrend(t)).catch(() => !stale && setTrend(null))
    return () => {
      stale = true
    }
  }, [jobId, retry])

  // 볼 작업 실행 — 주소에 있으면 그것(목록의 최근 100회 밖이어도), 없으면 가장 최근에 끝난 것
  const runId = useMemo(() => {
    if (runParam != null) return runParam
    if (!jobRuns) return null
    return (jobRuns.find((r) => r.status !== "RUNNING") ?? jobRuns[0])?.id ?? null
  }, [runParam, jobRuns])

  useEffect(() => {
    setReport(null)
    setReportError(null)
    setShowAll(false)
    setShowAllRules(false)
    if (runId == null) return
    let stale = false
    fetchQualityJobRunReport(runId)
      .then((r) => !stale && setReport(r))
      .catch((e) => !stale && setReportError(errorMessage(e) ?? String(e)))
    return () => {
      stale = true
    }
  }, [runId, retry])

  const download = async (format: "xlsx" | "pdf") => {
    if (runId == null) return
    setDownloading(format)
    try {
      const blob = await (format === "pdf" ? fetchQualityJobRunReportPdf(runId) : fetchQualityJobRunReportXlsx(runId))
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `quality-report-${runId}.${format}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      notifyError(e, "보고서를 내려받지 못했습니다.")
    } finally {
      setDownloading(null)
    }
  }

  const jr = report?.job_run
  const prev = report?.previous ?? null
  const datasets = report ? (showAll ? report.datasets : report.datasets.slice(0, DATASET_ROWS)) : []
  const sortedRules = useMemo(() => failedFirst(report?.rules ?? []), [report])
  const rules = showAllRules ? sortedRules : sortedRules.slice(0, RULE_ROWS)
  // 데이터셋 하나만 진단한 실행 — 데이터셋 표 대신 머리글에 그 데이터셋을 적는다
  const only = report && jr && report.datasets.length === 1 && jr.targets_total <= 1 && jr.skipped_total === 0
    ? report.datasets[0] ?? null : null
  const jobMissing = jobs != null && jobId != null && !jobs.some((j) => j.id === jobId)
  // 주소로 온 실행이 선택 목록(최근 100회)에 없으면 목록에 덧붙여 고른 것이 보이게 한다
  const listed = jobRuns ?? []
  const extraRun = jr && runId === jr.id && !listed.some((r) => r.id === jr.id) ? jr : null
  const again = () => setRetry((n) => n + 1)
  const ready = report != null && jr != null && !jobMissing

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={jobId != null && !jobMissing ? String(jobId) : ""}
          onValueChange={(v) => router.replace(qualityReportHref(Number(v)), { scroll: false })}>
          <SelectTrigger className="h-8 w-64"><SelectValue placeholder="품질 진단 작업 선택" /></SelectTrigger>
          <SelectContent>
            {(jobs ?? []).map((j) => <SelectItem key={j.id} value={String(j.id)}>{j.name}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={runId != null && ready ? String(runId) : ""} disabled={listed.length === 0 && !extraRun}
          onValueChange={(v) => router.replace(qualityReportHref(jobId, Number(v)), { scroll: false })}>
          <SelectTrigger className="h-8 w-72"><SelectValue placeholder="작업 실행 선택" /></SelectTrigger>
          <SelectContent>
            {[...listed, ...(extraRun ? [extraRun] : [])].map((r) => (
              <SelectItem key={r.id} value={String(r.id)}>
                {fmtTime(r.started_at)} · {QUALITY_JOB_RUN_STATUS_LABEL[r.status]} · #{r.id}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {canExport && <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={!ready || downloading != null}
            onClick={() => void download("pdf")}>
            <FileText className="mr-1 size-4" /> {downloading === "pdf" ? "내려받는 중…" : "PDF 내려받기"}
          </Button>
          <Button variant="outline" size="sm" disabled={!ready || downloading != null}
            onClick={() => void download("xlsx")}>
            <FileSpreadsheet className="mr-1 size-4" /> {downloading === "xlsx" ? "내려받는 중…" : "엑셀 내려받기"}
          </Button>
        </div>}
      </div>

      {jobsError ? (
        <Notice onRetry={again}>품질 진단 작업 목록을 불러오지 못했습니다. {jobsError}</Notice>
      ) : jobs == null ? (
        <Notice>불러오는 중…</Notice>
      ) : jobs.length === 0 ? (
        <Notice>품질 진단 작업이 없습니다. 품질 진단 작업 탭에서 작업을 등록하고 실행하면 리포트가 만들어집니다.</Notice>
      ) : jobMissing ? (
        <Notice>품질 진단 작업 #{jobId} 을(를) 찾을 수 없습니다. 지워졌을 수 있습니다 — 위에서 다른 작업을 고르세요.</Notice>
      ) : reportError ? (
        <Notice onRetry={again} action={runParam != null && (
          <Button variant="outline" size="sm" onClick={() => router.replace(qualityReportHref(jobId), { scroll: false })}>
            가장 최근 실행 보기
          </Button>
        )}>
          작업 실행 #{runId} 의 리포트를 불러오지 못했습니다. {reportError}
        </Notice>
      ) : runsError && runId == null ? (
        <Notice onRetry={again}>작업 실행 목록을 불러오지 못했습니다. {runsError}</Notice>
      ) : jobId == null || (jobRuns == null && runId == null) ? (
        <Notice>불러오는 중…</Notice>
      ) : runId == null ? (
        <Notice>이 품질 진단 작업은 아직 실행한 적이 없습니다.</Notice>
      ) : !report || !jr ? (
        <Notice>불러오는 중…</Notice>
      ) : (
        <>
          <div className="grid gap-1">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-semibold">{jr.job_name}</span>
              <StatusBadge tone={QUALITY_JOB_RUN_STATUS_TONE[jr.status]}>{QUALITY_JOB_RUN_STATUS_LABEL[jr.status]}</StatusBadge>
              <span className="text-xs">
                {fmtTime(jr.started_at)} 시작 · {fmtDuration(jobRunElapsedSeconds(jr))} ·{" "}
                {jr.trigger_type === "SCHEDULE" ? "예약" : `수동(${jr.requested_by ?? "-"})`} · 품질 지표{" "}
                {describeMeasured(jr, indicators)}
              </span>
            </div>
            {only && (
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                <span>데이터셋</span>
                <span className="text-sm font-medium text-foreground">{only.dataset_name ?? `데이터셋 #${only.dataset_id}`}</span>
                {only.datasource_name && <span>· 원본 {only.datasource_name}</span>}
                {only.engine_table && <span>· 진단 테이블 <code className="font-mono">{only.engine_table}</code></span>}
                {only.status !== "SUCCEEDED" && (
                  <StatusBadge tone={QUALITY_STATUS_TONE[only.status]}>{QUALITY_STATUS_LABEL[only.status]}</StatusBadge>
                )}
                <Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={() => setDetailId(only.run_id)}>
                  실행 상세(쿼리 보기)
                </Button>
              </div>
            )}
            {only?.error_message && <p className="text-xs text-destructive">{only.error_message}</p>}
            {jr.message && <p className="text-xs">{jr.message}</p>}
            {runsError && <p className="text-xs text-destructive">작업 실행 목록을 불러오지 못했습니다. {runsError}</p>}
          </div>

          <div className={`grid grid-cols-2 gap-3 ${only ? "md:grid-cols-4" : "md:grid-cols-5"}`}>
            <Tile label="품질 점수" value={jr.score != null ? jr.score.toFixed(Number.isInteger(jr.score) ? 0 : 1) : "-"}
              sub="평가한 규칙 중 통과 비율" change={describeChange(jr.score, prev?.score, { unit: "점", lowerIsBetter: false, digits: 1 })} />
            <Tile label="오류율" value={jr.checked_count != null ? fmtErrorRate(jr.error_rate) : "-"}
              sub="오류 건수 ÷ 진단 건수" alert={!!jr.error_rate}
              change={describeChange(jr.error_rate, prev?.error_rate, { unit: "%p", digits: 2 })} />
            <Tile label="오류 건수" value={jr.checked_count != null ? (jr.error_count ?? 0).toLocaleString() : "-"}
              sub={`진단 ${jr.checked_count?.toLocaleString() ?? "-"}건`} alert={!!jr.error_count}
              change={describeChange(jr.checked_count != null ? jr.error_count ?? 0 : null,
                prev?.checked_count != null ? prev.error_count ?? 0 : null, { unit: "건" })} />
            <Tile label="실패한 규칙" value={`${report.failed_rules}/${report.rules_total}`}
              sub="이 실행에서 평가한 규칙" alert={report.failed_rules > 0}
              change={prev ? describeChange(report.failed_rules, prev.failed_rules, { unit: "개" }) : null} />
            {!only && (
              <Tile label="진단한 데이터셋" value={`${jr.runs_succeeded}/${jr.runs_total}`}
                sub={[
                  jr.runs_failed ? `실패 ${jr.runs_failed}` : "",
                  jr.skipped_total ? `시작 못 함 ${jr.skipped_total}` : "",
                  `대상 ${jr.targets_total.toLocaleString()}`,
                ].filter(Boolean).join(" · ")}
                alert={jr.runs_failed + jr.skipped_total > 0} />
            )}
          </div>
          <p className="-mt-2 text-xs">
            {prev ? (
              <>
                증감은 직전 회차(
                <Link className="underline underline-offset-2" href={qualityReportHref(jr.job_id, prev.job_run_id)} scroll={false}>
                  {fmtTime(prev.started_at)} · #{prev.job_run_id}
                </Link>
                )와 견준 값입니다.
              </>
            ) : "이 작업의 첫 회차라 직전 회차와 견준 증감이 없습니다."}
            {jr.status === "RUNNING" && " 아직 실행 중이라 지금까지의 집계입니다."}
          </p>

          {report.areas.length > 0 ? (
            <>
              <div className="flex flex-wrap items-center gap-1.5">
                <Button size="sm" variant={grouping === "guide" ? "default" : "outline"} onClick={() => setGrouping("guide")}>
                  관리지침 진단 영역
                </Button>
                <Button size="sm" variant={grouping === "ai" ? "default" : "outline"} onClick={() => setGrouping("ai")}>
                  AI-Ready 품질 지표
                </Button>
                {grouping === "ai" && (
                  <span className="text-xs">
                    같은 결과를 「공공데이터의 인공지능 친화적 관리 가이드라인」의 6개 지표로 묶었습니다(건수 기준은 빠짐).
                  </span>
                )}
              </div>
              {grouping === "guide" ? (
                <>
                  <GroupTable title="영역별" groups={report.areas} />
                  <GroupTable title="진단 기준별" groups={report.indicators} withArea />
                </>
              ) : (report.ai_ready_areas?.length ?? 0) > 0 ? (
                <>
                  <GroupTable title="AI-Ready 지표별" groups={report.ai_ready_areas ?? []} areaLabel="AI-Ready 지표" />
                  <GroupTable title="진단 기준별" groups={report.ai_ready_indicators ?? []} withArea withDetail
                    areaLabel="AI-Ready 지표" />
                </>
              ) : (
                <p className="rounded border px-3 py-6 text-center text-xs text-muted-foreground">
                  AI-Ready 품질 지표에 드는 진단 결과가 없습니다(건수 기준만 진단함).
                </p>
              )}
            </>
          ) : (
            <p className="rounded border px-3 py-6 text-center text-xs text-muted-foreground">
              이 작업 실행이 남긴 규칙 결과가 없습니다.
            </p>
          )}

          {sortedRules.length > 0 && (
            <div className="grid gap-1">
              <h4 className="text-sm font-semibold">
                규칙별 결과 — 실패한 규칙 먼저 ({report.failed_rules}/{report.rules_total})
              </h4>
              <div className="overflow-auto rounded border">
                <table className="w-full text-xs">
                  <thead className="bg-muted text-left">
                    <tr>
                      {!only && <th className="px-3 py-1.5">데이터셋</th>}
                      <th className="px-3 py-1.5">컬럼</th>
                      <th className="px-3 py-1.5">규칙</th>
                      <th className="px-3 py-1.5">진단 기준</th>
                      <th className="px-3 py-1.5">품질 지표</th>
                      <th className="px-3 py-1.5">결과</th>
                      <th className="px-3 py-1.5 text-right">진단 건수</th>
                      <th className="px-3 py-1.5 text-right">오류 건수</th>
                      <th className="px-3 py-1.5 text-right">오류율</th>
                      <th className="px-3 py-1.5">상세</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rules.map((r) => (
                      <tr key={`${r.run_id}:${r.rule_id}`} className="border-t align-top">
                        {!only && <td className="px-3 py-1.5">{r.dataset_name ?? `데이터셋 #${r.dataset_id}`}</td>}
                        <td className="px-3 py-1.5 font-mono">{r.column_name ?? "-"}</td>
                        <td className="px-3 py-1.5">
                          <span className="font-medium">{r.rule_name ?? `규칙 #${r.rule_id}`}</span>
                          {r.check_type && <span className="ml-1">{r.check_type}</span>}
                        </td>
                        <td className="px-3 py-1.5">{r.indicator_name ?? "-"}</td>
                        <td className="px-3 py-1.5">{r.definition_name ?? <span >직접 만든 규칙</span>}</td>
                        <td className="px-3 py-1.5">
                          <StatusBadge {...checkResult(!!r.passed)} />
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{r.checked_count?.toLocaleString() ?? "-"}</td>
                        <td className={`px-3 py-1.5 text-right tabular-nums ${r.error_count ? "text-destructive" : ""}`}>
                          {r.checked_count != null ? (r.error_count ?? 0).toLocaleString() : "-"}
                        </td>
                        <td className={`px-3 py-1.5 text-right tabular-nums ${r.error_rate ? "text-destructive" : ""}`}>
                          {r.checked_count != null ? fmtErrorRate(r.error_rate) : "-"}
                        </td>
                        <td className="max-w-80 px-3 py-1.5">{r.detail}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {sortedRules.length > RULE_ROWS && !showAllRules && (
                <Button variant="outline" size="sm" className="justify-self-center" onClick={() => setShowAllRules(true)}>
                  나머지 {(sortedRules.length - RULE_ROWS).toLocaleString()}개 보기
                </Button>
              )}
            </div>
          )}

          {report.rules_missing > 0 && (
            <p className="text-xs">
              이 회차가 진단한 규칙 가운데 {report.rules_missing.toLocaleString()}개는 그 뒤에 지워져 결과가 남아 있지 않습니다 —
              위 요약(진단 건수 · 오류율)에는 들어 있고, 영역 · 지표 · 규칙별 표에는 빠져 있습니다.
            </p>
          )}

          {trend && <TrendChart trend={trend} indicators={indicators} currentId={jr.id} />}

          {!only && (
            <div className="grid gap-1">
              <h4 className="text-sm font-semibold">데이터셋별 — 오류율 높은 순 ({report.datasets.length})</h4>
              <div className="overflow-auto rounded border">
                <table className="w-full text-xs">
                  <thead className="bg-muted text-left">
                    <tr>
                      <th className="px-3 py-1.5">데이터셋</th>
                      <th className="px-3 py-1.5">상태</th>
                      <th className="px-3 py-1.5 text-right">규칙</th>
                      <th className="px-3 py-1.5 text-right">실패한 규칙</th>
                      <th className="px-3 py-1.5 text-right">진단 건수</th>
                      <th className="px-3 py-1.5 text-right">오류 건수</th>
                      <th className="px-3 py-1.5 text-right">오류율</th>
                    </tr>
                  </thead>
                  <tbody>
                    {datasets.length === 0 ? (
                      <tr><td colSpan={7} className="px-3 py-6 text-center text-muted-foreground">진단한 데이터셋이 없습니다.</td></tr>
                    ) : datasets.map((d) => (
                      <tr key={d.run_id} className="cursor-pointer border-t hover:bg-muted/40" title="실행 상세 보기"
                        onClick={() => setDetailId(d.run_id)}>
                        <td className="px-3 py-1.5">
                          <span className="font-medium">{d.dataset_name ?? `데이터셋 #${d.dataset_id}`}</span>
                          <span className="ml-1">{d.datasource_name}</span>
                        </td>
                        <td className="px-3 py-1.5">
                          <StatusBadge tone={QUALITY_STATUS_TONE[d.status]}>{QUALITY_STATUS_LABEL[d.status]}</StatusBadge>
                        </td>
                        <td className="px-3 py-1.5 text-right">{d.rules}</td>
                        <td className={`px-3 py-1.5 text-right ${d.failed_rules ? "text-destructive" : ""}`}>{d.failed_rules}</td>
                        <td className="px-3 py-1.5 text-right">{d.checked_count?.toLocaleString() ?? "-"}</td>
                        <td className="px-3 py-1.5 text-right">{d.checked_count != null ? (d.error_count ?? 0).toLocaleString() : "-"}</td>
                        <td className={`px-3 py-1.5 text-right ${d.error_rate ? "text-destructive" : ""}`}>
                          {d.checked_count != null ? fmtErrorRate(d.error_rate) : "-"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {report.datasets.length > DATASET_ROWS && !showAll && (
                <Button variant="outline" size="sm" className="self-center justify-self-center" onClick={() => setShowAll(true)}>
                  나머지 {report.datasets.length - DATASET_ROWS}개 보기
                </Button>
              )}
              {(jr.skipped_total > 0 || jr.no_rules > 0) && (
                <p className="text-xs">
                  {jr.skipped_total > 0 &&
                    `진단을 시작하지 못한 데이터셋 ${jr.skipped_total}개(사유는 작업 이력과 내려받은 리포트 — PDF·엑셀 — 에 있습니다). `}
                  {jr.no_rules > 0 && `고른 품질 지표의 규칙이 없어 진단하지 않은 데이터셋 ${jr.no_rules.toLocaleString()}개.`}
                </p>
              )}
            </div>
          )}
        </>
      )}

      <QualityRunDetailDialog runId={detailId} onOpenChange={(o) => !o && setDetailId(null)} />
    </div>
  )
}
