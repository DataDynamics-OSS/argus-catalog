"use client"

import { InfoTip, Tip } from "@/components/tip"
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react"
import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { RefreshCw, RotateCcw, Square } from "lucide-react"

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
import { Switch } from "@workspace/ui/components/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { DashboardHeader } from "@/components/dashboard-header"
import { datasetHref } from "@/features/datasets/links"
import { fetchDatasources, type DatasourceRow } from "@/features/datasources/api"
import { parseSyncRunsParams, SYNC_RUNS_ALL as ALL, syncRunsQuery } from "@/features/datasources/links"
import { fmtDuration, fmtTime } from "@/features/datasources/sync-format"
import { usePermissions } from "@/features/permissions/use-permissions"
import {
  ACTIVE_QUALITY_STATUSES,
  cancelQualityRun,
  fetchQualityRuns,
  fetchQualityServiceStatus,
  retryQualityRun,
  type QualityRun,
  type QualityServiceStatus,
} from "@/features/quality/api"
import { QualityDefinitionsTab } from "@/features/quality/components/quality-definitions-tab"
import { QualityJobRunsTab } from "@/features/quality/components/quality-job-runs-tab"
import { QualityJobsTab } from "@/features/quality/components/quality-jobs-tab"
import { QualityReportTab } from "@/features/quality/components/quality-report-tab"
import { EmptyRow, StaleNote } from "@/features/quality/components/quality-list-state"
import { QualityRunDetailDialog, ScoreBar } from "@/features/quality/components/quality-run-detail"
import {
  fmtErrorRate,
  isProfileRun,
  QUALITY_STATUS_LABEL,
  QUALITY_STATUS_TONE,
  QUALITY_TRIGGER_LABEL,
  qualityElapsedSeconds,
  qualityPercent,
} from "@/features/quality/format"
import { useLatest, usePolling } from "@/features/quality/use-polling"
import { errorMessage, toast, notifyError } from "@/lib/notify"

const POLL_MS = 5000
const PAGE = 50
const MAX_ROWS = 200

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: "ACTIVE", label: "실행 중·대기" },
  { value: "PROBLEM", label: "실패·일부 실패" },
  { value: "SUCCEEDED", label: "완료" },
  { value: "CANCELLED", label: "중지됨" },
  { value: ALL, label: "전체 상태" },
]
const PERIODS: { value: string; label: string; hours: number | null }[] = [
  { value: "24h", label: "최근 24시간", hours: 24 },
  { value: "7d", label: "최근 7일", hours: 24 * 7 },
  { value: "30d", label: "최근 30일", hours: 24 * 30 },
  { value: "all", label: "전체 기간", hours: null },
]

function ServiceStatusBar({ status, failed }: { status: QualityServiceStatus | null; failed: boolean }) {
  if (failed) return <span className="text-xs text-destructive">품질 워커 상태를 확인하지 못했습니다.</span>
  if (!status) return <span className="text-xs text-muted-foreground">품질 워커 상태 확인 중…</span>
  if (status.workers_alive === 0) {
    return (
      <span className="flex items-center gap-1 text-xs">
        <span className="size-2 rounded-full bg-destructive" /> 실행 중인 품질 워커 없음
        <span >
          — 대기 {status.queued}건이 실행되지 않습니다. 품질 워커(argus-catalog-quality-worker)를 시작하세요.
        </span>
      </span>
    )
  }
  const names = status.workers.map((w) => `${w.worker_id}${w.version ? ` v${w.version}` : ""}`).join("\n")
  return (
    <span className="flex items-center gap-1 text-xs" title={names}>
      <span className="size-2 rounded-full bg-emerald-500" /> 품질 워커 {status.workers_alive}대 정상
      <span >· 실행 {status.running} · 대기 {status.queued}</span>
    </span>
  )
}

