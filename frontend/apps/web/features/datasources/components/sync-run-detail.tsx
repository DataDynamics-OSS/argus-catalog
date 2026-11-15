"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Database, Download, History, Table2 } from "lucide-react"

import { StatusBadge } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import {
  ACTIVE_SYNC_STATUSES,
  fetchDatasourceSyncRuns,
  fetchSyncRun,
  fetchSyncRunItems,
  type SyncRun,
  type SyncRunItem,
} from "@/features/datasources/api"
import { datasourceDatasetsHref, datasourceHref, syncRunsHref } from "@/features/datasources/links"
import {
  csvCell,
  elapsedSeconds,
  etaSeconds,
  fmtDuration,
  fmtTime,
  PHASE_LABEL,
  scopeSummary,
  STATUS_LABEL,
  STATUS_TONE,
  TRIGGER_LABEL,
} from "@/features/datasources/sync-format"
import { errorMessage } from "@/lib/notify"
import { SearchInput } from "@/components/search-input"

const POLL_MS = 3000

/**
 * 수집 결과로 가는 링크 — 데이터 소스 화면, 이 데이터 소스의 데이터셋, 메타데이터 수집 전체 이력(TODO-074).
 * 데이터셋 링크는 외부 식별자(key)가 있을 때만, 전체 이력은 메타데이터 수집 화면을 볼 수 있을 때만 보인다.
 */
export function SyncResultLinks({ datasource, showDatasource = false, showHistory = true, onNavigate }: {
  datasource: { id: number; key?: string | null }
  showDatasource?: boolean
  showHistory?: boolean
  /** 링크를 누른 뒤 — 다이얼로그 닫기 등 */
  onNavigate?: () => void
}) {
  const cls = "inline-flex items-center gap-1 text-primary hover:underline"
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
      {showDatasource && (
        <Link href={datasourceHref(datasource)} className={cls} onClick={onNavigate}>
          <Database className="size-3.5" /> 데이터 소스 열기
        </Link>
      )}
      {datasource.key && (
        <Link href={datasourceDatasetsHref(datasource.key)} className={cls} onClick={onNavigate}>
          <Table2 className="size-3.5" /> 이 데이터 소스의 데이터셋 보기
        </Link>
      )}
      {showHistory && (
        <Link href={syncRunsHref({ datasource: String(datasource.id) })} className={cls} onClick={onNavigate}>
          <History className="size-3.5" /> 전체 수집 이력
        </Link>
      )}
    </div>
  )
}

/**
 * 수집 실행 상세 — 범위·요청자·소요 시간·처리 속도, 실패·경고 항목(검색·CSV), 같은 데이터 소스의 이전 실행 비교.
 * 결과 데이터셋·전체 이력(필요하면 데이터 소스 화면)으로 가는 링크를 함께 둔다.
 */
