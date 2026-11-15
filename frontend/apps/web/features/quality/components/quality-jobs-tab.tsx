"use client"

import { Tip } from "@/components/tip"
import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { History, Pencil, Play, Plus, RefreshCw, Square, Trash2 } from "lucide-react"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@workspace/ui/components/alert-dialog"
import { Badge } from "@workspace/ui/components/badge"
import { StatusBadge } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import { fmtTime } from "@/features/datasources/sync-format"
import { usePermissions } from "@/features/permissions/use-permissions"
import {
  cancelQualityJobRun,
  deleteQualityJob,
  fetchQualityIndicators,
  fetchQualityJobs,
  runQualityJob,
  type QualityIndicator,
  type QualityJob,
  type QualityJobRun,
} from "@/features/quality/api"
import { QualityJobDialog } from "@/features/quality/components/quality-job-dialog"
import { EmptyRow, StaleNote } from "@/features/quality/components/quality-list-state"
import {
  describeMeasured,
  describeSchedule,
  describeTargets,
  fmtErrorRate,
  QUALITY_JOB_RUN_STATUS_LABEL,
  QUALITY_JOB_RUN_STATUS_TONE,
} from "@/features/quality/format"
import { qualityJobRunsHref } from "@/features/quality/links"
import { usePolling } from "@/features/quality/use-polling"
import { errorMessage, toast, notifyError } from "@/lib/notify"

const POLL_MS = 5000

function LastRunCell({ run }: { run: QualityJobRun | null }) {
  if (!run) return <span className="text-xs text-muted-foreground">실행한 적 없음</span>
  const finished = run.runs_succeeded + run.runs_failed
  return (
    <div className="grid gap-0.5 text-xs">
      <span className="flex items-center gap-1.5">
        <StatusBadge tone={QUALITY_JOB_RUN_STATUS_TONE[run.status]}>{QUALITY_JOB_RUN_STATUS_LABEL[run.status]}</StatusBadge>
        {run.status === "RUNNING"
          ? <span>데이터셋 {finished}/{run.runs_total}</span>
          : run.runs_total > 0 && <span>성공 {run.runs_succeeded} · 실패 {run.runs_failed}</span>}
        {run.checked_count != null && (
          <span className={run.error_rate ? "text-destructive" : ""}>오류율 {fmtErrorRate(run.error_rate)}</span>
        )}
      </span>
      <span  title={run.message ?? undefined}>
        {fmtTime(run.started_at)}
        {run.message ? ` · ${run.message}` : ""}
      </span>
    </div>
  )
}

