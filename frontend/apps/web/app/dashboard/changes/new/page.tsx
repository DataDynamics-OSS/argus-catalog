"use client"

import { datasourceTypeLabel } from "@/features/datasources/datasource-configs"
import { Tip } from "@/components/tip"
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
  type Referrer,
  createChangeRequest,
  getChangeRequest,
  updateChangeRequest,
} from "@/features/change-mgmt/api"
import {
  type SchemaRow,
  SchemaChangeEditor,
  buildSchemaChange,
  rowsFromFields,
  rowsWithSpec,
  suggestChangeType,
} from "@/features/change-mgmt/schema-change"
import { fetchDataset, fetchDatasets } from "@/features/datasets/api"
import { type DatasetSummary } from "@/features/datasets/data/schema"
import { fetchUsers } from "@/features/users/api"
import { type User } from "@/features/users/data/schema"
import { useConfirm } from "@/components/confirm-provider"
import { FieldError } from "@/components/field-error"
import { errorMessage, notifyError, notifySuccess, toast } from "@/lib/notify"
import { usePermissions } from "@/features/permissions/use-permissions"
import { SearchInput } from "@/components/search-input"

const userDisplayName = (u: User) => `${u.lastName ?? ""}${u.firstName ?? ""}`.trim() || u.username

const CHANGE_TYPES: ChangeType[] = ["BREAKING", "NON_BREAKING", "ADDITIVE", "COSMETIC"]
const PRIORITIES: Priority[] = ["EMERGENCY", "HIGH", "NORMAL", "LOW"]
const CHANGE_TYPE_LABEL: Record<ChangeType, string> = {
  BREAKING: "호환성 깨짐 (Breaking)",
  NON_BREAKING: "호환 유지 (Non-breaking)",
  ADDITIVE: "추가 (Additive)",
  // 기록용 — 승인·적용해도 데이터셋을 바꾸지 않는다(DEF-065)
  COSMETIC: "표기·메타데이터 — 기록용 (Cosmetic)",
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
  const platform = `${d.datasource_name}${d.datasource_type ? ` (${datasourceTypeLabel(d.datasource_type)})` : ""}`
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
  const askConfirm = useConfirm()
  const searchParams = useSearchParams()
  const [form, setForm] = useState(EMPTY_FORM)
  const [dsSearch, setDsSearch] = useState("")
  const [dsResults, setDsResults] = useState<DatasetSummary[]>([])
  const [dataset, setDataset] = useState<SelectedDataset | null>(null)
  // 데이터셋 상세에서 "변경 요청"으로 진입한 경우 대상이 잠긴다 (?dataset_id=)
  const [datasetLocked, setDatasetLocked] = useState(false)
  const [steps, setSteps] = useState<{ approver: string }[]>([{ approver: "" }])
  const [referrers, setReferrers] = useState<{ name: string; email: string }[]>([])
  // 초안 수정(?edit=<id>) — 작성 중(DRAFT) 변경 요청을 같은 양식으로 고친다(TODO-009)
  const editId = Number(searchParams.get("edit")) || null
  // 작성 · 초안 수정은 기능 권한 changes.create 가 있어야 한다(서버도 403) — 없으면 양식 대신 안내
  const perms = usePermissions()
  const canCreate = perms.canUseFeature("changes.create")
  const [editLoaded, setEditLoaded] = useState(!editId)
  const [editError, setEditError] = useState<string | null>(null)
  // 수정 때 저장할 변경 명세 — 편집기 행이 준비되면 한 번 입힌다
  const pendingSpecRef = useRef<string | null>(null)
  // 이 화면에서 고를 수 없는 참조자(Slack 등) — 수정 때 그대로 유지
  const [otherReferrers, setOtherReferrers] = useState<Referrer[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [saving, setSaving] = useState(false)
  const editorRef = useRef<Editor | null>(null)
  const [insertingSchema, setInsertingSchema] = useState(false)
  // 검증 오류 — 필수 입력란별 문구(입력란 아래), 스키마 표 삽입 버튼 오류, 저장 시 스키마 명세 오류를 버튼 옆에 요약할지
  const [reqErrors, setReqErrors] = useState<Record<string, string>>({})
  const clearReqError = (k: string) => setReqErrors((e) => { if (!(k in e)) return e; const n = { ...e }; delete n[k]; return n })
  const [insertError, setInsertError] = useState<string | null>(null)
  const [schemaBlocked, setSchemaBlocked] = useState(false)
  // 스키마 변경 명세 — 대상 데이터셋의 현재 스키마를 불러와 편집한다(COSMETIC 은 미사용).
  const [schemaRows, setSchemaRows] = useState<SchemaRow[]>([])
  const [baseRows, setBaseRows] = useState<SchemaRow[]>([])
  const [schemaLoading, setSchemaLoading] = useState(false)
  const [schemaError, setSchemaError] = useState<string | null>(null)

  useEffect(() => {
    fetchUsers({ pageSize: 0 }).then((r) => setUsers(r.items)).catch(() => {})
  }, [])

  // 초안 수정 — 저장된 내용으로 양식을 채운다. 대상 데이터셋은 고정
  useEffect(() => {
    if (!editId) return
    getChangeRequest(editId)
      .then((cr) => {
        if (cr.status !== "DRAFT") { setEditError("작성 중(DRAFT)인 변경 요청만 수정할 수 있습니다."); return }
        const local = (iso?: string | null) => {
          if (!iso) return ""
          const d = new Date(iso)
          const pad = (n: number) => String(n).padStart(2, "0")
          return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
        }
        setForm({
          title: cr.title,
          description: cr.description ?? "",
          change_type: cr.change_type,
          priority: cr.priority,
          rollback_plan: cr.rollback_plan,
          business_justification: cr.business_justification,
          scheduled_at: local(cr.scheduled_at),
        })
        setDataset({ id: cr.dataset_id, display: cr.dataset_name ?? `#${cr.dataset_id}` })
        setDatasetLocked(true)
        setSteps(cr.approval_steps.length > 0
          ? [...cr.approval_steps].sort((a, b) => a.step_order - b.step_order).map((st) => ({ approver: st.approver }))
          : [{ approver: "" }])
        setReferrers(cr.referrers.filter((r) => r.email).map((r) => ({ name: r.name ?? "", email: r.email ?? "" })))
        setOtherReferrers(cr.referrers.filter((r) => !r.email))
        pendingSpecRef.current = cr.schema_after ?? null
        setEditLoaded(true)
      })
      .catch((e) => setEditError(errorMessage(e) ?? "변경 요청을 불러오지 못했습니다."))
  }, [editId])

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
        const platform = `${d.datasource.name}${d.datasource.type ? ` (${datasourceTypeLabel(d.datasource.type)})` : ""}`
        setDataset({ id: d.id, display: upperPath ? `${platform} - ${upperPath}` : platform })
        setDatasetLocked(true)
      })
      .catch(() => {})
  }, [searchParams])

  // 대상 데이터셋이 바뀌면 현재 스키마를 불러와 편집기를 초기화한다.
  const datasetId = dataset?.id
  useEffect(() => {
    setSchemaRows([]); setBaseRows([]); setSchemaError(null)
    if (datasetId == null) return
    let alive = true
    setSchemaLoading(true)
    fetchDataset(datasetId)
      .then((d) => {
        if (!alive) return
        const rows = rowsFromFields(
          [...(d.schema_fields ?? [])]
            .sort((a, b) => a.ordinal - b.ordinal)
            // 데이터셋 상세 스키마 표와 같은 속성(TODO-132) — 예전에는 이름 · 타입 · NULL 만 읽었다
            .map((f) => ({
              field_path: f.field_path, field_type: f.field_type, nullable: f.nullable === "true",
              native_type: f.native_type ?? "", is_indexed: f.is_indexed === "true",
              is_primary_key: f.is_primary_key === "true", is_unique: f.is_unique === "true",
              is_partition_key: f.is_partition_key === "true", is_distribution_key: f.is_distribution_key === "true",
            })),
        )
        setBaseRows(rows)
        // 초안 수정이면 저장된 변경 명세를 지금 스키마 위에 다시 입힌다
        setSchemaRows(pendingSpecRef.current ? rowsWithSpec(rows, pendingSpecRef.current) : rows)
        pendingSpecRef.current = null
      })
      .catch((e) => alive && setSchemaError(errorMessage(e) ?? "스키마를 불러오지 못했습니다."))
      .finally(() => alive && setSchemaLoading(false))
    return () => { alive = false }
  }, [datasetId])

  const isCosmetic = form.change_type === "COSMETIC"
  const schemaChange = buildSchemaChange(schemaRows)
  const suggestedType = suggestChangeType(schemaRows)

  const goList = () => router.push("/dashboard/changes")
  // 저장 뒤 상세로 — 폼 화면은 히스토리에서 바꿔치워 상세의 '뒤로' 가 진입 화면(데이터셋 상세 등)으로 간다.
  // submit=1 이면 상세에서 상신 창을 바로 연다.
  const goDetail = (id: number, submit: boolean) =>
    router.replace(`/dashboard/changes/${id}${submit ? "?submit=1" : ""}`)
  // 뒤로/취소 — 데이터셋 상세 등 진입한 화면으로 돌아간다.
  // 히스토리가 없으면(직접 URL 진입) 변경 요청 목록으로 폴백.
  const goBack = () => {
    if (window.history.length > 1) router.back()
    else goList()
  }

  // 선택한 데이터셋의 현재 스키마를 표로 만들어 설명 편집기에 삽입.
  const insertSchemaTable = async () => {
    if (!dataset) { setInsertError("먼저 대상 데이터셋을 선택하세요."); return }
    setInsertError(null)
    if (!editorRef.current) { toast.warning("편집기가 아직 준비되지 않았습니다. 잠시 후 다시 시도하세요."); return }
    setInsertingSchema(true)
    try {
      const detail = await fetchDataset(dataset.id)
      const fields = detail.schema_fields ?? []
      if (fields.length === 0) { toast.warning("이 데이터셋에 스키마 필드가 없습니다."); return }
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
      notifyError(e, "스키마를 불러오지 못했습니다.")
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

  const onCreate = async (submit = false) => {
    // 필수 항목 — 빈 입력란마다 아래에 "필수 항목입니다." 를 보이고 첫 빈 입력란으로 포커스
    const missing: [string, string][] = []
    if (!form.title.trim()) missing.push(["title", "cr-title"])
    if (!dataset) missing.push(["dataset", "cr-dataset-search"])
    if (!form.rollback_plan.trim()) missing.push(["rollback_plan", "cr-rollback-plan"])
    if (!form.business_justification.trim()) missing.push(["business_justification", "cr-business-justification"])
    setReqErrors(Object.fromEntries(missing.map(([k]) => [k, "필수 항목입니다."])))
    if (missing.length > 0 || !dataset) {
      setSchemaBlocked(false)
      document.getElementById(missing[0]?.[1] ?? "")?.focus()
      return
    }
    // 스키마 변경 명세(schema_after) — 승인 후 워커가 이 내용을 데이터셋 스키마에 적용한다.
    let schema_after: string | undefined
    if (!isCosmetic) {
      // 명세 오류는 스키마 편집기 아래에 늘 보이고, 저장 버튼 옆에도 요약한다
      if (schemaChange.error) { setSchemaBlocked(true); return }
      setSchemaBlocked(false)
      if (schemaChange.spec) {
        // schema_before 는 서버가 생성 시점의 데이터셋 스키마로 자동 채운다.
        schema_after = JSON.stringify(schemaChange.spec)
      } else if (!(await askConfirm({
        title: "스키마 변경 없이 생성",
        description: "스키마 변경 내용이 없습니다. 승인 후 적용해도 스키마는 바뀌지 않습니다. 그대로 생성할까요?",
        confirmText: "만들기",
      }))) {
        return
      }
    }
    setSaving(true)
    try {
      const approval_chain = steps
        .filter((s) => s.approver.trim())
        .map((s, i) => ({ step_order: i + 1, approver: s.approver.trim() }))
      const refPayload = [
        ...referrers
          .filter((r) => r.email.trim())
          .map((r) => ({
            name: r.name.trim() || undefined,
            email: r.email.trim() || undefined,
            channel: "EMAIL" as const,
          })),
        ...otherReferrers.map((r) => ({
          name: r.name ?? undefined, channel: r.channel, slack_target: r.slack_target ?? undefined,
        })),
      ]
      // 요청자는 서버가 인증 사용자에서 도출한다
      const payload = {
        title: form.title.trim(),
        description: form.description.trim() || undefined,
        dataset_id: dataset.id,
        change_type: form.change_type,
        priority: form.priority,
        rollback_plan: form.rollback_plan.trim(),
        business_justification: form.business_justification.trim(),
        // datetime-local 값은 시간대가 없다 — 그대로 보내면 서버가 UTC 로 받아 KST 기준 9시간 늦게
        // 적용된다. 브라우저 로컬 시각으로 해석해 UTC ISO 문자열(…Z)로 보낸다.
        scheduled_at: form.scheduled_at ? new Date(form.scheduled_at).toISOString() : undefined,
        schema_after,
        approval_chain,
        referrers: refPayload.length > 0 ? refPayload : (editId ? [] : undefined),
      }
      const saved = editId ? await updateChangeRequest(editId, payload) : await createChangeRequest(payload)
      notifySuccess(`변경 요청 '${saved?.cr_code || form.title.trim()}' 을(를) ${editId ? "수정했" : "만들었"}습니다.`)
      // 예전에는 전체 목록으로 가 다시 찾아 열어야 상신할 수 있었다(TODO-077)
      goDetail(saved.id, submit)
    } catch (e) {
      notifyError(e, editId ? "변경 요청을 수정하지 못했습니다." : "변경 요청을 생성하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <DashboardHeader title={editId ? "변경 요청 편집" : "변경 요청 만들기"} />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <Button variant="ghost" size="sm" className="w-fit -ml-2" onClick={goBack}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 뒤로
        </Button>

        {perms.loaded && !canCreate ? (
          <p className="text-sm text-muted-foreground">변경 요청을 작성할 권한이 없습니다. 관리자에게 권한을 요청하세요.</p>
        ) : editError ? (
          <p className="text-sm text-destructive">{editError}</p>
        ) : !editLoaded ? (
          <p className="text-sm text-muted-foreground">불러오는 중…</p>
        ) : (
        <div className="grid max-w-3xl gap-4">
          {/* 제목 */}
          <div className="grid gap-1.5">
            <Label className="text-sm">제목 <span className="text-destructive">*</span></Label>
            <Input id="cr-title" value={form.title} aria-invalid={!!reqErrors.title || undefined} onChange={(e) => { setForm((f) => ({ ...f, title: e.target.value })); clearReqError("title") }} placeholder="예: 고객 테이블 컬럼 추가" className="h-9 text-sm" />
            <FieldError>{reqErrors.title}</FieldError>
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
                <SearchInput id="cr-dataset-search" value={dsSearch} aria-invalid={!!reqErrors.dataset || undefined} onValueChange={runDsSearch} placeholder="데이터셋 검색…" />
                {dsResults.length > 0 && (
                  <div className="flex max-h-40 flex-col gap-1 overflow-y-auto rounded border p-1">
                    {dsResults.map((d) => (
                      <button
                        key={d.id} type="button"
                        className="flex items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-muted"
                        onClick={() => { setDataset(selectDataset(d)); setDsResults([]); setDsSearch(""); clearReqError("dataset"); setInsertError(null) }}
                      >
                        <span className={`truncate font-medium ${d.display_name ? "" : "uppercase"}`}>{d.display_name || d.name}</span>
                        <span className="shrink-0 text-xs">{d.datasource_name}</span>
                      </button>
                    ))}
                  </div>
                )}
                <FieldError>{reqErrors.dataset}</FieldError>
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
            <div className="mt-2 space-y-3 text-xs">
              <div>
                <p className="font-medium text-foreground">변경 유형 — 소비자 영향도</p>
                <ul className="ml-4 list-disc space-y-0.5">
                  <li><b>호환성 깨짐 (Breaking)</b> — 기존 소비자가 깨지는 비호환 변경(컬럼 삭제·타입 변경·필수화 등). 결재·통지 가장 강하게.</li>
                  <li><b>호환 유지 (Non-breaking)</b> — 동작 호환을 유지하는 변경(기본값 변경, 길이 확장 등 기존 사용에 영향 없음).</li>
                  <li><b>추가 (Additive)</b> — 순수 추가(선택 컬럼/필드 신설). 기존 소비자 미영향.</li>
                  <li><b>표기·메타데이터 — 기록용 (Cosmetic)</b> — 설명·태그·표기처럼 스키마와 무관한 변경을 결재·통지 기록으로만 남깁니다. 자동 적용은 없으므로 실제 수정은 편집 권한자가 데이터셋 화면에서 직접 합니다. 스키마 입력 불필요.</li>
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

          {/* COSMETIC 은 기록용 — 적용 단계에서 아무것도 바꾸지 않는다는 것을 미리 알린다(DEF-065) */}
          {isCosmetic && (
            <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              기록용 변경 요청입니다 — 승인·적용해도 데이터셋은 자동으로 바뀌지 않습니다.
              설명·태그·표기 수정은 편집 권한자가 데이터셋 화면에서 직접 하고, 이 요청에는 결재·통지 기록만 남깁니다.
            </p>
          )}

          {/* 스키마 변경 — 승인 후 적용될 내용. COSMETIC 은 스키마와 무관하므로 숨김 */}
          {dataset && !isCosmetic && (
            <div className="grid gap-1.5">
              <Label className="text-sm">스키마 변경</Label>
              <p className="text-xs">
                현재 스키마에서 타입·NULL 을 바꾸거나 컬럼을 추가·삭제하세요. 승인되면 이 변경이 데이터셋 스키마에 적용됩니다.
              </p>
              {schemaLoading ? (
                <p className="text-sm text-muted-foreground">스키마 불러오는 중…</p>
              ) : schemaError ? (
                <p className="text-sm text-destructive">{schemaError}</p>
              ) : (
                <SchemaChangeEditor rows={schemaRows} onChange={setSchemaRows} onReset={() => setSchemaRows(baseRows)} />
              )}
              <FieldError>{schemaChange.error}</FieldError>
              {suggestedType && suggestedType !== form.change_type && (
                <p className="text-xs text-amber-600 dark:text-amber-400">
                  변경 내용상 「{CHANGE_TYPE_LABEL[suggestedType]}」 유형으로 보입니다.{" "}
                  <button
                    type="button"
                    className="underline underline-offset-2"
                    onClick={() => setForm((f) => ({ ...f, change_type: suggestedType }))}
                  >
                    변경 유형 바꾸기
                  </button>
                </p>
              )}
            </div>
          )}

          {/* 설명 — 데이터셋 "상세 설명"과 동일한 리치 편집기(CommentEditor: 서식·색상·이모지) */}
          <div className="grid gap-1.5">
            <div className="flex items-center justify-between">
              <Label className="text-sm">설명</Label>
              <Button size="sm" variant="outline" className="h-7" onClick={insertSchemaTable} disabled={!dataset || insertingSchema}>
                <TableIcon className="mr-1 h-3.5 w-3.5" /> {insertingSchema ? "불러오는 중…" : "스키마 추가"}
              </Button>
            </div>
            <FieldError className="text-right">{dataset ? null : insertError}</FieldError>
            <CommentEditor
              showCategory={false}
              hideActions
              placeholder="변경 내용을 작성하세요"
              initialContent={form.description || undefined}
              onReady={(ed) => { editorRef.current = ed }}
              onChange={(html) => setForm((f) => ({ ...f, description: html }))}
              onSubmit={async () => {}}
            />
          </div>

          {/* 롤백 계획 / 업무 사유 */}
          <div className="grid gap-1.5">
            <Label className="text-sm">롤백 계획 <span className="text-destructive">*</span></Label>
            <Textarea id="cr-rollback-plan" value={form.rollback_plan} aria-invalid={!!reqErrors.rollback_plan || undefined} onChange={(e) => { setForm((f) => ({ ...f, rollback_plan: e.target.value })); clearReqError("rollback_plan") }} placeholder="실패 시 되돌리는 방법" className="min-h-[60px] text-sm" />
            <FieldError>{reqErrors.rollback_plan}</FieldError>
          </div>
          <div className="grid gap-1.5">
            <Label className="text-sm">업무 사유 <span className="text-destructive">*</span></Label>
            <Textarea id="cr-business-justification" value={form.business_justification} aria-invalid={!!reqErrors.business_justification || undefined} onChange={(e) => { setForm((f) => ({ ...f, business_justification: e.target.value })); clearReqError("business_justification") }} placeholder="변경이 필요한 비즈니스 사유" className="min-h-[60px] text-sm" />
            <FieldError>{reqErrors.business_justification}</FieldError>
          </div>

          {/* 적용 예정 시각 */}
          <div className="grid gap-1.5">
            <Label className="text-sm">적용 예정 시각</Label>
            <Input type="datetime-local" value={form.scheduled_at} onChange={(e) => setForm((f) => ({ ...f, scheduled_at: e.target.value }))} className="h-9 w-60 text-sm" />
          </div>

          {/* 결재선 */}
          <div className="grid gap-1.5">
            <Label className="text-sm">결재선 (순서대로)</Label>
            <p className="text-sm">
              결재선을 비우면 영향 분석 뒤 승인 없이 바로 적용됩니다. 이 데이터셋을 직접 편집할 권한(생성자·관리자)이 없으면
              본인이 아닌 결재자를 1명 이상 지정해야 합니다.
            </p>
            <div className="flex flex-col gap-2">
              {steps.map((s, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="w-6 shrink-0 text-center text-xs">#{i + 1}</span>
                  <Select value={s.approver || "none"} onValueChange={(v) => setSteps((arr) => arr.map((x, j) => (j === i ? { ...x, approver: v === "none" ? "" : v } : x)))}>
                    <SelectTrigger className="h-8 w-[200px] text-sm"><SelectValue placeholder="승인자 선택" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none" className="text-muted-foreground">미지정</SelectItem>
                      {users.map((u) => <SelectItem key={u.id} value={u.username}>{userDisplayName(u)} ({u.username})</SelectItem>)}
                    </SelectContent>
                  </Select>
                  <Tip label="결재자 삭제"><Button size="sm" variant="ghost" className="h-8 w-8 shrink-0 p-0 text-destructive" onClick={() => setSteps((arr) => (arr.length > 1 ? arr.filter((_, j) => j !== i) : arr))} disabled={steps.length <= 1} aria-label="결재자 삭제">
                    <Trash2 className="h-4 w-4" />
                  </Button></Tip>
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
            <p className="text-xs">
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
                  <Tip label="참조자 삭제"><Button size="sm" variant="ghost" className="h-8 w-8 shrink-0 p-0 text-destructive" onClick={() => setReferrers((arr) => arr.filter((_, j) => j !== i))} aria-label="참조자 삭제">
                    <Trash2 className="h-4 w-4" />
                  </Button></Tip>
                </div>
              ))}
              <Button size="sm" variant="outline" className="self-start" onClick={() => setReferrers((arr) => [...arr, { name: "", email: "" }])}>
                <Plus className="mr-1 h-4 w-4" /> 참조자 추가
              </Button>
            </div>
          </div>

          {/* 저장 버튼에서 위쪽 입력란 오류가 보이지 않을 수 있어 요약한다 */}
          <FieldError className="text-right">
            {Object.keys(reqErrors).length > 0
              ? "필수 항목(제목·대상 데이터셋·롤백 계획·업무 사유)을 입력하세요."
              : schemaBlocked && !isCosmetic ? schemaChange.error : null}
          </FieldError>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={goBack} disabled={saving}>취소</Button>
            <Button variant="outline" onClick={() => onCreate(false)} disabled={saving}>
              {saving ? "저장 중…" : editId ? "저장" : "만들기"}
            </Button>
            <Button onClick={() => onCreate(true)} disabled={saving}>
              {editId ? "저장하고 상신" : "만들고 상신"}
            </Button>
          </div>
        </div>
        )}
      </div>
    </>
  )
}
