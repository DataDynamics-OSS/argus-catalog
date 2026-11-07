"use client"

import { type ReactNode, useCallback, useEffect, useState } from "react"
import { AlertTriangle, CheckCircle2, FileText, Info, Loader2, X } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Tabs, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { Textarea } from "@workspace/ui/components/textarea"

import { type SpecPreview, previewApiSpec } from "../api"
import { SPEC_FORMAT_LABEL } from "./apis-constants"
import { FileLoadButton, LARGE_SPEC_BYTES, SPEC_FILE_ACCEPT, fmtBytes, urlError } from "./apis-form-utils"
import { errorMessage } from "@/lib/notify"

// ---------------------------------------------------------------------------
// OpenAPI 스펙 입력(붙여넣기·파일·URL) + 미리보기 — 등록 다이얼로그와 "스펙 새 버전 등록"이 함께 쓴다.
// ---------------------------------------------------------------------------

export type SpecSource = {
  mode: "text" | "url"
  text: string
  url: string
  /** 불러온 파일 — 큰 파일은 입력란을 접어 두고(collapsed) 파일 정보만 표시 */
  file: { name: string; size: number } | null
  collapsed: boolean
}

export const EMPTY_SPEC_SOURCE: SpecSource = { mode: "text", text: "", url: "", file: null, collapsed: false }

export const specSourceHasInput = (s: SpecSource) => (s.mode === "text" ? !!s.text.trim() : !!s.url.trim())
export const specSourceUrlError = (s: SpecSource) => (s.mode === "url" ? urlError(s.url) : null)
export const specSourcePayload = (s: SpecSource) => (s.mode === "text" ? { spec_text: s.text } : { spec_url: s.url.trim() })
export const specSourceMissingMessage = (s: SpecSource) =>
  s.mode === "text" ? "OpenAPI 스펙(JSON/YAML)을 붙여넣거나 파일을 불러오세요." : "스펙 URL을 입력하세요."

const PREVIEW_DEBOUNCE_MS = 800
type PreviewResult = { key: string; data?: SpecPreview; error?: string }
export type SpecPreviewState = {
  status: "idle" | "loading" | "ok" | "error"
  /** 현재 입력에 대한 결과(입력이 바뀌면 null) */
  data: SpecPreview | null
  error: string | null
  /** URL 미리보기 실행(외부 조회라 자동 실행하지 않음) */
  runUrl: () => Promise<void>
  urlPreviewing: boolean
  reset: () => void
}

/** 스펙 미리보기 — 붙여넣기·파일은 입력이 멈추면 자동, URL 은 runUrl 로만. active=false 면 아무것도 하지 않음. */
export function useSpecPreview(src: SpecSource, active: boolean): SpecPreviewState {
  const [result, setResult] = useState<PreviewResult | null>(null)
  const [urlPreviewing, setUrlPreviewing] = useState(false)
  const key = !specSourceHasInput(src) ? null : src.mode === "text" ? `t:${src.text}` : `u:${src.url.trim()}`

  useEffect(() => {
    if (!active || src.mode !== "text" || !src.text.trim()) return
    let alive = true
    const k = `t:${src.text}`
    const timer = setTimeout(() => {
      previewApiSpec({ spec_text: src.text })
        .then((data) => { if (alive) setResult({ key: k, data }) })
        .catch((e) => { if (alive) setResult({ key: k, error: errorMessage(e) ?? "스펙 미리보기를 불러오지 못했습니다." }) })
    }, PREVIEW_DEBOUNCE_MS)
    return () => { alive = false; clearTimeout(timer) }
  }, [active, src.mode, src.text])

  const runUrl = async () => {
    const url = src.url.trim()
    if (!active || src.mode !== "url" || !url || urlError(url)) return
    const k = `u:${url}`
    setUrlPreviewing(true)
    try { setResult({ key: k, data: await previewApiSpec({ spec_url: url }) }) }
    catch (e) { setResult({ key: k, error: errorMessage(e) ?? "스펙 미리보기를 불러오지 못했습니다." }) }
    finally { setUrlPreviewing(false) }
  }

  const reset = useCallback(() => { setResult(null); setUrlPreviewing(false) }, [])
  const cur = key && result?.key === key ? result : null
  const status: SpecPreviewState["status"] =
    !active || !key ? "idle" : cur ? (cur.data ? "ok" : "error") : src.mode === "text" || urlPreviewing ? "loading" : "idle"
  return {
    status, data: cur?.data ?? null, error: cur?.error ?? null, runUrl, urlPreviewing, reset,
  }
}

/** 스펙 서버 주소가 없거나 상대 경로(/v1 등)인지 — 저장 자격증명은 절대 주소(서버·Base URL)로만 전송된다 */
export const needsBaseUrl = (p: SpecPreview) => !p.base_url || !/^https?:\/\//i.test(p.base_url)

