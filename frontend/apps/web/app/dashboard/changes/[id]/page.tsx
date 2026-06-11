"use client"

import { useCallback, useEffect, useState } from "react"
import { useParams, useRouter } from "next/navigation"
import {
  ArrowLeft,
  CheckCircle2,
  Circle,
  Clock,
  XCircle,
  AlertTriangle,
} from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { DashboardHeader } from "@/components/dashboard-header"
import { RichTextViewer } from "@/components/comments/rich-text-viewer"
import {
  type ChangeRequest,
  type NotificationLog,
  cancelChangeRequest,
  getChangeRequest,
  listNotifications,
  submitChangeRequest,
  submitDecision,
} from "@/features/change-mgmt/api"
import {
  CHANGE_TYPE_LABEL,
  PRIORITY_LABEL,
  StatusBadge,
} from "@/features/change-mgmt/labels"
import { fetchDataset } from "@/features/datasets/api"
import { fetchUsers } from "@/features/users/api"
import { type User } from "@/features/users/data/schema"
import { useAuth } from "@/features/auth"

const userDisplayName = (u: User) => `${u.lastName ?? ""}${u.firstName ?? ""}`.trim() || u.username

function fmtDateTime(s?: string | null): string {
  if (!s) return "—"
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return "—"
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** JSON 문자열이면 보기 좋게 들여쓰기, 아니면 원문 반환. */
function prettyJson(s: string): string {
  try {
    return JSON.stringify(JSON.parse(s), null, 2)
  } catch {
    return s
  }
}

const CHANNEL_LABEL: Record<string, string> = {
  EMAIL: "이메일",
  SLACK: "Slack",
  MATTERMOST: "Mattermost",
  WEBHOOK: "Webhook",
  SMS: "SMS",
  IN_APP: "인앱",
}

const STAGE_LABEL: Record<string, string> = {
  SUBMITTED: "상신",
  T_MINUS_30: "D-30",
  T_MINUS_7: "D-7",
  T_MINUS_1: "D-1",
  T_MINUS_1H: "H-1",
  APPLIED: "적용",
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-sm text-foreground">{children}</span>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-md border bg-white p-4">
      <h2 className="mb-3 text-sm font-semibold">{title}</h2>
      {children}
    </div>
  )
}

