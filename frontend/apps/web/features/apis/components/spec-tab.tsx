"use client"

import { useState } from "react"
import { AlertTriangle, Loader2, Pencil, Save, Upload } from "lucide-react"
import { errorMessage, notifyError, toast } from "@/lib/notify"
import { useConfirm } from "@/components/confirm-provider"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"

import { type ApiDetail, updateApi, uploadApiSpec } from "../api"
import { CodeEditor, CodeViewer, detectContractKind, fmtDateTime } from "./apis-detail-shared"
import { CONTRACT_FILE_ACCEPT, FileLoadButton, urlError } from "./apis-form-utils"
import {
  EMPTY_SPEC_SOURCE, type SpecSource, SpecPreviewSummary, SpecSourceInput, needsBaseUrl,
  specSourceHasInput, specSourceMissingMessage, specSourcePayload, specSourceUrlError, useSpecPreview,
} from "./spec-source-input"

// ---------------------------------------------------------------------------
// 스펙 새 버전 등록 — 붙여넣기·파일·URL(등록 다이얼로그와 같은 공용 입력·미리보기).
// 미리보기에서 버전·엔드포인트 수를 현재와 비교한다.
// ---------------------------------------------------------------------------
function SpecUploadDialog({ api, open, onOpenChange, onUploaded }: {
  api: ApiDetail
  open: boolean
  onOpenChange: (open: boolean) => void
  onUploaded: () => Promise<void>
}) {
  const askConfirm = useConfirm()
  const [spec, setSpec] = useState<SpecSource>(EMPTY_SPEC_SOURCE)
  const preview = useSpecPreview(spec, open)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const urlErr = specSourceUrlError(spec)
  const hasInput = specSourceHasInput(spec)
  const dirty = !!(spec.text.trim() || spec.url.trim())
  // 같은 버전 번호는 등록할 수 없다((API, 버전) 유일) — 미리보기에서 미리 막는다
  const dupVersion = preview.data ? (api.specs.find((x) => x.version === (preview.data!.version || api.version))?.version ?? null) : null

  const close = () => { onOpenChange(false); setSpec(EMPTY_SPEC_SOURCE); preview.reset(); setError(null) }
  const requestClose = async () => {
    if (saving) return
    if (dirty && !(await askConfirm({
      title: "입력 내용 버리기",
      description: "입력한 내용이 사라집니다. 닫을까요?",
      confirmText: "닫기",
      destructive: true,
    }))) return
    close()
  }

  const submit = async () => {
    setError(null)
    if (!hasInput) { setError(specSourceMissingMessage(spec)); return }
    if (urlErr) { setError(urlErr); return }
    if (dupVersion) { setError(`이미 등록된 버전(v${dupVersion})입니다. 스펙의 info.version 을 올려 주세요.`); return }
    setSaving(true)
    try {
      await uploadApiSpec(api.name, specSourcePayload(spec))
    } catch (e) {
      setError(errorMessage(e) ?? "스펙을 등록하지 못했습니다.")
      setSaving(false)
      return
    }
    toast.success("새 스펙 버전을 등록했습니다.")
    close()
    try { await onUploaded() }
    catch { toast.warning("등록했지만 화면을 새로고침하지 못했습니다.") }
    finally { setSaving(false) }
  }

  const baseOverridden = api.base_url_overridden === "true" && !!api.base_url
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) void requestClose() }}>
      <DialogContent className="flex max-h-[90vh] max-w-2xl flex-col">
        <DialogHeader>
          <DialogTitle>스펙 새 버전 등록</DialogTitle>
          <DialogDescription>
            현재 v{api.version} · 엔드포인트 {api.endpoint_count}개. 새 스펙을 등록하면 엔드포인트·서버·인증 정보가 새 스펙 기준으로 바뀌고,
            이전 버전과 비교해 Breaking 변경이 있으면 상단에 알림이 표시됩니다. 이전 버전은 이력 탭에서 비교할 수 있습니다.
          </DialogDescription>
        </DialogHeader>
        <div className="-mx-6 min-h-0 flex-1 space-y-3 overflow-y-auto px-6 pb-1">
          <SpecSourceInput
            value={spec}
            onChange={setSpec}
            onError={setError}
            preview={preview}
            urlSuggestion={api.specs[0]?.source_url}
            textPlaceholder="OpenAPI 2.0(Swagger) / 3.x 스펙(JSON 또는 YAML)"
          />
          <SpecPreviewSummary preview={preview}>
            {(p) => (
              <>
                <div className="text-muted-foreground">
                  버전: v{api.version} → <span className="font-medium text-foreground">v{p.version}</span>
                  {dupVersion && <span className="text-destructive"> — 이미 등록된 버전입니다. 스펙의 info.version 을 올려야 등록할 수 있습니다.</span>}
                </div>
                <div className="text-muted-foreground">
                  엔드포인트: {api.endpoint_count}개 → <span className="font-medium text-foreground">{p.endpoint_count}개</span>
                  {p.endpoint_count < api.endpoint_count && <span className="text-amber-600"> (줄어듦 — Breaking 변경일 수 있습니다)</span>}
                </div>
                {baseOverridden ? (
                  <div className="text-muted-foreground">Base URL: 직접 지정한 <span className="font-mono">{api.base_url}</span> 이 그대로 유지됩니다.</div>
                ) : needsBaseUrl(p) ? (
                  <div className="text-muted-foreground">등록 후 개요 탭의 편집에서 Base URL 을 입력할 수 있습니다.</div>
                ) : null}
              </>
            )}
          </SpecPreviewSummary>
        </div>
        <div className="space-y-3 border-t pt-3">
          {error && (
            <div role="alert" className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><span className="break-words">{error}</span>
            </div>
          )}
          <DialogFooter className="mt-0">
            <Button variant="outline" onClick={() => { void requestClose() }} disabled={saving}>취소</Button>
            <Button onClick={() => void submit()} disabled={saving || !hasInput || !!urlErr || !!dupVersion}>
              {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Upload className="mr-1 h-4 w-4" />}등록
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function ContractKindBadge({ label }: { label: string }) {
  return <Badge variant="outline" className="h-5 px-1.5 text-[11px] font-normal">{label}</Badge>
}

