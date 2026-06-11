"use client"

/**
 * 내 결재함 — 현재 사용자가 "지금 결재할 차례"인 변경 요청을 한곳에 모아 보여주고,
 * 목록에서 바로 승인/반려할 수 있는 인박스.
 *
 * 데이터는 서버가 결재자 기준으로 필터링한 GET /changes/inbox 를 사용한다
 * (현재 단계 결재자=나). 결재 처리는 Temporal 시그널이라 DB 단계 반영에 지연이
 * 있을 수 있어, 결정 후 짧게 지연을 두고 목록을 다시 불러온다.
 */

import { useCallback, useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { AlertTriangle, CheckCircle2, Clock, Inbox, RefreshCw } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { DashboardHeader } from "@/components/dashboard-header"
import {
  type ChangeRequest,
  listMyInbox,
  submitDecision,
} from "@/features/change-mgmt/api"
import {
  CHANGE_TYPE_LABEL,
  PRIORITY_LABEL,
  StatusBadge,
  useUserNames,
} from "@/features/change-mgmt/labels"
import { useAuth } from "@/features/auth"

function fmtDateTime(s?: string | null): string {
  if (!s) return "—"
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return "—"
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 내 현재 결재 단계(가장 낮은 PENDING 이고 결재자=나)의 due_at 을 찾는다. */
function myDueAt(cr: ChangeRequest, me: string | undefined): string | null {
  if (!me) return null
  const mine = cr.approval_steps
    .filter((s) => s.decision === "PENDING" && s.approver === me)
    .sort((a, b) => a.step_order - b.step_order)[0]
  return mine?.due_at ?? null
}

/** due_at 이 지났는지 — 인박스에서 지연(SLA 초과) 강조용. */
function isOverdue(due?: string | null): boolean {
  if (!due) return false
  const d = new Date(due)
  return !Number.isNaN(d.getTime()) && d.getTime() < Date.now()
}

export default function MyApprovalsPage() {
  const router = useRouter()
  const { user } = useAuth()
  const me = user?.username
  const { approverLabel } = useUserNames()

  const [items, setItems] = useState<ChangeRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setItems(await listMyInbox())
    } catch (e) {
      setError(e instanceof Error ? e.message : "결재 인박스 조회 실패")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
    // 결재 진행에 따라 인박스가 바뀌므로 주기적으로 갱신한다
    const t = setInterval(() => void reload(), 60_000)
    return () => clearInterval(t)
  }, [reload])

  const onDecide = async (cr: ChangeRequest, decision: "APPROVED" | "REJECTED") => {
    let comment: string | undefined
    if (decision === "REJECTED") {
      const reason = window.prompt("반려 사유를 입력하세요 (선택):")
      if (reason === null) return // 취소
      comment = reason.trim() || undefined
    }
    setBusyId(cr.id)
    try {
      await submitDecision(cr.id, { decision, comment })
      // 워크플로 시그널 반영에 약간의 지연 — 잠시 후 목록 갱신
      await new Promise((r) => setTimeout(r, 600))
      await reload()
    } catch (e) {
      alert(e instanceof Error ? e.message : "결재 처리 실패")
    } finally {
      setBusyId(null)
    }
  }

  return (
    <>
      <DashboardHeader title="내 결재함" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            지금 내가 결재할 차례인 변경 요청 {items.length}건
          </p>
          <Button variant="outline" size="sm" onClick={() => void reload()} disabled={loading}>
            <RefreshCw className={`mr-1 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> 새로고침
          </Button>
        </div>

        {error && (
          <div className="flex items-center gap-2 rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800">
            <AlertTriangle className="h-4 w-4" />
            {error}
          </div>
        )}

        {loading && items.length === 0 ? (
          <div className="text-sm text-muted-foreground">불러오는 중...</div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-md border border-dashed p-10 text-center text-sm text-muted-foreground">
            <Inbox className="h-8 w-8 opacity-40" />
            결재 대기 중인 변경 요청이 없습니다.
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {items.map((cr) => {
              const due = myDueAt(cr, me)
              const overdue = isOverdue(due)
              const busy = busyId === cr.id
              return (
                <div
                  key={cr.id}
                  className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between"
                >
                  <button
                    type="button"
                    className="flex flex-1 flex-col gap-1 text-left"
                    onClick={() => router.push(`/dashboard/changes/${cr.id}`)}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-muted-foreground">{cr.cr_code}</span>
                      <span className="font-medium">{cr.title}</span>
                      <StatusBadge status={cr.status} />
                      <Badge variant="outline">{CHANGE_TYPE_LABEL[cr.change_type]}</Badge>
                      <Badge variant="outline">{PRIORITY_LABEL[cr.priority]}</Badge>
                    </div>
                    <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                      <span>요청자: {approverLabel(cr.requested_by)}</span>
                      <span className={overdue ? "font-medium text-rose-600" : ""}>
                        <Clock className="mr-0.5 inline h-3 w-3" />
                        기한: {fmtDateTime(due)}{overdue ? " (지연)" : ""}
                      </span>
                    </div>
                  </button>

                  <div className="flex shrink-0 gap-1">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => onDecide(cr, "APPROVED")}
                    >
                      <CheckCircle2 className="mr-1 h-4 w-4" /> 승인
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busy}
                      onClick={() => onDecide(cr, "REJECTED")}
                    >
                      반려
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </>
  )
}
