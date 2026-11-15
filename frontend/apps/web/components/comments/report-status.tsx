"use client"

/**
 * 오류 신고·개선 요청 처리 표시 (TODO-096 4단계, 가이드라인 체크리스트 C31).
 *
 * 데이터셋 댓글 중 분류가 '오류 신고'(bug)·'개선 요청'(suggestion)인 최상위 댓글은 접수되고 처리 기한이 붙는다.
 * 댓글 탭과 거버넌스 › 오류 신고 처리 목록이 같은 배지·기한 표시·처리 대화상자를 쓴다.
 */

import { useEffect, useState } from "react"
import { Loader2 } from "lucide-react"

import { StatusBadge, type StatusTone } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Label } from "@workspace/ui/components/label"
import { RadioGroup, RadioGroupItem } from "@workspace/ui/components/radio-group"
import { Textarea } from "@workspace/ui/components/textarea"
import { cn } from "@workspace/ui/lib/utils"

import { updateReportStatus, type CommentData, type ReportStatus } from "@/features/comments/api"
import { notifyError, notifySuccess } from "@/lib/notify"

export const REPORT_STATUS_LABEL: Record<ReportStatus, string> = {
  OPEN: "접수",
  IN_PROGRESS: "처리 중",
  RESOLVED: "해결",
  REJECTED: "소명",
}

/** 처리 상태 톤 — 알림 처리 상태(`features/alerts/labels.ts` `STATUS_TONE`)와 같은 기준(TODO-107). */
const REPORT_STATUS_TONE: Record<ReportStatus, StatusTone> = {
  OPEN: "warning",
  IN_PROGRESS: "info",
  RESOLVED: "success",
  REJECTED: "muted",
}

export function ReportStatusBadge({ status }: { status: ReportStatus }) {
  return <StatusBadge tone={REPORT_STATUS_TONE[status] ?? "neutral"} label={REPORT_STATUS_LABEL[status] ?? status} />
}

function formatDate(v: string): string {
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleDateString("ko-KR", { month: "2-digit", day: "2-digit" })
}

/** 처리 기한 — 처리 중이면 남은 날(D-n)·넘긴 날, 끝났으면 처리일. */
export function DueText({
  status, dueAt, resolvedAt, overdue, className,
}: {
  status: ReportStatus
  dueAt: string | null | undefined
  resolvedAt?: string | null
  overdue?: boolean
  className?: string
}) {
  if (status === "RESOLVED" || status === "REJECTED") {
    return <span className={cn("text-xs", className)}>{resolvedAt ? `${formatDate(resolvedAt)} 처리` : "처리됨"}</span>
  }
  if (!dueAt) return null
  return <OpenDueText dueAt={dueAt} overdue={overdue} className={className} />
}

function OpenDueText({ dueAt, overdue, className }: { dueAt: string; overdue?: boolean; className?: string }) {
  const [now] = useState(() => Date.now())  // 렌더마다 시계를 읽지 않게 처음 그릴 때 한 번
  const days = Math.ceil((new Date(dueAt).getTime() - now) / 86_400_000)
  if (overdue || days < 0) {
    return (
      <span className={cn("text-xs font-medium text-red-600", className)}>
        기한 {formatDate(dueAt)} · {Math.max(1, -days)}일 넘김
      </span>
    )
  }
  return (
    <span className={cn("text-xs", days <= 2 ? "text-amber-600" : "text-muted-foreground", className)}>
      기한 {formatDate(dueAt)} · {days === 0 ? "오늘까지" : `D-${days}`}
    </span>
  )
}

/** 처리 상태 변경 대화상자 — 데이터셋 소유자(생성자)·관리자. */
export function ReportStatusDialog({
  report, onClose, onSaved,
}: {
  report: Pick<CommentData, "id" | "status" | "resolution" | "content_plain"> | null
  onClose: () => void
  onSaved: () => void
}) {
  const [status, setStatus] = useState<ReportStatus>("IN_PROGRESS")
  const [resolution, setResolution] = useState("")
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!report) return
    setStatus(report.status === "OPEN" ? "IN_PROGRESS" : (report.status ?? "IN_PROGRESS"))
    setResolution(report.resolution ?? "")
  }, [report])

  const needsReason = status === "REJECTED" && !resolution.trim()

  const save = async () => {
    if (!report) return
    setSaving(true)
    try {
      await updateReportStatus(report.id, { status, resolution: resolution.trim() || null })
      notifySuccess(`'${REPORT_STATUS_LABEL[status]}'(으)로 바꿨습니다.`)
      onSaved()
    } catch (e) {
      notifyError(e, "처리 상태를 바꾸지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={!!report} onOpenChange={(open) => { if (!open && !saving) onClose() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>신고 처리</DialogTitle>
          <DialogDescription className="line-clamp-3">{report?.content_plain || "(내용 없음)"}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <RadioGroup value={status} onValueChange={(v) => setStatus(v as ReportStatus)} className="grid grid-cols-2 gap-2">
            {(["OPEN", "IN_PROGRESS", "RESOLVED", "REJECTED"] as const).map((s) => (
              <div key={s} className="flex items-center gap-1.5">
                <RadioGroupItem id={`report-${s}`} value={s} />
                <Label htmlFor={`report-${s}`} className="font-normal">
                  {REPORT_STATUS_LABEL[s]}
                  <span className="ml-1 text-xs">
                    {s === "OPEN" ? "— 다시 열기" : s === "IN_PROGRESS" ? "— 확인함" : s === "RESOLVED" ? "— 데이터를 고침" : "— 고치지 않는 이유 설명"}
                  </span>
                </Label>
              </div>
            ))}
          </RadioGroup>
          <div className="space-y-1.5">
            <Label htmlFor="report-resolution">{status === "REJECTED" ? "소명 사유 *" : "처리 결과"}</Label>
            <Textarea id="report-resolution" rows={3} value={resolution} onChange={(e) => setResolution(e.target.value)}
              placeholder={status === "REJECTED" ? "예: 측정 기준이 달라 생긴 차이로 오류가 아닙니다" : "예: 2025-11 적재분에서 수정함(버전 v2)"} />
            <p className="text-xs">신고한 사람이 볼 수 있도록 댓글 아래에 함께 표시됩니다.</p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={saving}>취소</Button>
          <Button onClick={save} disabled={saving || needsReason}>
            {saving && <Loader2 className="mr-1 h-4 w-4 animate-spin" />} 저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