function ProgressCell({ run, queuePos }: { run: QualityRun; queuePos: number | null }) {
  if (run.status === "QUEUED") {
    return <span >대기{queuePos ? ` (앞에 ${queuePos}개)` : ""}</span>
  }
  if (run.status === "RUNNING") {
    if (run.unresponsive) return <span className="text-destructive">워커 응답 없음 ⚠</span>
    const pct = qualityPercent(run)
    if (pct === null) return <span >{run.current_target ?? "진행 중"}</span>
    return (
      <div className="grid gap-0.5">
        <div className="h-2 w-40 overflow-hidden rounded bg-muted">
          <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
        </div>
        <span className="max-w-64 truncate text-[11px]" title={run.current_target ?? undefined}>
          쿼리 {run.progress_done}/{run.progress_total}{run.current_target ? ` · ${run.current_target}` : ""}
        </span>
      </div>
    )
  }
  return <StatusBadge tone={QUALITY_STATUS_TONE[run.status]}>{QUALITY_STATUS_LABEL[run.status]}</StatusBadge>
}

function QualityRunsContent() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const { canUseFeature } = usePermissions()
  // 관리 › 데이터 품질 메뉴의 실행 · 취소 · 다시 실행은 품질 관리 권한(quality.manage)으로 — 서버와 같은 기준
  const canRun = canUseFeature("quality.manage")
  // 기본 조건은 "전체 상태 + 최근 24시간". ?datasource=<데이터 소스 id> 로 들어오면 그 데이터 소스의 진단만
  const [initial] = useState(() => parseSyncRunsParams(params))
  const [status, setStatus] = useState(initial.status)
  const [datasourceId, setDatasourceId] = useState(initial.datasource)
  const [period, setPeriod] = useState(initial.period)

  // 조건을 주소에 남긴다 — 데이터셋에 갔다 돌아와도 같은 조건(기록은 쌓지 않음)
  const queryKey = params.toString()
  useEffect(() => {
    const next = syncRunsQuery({ status, datasource: datasourceId, period })
    if (next !== queryKey) router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false })
    // queryKey 는 비교용 — 주소가 바뀌었다고 다시 쓰지 않는다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, datasourceId, period, pathname, router])

  // 같은 화면 안에서 주소가 바뀌면(상세의 "전체 이력" 링크 등) 조건을 주소에 맞춘다
  useEffect(() => {
    const s = parseSyncRunsParams(new URLSearchParams(queryKey))
    setStatus(s.status)
    setDatasourceId(s.datasource)
    setPeriod(s.period)
  }, [queryKey])

  const [auto, setAuto] = useState(true)
  const [runs, setRuns] = useState<QualityRun[]>([])
  const [more, setMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [service, setService] = useState<QualityServiceStatus | null>(null)
  const [serviceFailed, setServiceFailed] = useState(false)
  const [datasources, setDatasources] = useState<DatasourceRow[]>([])
  const [detailId, setDetailId] = useState<number | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [, setTick] = useState(0)

  const filters = useMemo(() => ({
    status: status === ALL ? undefined : status,
    datasourceId: datasourceId === ALL ? undefined : Number(datasourceId),
    hours: PERIODS.find((p) => p.value === period)?.hours ?? null,
  }), [status, datasourceId, period])

  // 보이는 줄 수 — 새로고침은 "더 보기" 로 불러온 만큼 다시 읽는다
  const shown = useRef(PAGE)
  const begin = useLatest()
  const load = useCallback(async (opts: { quiet?: boolean; more?: boolean } = {}) => {
    const current = begin()
    if (!opts.quiet) setLoading(true)
    const want = Math.min(MAX_ROWS, opts.more ? shown.current + PAGE : shown.current)
    try {
      const { hours, ...rest } = filters
      const since = hours ? new Date(Date.now() - hours * 3600_000).toISOString() : undefined
      const rows = await fetchQualityRuns({ ...rest, since, limit: want })
      if (!current()) return // 그 사이 조건이 바뀌었다 — 늦게 온 응답은 버린다
      shown.current = Math.max(PAGE, want)
      setRuns(rows)
      setMore(rows.length === want && want < MAX_ROWS)
      setError(null)
    } catch (e) {
      if (!current()) return
      const message = errorMessage(e) ?? String(e)
      setError(message)
      if (!opts.quiet) notifyError(e, "품질 진단 실행 이력을 불러오지 못했습니다.")
    } finally {
      if (current()) setLoading(false)
    }
    try {
      const next = await fetchQualityServiceStatus()
      if (!current()) return
      setService(next)
      setServiceFailed(false)
    } catch {
      if (current()) setServiceFailed(true)
    }
  }, [filters, begin])

  // 조건이 바뀌면 처음 쪽부터 — 앞 조건의 목록을 남겨 두지 않는다
  useEffect(() => {
    shown.current = PAGE
    setRuns([])
    setError(null)
    void load()
  }, [load])

  useEffect(() => {
    fetchDatasources().then(setDatasources).catch(() => setDatasources([]))
  }, [])

  // 실행 중·대기가 있고 자동 새로고침이 켜져 있을 때만 다시 읽는다. 한 번 실패해도 다음 차례에 다시 읽는다
  const hasActive = runs.some((r) => ACTIVE_QUALITY_STATUSES.has(r.status))
  usePolling(auto && hasActive, () => load({ quiet: true }), POLL_MS)

  // 경과 시간 표시용 1초 틱(실행 중일 때만)
  useEffect(() => {
    if (!hasActive) return
    const t = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [hasActive])

  // 대기 순번 — 앞선 대기 수(목록에 보이는 것 기준)
  const queuePos = useMemo(() => {
    const queued = runs.filter((r) => r.status === "QUEUED").sort((a, b) => a.id - b.id)
    return new Map(queued.map((r, i) => [r.id, i]))
  }, [runs])

  const act = async (run: QualityRun, fn: () => Promise<QualityRun>, ok: string, fail: string) => {
    setBusy(run.id)
    try {
      await fn()
      toast.success(ok)
    } catch (e) {
      notifyError(e, fail)
    } finally {
      setBusy(null)
      await load()
    }
  }

  return (
    <div className="flex flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <ServiceStatusBar status={service} failed={serviceFailed} />
        <span className="ml-auto flex items-center gap-2 text-xs">
          <Switch checked={auto} onCheckedChange={setAuto} id="auto-refresh" />
          <label htmlFor="auto-refresh">자동 새로고침</label>
          <Tip label="새로고침"><Button aria-label="새로고침" variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
          </Button></Tip>
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="h-8 w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            {STATUS_FILTERS.map((f) => <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={datasourceId} onValueChange={setDatasourceId}>
          <SelectTrigger className="h-8 w-56"><SelectValue placeholder="데이터 소스" /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>전체 데이터 소스</SelectItem>
            {datasources.map((d) => <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={period} onValueChange={setPeriod}>
          <SelectTrigger className="h-8 w-36"><SelectValue /></SelectTrigger>
          <SelectContent>
            {PERIODS.map((p) => <SelectItem key={p.value} value={p.value}>{p.label}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      <StaleNote error={error} show={runs.length > 0} />
      <div className="overflow-auto rounded border">
        <table className="w-full text-sm">
          <thead className="bg-muted text-left text-xs">
            <tr>
              <th className="px-3 py-2">데이터셋</th>
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
            {runs.length === 0 ? (
              <EmptyRow colSpan={9} loading={loading} error={error} onRetry={() => void load()}>
                조건에 맞는 진단 실행이 없습니다. 데이터셋의 품질 탭에서 &quot;정밀 진단&quot; 을 실행하면 여기에 나타납니다.
              </EmptyRow>
            ) : runs.map((r) => {
              const active = ACTIVE_QUALITY_STATUSES.has(r.status)
              return (
                <tr key={r.id} className="cursor-pointer border-t hover:bg-muted/40" onClick={() => setDetailId(r.id)}>
                  <td className="px-3 py-2">
                    {/* 이름은 데이터셋 품질 탭으로 — 행 클릭(상세)과 겹치지 않게 전파를 막는다 */}
                    <Link href={datasetHref(r.dataset_id, "quality")} className="font-medium hover:underline"
                      onClick={(e) => e.stopPropagation()} title="데이터셋 품질 탭에서 열기">
                      {r.dataset_name ?? `데이터셋 #${r.dataset_id}`}
                    </Link>
                    <div className="text-xs">
                      #{r.id}{isProfileRun(r) && <> · <Badge size="sm" variant="outline">프로파일</Badge></>}
                      {" "}· {r.datasource_name ?? "-"} · <span className="font-mono">{r.engine_table ?? "-"}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    <div>{r.trigger_type === "MANUAL" ? (r.requested_by ?? "수동") : QUALITY_TRIGGER_LABEL[r.trigger_type] ?? r.trigger_type}</div>
                    {r.job_name && <div  title="이 실행을 넣은 품질 진단 작업">작업: {r.job_name}</div>}
                    <div >{fmtTime(r.started_at ?? r.queued_at)}</div>
                  </td>
                  <td className="px-3 py-2 text-xs"><ProgressCell run={r} queuePos={queuePos.get(r.id) ?? null} /></td>
                  <td className="px-3 py-2 text-xs">{r.started_at ? fmtDuration(qualityElapsedSeconds(r)) : ""}</td>
                  <td className="px-3 py-2 text-xs">
                    {isProfileRun(r) ? "" : active ? `-/${r.rules_total}` : `${r.rules_evaluated}/${r.rules_total}`}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {r.checked_count != null ? `${r.checked_count.toLocaleString()} / ${(r.error_count ?? 0).toLocaleString()}` : ""}
                  </td>
                  <td className={`px-3 py-2 text-xs ${r.error_rate ? "text-destructive" : ""}`}>
                    {r.checked_count != null ? fmtErrorRate(r.error_rate) : ""}
                  </td>
                  <td className="px-3 py-2 text-xs">{r.score != null && <ScoreBar score={r.score} compact />}</td>
                  <td className="px-3 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                    {canRun && active && !r.cancel_requested_by && (
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={busy === r.id}
                        onClick={() => act(r, () => cancelQualityRun(r.id), "중지를 요청했습니다.", "품질 진단을 중지하지 못했습니다.")}>
                        <Square className="mr-1 size-3" /> 중지
                      </Button>
                    )}
                    {active && r.cancel_requested_by && <span className="text-xs">중지 요청됨</span>}
                    {canRun && !active && (
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={busy === r.id}
                        onClick={() => act(r, () => retryQualityRun(r.id), "다시 실행을 요청했습니다.", "품질 진단을 다시 실행하지 못했습니다.")}>
                        <RotateCcw className="mr-1 size-3" /> 다시 실행
                      </Button>
                    )}
                  </td>
                </tr>
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

const TABS = ["runs", "definitions", "jobs", "history", "report"] as const

// 탭을 주소(?tab=jobs)에 남긴다. 실행 현황 탭은 자기 조건(상태·데이터 소스·기간)을 주소에 쓰므로 tab 값을 두지 않는다.
function QualityTabs() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const tab = TABS.find((t) => t === params.get("tab")) ?? "runs"

  return (
    <Tabs
      value={tab}
      className="flex flex-1 flex-col p-4"
      onValueChange={(v) => router.replace(v === "runs" ? pathname : `${pathname}?tab=${v}`, { scroll: false })}
    >
      <TabsList variant="line">
        <TabsTrigger value="runs" className="text-base">실행 현황</TabsTrigger>
        <TabsTrigger value="definitions" className="text-base">품질 지표</TabsTrigger>
        <TabsTrigger value="jobs" className="text-base">품질 진단 작업</TabsTrigger>
        <TabsTrigger value="history" className="text-base">작업 이력</TabsTrigger>
        <TabsTrigger value="report" className="text-base">리포트</TabsTrigger>
      </TabsList>
      <TabsContent value="runs" className="mt-4">
        {tab === "runs" && <QualityRunsContent />}
      </TabsContent>
      <TabsContent value="definitions" className="mt-4">
        {tab === "definitions" && <QualityDefinitionsTab />}
      </TabsContent>
      <TabsContent value="jobs" className="mt-4">
        {tab === "jobs" && <QualityJobsTab />}
      </TabsContent>
      <TabsContent value="history" className="mt-4">
        {tab === "history" && <QualityJobRunsTab />}
      </TabsContent>
      <TabsContent value="report" className="mt-4">
        {tab === "report" && <QualityReportTab />}
      </TabsContent>
    </Tabs>
  )
}

// 관리 > 데이터 품질 — 실행 현황(모든 데이터셋의 정밀 진단 실행), 품질 지표(미리 정해 컬럼에 할당하는 기준), 품질 진단 작업(주기 진단), 작업 이력, 리포트를 탭으로 본다.
export default function QualityRunsPage() {
  return (
    <>
      <DashboardHeader title="데이터 품질" />
      <Suspense>
        <QualityTabs />
      </Suspense>
    </>
  )
}
