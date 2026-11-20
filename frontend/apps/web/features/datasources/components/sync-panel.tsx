"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { ListFilter, Play, RefreshCw, RotateCcw, Square } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { StatusBadge } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import { DisabledReason } from "@/components/tip"
import { Label } from "@workspace/ui/components/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { Switch } from "@workspace/ui/components/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@workspace/ui/components/table"
import { DetailSection } from "@/components/detail-section"
import {
  ACTIVE_SYNC_STATUSES,
  cancelSyncRun,
  fetchDatasourceSyncRuns,
  fetchSyncSettings,
  retrySyncRun,
  saveSyncSchedule,
  startSyncRun,
  type SyncRun,
  type SyncSettings,
} from "@/features/datasources/api"
import { SyncResultLinks, SyncRunDetailDialog } from "@/features/datasources/components/sync-run-detail"
import { SyncScopeDialog } from "@/features/datasources/components/sync-scope-dialog"
import {
  etaSeconds,
  fmtDuration,
  fmtTime,
  intervalLabel,
  PHASE_LABEL,
  percent,
  SCHEDULE_INTERVALS,
  scopeSummary,
  SILENT_WARN_SECONDS,
  STATUS_LABEL,
  STATUS_TONE,
  SYNC_CATALOG_MISSING_MESSAGE,
  SYNC_PERMISSION_MISSING_MESSAGE,
} from "@/features/datasources/sync-format"
import { errorMessage, toast, notifyError } from "@/lib/notify"

const POLL_MS = 3000

/**
 * 데이터 소스 상세의 "수집" 영역 — 현재 범위, 범위 선택, 지금 수집, 진행 막대, 최근 실행.
 * 실행 중이면 3초마다 다시 읽는다. 범위를 저장하지 않으면 수집할 수 없다.
 */