export function SpecSourceInput({ value, onChange, onError, preview, urlSuggestion, textPlaceholder }: {
  value: SpecSource
  onChange: (next: SpecSource) => void
  onError: (message: string) => void
  preview: SpecPreviewState
  /** URL 모드에서 "다시 사용" 제안(직전에 등록한 스펙 URL 등) */
  urlSuggestion?: string | null
  textPlaceholder?: string
}) {
  const set = (patch: Partial<SpecSource>) => onChange({ ...value, ...patch })
  const urlErr = specSourceUrlError(value)
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <Tabs value={value.mode} onValueChange={(v) => set({ mode: v as SpecSource["mode"] })}>
          <TabsList>
            <TabsTrigger value="text">스펙 붙여넣기</TabsTrigger>
            <TabsTrigger value="url">URL</TabsTrigger>
          </TabsList>
        </Tabs>
        {value.mode === "text" && (
          <FileLoadButton
            accept={SPEC_FILE_ACCEPT}
            onLoad={(text, f) => set({ text, file: { name: f.name, size: f.size }, collapsed: f.size > LARGE_SPEC_BYTES })}
            onError={onError}
          />
        )}
      </div>
      {value.mode === "text" ? (
        value.collapsed && value.file ? (
          <div className="flex items-center gap-3 rounded-md border bg-muted/20 px-3 py-3 text-sm">
            <FileText className="h-5 w-5 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium" title={value.file.name}>{value.file.name}</p>
              <p className="text-xs text-muted-foreground">{fmtBytes(value.file.size)} · 큰 파일이라 내용을 접어 두었습니다.</p>
            </div>
            <Button type="button" size="sm" variant="outline" className="h-8" onClick={() => set({ collapsed: false })}>내용 펼치기</Button>
            <Button type="button" size="icon" variant="ghost" className="h-8 w-8" title="지우기" aria-label="불러온 스펙 지우기" onClick={() => set({ text: "", file: null, collapsed: false })}>
              <X className="h-4 w-4" />
            </Button>
          </div>
        ) : (
          <>
            {value.file && (
              <div className="flex items-center gap-1">
                <p className="min-w-0 flex-1 truncate text-xs text-muted-foreground" title={value.file.name}>불러온 파일: {value.file.name} ({fmtBytes(value.file.size)})</p>
                <Button type="button" size="icon" variant="ghost" className="h-6 w-6 shrink-0" title="지우기" aria-label="불러온 스펙 지우기" onClick={() => set({ text: "", file: null, collapsed: false })}>
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
            )}
            <Textarea
              aria-label="OpenAPI 스펙"
              placeholder={textPlaceholder ?? '{"openapi":"3.0.0","info":{"title":"My API","version":"1.0.0"}, ...}  또는 YAML (파일 불러오기 가능)'}
              value={value.text}
              onChange={(e) => set({ text: e.target.value })}
              className="max-h-80 min-h-48 font-mono text-xs"
            />
          </>
        )
      ) : (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <Input
              aria-label="스펙 URL"
              aria-invalid={urlErr ? true : undefined}
              placeholder="https://example.com/openapi.json"
              value={value.url}
              onChange={(e) => set({ url: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void preview.runUrl() } }}
            />
            <Button type="button" variant="outline" size="sm" className="h-9 shrink-0" disabled={!value.url.trim() || !!urlErr || preview.urlPreviewing} onClick={() => void preview.runUrl()}>
              {preview.urlPreviewing ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}불러와서 확인
            </Button>
          </div>
          {urlErr && <p className="text-[11px] text-destructive">{urlErr}</p>}
          {urlSuggestion && !value.url && (
            <button type="button" className="text-xs text-primary hover:underline" onClick={() => set({ url: urlSuggestion })}>
              이전 등록 URL 다시 사용: {urlSuggestion}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/** 미리보기 요약 — 제목·버전·형식·개수·Base URL. children 으로 화면별 항목(식별자·현재 버전 비교 등)을 덧붙인다. */
export function SpecPreviewSummary({ preview, children }: { preview: SpecPreviewState; children?: (p: SpecPreview) => ReactNode }) {
  if (preview.status === "idle") return null
  if (preview.status === "loading") {
    return <div className="flex items-center gap-2 rounded-md border bg-muted/20 px-3 py-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" /> 스펙 확인 중...</div>
  }
  if (preview.status === "error" || !preview.data) {
    return (
      <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>스펙을 해석하지 못했습니다. {preview.error}</span>
      </div>
    )
  }
  const p = preview.data
  return (
    <div className="space-y-1 rounded-md border border-emerald-600/30 bg-emerald-50/50 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
        <span className="font-medium text-foreground">{p.title || "(제목 없음)"}</span>
        <span className="text-muted-foreground">v{p.version} · {SPEC_FORMAT_LABEL[p.format] ?? p.format} · 엔드포인트 {p.endpoint_count}개 · 서버 {p.server_count} · 보안 스킴 {p.security_count}</span>
      </div>
      {p.base_url && <div className="text-muted-foreground">서버 주소: <span className="font-mono">{p.base_url}</span></div>}
      {needsBaseUrl(p) && (
        <div className="flex items-start gap-1 text-amber-700">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            {p.base_url ? "스펙의 서버 주소가 상대 경로입니다(https://… 형식 아님)." : "스펙에 서버 주소가 없습니다."}{" "}
            저장된 자격증명은 이 API 의 서버 주소·Base URL 로만 전송되므로, 호출(Try-it)에 자격증명을 쓰려면 Base URL 을 입력하세요.
          </span>
        </div>
      )}
      {children?.(p)}
    </div>
  )
}