/** 데이터 품질 > 품질 진단 작업 탭 — 주기 진단의 등록·수정·삭제·지금 실행. */
export function QualityJobsTab() {
  const { canUseFeature } = usePermissions()
  // 관리 › 데이터 품질 메뉴의 실행 · 취소 · 다시 실행은 품질 관리 권한(quality.manage)으로 — 서버와 같은 기준
  const canRun = canUseFeature("quality.manage")
  // 등록·수정은 컬럼에 지표를 할당·해제한다 — 그 권한이 없으면 저장이 중간에 막히므로 단추를 열지 않는다
  const canEdit = canRun && canUseFeature("quality.rules-manage")
  const [jobs, setJobs] = useState<QualityJob[]>([])
  const [indicators, setIndicators] = useState<QualityIndicator[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<QualityJob | null>(null)
  const [deleting, setDeleting] = useState<QualityJob | null>(null)

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    try {
      setJobs(await fetchQualityJobs())
      setError(null)
    } catch (e) {
      const message = errorMessage(e) ?? String(e)
      setError(message)
      if (!quiet) notifyError(e, "품질 진단 작업 목록을 불러오지 못했습니다.")
    } finally {
      if (!quiet) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    fetchQualityIndicators().then(setIndicators).catch(() => setIndicators([]))
  }, [load])

  // 실행 중인 작업이 있으면 끝날 때까지 다시 읽는다. 한 번 실패해도 다음 차례에 다시 읽는다
  const hasRunning = jobs.some((j) => j.last_run?.status === "RUNNING")
  usePolling(hasRunning, () => load(true), POLL_MS)

  // "지금 실행" 은 시작하지 못한 실행(건너뜀·실패)도 200 으로 돌려준다 — 돌아온 상태대로 알린다
  const runNow = async (job: QualityJob) => {
    setBusy(job.id)
    try {
      const run = await runQualityJob(job.id)
      if (run.status === "RUNNING") toast.success("작업 실행을 시작했습니다.")
      else if (run.status === "SUCCEEDED") toast.success("작업 실행을 마쳤습니다.")
      else notifyError(run.message, `작업을 실행하지 못했습니다(${QUALITY_JOB_RUN_STATUS_LABEL[run.status]}).`)
    } catch (e) {
      notifyError(e, "작업을 실행하지 못했습니다.")
    } finally {
      setBusy(null)
      await load()
    }
  }

  const act = async (job: QualityJob, fn: () => Promise<unknown>, ok: string, fail: string) => {
    setBusy(job.id)
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

  const openDialog = (job: QualityJob | null) => {
    setEditing(job)
    setDialogOpen(true)
  }

  return (
    <div className="flex flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs">
          데이터셋 하나와 컬럼별 품질 지표·주기를 정해 두면 주기마다 정밀 진단을 대기열에 넣습니다. 결과는 작업 이력 탭에서 봅니다.
        </span>
        <span className="ml-auto flex items-center gap-2">
          <Tip label="새로고침"><Button aria-label="새로고침" variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
          </Button></Tip>
          {canEdit && (
            <Button size="sm" onClick={() => openDialog(null)}>
              <Plus className="mr-1 size-4" /> 품질 진단 작업 등록
            </Button>
          )}
        </span>
      </div>

      <StaleNote error={error} show={jobs.length > 0} />
      <div className="overflow-auto rounded border">
        <table className="w-full text-sm">
          <thead className="bg-muted text-left text-xs">
            <tr>
              <th className="px-3 py-2">작업</th>
              <th className="px-3 py-2">대상</th>
              <th className="px-3 py-2">품질 지표</th>
              <th className="px-3 py-2">주기</th>
              <th className="px-3 py-2">다음 실행</th>
              <th className="px-3 py-2">마지막 실행</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 ? (
              <EmptyRow colSpan={7} loading={loading} error={error} onRetry={() => void load()}>
                등록한 품질 진단 작업이 없습니다. &quot;품질 진단 작업 등록&quot; 으로 주기 진단을 만드세요.
              </EmptyRow>
            ) : jobs.map((j) => {
              const running = j.last_run?.status === "RUNNING"
              return (
                <tr key={j.id} className="border-t align-top">
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-1.5 font-medium">
                      {j.name}
                      {!j.enabled && <Badge variant="outline">사용 안 함</Badge>}
                    </div>
                    {j.description && <div className="max-w-72 truncate text-xs" title={j.description}>{j.description}</div>}
                  </td>
                  <td className="px-3 py-2 text-xs" title={j.targets.map((t) => t.label).join("\n")}>
                    {describeTargets(j.targets)}
                  </td>
                  <td className="max-w-56 px-3 py-2 text-xs">{describeMeasured(j, indicators)}</td>
                  <td className="px-3 py-2 text-xs">{describeSchedule(j)}</td>
                  <td className="px-3 py-2 text-xs">
                    {j.next_run_at ? fmtTime(j.next_run_at) : <span className="text-muted-foreground">-</span>}
                  </td>
                  <td className="px-3 py-2"><LastRunCell run={j.last_run} /></td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    {canRun && (
                      <>
                        {running && j.last_run && !j.last_run.cancel_requested_by ? (
                          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={busy === j.id}
                            onClick={() => act(j, () => cancelQualityJobRun(j.last_run!.id), "중지를 요청했습니다.", "작업 실행을 중지하지 못했습니다.")}>
                            <Square className="mr-1 size-3" /> 중지
                          </Button>
                        ) : (
                          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={busy === j.id || running}
                            onClick={() => void runNow(j)}>
                            <Play className="mr-1 size-3" /> 지금 실행
                          </Button>
                        )}
                        <Tip label="이 작업의 작업 이력"><Button asChild variant="ghost" size="sm" className="h-7 px-2 text-xs">
                          <Link href={qualityJobRunsHref(j.id)} aria-label="작업 이력">
                            <History className="size-3" />
                          </Link>
                        </Button></Tip>
                        {canEdit && (
                          <Tip label="수정"><Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => openDialog(j)}
                            aria-label="수정">
                            <Pencil className="size-3" />
                          </Button></Tip>
                        )}
                        <Tip label="삭제"><Button variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={running}
                          onClick={() => setDeleting(j)} aria-label="삭제">
                          <Trash2 className="size-3" />
                        </Button></Tip>
                      </>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <QualityJobDialog open={dialogOpen} job={editing}
        onOpenChange={setDialogOpen} onSaved={() => void load()} />

      <AlertDialog open={deleting != null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>품질 진단 작업을 삭제할까요?</AlertDialogTitle>
            <AlertDialogDescription>
              &quot;{deleting?.name}&quot; 과 그 작업 이력을 지웁니다. 이미 진단한 데이터셋의 실행 기록과 규칙 결과는 남습니다.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>닫기</AlertDialogCancel>
            <AlertDialogAction onClick={() => {
              const job = deleting
              setDeleting(null)
              if (job) void act(job, () => deleteQualityJob(job.id), "품질 진단 작업을 삭제했습니다.", "품질 진단 작업을 삭제하지 못했습니다.")
            }}>
              삭제
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
