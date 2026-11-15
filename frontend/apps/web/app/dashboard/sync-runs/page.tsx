"use client"

import { Tip } from "@/components/tip"
import { useCallback, useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { Suspense } from "react"
import { RefreshCw, RotateCcw, Square } from "lucide-react"

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
import { DashboardHeader } from "@/components/dashboard-header"
import {
  ACTIVE_SYNC_STATUSES,
  cancelSyncRun,
  fetchDatasources,
  fetchSyncRuns,
  fetchSyncServiceStatus,
  retrySyncRun,
  type DatasourceRow,
  type SyncRun,
  type SyncServiceStatus,
} from "@/features/datasources/api"
import { SyncRunDetailDialog } from "@/features/datasources/components/sync-run-detail"
import {
  datasourceHref,
  parseSyncRunsParams,
  SYNC_RUNS_ALL as ALL,
  syncRunsQuery,
} from "@/features/datasources/links"
import {
  elapsedSeconds,
  etaSeconds,
  fmtDuration,
  fmtTime,
  PHASE_LABEL,
  percent,
  SILENT_WARN_SECONDS,
  STATUS_LABEL,
  STATUS_TONE,
  TRIGGER_LABEL,
} from "@/features/datasources/sync-format"
import { toast, notifyError } from "@/lib/notify"

const POLL_MS = 5000
const PAGE = 50

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: "ACTIVE", label: "실행 중·대기" },
  { value: "PROBLEM", label: "실패·일부 실패" },
  { value: "SUCCEEDED", label: "성공" },
  { value: "CANCELLED", label: "중지됨" },
  { value: ALL, label: "전체 상태" },
]
const PERIODS: { value: string; label: string; hours: number | null }[] = [
  { value: "24h", label: "최근 24시간", hours: 24 },
  { value: "7d", label: "최근 7일", hours: 24 * 7 },
  { value: "30d", label: "최근 30일", hours: 24 * 30 },
  { value: "all", label: "전체 기간", hours: null },
]

function ServiceStatusBar({ status }: { status: SyncServiceStatus | null }) {
  if (!status) return <span className="text-xs text-muted-foreground">metadata-sync 상태 확인 중…</span>
  if (!status.reachable) {
    return (
      <span className="flex items-center gap-1 text-xs">
        <span className="size-2 rounded-full bg-destructive" /> metadata-sync 연결 안 됨
        {status.message && <span >— {status.message}</span>}
      </span>
    )
  }
  return (
    <span className="flex items-center gap-1 text-xs">
      <span className="size-2 rounded-full bg-emerald-500" /> metadata-sync 정상
      <span >
        · 워커 {status.running ?? 0}/{status.workers ?? "-"} 사용 · 대기 {status.queued ?? 0}
        {status.version ? ` · v${status.version}` : ""}
      </span>
    </span>
  )
}

function ProgressCell({ run, queuePos }: { run: SyncRun; queuePos: number | null }) {
  if (run.status === "QUEUED") {
    return <span >대기{queuePos ? ` (앞에 ${queuePos}개)` : ""}</span>
  }
  if (run.status === "RUNNING") {
    const silent = (run.silent_seconds ?? 0) >= SILENT_WARN_SECONDS
    if (silent) {
      return <span className="text-destructive">응답 없음 {Math.floor((run.silent_seconds ?? 0) / 60)}분 ⚠</span>
    }
    const pct = percent(run)
    if (pct === null) {
      return (
        <span >
          {PHASE_LABEL[run.phase ?? ""] ?? "진행 중"}{run.current_target ? ` (${run.current_target})` : ""}
        </span>
      )
    }
    return (
      <div className="grid gap-0.5">
        <div className="h-2 w-40 overflow-hidden rounded bg-muted">
          <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
        </div>
        <span className="text-[11px]">
          {run.progress_done.toLocaleString()}/{run.progress_total?.toLocaleString()} · {PHASE_LABEL[run.phase ?? ""] ?? ""}
        </span>
      </div>
    )
  }
  return (
    <span className="flex items-center gap-1">
      <StatusBadge tone={STATUS_TONE[run.status]}>{STATUS_LABEL[run.status]}</StatusBadge>
      {run.failed_count > 0 && <span className="text-destructive">실패 {run.failed_count}건</span>}
    </span>
  )
}

