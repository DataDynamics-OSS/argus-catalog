"use client"

import { useCallback, useEffect, useState } from "react"
import { AlertTriangle, CheckCircle2, Info, Loader2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { Tabs, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { Textarea } from "@workspace/ui/components/textarea"

import { useConfirm } from "@/components/confirm-provider"
import { useAuth } from "@/features/auth/auth-context"
import { checkApiName, createApi } from "../api"
import { OwnerPicker, ownerDisplayName, type OwnerPick } from "./api-owner"
import { MANUAL_PROTOCOLS } from "./apis-constants"
import { ComboInput, OPT_API_CATEGORY, TagsInput } from "./apis-detail-shared"
import { CONTRACT_FILE_ACCEPT, FileLoadButton, urlError } from "./apis-form-utils"
import { useApis } from "./apis-provider"
import {
  EMPTY_SPEC_SOURCE, type SpecSource, SpecPreviewSummary, SpecSourceInput, needsBaseUrl,
  specSourceHasInput, specSourceMissingMessage, specSourcePayload, specSourceUrlError, useSpecPreview,
} from "./spec-source-input"
import { errorMessage } from "@/lib/notify"

// API 식별자 규칙 — 백엔드 검증(API_NAME_RE)·자동 생성(slugify)과 동일하게 영문·숫자·점·하이픈·언더스코어.
// URN(`<name>.api`)·URL 경로에 사용.
const NAME_PATTERN = /^[A-Za-z0-9._-]+$/
const NAME_MAX = 255

function nameError(name: string): string | null {
  const n = name.trim()
  if (!n) return null
  if (n.length > NAME_MAX) return `최대 ${NAME_MAX}자까지 입력할 수 있습니다.`
  if (!NAME_PATTERN.test(n)) return "영문·숫자·점(.)·하이픈(-)·언더스코어(_)만 사용할 수 있습니다."
  return null
}

const NAME_CHECK_DEBOUNCE_MS = 400
// 저장된 자격증명은 서버 주소·Base URL 로만 전송된다(호출 시 SSRF·유출 방지) — Base URL 안내 문구
const BASE_URL_CRED_HINT = "저장된 자격증명은 이 주소로 호출할 때만 쓰입니다. 비워 두면 호출(Try-it)에 자격증명을 쓸 수 없습니다."

export function ApisAddDialog() {
  const { open, setOpen, refreshApis, setSelectedApiName } = useApis()
  const { user: me } = useAuth()
  const askConfirm = useConfirm()
  const isOpen = open === "add"
  const [regMode, setRegMode] = useState<"spec" | "manual">("spec")
  // 스펙 입력(붙여넣기·파일·URL) — 상세의 "스펙 새 버전 등록"과 같은 공용 입력
  const [spec, setSpec] = useState<SpecSource>(EMPTY_SPEC_SOURCE)
  // 스펙에 서버 주소가 없거나 상대 경로일 때 직접 지정하는 Base URL
  const [specBaseUrl, setSpecBaseUrl] = useState("")
  const [name, setName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [description, setDescription] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [version, setVersion] = useState("")
  const [protocol, setProtocol] = useState<string>("REST")
  const [contractUrl, setContractUrl] = useState("")
  const [contractText, setContractText] = useState("")
  // 소유자 — undefined 면 "아직 고르지 않음"으로 로그인 사용자를 기본값으로 쓴다(null = 사용자가 미지정을 선택).
  const [ownerPick, setOwnerPick] = useState<OwnerPick | null | undefined>(undefined)
  const [category, setCategory] = useState("")
  const [tags, setTags] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 식별자 중복 사전 확인 결과 — 확인한 이름과 함께 보관해, 입력이 바뀌면 자동으로 "확인 중"으로 본다.
  const [nameCheck, setNameCheck] = useState<{ name: string; result: "available" | "taken" | "error" } | null>(null)

  // 기본 소유자 = 로그인 사용자(등록자가 곧 소유자인 경우가 대부분). 부서도 함께 채운다.
  const defaultOwner: OwnerPick | null = me?.email
    ? { email: me.email, label: ownerDisplayName({ lastName: me.last_name, firstName: me.first_name, username: me.username }), department: me.department || null }
    : null
  const owner = ownerPick === undefined ? defaultOwner : ownerPick

  const isSpec = regMode === "spec"
  const nameErr = nameError(name)
  const trimmedName = name.trim()
  const nameStatus: "idle" | "checking" | "available" | "taken" | "error" =
    !trimmedName || nameErr ? "idle" : nameCheck?.name === trimmedName ? nameCheck.result : "checking"

  // 스펙 미리보기 — 붙여넣기·파일은 자동, URL 은 "불러와서 확인"으로
  const preview = useSpecPreview(spec, isOpen && isSpec)
  const previewReset = preview.reset
  // 스펙 모드 Base URL 입력 — 미리보기에서 서버 주소가 없거나 상대 경로로 확인됐을 때(또는 이미 입력했을 때) 표시
  const showSpecBaseUrl = isSpec && (!!specBaseUrl.trim() || (!!preview.data && needsBaseUrl(preview.data)))

  // URL 형식 — 지금 화면에 보이는 입력만 검사
  const specUrlErr = isSpec ? specSourceUrlError(spec) : null
  const baseUrlErr = !isSpec ? urlError(baseUrl) : showSpecBaseUrl ? urlError(specBaseUrl) : null
  const contractUrlErr = !isSpec && protocol !== "REST" ? urlError(contractUrl) : null
  const hasUrlErr = !!(specUrlErr || baseUrlErr || contractUrlErr)

  useEffect(() => {
    if (!isOpen || !trimmedName || nameErr) return
    let active = true
    const timer = setTimeout(() => {
      checkApiName(trimmedName)
        .then((ok) => { if (active) setNameCheck({ name: trimmedName, result: ok ? "available" : "taken" }) })
        .catch(() => { if (active) setNameCheck({ name: trimmedName, result: "error" }) })
    }, NAME_CHECK_DEBOUNCE_MS)
    return () => { active = false; clearTimeout(timer) }
  }, [isOpen, trimmedName, nameErr])

  const reset = useCallback(() => {
    setRegMode("spec"); setSpec(EMPTY_SPEC_SOURCE); setSpecBaseUrl("")
    setName(""); setDisplayName(""); setDescription(""); setBaseUrl(""); setVersion(""); setProtocol("REST")
    setContractUrl(""); setContractText("")
    setOwnerPick(undefined); setCategory(""); setTags(""); setError(null)
    setNameCheck(null)
    previewReset()
  }, [previewReset])

  const close = useCallback(() => { setOpen(null); reset() }, [setOpen, reset])

  // 입력한 내용이 있는지 — 실수로 닫을 때(ESC·바깥 클릭·취소) 확인용
  const dirty = !!(spec.text.trim() || spec.url.trim() || specBaseUrl.trim() || name.trim() || displayName.trim() || description.trim()
    || baseUrl.trim() || version.trim() || contractUrl.trim() || contractText.trim() || category.trim() || tags.trim()
    || ownerPick !== undefined)

  // 사용자가 닫으려 할 때 — 저장 중에는 닫지 않고(결과를 볼 수 있게), 입력이 있으면 확인 후 닫는다.
  const requestClose = async () => {
    if (saving) return
    if (dirty && !(await askConfirm({
      title: "등록 취소",
      description: "입력한 내용이 사라집니다. 등록을 취소할까요?",
      confirmText: "버리기",
      destructive: true,
    }))) return
    close()
  }

  const submit = useCallback(async () => {
    setError(null)
    if (regMode === "spec") {
      if (!specSourceHasInput(spec)) { setError(specSourceMissingMessage(spec)); return }
    } else if (!name.trim()) {
      setError("수동 등록 시 API 식별자는 필수입니다.")
      return
    }
    if (nameErr) { setError(`API 식별자: ${nameErr}`); return }
    if (nameStatus === "taken") { setError(`이미 존재하는 API 식별자입니다: ${trimmedName}`); return }
    if (hasUrlErr) { setError("주소 형식이 올바르지 않은 항목이 있습니다."); return }
    setSaving(true)
    try {
      const tagList = tags.split(",").map((t) => t.trim()).filter(Boolean)
      // 공통 메타데이터 — 스펙 등록에서 표시명·설명을 비우면 스펙의 title/description 을 사용(백엔드).
      const common = {
        display_name: displayName.trim() || undefined,
        description: description.trim() || undefined,
        // 소유자를 고르면 그 사용자의 부서를 함께 저장(개요 편집과 동일). 미지정이면 둘 다 비움.
        owner_email: owner?.email || undefined,
        department: owner?.department || undefined,
        category: category.trim() || undefined,
        tags: tagList.length > 0 ? tagList : undefined,
      }
      // 계약 문서는 REST 가 아닐 때만 화면에 보이므로 그때만 보낸다(프로토콜을 바꾸면 숨겨진 값이 저장되지 않게).
      const withContract = protocol !== "REST"
      const created = await createApi(
        regMode === "spec"
          ? {
              ...common,
              name: name.trim() || undefined,
              ...specSourcePayload(spec),
              base_url: showSpecBaseUrl ? specBaseUrl.trim() || undefined : undefined,
            }
          : {
              ...common,
              name: name.trim(),
              base_url: baseUrl.trim() || undefined,
              version: version.trim() || undefined,
              protocol,
              contract_url: withContract ? contractUrl.trim() || undefined : undefined,
              contract_text: withContract ? contractText.trim() || undefined : undefined,
            },
      )
      close()
      await refreshApis()
      setSelectedApiName(created.name)
    } catch (e) {
      setError(errorMessage(e) ?? "API를 등록하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }, [regMode, spec, showSpecBaseUrl, specBaseUrl, name, nameErr, nameStatus, trimmedName, hasUrlErr, displayName, description, baseUrl, version, protocol, contractUrl, contractText, owner, category, tags, close, refreshApis, setSelectedApiName])

  const fieldError = (msg: string | null) => msg && <p className="text-[11px] text-destructive">{msg}</p>

  // 두 모드 공통 필드 — 식별자·표시명·설명
  const identityFields = (
    <>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="api-add-name">API 식별자 {isSpec ? "(선택, 미지정 시 스펙 제목으로 자동 생성)" : "(필수)"}</Label>
          <Input
            id="api-add-name"
            placeholder={isSpec ? "예: payments-api" : "예: billing-api"}
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={nameErr || nameStatus === "taken" ? true : undefined}
            aria-describedby="api-add-name-hint"
          />
          <p id="api-add-name-hint" className="text-[11px]">
            {nameErr ? <span className="text-destructive">{nameErr}</span>
              : nameStatus === "checking" ? <span className="inline-flex items-center gap-1 text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" /> 중복 확인 중...</span>
              : nameStatus === "taken" ? <span className="text-destructive">이미 존재하는 식별자입니다.</span>
              : nameStatus === "available" ? <span className="inline-flex items-center gap-1 text-emerald-600"><CheckCircle2 className="h-3 w-3" /> 사용할 수 있는 식별자입니다.</span>
              : nameStatus === "error" ? <span className="text-muted-foreground">중복 여부를 확인하지 못했습니다(등록 시 다시 확인).</span>
              : <span className="text-muted-foreground">영문·숫자·점(.)·하이픈(-)·언더스코어(_)만 사용</span>}
          </p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="api-add-display-name">표시명 (선택)</Label>
          <Input id="api-add-display-name" placeholder={isSpec ? "비우면 스펙의 title 사용" : "예: 결제 API"} value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="api-add-description">설명 (선택)</Label>
        <Textarea
          id="api-add-description"
          placeholder={isSpec ? "비우면 스펙의 description 사용" : "API 용도·기능 요약"}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="min-h-16 text-sm"
        />
      </div>
    </>
  )

  // 두 모드 공통 필드 — 카테고리·소유자·태그 (카테고리는 개요 편집과 같은 프리셋 + 사용자 입력)
  const classificationFields = (
    <>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="api-add-category">카테고리 (선택)</Label>
          <ComboInput id="api-add-category" value={category} onChange={setCategory} options={OPT_API_CATEGORY} size="default" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="api-add-owner">소유자 (선택, 기본: 본인)</Label>
          <OwnerPicker id="api-add-owner" value={owner?.email ?? ""} label={owner?.label} onChange={setOwnerPick} />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="api-add-tags">태그 (선택, 입력 후 Enter)</Label>
        <TagsInput id="api-add-tags" value={tags} onChange={setTags} />
      </div>
    </>
  )

  return (
    <Dialog open={isOpen} onOpenChange={(o) => { if (!o) void requestClose() }}>
      {/* 본문만 스크롤하고 오류·버튼은 항상 하단에 보이도록 — 내용이 길어도 등록 결과를 놓치지 않게 */}
      <DialogContent className="flex max-h-[90vh] max-w-2xl flex-col">
        <DialogHeader><DialogTitle>API 등록</DialogTitle></DialogHeader>
        <div className="-mx-6 min-h-0 flex-1 space-y-3 overflow-y-auto px-6 pb-1">
          <Tabs value={regMode} onValueChange={(v) => setRegMode(v as "spec" | "manual")}>
            <TabsList>
              <TabsTrigger value="spec">스펙(Swagger/OpenAPI)</TabsTrigger>
              <TabsTrigger value="manual">수동 등록</TabsTrigger>
            </TabsList>
          </Tabs>

          {isSpec ? (
            <>
              <p className="text-sm text-muted-foreground">
                OpenAPI 2.0(Swagger) / 3.x 스펙을 붙여넣거나 URL로 등록합니다. 이름·버전·엔드포인트는 스펙에서 자동 추출됩니다. AsyncAPI 문서는 "수동 등록"에서 계약 문서로 등록하세요.
              </p>
              <SpecSourceInput value={spec} onChange={setSpec} onError={setError} preview={preview} />
              <SpecPreviewSummary preview={preview}>
                {(p) => (
                  <div className="text-muted-foreground">
                    등록될 식별자: <span className="font-mono text-foreground">{trimmedName || p.name}</span>
                    {!trimmedName && p.name_auto && " (스펙 제목으로 자동 생성 — 아래에서 직접 지정할 수 있습니다)"}
                    {trimmedName && nameStatus === "taken" && <span className="text-destructive"> — 이미 존재합니다</span>}
                  </div>
                )}
              </SpecPreviewSummary>
              {showSpecBaseUrl && (
                <div className="space-y-1.5">
                  <Label htmlFor="api-add-spec-base-url">Base URL (선택 — 스펙의 서버 주소 대신 사용)</Label>
                  <Input id="api-add-spec-base-url" aria-invalid={baseUrlErr ? true : undefined} placeholder="https://api.example.com" value={specBaseUrl} onChange={(e) => setSpecBaseUrl(e.target.value)} />
                  {fieldError(baseUrlErr) || <p className="text-[11px] text-muted-foreground">{BASE_URL_CRED_HINT}</p>}
                </div>
              )}
              {identityFields}
              {classificationFields}
            </>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                스펙 없이 API를 직접 등록합니다(REST·GraphQL·gRPC·SOAP·Webhook 등). 기본 메타데이터만 입력해 생성한 뒤, 상세 화면의 엔드포인트 탭에서 엔드포인트(오퍼레이션)를 추가하세요.
              </p>
              {identityFields}
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="api-add-protocol">프로토콜</Label>
                  <Select value={protocol} onValueChange={setProtocol}>
                    <SelectTrigger id="api-add-protocol" className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>{MANUAL_PROTOCOLS.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="api-add-version">버전</Label>
                  <Input id="api-add-version" placeholder="예: 1.0.0 (미입력 시 1.0.0)" value={version} onChange={(e) => setVersion(e.target.value)} />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="api-add-base-url">Base URL (선택)</Label>
                <Input id="api-add-base-url" aria-invalid={baseUrlErr ? true : undefined} placeholder="https://api.example.com" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
                {fieldError(baseUrlErr) || (
                  <p className="flex items-start gap-1 text-[11px] text-muted-foreground"><Info className="mt-px h-3 w-3 shrink-0" />{BASE_URL_CRED_HINT}</p>
                )}
              </div>
              {classificationFields}
              {protocol !== "REST" && (
                <>
                  <div className="space-y-1.5">
                    <Label htmlFor="api-add-contract-url">계약 문서 URL (선택)</Label>
                    <Input id="api-add-contract-url" aria-invalid={contractUrlErr ? true : undefined} placeholder="SDL/WSDL/.proto/AsyncAPI 문서 URL" value={contractUrl} onChange={(e) => setContractUrl(e.target.value)} />
                    {fieldError(contractUrlErr)}
                  </div>
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <Label htmlFor="api-add-contract-text">계약 문서 원문 (선택)</Label>
                      <FileLoadButton
                        accept={CONTRACT_FILE_ACCEPT}
                        onLoad={(text) => { setContractText(text); setError(null) }}
                        onError={setError}
                      />
                    </div>
                    <Textarea id="api-add-contract-text" placeholder="SDL / WSDL / .proto / AsyncAPI 원문을 붙여넣기" value={contractText} onChange={(e) => setContractText(e.target.value)} className="max-h-80 min-h-24 font-mono text-xs" />
                  </div>
                </>
              )}
            </>
          )}
        </div>
        {/* 하단 고정 — 오류 메시지 + 버튼 */}
        <div className="space-y-3 border-t pt-3">
          {error && (
            <div role="alert" className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span className="break-words">{error}</span>
            </div>
          )}
          <DialogFooter className="mt-0">
            <Button variant="outline" onClick={() => { void requestClose() }} disabled={saving}>취소</Button>
            <Button onClick={submit} disabled={saving || !!nameErr || nameStatus === "taken" || hasUrlErr}>
              {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
              등록
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  )
}