export default function ChangeRequestDetailPage() {
  const params = useParams<{ id: string }>()
  const router = useRouter()
  const { user } = useAuth()
  const crId = Number(params.id)

  const [cr, setCr] = useState<ChangeRequest | null>(null)
  const [notifications, setNotifications] = useState<NotificationLog[]>([])
  const [datasetName, setDatasetName] = useState<string | null>(null)
  const [userMap, setUserMap] = useState<Record<string, string>>({})
  const [emailMap, setEmailMap] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getChangeRequest(crId)
      setCr(data)
      // 데이터셋 이름은 best-effort 로 조회
      try {
        const ds = await fetchDataset(data.dataset_id)
        setDatasetName(ds.qualified_name || ds.name || null)
      } catch {
        setDatasetName(null)
      }
      // 알림 발송 이력도 best-effort 로 조회
      try {
        setNotifications(await listNotifications(crId))
      } catch {
        setNotifications([])
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "조회 실패")
    } finally {
      setLoading(false)
    }
  }, [crId])

  useEffect(() => {
    if (!Number.isNaN(crId)) void reload()
  }, [crId, reload])

  // 결재자/요청자 username → 표시 이름 매핑 (best-effort)
  useEffect(() => {
    fetchUsers({ pageSize: 0 })
      .then((r) => {
        const m: Record<string, string> = {}
        const e: Record<string, string> = {}
        for (const u of r.items) {
          m[u.username] = userDisplayName(u)
          if (u.email) e[u.email] = userDisplayName(u)
        }
        setUserMap(m)
        setEmailMap(e)
      })
      .catch(() => {})
  }, [])

  const onSubmit = async () => {
    if (!cr) return
    setBusy(true)
    try {
      await submitChangeRequest(cr.id)
      await reload()
    } catch (e) {
      alert(e instanceof Error ? e.message : "결재 상신 실패")
    } finally {
      setBusy(false)
    }
  }

  const onCancel = async () => {
    if (!cr) return
    if (!confirm("이 변경 요청을 취소하시겠습니까?")) return
    setBusy(true)
    try {
      await cancelChangeRequest(cr.id)
      await reload()
    } catch (e) {
      alert(e instanceof Error ? e.message : "취소 실패")
    } finally {
      setBusy(false)
    }
  }

  // 결재 처리 — 대상 단계·결재자는 서버가 인증 사용자/현재 차례로 결정한다.
  const onDecide = async (decision: "APPROVED" | "REJECTED") => {
    if (!cr) return
    let comment: string | undefined
    if (decision === "REJECTED") {
      const reason = window.prompt("반려 사유를 입력하세요 (선택):")
      if (reason === null) return // 취소
      comment = reason.trim() || undefined
    }
    setBusy(true)
    try {
      await submitDecision(cr.id, { decision, comment })
      await reload()
    } catch (e) {
      alert(e instanceof Error ? e.message : "결재 처리 실패")
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <DashboardHeader title="변경 요청 상세" />
      <div className="flex flex-1 flex-col gap-4 p-4">

      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" onClick={() => router.push("/dashboard/changes")}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 목록으로
        </Button>
        {cr && (
          <div className="flex gap-2">
            {cr.status === "DRAFT" && (
              <Button size="sm" onClick={onSubmit} disabled={busy}>
                결재 상신
              </Button>
            )}
            {["DRAFT", "SUBMITTED", "APPROVING", "SCHEDULED"].includes(cr.status) && (
              <Button size="sm" variant="outline" onClick={onCancel} disabled={busy}>
                취소
              </Button>
            )}
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          <AlertTriangle className="h-4 w-4" />
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-sm text-muted-foreground">불러오는 중...</div>
      ) : !cr ? (
        <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
          변경 요청을 찾을 수 없습니다.
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {/* 헤더 */}
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-mono text-xs text-muted-foreground">{cr.cr_code}</span>
            <span className="text-lg font-semibold">{cr.title}</span>
            <StatusBadge status={cr.status} />
            <Badge variant="outline">{CHANGE_TYPE_LABEL[cr.change_type]}</Badge>
            <Badge variant="outline">{PRIORITY_LABEL[cr.priority]}</Badge>
          </div>

          {/* 개요 */}
          <Section title="개요">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Field label="요청자">
                {userMap[cr.requested_by]
                  ? `${userMap[cr.requested_by]} (${cr.requested_by})`
                  : cr.requested_by}
              </Field>
              <Field label="대상 데이터셋">
                {datasetName ?? `#${cr.dataset_id}`}
              </Field>
              <Field label="적용 예정">{fmtDateTime(cr.scheduled_at)}</Field>
              <Field label="적용 완료">{fmtDateTime(cr.applied_at)}</Field>
              <Field label="생성일">{fmtDateTime(cr.created_at)}</Field>
              <Field label="수정일">{fmtDateTime(cr.updated_at)}</Field>
            </div>
          </Section>

          {/* 설명 */}
          {cr.description && (
            <Section title="설명">
              <div className="text-sm text-muted-foreground">
                <RichTextViewer html={cr.description} />
              </div>
            </Section>
          )}

          {/* 결재선 — 현재 차례(가장 낮은 PENDING)이고 그 결재자가 본인일 때만 버튼 노출 */}
          <Section title="결재선">
            {cr.approval_steps.length === 0 ? (
              <p className="text-sm text-muted-foreground">결재선이 없습니다.</p>
            ) : (
              <div className="flex flex-col gap-1">
                {(() => {
                  const pendingOrders = cr.approval_steps
                    .filter((s) => s.decision === "PENDING")
                    .map((s) => s.step_order)
                  const currentOrder = pendingOrders.length ? Math.min(...pendingOrders) : null
                  return cr.approval_steps.map((s) => {
                  const canDecide =
                    cr.status === "APPROVING" &&
                    s.decision === "PENDING" &&
                    s.step_order === currentOrder &&
                    s.approver === user?.username
                  return (
                  <div
                    key={s.id}
                    className="flex items-center justify-between rounded border px-2 py-1.5 text-sm"
                  >
                    <div className="flex items-center gap-2">
                      {s.decision === "APPROVED" ? (
                        <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                      ) : s.decision === "REJECTED" ? (
                        <XCircle className="h-4 w-4 text-rose-600" />
                      ) : s.decision === "PENDING" ? (
                        <Clock className="h-4 w-4 text-amber-600" />
                      ) : (
                        <Circle className="h-4 w-4 text-gray-400" />
                      )}
                      <span className="text-xs text-muted-foreground">#{s.step_order}</span>
                      <span>
                        {userMap[s.approver] ?? s.approver}
                        {userMap[s.approver] && (
                          <span className="ml-1 text-xs text-muted-foreground">({s.approver})</span>
                        )}
                      </span>
                      {s.comment && (
                        <span className="text-xs text-muted-foreground">— {s.comment}</span>
                      )}
                    </div>
                    {canDecide ? (
                      <div className="flex gap-1">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() => onDecide("APPROVED")}
                        >
                          승인
                        </Button>
                        <Button
                          size="sm"
                          variant="destructive"
                          disabled={busy}
                          onClick={() => onDecide("REJECTED")}
                        >
                          반려
                        </Button>
                      </div>
                    ) : s.decided_at ? (
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {fmtDateTime(s.decided_at)}
                      </span>
                    ) : null}
                  </div>
                  )
                })
                })()}
              </div>
            )}
          </Section>

          {/* 참조자 */}
          {cr.referrers && cr.referrers.length > 0 && (
            <Section title="참조자 (CC)">
              <div className="flex flex-wrap gap-1.5">
                {cr.referrers.map((r) => {
                  const name = r.name || (r.email ? emailMap[r.email] : undefined)
                  return (
                    <Badge key={r.id} variant="outline" className="text-xs">
                      {name ?? r.email ?? "-"}
                      {name && r.email && (
                        <span className="ml-1 text-muted-foreground">({r.email})</span>
                      )}
                    </Badge>
                  )
                })}
              </div>
            </Section>
          )}

          {/* 롤백 계획 / 비즈니스 근거 */}
          <Section title="롤백 계획">
            <p className="whitespace-pre-wrap text-sm text-muted-foreground">{cr.rollback_plan}</p>
          </Section>
          <Section title="비즈니스 근거">
            <p className="whitespace-pre-wrap text-sm text-muted-foreground">
              {cr.business_justification}
            </p>
          </Section>

          {/* 영향 분석 리포트 */}
          {cr.impact_report && (
            <Section title="영향 분석">
              <pre className="max-h-80 overflow-auto rounded border bg-muted/30 p-2 text-xs">
                {prettyJson(cr.impact_report)}
              </pre>
            </Section>
          )}

          {/* 알림 발송 이력 */}
          <Section title="알림 발송 이력">
            {notifications.length === 0 ? (
              <p className="text-sm text-muted-foreground">발송된 알림이 없습니다.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-xs text-muted-foreground">
                      <th className="py-1.5 pr-3 font-medium">채널</th>
                      <th className="py-1.5 pr-3 font-medium">단계</th>
                      <th className="py-1.5 pr-3 font-medium">수신처</th>
                      <th className="py-1.5 pr-3 font-medium">상태</th>
                      <th className="py-1.5 pr-3 font-medium">발송 시각</th>
                    </tr>
                  </thead>
                  <tbody>
                    {notifications.map((n) => (
                      <tr key={n.id} className="border-b last:border-0">
                        <td className="py-1.5 pr-3">{CHANNEL_LABEL[n.channel] ?? n.channel}</td>
                        <td className="py-1.5 pr-3">{STAGE_LABEL[n.stage] ?? n.stage}</td>
                        <td className="py-1.5 pr-3 text-muted-foreground">{n.recipient ?? "—"}</td>
                        <td className="py-1.5 pr-3">
                          <Badge
                            className={
                              n.status === "SENT"
                                ? "bg-emerald-200 text-emerald-800"
                                : n.status === "FAILED"
                                  ? "bg-rose-200 text-rose-800"
                                  : "bg-gray-200 text-gray-700"
                            }
                          >
                            {n.status === "SENT" ? "발송됨" : n.status === "FAILED" ? "실패" : n.status}
                          </Badge>
                        </td>
                        <td className="py-1.5 pr-3 text-muted-foreground">{fmtDateTime(n.sent_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
        </div>
      )}
      </div>
    </>
  )
}
