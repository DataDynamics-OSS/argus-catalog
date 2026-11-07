"use client"

import { Fragment, useEffect, useState } from "react"
import { Loader2, Pencil, Save } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"
import { useConfirm } from "@/components/confirm-provider"

import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { Textarea } from "@workspace/ui/components/textarea"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@workspace/ui/components/tooltip"
import { cn } from "@workspace/ui/lib/utils"

import { type ApiDetail, type ApiUpdatePayload, updateApi } from "../api"
import { OwnerPicker, OwnerValue, ownerInfoName } from "./api-owner"
import { API_STATUS_LABEL, CERT_LABEL, SPEC_FORMAT_LABEL, TIER_LABEL, protocolLabel } from "./apis-constants"
import { ComboInput, OPT_API_CATEGORY, TagsInput, fmtDateTime } from "./apis-detail-shared"
import { urlError } from "./apis-form-utils"


// 개요 표 key 별 설명 — 점선 밑줄 + hover 툴팁으로 노출.
const KEY_DESC: Record<string, string> = {
  "이름": "API 의 고유 식별 이름(시스템 내 유일, URN 의 기반)",
  "표시명": "사람이 읽기 좋은 API 표시 이름",
  "버전": "API 버전(예: 1.2.0). 스펙 등록 API 는 스펙에서 자동 추출, 수동 등록 API 는 직접 입력",
  "상태": "초안(draft)·게시(published)·사용 중단(deprecated)·폐기(retired)",
  "설명": "API 의 용도·기능에 대한 요약 설명",
  "프로토콜": "통신 프로토콜(REST 등). 스펙에서 자동 판별",
  "스펙 포맷": "스펙 형식 — OpenAPI 3.x / Swagger 2.0 등(스펙에서 자동 판별)",
  "Base URL": "기본 호출 서버 주소. 스펙의 servers/host 첫 항목에서 자동 추출됩니다(초록). 직접 입력하면(파랑) 수동 값이 우선하고, 비우고 저장하면 스펙의 첫 서버 주소로 되돌아갑니다.",
  "카테고리": "용도별 분류(데이터·결제·인증 등)",
  "소유자": "API 책임자(사용자 관리에 등록된 사용자 중에서 선택, 이메일 직접 입력 불가). 이름에 마우스를 올리면 소속·부서·Username·Email 표시",
  "부서": "API 를 운영·소유하는 부서/팀(소유자 선택 시 자동 입력)",
  "인증 상태": "인증됨(CERTIFIED): 검증된 신뢰 가능 API · 검토 중(IN_REVIEW): 인증 검토 진행 중 · 지원 중단(DEPRECATED): 폐기·대체 예정 · 없음(NONE): 미진행(기본)",
  "등급": "서비스 등급 — 골드(Gold): 공식·고신뢰 · 실버(Silver): 일반 활용 · 브론즈(Bronze): 실험·낮은 보장",
  "태그": "검색·분류용 라벨(여러 개 입력 가능)",
  "비고": "운영·관리에 참고할 자유 메모",
  "엔드포인트 수": "스펙에서 추출된 엔드포인트(메서드+경로) 개수",
  "등록일": "API 등록 일시",
  "수정일": "API 마지막 수정 일시",
}

// key 라벨 — 설명이 있으면 점선 밑줄 + hover 툴팁.
function KeyLabel({ label }: { label: string }) {
  const desc = KEY_DESC[label]
  if (!desc) return <>{label}</>
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-help underline decoration-dotted decoration-muted-foreground/50 underline-offset-2">{label}</span>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-sm whitespace-pre-wrap">{desc}</TooltipContent>
    </Tooltip>
  )
}

// 정보 출처 구분: 파랑=사용자 입력, 초록=자동 수집(스펙 파싱·파생). 의미는 hover title 로 확인.
type Source = "input" | "auto"
function SourceBadge({ source }: { source?: Source }) {
  if (!source) return null
  const isAuto = source === "auto"
  const label = isAuto ? "자동 수집" : "사용자 입력"
  return (
    <span
      title={label}
      aria-label={label}
      className={cn("inline-block h-1.5 w-1.5 shrink-0 rounded-full align-middle", isAuto ? "bg-emerald-500" : "bg-sky-500")}
    />
  )
}