// ---------------------------------------------------------------------------
// 수동 API 계약 문서 — 보기 / 편집(URL·원문, 파일 불러오기)
// ---------------------------------------------------------------------------
function ContractSection({ api, canEdit, onChanged }: { api: ApiDetail; canEdit: boolean; onChanged: () => Promise<void> }) {
  const askConfirm = useConfirm()
  const [editing, setEditing] = useState(false)
  const [url, setUrl] = useState("")
  const [text, setText] = useState("")
  const [saving, setSaving] = useState(false)
  const urlErr = urlError(url)
  const baseUrl = api.contract_url ?? ""
  const baseText = api.contract_text ?? ""
  const dirty = editing && (url.trim() !== baseUrl.trim() || text !== baseText)
  // 문법 하이라이트 언어 — 편집 중엔 입력 내용, 보기에선 저장된 원문 기준(본문 우선, 안 되면 프로토콜)
  const kind = detectContractKind(editing ? text : baseText, api.protocol)

  const start = () => { setUrl(baseUrl); setText(baseText); setEditing(true) }
  const cancel = async () => {
    if (dirty && !(await askConfirm({
      title: "변경 내용 버리기",
      description: "저장하지 않은 변경 사항을 버릴까요?",
      confirmText: "버리기",
      destructive: true,
    }))) return
    setEditing(false)
  }
  const save = async () => {
    if (urlErr) { toast.error(`계약 문서 URL: ${urlErr}`); return }
    const payload: { contract_url?: string | null; contract_text?: string | null } = {}
    if (url.trim() !== baseUrl.trim()) payload.contract_url = url.trim() || null
    if (text !== baseText) payload.contract_text = text.trim() ? text : null
    if (Object.keys(payload).length === 0) { setEditing(false); toast.info("변경된 내용이 없습니다."); return }
    setSaving(true)
    try { await updateApi(api.name, payload) }
    catch (e) { notifyError(e, "계약 문서를 저장하지 못했습니다."); setSaving(false); return }
    setEditing(false)
    toast.success("계약 문서를 저장했습니다.")
    try { await onChanged() }
    catch { toast.warning("저장했지만 화면을 새로고침하지 못했습니다.") }
    finally { setSaving(false) }
  }

  return (
    <>
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          수동 등록 API 의 계약/스키마 문서(SDL/WSDL/.proto/AsyncAPI 등)입니다.
        </p>
        {canEdit && (
          <div className="flex shrink-0 gap-2">
            {editing ? (
              <>
                <Button size="sm" variant="outline" disabled={saving} onClick={() => { void cancel() }}>취소</Button>
                <Button size="sm" disabled={saving || !!urlErr} onClick={() => void save()}>
                  {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}저장
                </Button>
              </>
            ) : (
              <Button size="sm" variant="outline" onClick={start}><Pencil className="mr-1 h-4 w-4" /> 편집</Button>
            )}
          </div>
        )}
      </div>
      {editing ? (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="api-contract-url">계약 문서 URL (선택)</Label>
            <Input id="api-contract-url" aria-invalid={urlErr ? true : undefined} placeholder="SDL/WSDL/.proto/AsyncAPI 문서 URL" value={url} onChange={(e) => setUrl(e.target.value)} />
            {urlErr && <p className="text-[11px] text-destructive">{urlErr}</p>}
          </div>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Label>계약 문서 원문 (선택)</Label>
                {text.trim() && <ContractKindBadge label={kind.label} />}
              </div>
              <FileLoadButton accept={CONTRACT_FILE_ACCEPT} onLoad={(t) => setText(t)} onError={(m) => toast.error(m)} />
            </div>
            <CodeEditor
              language={kind.language}
              value={text}
              onChange={setText}
              ariaLabel="계약 문서 원문"
              placeholder="SDL / WSDL / .proto / AsyncAPI 원문을 붙여넣기"
              height={320}
              minHeight={160}
              maxHeight="70vh"
            />
          </div>
        </div>
      ) : (
        <>
          {api.contract_url && (
            <p className="text-sm">문서 URL:{" "}
              {/* 서버도 http(s) 만 받지만, 이전에 저장된 값 대비 http(s) 일 때만 링크로 렌더 */}
              {/^https?:\/\//i.test(api.contract_url)
                ? <a href={api.contract_url} target="_blank" rel="noreferrer" className="break-all text-primary hover:underline">{api.contract_url}</a>
                : <span className="break-all">{api.contract_url}</span>}
            </p>
          )}
          {api.contract_text && (
            <div className="flex items-center gap-2">
              <p className="text-xs text-muted-foreground">계약 문서</p>
              <ContractKindBadge label={kind.label} />
            </div>
          )}
          <CodeViewer text={api.contract_text || null} language={kind.language} emptyLabel={canEdit ? "계약 문서가 없습니다. 편집 버튼으로 입력하세요." : "계약 문서가 없습니다."} lineNumbers scroll />
        </>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// 원본 탭 — 스펙(JSON) 또는 수동 등록 API 의 계약 문서
// ---------------------------------------------------------------------------
export function SpecTab({ api, canEdit, onChanged }: {
  api: ApiDetail
  /** 스펙 새 버전 등록·계약 문서 편집 가능(생성자 본인 또는 관리자) */
  canEdit: boolean
  onChanged: () => Promise<void>
}) {
  const [uploadOpen, setUploadOpen] = useState(false)
  if (api.source === "manual") return <ContractSection api={api} canEdit={canEdit} onChanged={onChanged} />

  const cur = api.specs.find((s) => s.is_current === "true") ?? api.specs[0]
  return (
    <>
      <div className="flex items-start justify-between gap-2">
        <div className="space-y-1">
          <p className="text-sm text-muted-foreground">
            현재 OpenAPI/Swagger 스펙의 원본(JSON)입니다. 줄 번호·접기를 지원하며 depth 4 이상은 자동으로 접혀 있습니다(거터 화살표로 펼치거나 접을 수 있습니다). 우상단 버튼으로 전체를 클립보드에 복사할 수 있습니다.
          </p>
          {cur && (
            <p className="text-xs text-muted-foreground">
              현재 버전 v{cur.version} · {fmtDateTime(cur.created_at)} 등록{cur.created_by ? ` (${cur.created_by})` : ""}
              {cur.source_url && <> · 출처 <a href={cur.source_url} target="_blank" rel="noreferrer" className="break-all text-primary hover:underline">{cur.source_url}</a></>}
              {api.specs.length > 1 && ` · 전체 ${api.specs.length}개 버전(이력 탭에서 비교)`}
            </p>
          )}
        </div>
        {canEdit && (
          <Button size="sm" variant="outline" className="shrink-0" onClick={() => setUploadOpen(true)}>
            <Upload className="mr-1 h-4 w-4" /> 새 버전 등록
          </Button>
        )}
      </div>
      <CodeViewer
        text={api.raw_spec ? (() => { try { return JSON.stringify(JSON.parse(api.raw_spec!), null, 2) } catch { return api.raw_spec } })() : null}
        emptyLabel="원본 스펙이 없습니다."
        lineNumbers
        scroll
      />
      {canEdit && <SpecUploadDialog api={api} open={uploadOpen} onOpenChange={setUploadOpen} onUploaded={onChanged} />}
    </>
  )
}