export function SyncPanel({
  datasource,
  canSync,
}: {
  /** datasource_id — 외부 식별자(문자열). 있으면 "데이터셋 보기" 링크를 보인다 */
  datasource: { id: number; name?: string; type: string; datasource_id?: string }
  canSync: boolean
}) {
  const [settings, setSettings] = useState<SyncSettings | null>(null)
  const [runs, setRuns] = useState<SyncRun[]>([])
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [scopeOpen, setScopeOpen] = useState(false)
  const [onceOpen, setOnceOpen] = useState(false)
  const [detailId, setDetailId] = useState<number | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const prevActive = useRef<number | null>(null)

  const loadRuns = useCallback(async () => {
    try {
      setRuns(await fetchDatasourceSyncRuns(datasource.id, 5))
    } catch (e) {
      setLoadError(errorMessage(e) ?? String(e))
    }
  }, [datasource.id])

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const s = await fetchSyncSettings(datasource.id)
        if (!alive) return
        setSettings(s)
        await loadRuns()
      } catch (e) {
        if (alive) setLoadError(errorMessage(e) ?? String(e))
      }
    })()
    return () => {
      alive = false
    }
  }, [datasource.id, loadRuns])

  // 새로 고침 — 실행 목록과 함께 설정도 다시 읽는다(카탈로그 '수집' 연결을 바꾼 뒤 반영, DEF-068)
  const reloadAll = useCallback(async () => {
    try {
      setSettings(await fetchSyncSettings(datasource.id))
    } catch (e) {
      notifyError(e, "수집 설정을 불러오지 못했습니다.")
    }
    await loadRuns()
  }, [datasource.id, loadRuns])

  const active = runs.find((r) => ACTIVE_SYNC_STATUSES.has(r.status)) ?? null

  // 실행 중일 때만 폴링
  useEffect(() => {
    if (!active) return
    timer.current = setTimeout(() => void loadRuns(), POLL_MS)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [active, runs, loadRuns])

  // 지켜보던 실행이 끝나면 결과를 알린다
  useEffect(() => {
    const watched = prevActive.current
    prevActive.current = active?.id ?? null
    if (watched == null || active?.id === watched) return
    const done = runs.find((r) => r.id === watched)
    if (!done || ACTIVE_SYNC_STATUSES.has(done.status)) return
    const counts = `생성 ${done.created_count} · 갱신 ${done.updated_count} · 제거 ${done.removed_count}`
      + (done.skipped_count ? ` · 건너뜀 ${done.skipped_count}(수동 데이터셋에 매핑됨)` : "")
    if (done.status === "SUCCEEDED") toast.success("수집을 마쳤습니다.", { description: counts })
    else if (done.status === "PARTIAL") toast.warning("수집을 마쳤지만 일부 항목을 수집하지 못했습니다.", { description: `${counts} · 실패 ${done.failed_count}` })
    else if (done.status === "FAILED") notifyError(done.error_message, "수집하지 못했습니다.")
  }, [active, runs])

  if (loadError && !settings) {
    return <DetailSection title="수집"><p className="text-sm text-destructive">{loadError}</p></DetailSection>
  }
  if (!settings) {
    return <DetailSection title="수집"><p className="text-sm text-muted-foreground">수집 설정을 불러오는 중…</p></DetailSection>
  }

  if (settings.executor === null) {
    return (
      <DetailSection title="수집">
        <p className="text-sm text-muted-foreground">이 유형은 아직 화면에서 수집을 지원하지 않습니다.</p>
      </DetailSection>
    )
  }

  const act = async (fn: () => Promise<SyncRun>, ok: string, fail: string) => {
    setBusy(true)
    try {
      await fn()
      toast.success(ok)
    } catch (e) {
      notifyError(e, fail)
    } finally {
      setBusy(false)
      await loadRuns()
    }
  }

  const saved = settings.scope
  const latest = runs[0] ?? null
  // 쿼리 엔진에 '수집' 연결이 없으면 범위 저장·수집이 409 — 버튼을 막고 안내한다(DEF-068)
  const bindingMissing = !!settings.sync_catalog_missing

  return (
    <div className="grid gap-8">
    <DetailSection
      title="수집"
      actions={
        <Button variant="ghost" size="sm" onClick={() => void reloadAll()}>
          <RefreshCw className="mr-1 size-4" />
          새로고침
        </Button>
      }
    >

      {/* 범위 */}
      <div className="grid gap-2">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Label className="text-xs text-muted-foreground">수집 범위</Label>
          <span className={saved ? "" : "text-amber-600"}>
            {saved
              ? scopeSummary(saved, settings.container_label)
              : canSync
                ? "범위를 먼저 선택하세요"
                : "범위 없음"}
          </span>
          {settings.updated_by && (
            <span className="text-xs">({settings.updated_by}, {fmtTime(settings.updated_at)})</span>
          )}
        </div>
        {/* 수집 권한이 없으면 버튼 대신 권한 안내(DEF-069) */}
        {!canSync && (
          <p className="text-xs">{SYNC_PERMISSION_MISSING_MESSAGE}</p>
        )}
        {canSync && bindingMissing && (
          <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            {SYNC_CATALOG_MISSING_MESSAGE}
          </p>
        )}
        {canSync && (
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => setScopeOpen(true)} disabled={busy || bindingMissing}>
              <ListFilter className="mr-1 size-4" />
              범위 선택…
            </Button>
            <DisabledReason reason={bindingMissing ? "카탈로그에 '수집'으로 연결해야 합니다"
                : !saved ? "범위를 먼저 선택하세요" : active ? "이미 수집 중입니다" : undefined}><Button
              size="sm"
              onClick={() => act(() => startSyncRun(datasource.id), "수집을 시작했습니다.", "수집을 시작하지 못했습니다.")}
              disabled={busy || !saved || !!active || bindingMissing}
            >
              <Play className="mr-1 size-4" />
              지금 수집
            </Button></DisabledReason>
            <Button size="sm" variant="ghost" onClick={() => setOnceOpen(true)} disabled={busy || !!active || bindingMissing}
              title="저장된 범위는 그대로 두고 다른 범위로 한 번만 수집합니다">
              이번 한 번만…
            </Button>
          </div>
        )}
      </div>

      {/* 예약 */}
      <ScheduleRow settings={settings} canSync={canSync} blocked={bindingMissing} datasourceId={datasource.id} onSaved={setSettings} />

      {/* 진행 */}
      {active && (
        <ActiveRun run={active} canSync={canSync} busy={busy} onOpen={() => setDetailId(active.id)}
          onCancel={() => act(() => cancelSyncRun(active.id), "중지를 요청했습니다.", "수집을 중지하지 못했습니다.")} />
      )}

    </DetailSection>

      {/* 최근 실행 — 결과 데이터셋·전체 이력 링크(전체 이력은 메타데이터 수집 화면을 볼 수 있는 수집 권한자만, TODO-074) */}
      {runs.length > 0 && (
        <DetailSection
          title="최근 실행"
          actions={<SyncResultLinks datasource={{ id: datasource.id, key: datasource.datasource_id }} showHistory={canSync} />}
        >
          <div className="overflow-x-auto rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16">실행</TableHead>
                  <TableHead className="w-32">상태</TableHead>
                  <TableHead className="w-40">시각</TableHead>
                  <TableHead>생성 · 갱신 · 제거 · 실패</TableHead>
                  <TableHead className="w-28">요청자</TableHead>
                  <TableHead className="w-40" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((r) => (
                  <TableRow key={r.id} className="align-top">
                    <TableCell>
                      <button type="button" className="text-primary underline-offset-2 hover:underline"
                        onClick={() => setDetailId(r.id)}>
                        #{r.id}
                      </button>
                    </TableCell>
                    <TableCell>
                      <span className="flex gap-1">
                        <StatusBadge tone={STATUS_TONE[r.status]}>{STATUS_LABEL[r.status]}</StatusBadge>
                        {r.trigger_type === "SCHEDULE" && <Badge variant="outline">예약</Badge>}
                      </span>
                    </TableCell>
                    <TableCell className="tabular-nums">{fmtTime(r.finished_at ?? r.started_at ?? r.queued_at)}</TableCell>
                    <TableCell className="whitespace-normal">
                      <span className="tabular-nums">
                        {r.created_count} · {r.updated_count} · {r.removed_count} ·{" "}
                        <span className={r.failed_count > 0 ? "text-destructive" : undefined}>{r.failed_count}</span>
                      </span>
                      {r.error_message && !ACTIVE_SYNC_STATUSES.has(r.status) && (
                        <p className="mt-0.5 break-all">{r.error_message}</p>
                      )}
                    </TableCell>
                    <TableCell >{r.requested_by ?? (r.trigger_type === "SCHEDULE" ? "예약" : "-")}</TableCell>
                    <TableCell>
                      <span className="flex justify-end gap-1">
                        {r.items_total > 0 && (
                          <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={() => setDetailId(r.id)}>
                            실패 항목 {r.items_total}
                          </Button>
                        )}
                        {canSync && !bindingMissing && r === latest && !active && (r.status === "FAILED" || r.status === "PARTIAL" || r.status === "CANCELLED") && (
                          <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" disabled={busy}
                            onClick={() => act(() => retrySyncRun(r.id), "다시 실행했습니다.", "수집을 다시 실행하지 못했습니다.")}>
                            <RotateCcw className="mr-1 size-3" />
                            다시 실행
                          </Button>
                        )}
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </DetailSection>
      )}

      {canSync && (
        <SyncScopeDialog
          open={scopeOpen}
          onOpenChange={setScopeOpen}
          datasource={datasource}
          settings={settings}
          onSaved={(s, runNow) => {
            setSettings(s)
            if (runNow) void act(() => startSyncRun(datasource.id), "수집을 시작했습니다.", "수집을 시작하지 못했습니다.")
          }}
        />
      )}
      {canSync && (
        <SyncScopeDialog
          open={onceOpen}
          onOpenChange={setOnceOpen}
          datasource={datasource}
          settings={settings}
          once
          onSaved={() => {}}
          onRunOnce={(scope) => void act(() => startSyncRun(datasource.id, scope), "이번 한 번만 수집을 시작했습니다.", "수집을 시작하지 못했습니다.")}
        />
      )}
      <SyncRunDetailDialog runId={detailId} onOpenChange={(o) => !o && setDetailId(null)} showHistoryLink={canSync} />
    </div>
  )
}

/** 예약 수집 — 켜기·끄기와 주기, 다음 예정 시각. 범위가 없거나 막혀 있으면(blocked) 켤 수 없다(끄기는 가능). */
function ScheduleRow({ settings, canSync, blocked = false, datasourceId, onSaved }: {
  settings: SyncSettings
  canSync: boolean
  /** 엔진의 '수집' 연결 없음 등으로 수집할 수 없는 상태(DEF-068) */
  blocked?: boolean
  datasourceId: number
  onSaved: (s: SyncSettings) => void
}) {
  const [saving, setSaving] = useState(false)
  const enabled = !!settings.schedule_enabled
  const interval = settings.schedule_interval_minutes ?? 1440
  const save = async (on: boolean, minutes: number) => {
    setSaving(true)
    try {
      onSaved(await saveSyncSchedule(datasourceId, on, minutes))
      toast.success(on ? `예약 수집: ${intervalLabel(minutes)}마다` : "예약 수집을 껐습니다.")
    } catch (e) {
      notifyError(e, "예약 수집 설정을 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }
  const noScope = !settings.scope
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <Label htmlFor={`sync-schedule-${datasourceId}`} className="text-xs text-muted-foreground">예약</Label>
      <Switch
        id={`sync-schedule-${datasourceId}`}
        checked={enabled}
        disabled={!canSync || saving || ((noScope || blocked) && !enabled)}
        onCheckedChange={(on) => void save(on, interval)}
        aria-label="예약 수집"
      />
      <Select
        value={String(interval)}
        disabled={!canSync || saving || noScope || blocked}
        onValueChange={(v) => void save(enabled, Number(v))}
      >
        <SelectTrigger className="h-8 w-[110px] text-sm" aria-label="예약 주기">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {[...new Set([...SCHEDULE_INTERVALS, interval])].sort((a, b) => a - b).map((m) => (
            <SelectItem key={m} value={String(m)}>{intervalLabel(m)}마다</SelectItem>
          ))}
        </SelectContent>
      </Select>
      <span className="text-xs">
        {noScope ? (canSync ? "범위를 먼저 선택하세요" : "범위 없음")
          : enabled ? `다음 예정 ${fmtTime(settings.next_scheduled_at)} (마지막 실행 기준, 수동 실행 포함)`
            : "꺼짐 — 수동으로만 수집"}
      </span>
    </div>
  )
}

export function ActiveRun({ run, canSync, busy, onCancel, onOpen }: {
  run: SyncRun
  canSync: boolean
  busy: boolean
  onCancel: () => void
  onOpen?: () => void
}) {
  const pct = percent(run)
  const silent = (run.silent_seconds ?? 0) >= SILENT_WARN_SECONDS
  const eta = etaSeconds(run)
  return (
    <div className="grid gap-2 rounded border p-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <StatusBadge tone={STATUS_TONE[run.status]}>{STATUS_LABEL[run.status]}</StatusBadge>
        {run.phase && <span>{PHASE_LABEL[run.phase] ?? run.phase}</span>}
        {silent && (
          <StatusBadge tone="danger" title="2분 넘게 진행 보고가 없습니다. 5분이 지나면 실패로 처리합니다.">
            응답 없음 {Math.floor((run.silent_seconds ?? 0) / 60)}분
          </StatusBadge>
        )}
        {run.cancel_requested_by && <span >중지 요청됨</span>}
        <span className="ml-auto flex gap-1">
          {onOpen && (
            <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={onOpen}>상세</Button>
          )}
          {canSync && !run.cancel_requested_by && (
            <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={onCancel} disabled={busy}>
              <Square className="mr-1 size-3" />
              중지
            </Button>
          )}
        </span>
      </div>
      <div
        className="h-2 w-full overflow-hidden rounded bg-muted"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct ?? undefined}
      >
        <div
          className={pct === null ? "h-full w-1/3 animate-pulse bg-primary/60" : "h-full bg-primary transition-all"}
          style={pct === null ? undefined : { width: `${pct}%` }}
        />
      </div>
      <div className="flex flex-wrap gap-x-3 text-xs">
        <span>
          {run.progress_done}
          {run.progress_total ? ` / ${run.progress_total}` : ""}
          {pct !== null ? ` (${pct}%)` : ""}
        </span>
        {eta != null && <span>남은 시간 약 {fmtDuration(eta)}</span>}
        <span>생성 {run.created_count} · 갱신 {run.updated_count} · 실패 {run.failed_count}</span>
        {run.current_target && <span className="truncate">현재: {run.current_target}</span>}
      </div>
    </div>
  )
}