// ---------------------------------------------------------------------------
// 개요 행 정의 · 편집 폼 값
// ---------------------------------------------------------------------------
type Editor = "text" | "textarea" | "cert" | "tier" | "combo" | "owner" | "tags"
type OvRow = { label: string; display: string | null; field?: string; editor?: Editor; source: Source }

function buildRows(api: ApiDetail): OvRow[] {
  const isManual = api.source === "manual"
  return [
    { label: "이름", display: api.name, source: "input" },
    { label: "표시명", display: api.display_name, field: "display_name", editor: "text", source: "input" },
    // 수동 API 는 버전을 직접 입력
    { label: "버전", display: api.version, ...(isManual ? { field: "version", editor: "text" as const } : {}), source: "input" },
    { label: "상태", display: API_STATUS_LABEL[api.status] ?? api.status, source: "input" },
    { label: "설명", display: api.description, field: "description", editor: "textarea", source: "input" },
    { label: "프로토콜", display: protocolLabel(api.protocol), source: "auto" },
    // 스펙 포맷은 스펙 등록 API 에서만 의미 — 수동 API 는 숨김
    ...(isManual ? [] : [{ label: "스펙 포맷", display: api.spec_format ? (SPEC_FORMAT_LABEL[api.spec_format] ?? api.spec_format) : null, source: "auto" as const }]),
    { label: "Base URL", display: api.base_url, field: "base_url", editor: "text", source: api.base_url_overridden === "true" ? "input" : "auto" },
    { label: "카테고리", display: api.category, field: "category", editor: "combo", source: "input" },
    { label: "소유자", display: api.owner_email, field: "owner_email", editor: "owner", source: "input" },
    { label: "부서", display: api.department, field: "department", editor: "text", source: "input" },
    { label: "인증 상태", display: api.certification ? (CERT_LABEL[api.certification] ?? api.certification) : null, field: "certification", editor: "cert", source: "input" },
    { label: "등급", display: api.tier ? (TIER_LABEL[api.tier] ?? api.tier) : null, field: "tier", editor: "tier", source: "input" },
    { label: "태그", display: (api.tags ?? []).join(", "), field: "tags", editor: "tags", source: "input" },
    { label: "비고", display: api.note, field: "note", editor: "textarea", source: "input" },
    ...(isManual ? [
      { label: "계약 문서 URL", display: api.contract_url, field: "contract_url", editor: "text" as const, source: "input" as const },
    ] : []),
    { label: "엔드포인트 수", display: String(api.endpoint_count), source: "auto" },
    { label: "등록일", display: fmtDateTime(api.created_at), source: "auto" },
    { label: "수정일", display: fmtDateTime(api.updated_at), source: "auto" },
  ]
}

type OvForm = Record<string, string>

function formFromApi(api: ApiDetail): OvForm {
  return {
    display_name: api.display_name ?? "", description: api.description ?? "",
    category: api.category ?? "", owner_email: api.owner_email ?? "", department: api.department ?? "",
    certification: api.certification ?? "", tier: api.tier ?? "", base_url: api.base_url ?? "",
    version: api.version ?? "", contract_url: api.contract_url ?? "",
    tags: (api.tags ?? []).join(", "), note: api.note ?? "",
  }
}

// 주소 형식을 검사하는 항목(등록 다이얼로그와 같은 규칙)
const URL_FIELDS = ["base_url", "contract_url"] as const

const tagList = (s: string) => s.split(",").map((t) => t.trim()).filter(Boolean)
const TEXT_FIELDS = ["display_name", "description", "category", "owner_email", "department", "base_url", "note"] as const

/**
 * 편집 전(base)과 달라진 항목만 담은 수정 요청.
 * 바뀌지 않은 값을 보내지 않아야 Base URL 이 "수동 지정"으로 바뀌는 등의 부작용이 없다.
 */
function changedPayload(api: ApiDetail, base: OvForm, form: OvForm): ApiUpdatePayload {
  const val = (k: string) => (form[k] ?? "").trim()
  const changed = (k: string) => val(k) !== (base[k] ?? "").trim()
  const p: Record<string, unknown> = {}
  for (const k of TEXT_FIELDS) if (changed(k)) p[k] = val(k) || null
  for (const k of ["certification", "tier"]) if (changed(k)) p[k] = val(k) || null
  if (tagList(form.tags ?? "").join(",") !== tagList(base.tags ?? "").join(",")) p.tags = tagList(form.tags ?? "")
  if (api.source === "manual") {
    if (changed("version") && val("version")) p.version = val("version")
    if (changed("contract_url")) p.contract_url = val("contract_url") || null
  }
  return p as ApiUpdatePayload
}