export function SyncRunDetailDialog({ runId, onOpenChange, showDatasourceLink = false, showHistoryLink = true }: {
  runId: number | null
  onOpenChange: (o: boolean) => void
  /** 데이터 소스 화면으로 가는 링크 — 데이터 소스 화면 안에서 열 때는 끈다 */
  showDatasourceLink?: boolean
  /** 메타데이터 수집 전체 이력 링크 — 메타데이터 수집 화면을 볼 수 없는 사용자에게는 끈다 */
  showHistoryLink?: boolean
}) {
  const [run, setRun] = useState<SyncRun | null>(null)
  const [items, setItems] = useState<SyncRunItem[]>([])
  const [previous, setPrevious] = useState<SyncRun[]>([])
  const [level, setLevel] = useState("ALL")
  const [q, setQ] = useState("")
  const [error, setError] = useState<string | null>(null)

  const loadItems = useCallback(async (id: number, lv: string, query: string) => {
    try {
      setItems(await fetchSyncRunItems(id, { level: lv === "ALL" ? undefined : lv, q: query || undefined }))
    } catch (e) {
      setError(errorMessage(e) ?? String(e))
    }
  }, [])

  useEffect(() => {
    if (runId == null) return
    let alive = true
    setRun(null)
    setItems([])
    setPrevious([])
    setLevel("ALL")
    setQ("")
    setError(null)
    ;(async () => {
      try {
        const r = await fetchSyncRun(runId)
        if (!alive) return
        setRun(r)
        const [its, prev] = await Promise.all([
          fetchSyncRunItems(runId),
          fetchDatasourceSyncRuns(r.datasource_id, 6),
        ])
        if (!alive) return
        setItems(its)
        setPrevious(prev.filter((p) => p.id !== runId).slice(0, 5))
      } catch (e) {
        if (alive) setError(errorMessage(e) ?? String(e))
      }
    })()
    return () => {
      alive = false
    }
  }, [runId])

  // 실행 중이면 상태를 다시 읽는다
  useEffect(() => {
    if (!run || !ACTIVE_SYNC_STATUSES.has(run.status)) return
    const t = setTimeout(async () => {
      try {
        setRun(await fetchSyncRun(run.id))
      } catch {
        // 다음 주기에 다시 시도
      }
    }, POLL_MS)
    return () => clearTimeout(t)
  }, [run])

  const downloadCsv = () => {
    if (!run) return
    const rows = [["level", "target", "message", "occurred_at"].join(",")]
    for (const it of items) rows.push([it.level, it.target, it.message, it.occurred_at].map(csvCell).join(","))
    const blob = new Blob([String.fromCharCode(0xfeff) + rows.join("\n")], { type: "text/csv;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `sync-run-${run.id}-items.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const elapsed = run ? elapsedSeconds(run) : null
  const eta = run ? etaSeconds(run) : null
  const rate = run && elapsed && run.progress_done ? run.progress_done / elapsed : null

  return (
    <Dialog open={runId != null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            수집 실행 #{runId}
            {run?.datasource_name ? ` — ${run.datasource_name}` : ""}
          </DialogTitle>
          <DialogDescription>{run ? `${run.datasource_type ?? ""} · ${run.executor === "BUILTIN" ? "내장 수집" : "metadata-sync"}` : ""}</DialogDescription>
        </DialogHeader>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {run && (
          <SyncResultLinks
            datasource={{ id: run.datasource_id, key: run.datasource_key }}
            showDatasource={showDatasourceLink}
            showHistory={showHistoryLink}
            onNavigate={() => onOpenChange(false)}
          />
        )}
        {!run ? (
          !error && <p className="text-sm text-muted-foreground">불러오는 중…</p>
        ) : (
          <div className="grid gap-4 text-sm">
            <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1">
              <dt className="text-muted-foreground">상태</dt>
              <dd className="flex items-center gap-2">
                <StatusBadge tone={STATUS_TONE[run.status]}>{STATUS_LABEL[run.status]}</StatusBadge>
                {run.phase && <span >{PHASE_LABEL[run.phase] ?? run.phase}</span>}
                {run.current_target && ACTIVE_SYNC_STATUSES.has(run.status) && <span className="truncate">현재: {run.current_target}</span>}
              </dd>
              <dt className="text-muted-foreground">범위</dt>
              <dd>{scopeSummary(run.scope, "대상")}</dd>
              <dt className="text-muted-foreground">시작</dt>
              <dd>
                {TRIGGER_LABEL[run.trigger_type] ?? run.trigger_type}
                {run.requested_by ? ` · ${run.requested_by}` : ""} · 접수 {fmtTime(run.queued_at)}
              </dd>
              <dt className="text-muted-foreground">소요</dt>
              <dd>
                {fmtDuration(elapsed)}
                {rate ? ` · 초당 ${rate.toFixed(1)}개` : ""}
                {eta != null ? ` · 남은 시간 약 ${fmtDuration(eta)}` : ""}
                {run.finished_at ? ` · 종료 ${fmtTime(run.finished_at)}` : ""}
              </dd>
              <dt className="text-muted-foreground">처리</dt>
              <dd>
                {run.progress_done}{run.progress_total ? ` / ${run.progress_total}` : ""} · 생성 {run.created_count} · 갱신{" "}
                {run.updated_count} · 제거 {run.removed_count} · 실패 {run.failed_count}
              </dd>
              {run.cancel_requested_by && (
                <>
                  <dt className="text-muted-foreground">중지 요청</dt>
                  <dd>{run.cancel_requested_by}</dd>
                </>
              )}
              {run.error_message && (
                <>
                  <dt className="text-muted-foreground">오류</dt>
                  <dd className="break-all text-destructive">{run.error_message}</dd>
                </>
              )}
            </dl>

            <div className="grid gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <h4 className="font-semibold">실패·경고 항목 ({run.items_total})</h4>
                <Select value={level} onValueChange={(v) => { setLevel(v); void loadItems(run.id, v, q) }}>
                  <SelectTrigger className="h-8 w-28"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ALL">전체</SelectItem>
                    <SelectItem value="ERROR">오류</SelectItem>
                    <SelectItem value="WARNING">경고</SelectItem>
                  </SelectContent>
                </Select>
                <SearchInput size="sm" className="w-56" value={q} onValueChange={setQ} placeholder="대상·사유 검색…"
                  onSearch={() => void loadItems(run.id, level, q)}
                  onClear={() => {
                    setQ("")
                    void loadItems(run.id, level, "")
                  }} />
                <Button variant="outline" size="sm" className="ml-auto" onClick={downloadCsv} disabled={items.length === 0}>
                  <Download className="mr-1 size-4" /> CSV 내려받기
                </Button>
              </div>
              {items.length === 0 ? (
                <p className="text-xs text-muted-foreground">항목이 없습니다.</p>
              ) : (
                <div className="max-h-64 overflow-auto rounded border">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-muted">
                      <tr className="text-left">
                        <th className="px-2 py-1">수준</th>
                        <th className="px-2 py-1">대상</th>
                        <th className="px-2 py-1">사유</th>
                        <th className="px-2 py-1">시각</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((it) => (
                        <tr key={it.id} className="border-t align-top">
                          <td className={`px-2 py-1 ${it.level === "ERROR" ? "text-destructive" : "text-amber-600"}`}>{it.level}</td>
                          <td className="px-2 py-1 font-mono">{it.target ?? "-"}</td>
                          <td className="break-all px-2 py-1">{it.message}</td>
                          <td className="whitespace-nowrap px-2 py-1">{fmtTime(it.occurred_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {run.items_total > 1000 && (
                <p className="text-xs">1,000건까지만 저장합니다 — 외 {run.items_total - 1000}건</p>
              )}
            </div>

            {previous.length > 0 && (
              <div className="grid gap-1">
                <h4 className="font-semibold">이전 실행</h4>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left">
                      <th className="py-1">#</th><th>상태</th><th>시작</th><th>소요</th><th>생성/갱신/제거/실패</th>
                    </tr>
                  </thead>
                  <tbody>
                    {previous.map((p) => (
                      <tr key={p.id} className="border-t">
                        <td className="py-1">{p.id}</td>
                        <td><StatusBadge tone={STATUS_TONE[p.status]}>{STATUS_LABEL[p.status]}</StatusBadge></td>
                        <td>{fmtTime(p.started_at ?? p.queued_at)}</td>
                        <td>{fmtDuration(elapsedSeconds(p))}</td>
                        <td>{p.created_count}/{p.updated_count}/{p.removed_count}/{p.failed_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
