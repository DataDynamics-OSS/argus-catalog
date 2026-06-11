"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import type { Editor } from "@tiptap/react"
import { ArrowLeft, Plus, Table as TableIcon, Trash2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import { Textarea } from "@workspace/ui/components/textarea"
import { DashboardHeader } from "@/components/dashboard-header"
import { CommentEditor } from "@/components/comments/comment-editor"
import {
  type ChangeType,
  type Priority,
  createChangeRequest,
} from "@/features/change-mgmt/api"
import { fetchDataset, fetchDatasets } from "@/features/datasets/api"
import { type DatasetSummary } from "@/features/datasets/data/schema"
import { fetchUsers } from "@/features/users/api"
import { type User } from "@/features/users/data/schema"

const userDisplayName = (u: User) => `${u.lastName ?? ""}${u.firstName ?? ""}`.trim() || u.username

const CHANGE_TYPES: ChangeType[] = ["BREAKING", "NON_BREAKING", "ADDITIVE", "COSMETIC"]
const PRIORITIES: Priority[] = ["EMERGENCY", "HIGH", "NORMAL", "LOW"]
const CHANGE_TYPE_LABEL: Record<ChangeType, string> = {
  BREAKING: "호환성 깨짐 (Breaking)",
  NON_BREAKING: "호환 유지 (Non-breaking)",
  ADDITIVE: "추가 (Additive)",
  COSMETIC: "표기·메타데이터 (Cosmetic)",
}
const PRIORITY_LABEL: Record<Priority, string> = {
  EMERGENCY: "긴급 (Emergency)",
  HIGH: "높음 (High)",
  NORMAL: "보통 (Normal)",
  LOW: "낮음 (Low)",
}

type SelectedDataset = { id: number; display: string }

// URN(`{datasource}.{db}[.{schema}].{table}.dataset`) → "데이터소스명 (유형) - DB.SCHEMA.TABLE"(DB부터 대문자).
function selectDataset(d: DatasetSummary): SelectedDataset {
  const core = (d.urn || "").replace(/\.dataset$/, "")
  const parts = core.split(".").filter(Boolean)
  const path = parts.slice(1)                       // datasource id 제외 → db[.schema].table
  const upperPath = (path.length ? path : [d.name]).map((p) => p.toUpperCase()).join(".")
  const platform = `${d.datasource_name}${d.datasource_type ? ` (${d.datasource_type})` : ""}`
  return { id: d.id, display: upperPath ? `${platform} - ${upperPath}` : platform }
}

const EMPTY_FORM = {
  title: "",
  description: "",
  change_type: "NON_BREAKING" as ChangeType,
  priority: "NORMAL" as Priority,
  rollback_plan: "",
  business_justification: "",
  scheduled_at: "",
}

export default function NewChangeRequestPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [form, setForm] = useState(EMPTY_FORM)
  const [dsSearch, setDsSearch] = useState("")
  const [dsResults, setDsResults] = useState<DatasetSummary[]>([])
  const [dataset, setDataset] = useState<SelectedDataset | null>(null)
  // 데이터셋 상세에서 "변경 요청"으로 진입한 경우 대상이 잠긴다 (?dataset_id=)
  const [datasetLocked, setDatasetLocked] = useState(false)
  const [steps, setSteps] = useState<{ approver: string }[]>([{ approver: "" }])
  const [referrers, setReferrers] = useState<{ name: string; email: string }[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [saving, setSaving] = useState(false)
  const editorRef = useRef<Editor | null>(null)
  const [insertingSchema, setInsertingSchema] = useState(false)

  useEffect(() => {
    fetchUsers({ pageSize: 0 }).then((r) => setUsers(r.items)).catch(() => {})
  }, [])

  // ?dataset_id= 가 있으면 대상 데이터셋을 프리필하고 잠근다 (데이터셋 상세 진입점)
  useEffect(() => {
    const raw = searchParams.get("dataset_id")
    const id = raw ? Number(raw) : NaN
    if (!Number.isFinite(id)) return
    fetchDataset(id)
      .then((d) => {
        const core = (d.urn || "").replace(/\.dataset$/, "")
        const parts = core.split(".").filter(Boolean)
        const path = parts.slice(1)
        const upperPath = (path.length ? path : [d.name]).map((p) => p.toUpperCase()).join(".")
        const platform = `${d.datasource.name}${d.datasource.type ? ` (${d.datasource.type})` : ""}`
        setDataset({ id: d.id, display: upperPath ? `${platform} - ${upperPath}` : platform })
        setDatasetLocked(true)
      })
      .catch(() => {})
  }, [searchParams])

  const goList = () => router.push("/dashboard/changes")
  // 뒤로/취소 — 데이터셋 상세 등 진입한 화면으로 돌아간다.
  // 히스토리가 없으면(직접 URL 진입) 변경 요청 목록으로 폴백.
  const goBack = () => {
    if (window.history.length > 1) router.back()
    else goList()
  }

  // 선택한 데이터셋의 현재 스키마를 표로 만들어 설명 편집기에 삽입.
  const insertSchemaTable = async () => {
    if (!dataset) { alert("먼저 대상 데이터셋을 선택하세요."); return }
    if (!editorRef.current) { alert("편집기가 아직 준비되지 않았습니다. 잠시 후 다시 시도하세요."); return }
    setInsertingSchema(true)
    try {
      const detail = await fetchDataset(dataset.id)
      const fields = detail.schema_fields ?? []
      if (fields.length === 0) { alert("이 데이터셋에 스키마 필드가 없습니다."); return }
      const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      const head = "<tr><th>컬럼</th><th>타입</th><th>Null</th><th>키</th><th>설명</th></tr>"
      const body = fields
        .map((f) => {
          const key = f.is_primary_key === "true" ? "PK" : (f.is_unique === "true" ? "UQ" : "")
          return `<tr><td>${esc(f.field_path)}</td><td>${esc(f.field_type)}</td><td>${f.nullable === "true" ? "Y" : "N"}</td><td>${key}</td><td>${esc(f.description || "")}</td></tr>`
        })
        .join("")
      const table = `<p><strong>${esc(dataset.display)} 스키마</strong></p><table><tbody>${head}${body}</tbody></table><p></p>`
      editorRef.current.chain().focus().insertContent(table).run()
    } catch (e) {
      alert(e instanceof Error ? e.message : "스키마 불러오기 실패")
    } finally {
      setInsertingSchema(false)
    }
  }

  const runDsSearch = async (q: string) => {
    setDsSearch(q)
    if (!q.trim()) { setDsResults([]); return }
    const r = await fetchDatasets({ search: q, pageSize: 10 }).catch(() => null)
    setDsResults(r?.items ?? [])
  }

  const onCreate = async () => {
    if (!form.title.trim() || !dataset || !form.rollback_plan.trim() || !form.business_justification.trim()) {
      alert("필수 항목(제목·대상 데이터셋·롤백 계획·업무 사유)을 입력하세요.")
      return
    }
    setSaving(true)
    try {
      const approval_chain = steps
        .filter((s) => s.approver.trim())
        .map((s, i) => ({ step_order: i + 1, approver: s.approver.trim() }))
      const refPayload = referrers
        .filter((r) => r.email.trim())
        .map((r) => ({
          name: r.name.trim() || undefined,
          email: r.email.trim() || undefined,
          channel: "EMAIL" as const,
        }))
      // 요청자는 서버가 인증 사용자에서 도출한다
      await createChangeRequest({
        title: form.title.trim(),
        description: form.description.trim() || undefined,
        dataset_id: dataset.id,
        change_type: form.change_type,
        priority: form.priority,
        rollback_plan: form.rollback_plan.trim(),
        business_justification: form.business_justification.trim(),
        scheduled_at: form.scheduled_at || undefined,
        approval_chain,
        referrers: refPayload.length > 0 ? refPayload : undefined,
      })
      goList()
    } catch (e) {
      alert(e instanceof Error ? e.message : "변경 요청 생성 실패")
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <DashboardHeader title="변경 요청 생성" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <Button variant="ghost" size="sm" className="w-fit -ml-2" onClick={goBack}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 뒤로
        </Button>

        <div className="grid max-w-3xl gap-4">
          {/* 제목 */}
          <div className="grid gap-1.5">
            <Label className="text-sm">제목 <span className="text-destructive">*</span></Label>
            <Input value={form.title} onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))} placeholder="예: 고객 테이블 컬럼 추가" className="h-9 text-sm" />
          </div>

          {/* 대상 데이터셋 */}
          <div className="grid gap-1.5">
            <Label className="text-sm">대상 데이터셋 <span className="text-destructive">*</span></Label>
            {dataset ? (
              <div className="flex items-center gap-2 rounded border px-3 py-1.5 text-sm">
                <span className="font-medium">{dataset.display}</span>
                {/* 데이터셋 상세에서 진입(잠금)한 경우 대상 변경 불가 */}
                {!datasetLocked && (
                  <Button size="sm" variant="ghost" className="ml-auto h-6" onClick={() => setDataset(null)}>변경</Button>
                )}
              </div>
            ) : (
              <>
                <Input value={dsSearch} onChange={(e) => runDsSearch(e.target.value)} placeholder="데이터셋 검색..." className="h-9 text-sm" />
                {dsResults.length > 0 && (
                  <div className="flex max-h-40 flex-col gap-1 overflow-y-auto rounded border p-1">
                    {dsResults.map((d) => (
                      <button
                        key={d.id} type="button"
                        className="flex items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-muted"
                        onClick={() => { setDataset(selectDataset(d)); setDsResults([]); setDsSearch("") }}
                      >
                        <span className={`truncate font-medium ${d.display_name ? "" : "uppercase"}`}>{d.display_name || d.name}</span>
                        <span className="shrink-0 text-xs text-muted-foreground">{d.datasource_name}</span>
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>

          {/* 변경 유형 / 우선순위 */}
          <div className="grid grid-cols-2 gap-4">
            <div className="grid gap-1.5">
              <Label className="text-sm">변경 유형</Label>
              <Select value={form.change_type} onValueChange={(v) => setForm((f) => ({ ...f, change_type: v as ChangeType }))}>
                <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>{CHANGE_TYPES.map((t) => <SelectItem key={t} value={t} className="text-sm">{CHANGE_TYPE_LABEL[t]}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label className="text-sm">우선순위</Label>
              <Select value={form.priority} onValueChange={(v) => setForm((f) => ({ ...f, priority: v as Priority }))}>
                <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>{PRIORITIES.map((p) => <SelectItem key={p} value={p} className="text-sm">{PRIORITY_LABEL[p]}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          </div>

          {/* 변경 유형·우선순위 도움말(접기) */}
          <details className="rounded-md border bg-muted/20 p-3">
            <summary className="cursor-pointer text-xs font-medium text-muted-foreground">변경 유형·우선순위 도움말 (펼치기)</summary>
            <div className="mt-2 space-y-3 text-xs text-muted-foreground">
              <div>
                <p className="font-medium text-foreground">변경 유형 — 소비자 영향도</p>
                <ul className="ml-4 list-disc space-y-0.5">
                  <li><b>호환성 깨짐 (Breaking)</b> — 기존 소비자가 깨지는 비호환 변경(컬럼 삭제·타입 변경·필수화 등). 결재·통지 가장 강하게.</li>
                  <li><b>호환 유지 (Non-breaking)</b> — 동작 호환을 유지하는 변경(기본값 변경, 길이 확장 등 기존 사용에 영향 없음).</li>
                  <li><b>추가 (Additive)</b> — 순수 추가(선택 컬럼/필드 신설). 기존 소비자 미영향.</li>
                  <li><b>표기·메타데이터 (Cosmetic)</b> — 스키마 무관(설명·태그·표기 변경). 스키마 입력 불필요.</li>
                </ul>
              </div>
              <div>
                <p className="font-medium text-foreground">우선순위 — 처리 시급도</p>
                <ul className="ml-4 list-disc space-y-0.5">
                  <li><b>긴급 (Emergency)</b> — 장애·규제 대응 등 즉시 처리. 신속 결재·즉시 통지.</li>
                  <li><b>높음 (High)</b> — 빠른 처리 필요. 가까운 일정에 적용.</li>
                  <li><b>보통 (Normal)</b> — 일반 변경 일정(기본).</li>
                  <li><b>낮음 (Low)</b> — 여유 있는 처리. 정기 배포에 묶어 진행.</li>
                </ul>
              </div>
            </div>
          </details>

          {/* 설명 — 데이터셋 "상세 설명"과 동일한 리치 편집기(CommentEditor: 서식·색상·이모지) */}
          <div className="grid gap-1.5">
            <div className="flex items-center justify-between">
              <Label className="text-sm">설명</Label>
              <Button size="sm" variant="outline" className="h-7" onClick={insertSchemaTable} disabled={!dataset || insertingSchema}>
                <TableIcon className="mr-1 h-3.5 w-3.5" /> {insertingSchema ? "불러오는 중..." : "스키마 추가"}
              </Button>
            </div>
            <CommentEditor
              showCategory={false}
              hideActions
              placeholder="변경 내용을 작성하세요"
              onReady={(ed) => { editorRef.current = ed }}
              onChange={(html) => setForm((f) => ({ ...f, description: html }))}
              onSubmit={async () => {}}
            />
          </div>

          {/* 롤백 계획 / 업무 사유 */}
          <div className="grid gap-1.5">
            <Label className="text-sm">롤백 계획 <span className="text-destructive">*</span></Label>
            <Textarea value={form.rollback_plan} onChange={(e) => setForm((f) => ({ ...f, rollback_plan: e.target.value }))} placeholder="실패 시 되돌리는 방법" className="min-h-[60px] text-sm" />
          </div>
          <div className="grid gap-1.5">
            <Label className="text-sm">업무 사유 <span className="text-destructive">*</span></Label>
            <Textarea value={form.business_justification} onChange={(e) => setForm((f) => ({ ...f, business_justification: e.target.value }))} placeholder="변경이 필요한 비즈니스 사유" className="min-h-[60px] text-sm" />
          </div>

          {/* 적용 예정 시각 */}
          <div className="grid gap-1.5">
            <Label className="text-sm">적용 예정 시각</Label>
            <Input type="datetime-local" value={form.scheduled_at} onChange={(e) => setForm((f) => ({ ...f, scheduled_at: e.target.value }))} className="h-9 w-60 text-sm" />
          </div>

          {/* 결재선 */}
          <div className="grid gap-1.5">
            <Label className="text-sm">결재선 (순서대로)</Label>
            <div className="flex flex-col gap-2">
              {steps.map((s, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="w-6 shrink-0 text-center text-xs text-muted-foreground">#{i + 1}</span>
                  <Select value={s.approver || "none"} onValueChange={(v) => setSteps((arr) => arr.map((x, j) => (j === i ? { ...x, approver: v === "none" ? "" : v } : x)))}>
                    <SelectTrigger className="h-8 w-[200px] text-sm"><SelectValue placeholder="승인자 선택" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none" className="text-muted-foreground">미지정</SelectItem>
                      {users.map((u) => <SelectItem key={u.id} value={u.username}>{userDisplayName(u)} ({u.username})</SelectItem>)}
                    </SelectContent>
                  </Select>
                  <Button size="sm" variant="ghost" className="h-8 w-8 shrink-0 p-0 text-destructive" onClick={() => setSteps((arr) => (arr.length > 1 ? arr.filter((_, j) => j !== i) : arr))} disabled={steps.length <= 1} aria-label="결재자 삭제">
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
              <Button size="sm" variant="outline" className="self-start" onClick={() => setSteps((arr) => [...arr, { approver: "" }])}>
                <Plus className="mr-1 h-4 w-4" /> 결재자 추가
              </Button>
            </div>
          </div>

          {/* 참조자(CC) — 결재권 없이 통지만. 생성 시 이메일(우선)/Slack·Mattermost 통지 */}
          <div className="grid gap-1.5">
            <Label className="text-sm">참조자 (CC) — 생성 시 통지</Label>
            <p className="text-xs text-muted-foreground">
              결재권 없이 변경 내용을 통지받습니다. 통지 채널은 「설정 &gt; 변경관리」에서 전역으로 설정합니다.
            </p>
            <div className="flex flex-col gap-2">
              {referrers.map((r, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Select
                    value={r.email || "none"}
                    onValueChange={(v) => setReferrers((arr) => arr.map((x, j) => {
                      if (j !== i) return x
                      if (v === "none") return { ...x, name: "", email: "" }
                      const u = users.find((y) => y.email === v)
                      return { ...x, email: v, name: u ? userDisplayName(u) : x.name }
                    }))}
                  >
                    <SelectTrigger className="h-8 w-[200px] text-sm"><SelectValue placeholder="참조자 선택" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none" className="text-muted-foreground">미지정</SelectItem>
                      {users.filter((u) => u.email).map((u) => <SelectItem key={u.id} value={u.email}>{userDisplayName(u)} ({u.username})</SelectItem>)}
                    </SelectContent>
                  </Select>
                  <Button size="sm" variant="ghost" className="h-8 w-8 shrink-0 p-0 text-destructive" onClick={() => setReferrers((arr) => arr.filter((_, j) => j !== i))} aria-label="참조자 삭제">
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
              <Button size="sm" variant="outline" className="self-start" onClick={() => setReferrers((arr) => [...arr, { name: "", email: "" }])}>
                <Plus className="mr-1 h-4 w-4" /> 참조자 추가
              </Button>
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={goBack} disabled={saving}>취소</Button>
            <Button onClick={onCreate} disabled={saving}>{saving ? "생성 중..." : "생성"}</Button>
          </div>
        </div>
      </div>
    </>
  )
}