// ---------------------------------------------------------------------------
// 개요 탭 — 기본 메타데이터 표 + 인라인 편집
// ---------------------------------------------------------------------------
export function OverviewTab({ api, canEdit, onSaved, onDirtyChange }: {
  api: ApiDetail
  /** 편집 가능(생성자 본인 또는 관리자) — 아니면 편집 버튼을 숨긴다 */
  canEdit: boolean
  onSaved: () => Promise<void>
  /** 저장하지 않은 변경 여부 — 상위에서 목록 이동 시 확인용 */
  onDirtyChange?: (dirty: boolean) => void
}) {
  const askConfirm = useConfirm()
  // 편집 중 소유자 표시명(검색 선택기 버튼 라벨). 기존 미등록 이메일은 "(미등록 사용자)" 표기.
  const [ovOwnerLabel, setOvOwnerLabel] = useState<string | null>(null)
  const [editingOv, setEditingOv] = useState(false)
  const [savingOv, setSavingOv] = useState(false)
  // 편집 시작 시점의 값(base)과 현재 입력값(ov) — 차이만 저장한다.
  const [ovBase, setOvBase] = useState<OvForm>({})
  const [ov, setOv] = useState<OvForm>({})
  const setOvF = (k: string, v: string) => setOv((d) => ({ ...d, [k]: v }))

  const dirty = editingOv && Object.keys(changedPayload(api, ovBase, ov)).length > 0
  const urlErrors: Record<string, string> = {}
  if (editingOv) for (const k of URL_FIELDS) { const e = urlError(ov[k] ?? ""); if (e) urlErrors[k] = e }
  const hasUrlError = Object.keys(urlErrors).length > 0
  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange])

  const startEditOv = () => {
    const f = formFromApi(api)
    setOvBase(f)
    setOv(f)
    setOvOwnerLabel(
      !api.owner_email ? null
        : api.owner ? ownerInfoName(api.owner)
        : `${api.owner_email} (미등록 사용자)`,
    )
    setEditingOv(true)
  }
  const cancelEditOv = async () => {
    if (dirty && !(await askConfirm({
      title: "변경 내용 버리기",
      description: "저장하지 않은 변경 사항을 버릴까요?",
      confirmText: "버리기",
      destructive: true,
    }))) return
    setEditingOv(false)
  }
  const handleSaveOv = async () => {
    if (hasUrlError) { toast.error("주소 형식이 올바르지 않은 항목이 있습니다."); return }
    const payload = changedPayload(api, ovBase, ov)
    if (Object.keys(payload).length === 0) {
      setEditingOv(false)
      toast.info("변경된 내용이 없습니다.")
      return
    }
    setSavingOv(true)
    try {
      await updateApi(api.name, payload)
    } catch (e) {
      notifyError(e, "개요를 저장하지 못했습니다.")
      setSavingOv(false)
      return
    }
    setEditingOv(false)
    toast.success("개요를 저장했습니다.")
    // 저장 후 새로고침 실패는 저장 실패와 구분해 안내
    try { await onSaved() }
    catch { toast.warning("저장했지만 화면을 새로고침하지 못했습니다. 다시 열어 확인하세요.") }
    finally { setSavingOv(false) }
  }

  const renderEditor = (r: OvRow & { field: string }) => {
    switch (r.editor) {
      case "textarea":
        return <Textarea className="min-h-16 text-sm" value={ov[r.field] ?? ""} onChange={(e) => setOvF(r.field, e.target.value)} />
      case "owner":
        // 소유자는 등록 사용자 검색 선택만(직접 입력 불가). 선택 시 부서 자동 입력.
        // 기존 미등록 이메일은 바꾸지 않으면 그대로 저장되며, 변경 권장 안내를 표시.
        return (
          <div className="flex w-full flex-col gap-1">
            <OwnerPicker
              size="sm"
              value={ov.owner_email ?? ""}
              label={ovOwnerLabel}
              onChange={(p) => {
                if (!p) { setOvF("owner_email", ""); setOvOwnerLabel(null); return }
                setOv((d) => ({ ...d, owner_email: p.email, department: p.department ?? d.department ?? "" }))
                setOvOwnerLabel(p.label)
              }}
            />
            {ov.owner_email && ov.owner_email === api.owner_email && !api.owner && (
              <span className="text-xs text-muted-foreground">사용자 관리에 없는 이메일입니다. 등록된 사용자로 변경하는 것을 권장합니다.</span>
            )}
          </div>
        )
      case "tags":
        return <TagsInput value={ov[r.field] ?? ""} onChange={(v) => setOvF(r.field, v)} />
      case "combo":
        return <ComboInput value={ov[r.field] ?? ""} onChange={(v) => setOvF(r.field, v)} options={OPT_API_CATEGORY} />
      case "cert":
      case "tier": {
        const options = r.editor === "cert" ? ["NONE", "CERTIFIED", "IN_REVIEW", "DEPRECATED"] : ["GOLD", "SILVER", "BRONZE"]
        const labels = r.editor === "cert" ? CERT_LABEL : TIER_LABEL
        return (
          <Select value={ov[r.field] || "none"} onValueChange={(v) => setOvF(r.field, v === "none" ? "" : v)}>
            <SelectTrigger className="h-8 w-full text-sm"><SelectValue placeholder="미지정" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="none" className="text-muted-foreground">미지정</SelectItem>
              {options.map((o) => <SelectItem key={o} value={o}>{labels[o] ?? o}</SelectItem>)}
            </SelectContent>
          </Select>
        )
      }
      default: {
        const err = urlErrors[r.field]
        return (
          <div className="flex w-full flex-col gap-1">
            <Input className="h-8 w-full text-sm" aria-invalid={err ? true : undefined} value={ov[r.field] ?? ""} onChange={(e) => setOvF(r.field, e.target.value)} />
            {err && <span className="text-[11px] text-destructive">{err}</span>}
          </div>
        )
      }
    }
  }

  const renderVal = (r: OvRow) => {
    if (editingOv && r.field) return renderEditor(r as OvRow & { field: string })
    if (r.editor === "owner") return <OwnerValue email={api.owner_email} owner={api.owner} />
    return r.display === null || r.display === undefined || r.display === "" ? "-" : r.display
  }

  const rows = buildRows(api)
  const pairs: (OvRow | undefined)[][] = []
  for (let i = 0; i < rows.length; i += 2) pairs.push([rows[i], rows[i + 1]])

  return (
    <>
      <div className="mb-4 flex items-start justify-between gap-2">
        <div className="space-y-4">
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full bg-sky-500" /> 사용자 입력</span>
            <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500" /> 자동 수집</span>
          </div>
          <p className="text-sm text-muted-foreground">
            API 의 식별·소유, 분류, 버전·상태, 인증 등급 등 기본 메타데이터를 정리한 화면입니다.
          </p>
        </div>
        {canEdit && (
          <div className="flex shrink-0 gap-2">
            {editingOv ? (
              <>
                <Button size="sm" variant="outline" disabled={savingOv} onClick={() => { void cancelEditOv() }}>취소</Button>
                <Button size="sm" disabled={savingOv || hasUrlError} onClick={handleSaveOv}>{savingOv ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}저장</Button>
              </>
            ) : (
              <Button size="sm" variant="outline" onClick={startEditOv}><Pencil className="mr-1 h-4 w-4" /> 편집</Button>
            )}
          </div>
        )}
      </div>
      <TooltipProvider delayDuration={150}>
        <div className="overflow-hidden rounded-md border">
          <table className="w-full table-fixed border-collapse text-sm">
            <colgroup><col className="w-[140px]" /><col /><col className="w-[140px]" /><col /></colgroup>
            <tbody>
              {pairs.map((pair, idx) => (
                <tr key={idx}>
                  {[0, 1].map((j) => {
                    const r = pair[j]
                    if (!r) return <Fragment key={j}><th className="border bg-muted/50" /><td className="border" /></Fragment>
                    return (
                      <Fragment key={r.label}>
                        <th className="border bg-muted/50 px-3 py-2 text-left align-middle font-medium text-black">
                          <span className="inline-flex flex-wrap items-center gap-1"><KeyLabel label={r.label} /><SourceBadge source={r.source} /></span>
                        </th>
                        <td className="border px-3 py-2 align-middle break-words">{renderVal(r)}</td>
                      </Fragment>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </TooltipProvider>
    </>
  )
}