function SyncRunsContent() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  // 기본 조건은 "전체 상태 + 최근 24시간" — 지켜보던 실행이 끝나도 목록에 남는다(TODO-074).
  // ?datasource=<데이터 소스 id> 로 들어오면 그 데이터 소스의 실행만(TODO-073)
  const [initial] = useState(() => parseSyncRunsParams(params))
  const [status, setStatus] = useState(initial.status)
  const [datasourceId, setDatasourceId] = useState(initial.datasource)
  const [period, setPeriod] = useState(initial.period)

  // 조건을 주소에 남긴다 — 실행 상세·데이터셋에 갔다 돌아와도 같은 조건(기록은 쌓지 않음)
  const queryKey = params.toString()
  useEffect(() => {
    const next = syncRunsQuery({ status, datasource: datasourceId, period })
    if (next !== queryKey) router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false })
    // queryKey 는 비교용 — 주소가 바뀌었다고 다시 쓰지 않는다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, datasourceId, period, pathname, router])

  // 같은 화면 안에서 주소가 바뀌면(실행 상세의 "전체 이력" 링크 등) 조건을 주소에 맞춘다
  useEffect(() => {
    const s = parseSyncRunsParams(new URLSearchParams(queryKey))
    setStatus(s.status)
    setDatasourceId(s.datasource)
    setPeriod(s.period)
  }, [queryKey])
  const [auto, setAuto] = useState(true)
  const [runs, setRuns] = useState<SyncRun[]>([])
  const [more, setMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [service, setService] = useState<SyncServiceStatus | null>(null)
  const [datasources, setDatasources] = useState<DatasourceRow[]>([])
  const [detailId, setDetailId] = useState<number | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [, setTick] = useState(0)

  const filters = useMemo(() => ({
    status: status === ALL ? undefined : status,
    datasourceId: datasourceId === ALL ? undefined : Number(datasourceId),
    hours: PERIODS.find((p) => p.value === period)?.hours ?? null,
  }), [status, datasourceId, period])

  const load = useCallback(async (append = false, cursor = 0) => {
    setLoading(true)
    try {
      const { hours, ...rest } = filters
      const since = hours ? new Date(Date.now() - hours * 3600_000).toISOString() : undefined
      const rows = await fetchSyncRuns({ ...rest, since, cursor, limit: PAGE })
      setRuns((prev) => (append ? [...prev, ...rows] : rows))
      setMore(rows.length === PAGE)
    } catch (e) {
      notifyError(e, "수집 실행 이력을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
    try {
      setService(await fetchSyncServiceStatus())
    } catch {
      setService({ reachable: false, workers: null, running: null, queued: null, version: null, message: null })
    }
  }, [filters])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    fetchDatasources().then(setDatasources).catch(() => setDatasources([]))
  }, [])

  // 실행 중·대기가 있고 자동 새로고침이 켜져 있을 때만 다시 읽는다
  const hasActive = runs.some((r) => ACTIVE_SYNC_STATUSES.has(r.status))
  useEffect(() => {
    if (!auto || !hasActive) return
    const t = setTimeout(() => void load(), POLL_MS)
    return () => clearTimeout(t)
  }, [auto, hasActive, runs, load])

  // 경과 시간 표시용 1초 틱(실행 중일 때만)
  useEffect(() => {
    if (!hasActive) return
    const t = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [hasActive])

  // 대기 순번 — 같은 실행 방식의 앞선 대기 수(목록에 보이는 것 기준)
  const queuePos = useMemo(() => {
    const queued = runs.filter((r) => r.status === "QUEUED").sort((a, b) => a.id - b.id)
    return new Map(queued.map((r, i) => [r.id, i]))
  }, [runs])

  const act = async (run: SyncRun, fn: () => Promise<SyncRun>, ok: string, fail: string) => {
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
    <div className="flex flex-1 flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <ServiceStatusBar status={service} />
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

      <div className="overflow-auto rounded border">
        <table className="w-full text-sm">
          <thead className="bg-muted text-left text-xs">
            <tr>
              <th className="px-3 py-2">데이터 소스</th>
              <th className="px-3 py-2">시작</th>
              <th className="px-3 py-2">진행</th>
              <th className="px-3 py-2">경과</th>
              <th className="px-3 py-2">남은 시간</th>
              <th className="px-3 py-2">생성/갱신/실패</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-8 text-center text-muted-foreground">
                  {loading ? "불러오는 중…" : "조건에 맞는 수집 실행이 없습니다."}
                </td>
              </tr>
            ) : runs.map((r) => {
              const active = ACTIVE_SYNC_STATUSES.has(r.status)
              const eta = etaSeconds(r)
              return (
                <tr key={r.id} className="cursor-pointer border-t hover:bg-muted/40" onClick={() => setDetailId(r.id)}>
                  <td className="px-3 py-2">
                    {/* 이름은 데이터 소스 화면으로 — 행 클릭(실행 상세)과 겹치지 않게 전파를 막는다 */}
                    <Link href={datasourceHref({ id: r.datasource_id, key: r.datasource_key })}
                      className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}
                      title="데이터 소스 화면에서 열기">
                      {r.datasource_name ?? `#${r.datasource_id}`}
                    </Link>
                    <div className="text-xs">#{r.id} · {r.datasource_type}</div>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    <div>{r.trigger_type === "MANUAL" ? (r.requested_by ?? "수동") : TRIGGER_LABEL[r.trigger_type] ?? r.trigger_type}</div>
                    <div >{fmtTime(r.started_at ?? r.queued_at)}</div>
                  </td>
                  <td className="px-3 py-2 text-xs"><ProgressCell run={r} queuePos={queuePos.get(r.id) ?? null} /></td>
                  <td className="px-3 py-2 text-xs">{r.status === "QUEUED" ? "" : fmtDuration(elapsedSeconds(r))}</td>
                  <td className="px-3 py-2 text-xs">{active ? (eta != null ? `약 ${fmtDuration(eta)}` : "-") : ""}</td>
                  <td className="px-3 py-2 text-xs">
                    {r.status === "QUEUED" ? "" : `${r.created_count}/${r.updated_count}/${r.failed_count}`}
                  </td>
                  <td className="px-3 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                    {active && !r.cancel_requested_by && (
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={busy === r.id}
                        onClick={() => act(r, () => cancelSyncRun(r.id), "중지를 요청했습니다.", "수집을 중지하지 못했습니다.")}>
                        <Square className="mr-1 size-3" /> 중지
                      </Button>
                    )}
                    {active && r.cancel_requested_by && <span className="text-xs">중지 요청됨</span>}
                    {!active && r.status !== "SUCCEEDED" && (
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={busy === r.id}
                        onClick={() => act(r, () => retrySyncRun(r.id), "다시 실행했습니다.", "수집을 다시 실행하지 못했습니다.")}>
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
          onClick={() => void load(true, runs.length)}>
          더 보기
        </Button>
      )}

      <SyncRunDetailDialog runId={detailId} onOpenChange={(o) => !o && setDetailId(null)} showDatasourceLink />
    </div>
  )
}

// 관리 > 메타데이터 수집 — 모든 데이터 소스의 수집 실행을 한곳에서 본다(설계 §7.2).
export default function SyncRunsPage() {
  return (
    <>
      <DashboardHeader title="메타데이터 수집" />
      <Suspense>
        <SyncRunsContent />
      </Suspense>
    </>
  )
}
