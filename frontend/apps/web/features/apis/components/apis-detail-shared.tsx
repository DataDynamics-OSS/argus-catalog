"use client"

import { type ReactNode, useEffect, useRef, useState } from "react"
import dynamic from "next/dynamic"
import type { EditorProps, OnMount } from "@monaco-editor/react"
import { AlertTriangle, Check, Copy, Loader2, RotateCcw, X } from "lucide-react"
import { toast } from "@/lib/notify"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { cn } from "@workspace/ui/lib/utils"

import { MONACO_FONT_SIZE } from "@/lib/monaco"

import type { InvokeResult } from "../api"

// API 상세 화면의 여러 탭이 함께 쓰는 헬퍼·뷰어 컴포넌트.

export const MonacoEditor = dynamic(() => import("@monaco-editor/react").then((m) => m.default), {
  ssr: false,
  loading: () => <div className="flex h-40 items-center justify-center"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>,
})

// 콤보 선택 특수값 + API 카테고리 프리셋 — 등록 다이얼로그·개요 편집 공용.
export const COMBO_UNSET = "__unset__"
export const COMBO_CUSTOM = "__custom__"
export const OPT_API_CATEGORY = [
  "데이터", "결제", "인증/인가", "알림/메시징", "검색", "파일/스토리지",
  "분석/리포팅", "지도/위치", "AI/ML", "관리/운영", "외부 연동",
]

// 프리셋 선택 + "사용자 입력" 시 우측 Input 노출.
export function ComboInput({ id, value, onChange, options, placeholder, size = "sm" }: {
  /** 라벨 연결용(SelectTrigger 에 부여) */
  id?: string
  value: string; onChange: (v: string) => void; options: string[]; placeholder?: string
  /** sm: 표 안 인라인 편집(h-8) · default: 폼 기본 높이 */
  size?: "sm" | "default"
}) {
  const [custom, setCustom] = useState(false)
  const isPreset = value !== "" && options.includes(value)
  const showInput = custom || (value !== "" && !isPreset)
  const selectValue = showInput ? COMBO_CUSTOM : value === "" ? COMBO_UNSET : value
  const h = size === "sm" ? "h-8 text-sm" : ""
  return (
    <div className="flex w-full items-center gap-2">
      <Select
        value={selectValue}
        onValueChange={(v) => {
          if (v === COMBO_CUSTOM) setCustom(true)
          else if (v === COMBO_UNSET) { setCustom(false); onChange("") }
          else { setCustom(false); onChange(v) }
        }}
      >
        <SelectTrigger id={id} className={cn(h, showInput ? "min-w-0 flex-1" : "w-full")}><SelectValue placeholder="미지정" /></SelectTrigger>
        <SelectContent>
          <SelectItem value={COMBO_UNSET} className="text-muted-foreground">미지정</SelectItem>
          {options.map((o) => <SelectItem key={o} value={o}>{o}</SelectItem>)}
          <SelectItem value={COMBO_CUSTOM}>사용자 입력</SelectItem>
        </SelectContent>
      </Select>
      {showInput && <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder ?? "직접 입력"} className={cn("min-w-0 flex-1", h)} />}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 탭 공용 상태 표시 — 로딩·빈 상태·오류를 모든 탭에서 같은 모양으로.
// ---------------------------------------------------------------------------
export function TabLoading({ label = "불러오는 중..." }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" /> {label}
    </div>
  )
}

export function TabEmpty({ children }: { children: ReactNode }) {
  return <p className="py-2 text-sm text-muted-foreground">{children}</p>
}

export function TabError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-center gap-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm">
      <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" />
      <span className="flex-1 text-destructive">{message}</span>
      {onRetry && (
        <Button size="sm" variant="outline" className="h-7 shrink-0" onClick={onRetry}>
          <RotateCcw className="mr-1 h-3.5 w-3.5" /> 다시 시도
        </Button>
      )}
    </div>
  )
}

/** 파라미터 데이터 타입 표시 — array<items>, type (format), type. */
export function paramDataType(p: { type?: string; format?: string; schema?: { type?: string; format?: string; items?: { type?: string } } }): string {
  const base = p.schema?.type ?? p.type ?? "-"
  const items = p.schema?.items?.type
  const fmt = p.schema?.format ?? p.format
  return base === "array" && items ? `array<${items}>` : (fmt ? `${base} (${fmt})` : base)
}

/** 호출(Try-it) 결과 — 상태 코드·지연·오류·응답 본문. REST·GraphQL·SOAP 공용. */
// 응답 본문 유형 판별 — Content-Type 우선, 없거나 모호하면(text/plain·octet-stream 등) 본문 내용으로 추정.
type BodyKind = { language: string; label: string; text: string }
const CONTENT_TYPE_LANG: [RegExp, string, string][] = [
  [/[/+]json\b|\/x-ndjson/, "json", "JSON"],
  [/[/+]xml\b/, "xml", "XML"],
  [/\/html\b/, "html", "HTML"],
  [/[/+]yaml\b|\/x-yaml/, "yaml", "YAML"],
  [/\/(javascript|ecmascript)\b/, "javascript", "JavaScript"],
  [/\/css\b/, "css", "CSS"],
  [/\/graphql\b/, "graphql", "GraphQL"],
  [/\/csv\b/, "plaintext", "CSV"],
]
function prettyJson(body: string): string | null {
  try { return JSON.stringify(JSON.parse(body), null, 2) } catch { return null }
}
export function detectBodyKind(body: string, headers: Record<string, string> | undefined): BodyKind {
  const ct = Object.entries(headers ?? {}).find(([k]) => k.toLowerCase() === "content-type")?.[1]?.toLowerCase() ?? ""
  for (const [re, language, label] of CONTENT_TYPE_LANG) {
    if (!re.test(ct)) continue
    // JSON 은 보기 좋게 들여쓰기 — 파싱 실패(잘린 응답 등)면 원문 그대로
    if (language === "json") return { language, label, text: prettyJson(body) ?? body }
    return { language, label, text: body }
  }
  const head = body.trimStart()
  if (head.startsWith("{") || head.startsWith("[")) {
    const pretty = prettyJson(body)
    if (pretty !== null) return { language: "json", label: "JSON", text: pretty }
  }
  if (/^<!doctype html|^<html[\s>]/i.test(head)) return { language: "html", label: "HTML", text: body }
  if (head.startsWith("<")) return { language: "xml", label: "XML", text: body }
  return { language: "plaintext", label: ct ? ct.split(";")[0]!.trim() : "텍스트", text: body }
}

// 계약 문서(수동 API) 유형 판별 — 본문 내용 우선, 판별이 안 되면 API 프로토콜로. 저장하지 않고 렌더 시 계산.
export type ContractKind = { language: string; label: string }
const CONTRACT_XML: ContractKind = { language: "xml", label: "XML" }
const CONTRACT_JSON: ContractKind = { language: "json", label: "JSON" }
const CONTRACT_PROTO: ContractKind = { language: "proto", label: "Protobuf" }
const CONTRACT_YAML: ContractKind = { language: "yaml", label: "YAML" }
const CONTRACT_GRAPHQL: ContractKind = { language: "graphql", label: "GraphQL" }
const CONTRACT_TEXT: ContractKind = { language: "plaintext", label: "텍스트" }
const CONTRACT_BY_PROTOCOL: Record<string, ContractKind> = {
  graphql: CONTRACT_GRAPHQL, soap: CONTRACT_XML, grpc: CONTRACT_PROTO, webhook: CONTRACT_YAML,
}
export function detectContractKind(text: string | null | undefined, protocol?: string | null): ContractKind {
  const body = text ?? ""
  const head = body.trimStart()
  if (head) {
    if (head.startsWith("<")) return CONTRACT_XML                                       // WSDL·XSD
    if ((head.startsWith("{") || head.startsWith("[")) && prettyJson(body) !== null) return CONTRACT_JSON
    if (/^\s*syntax\s*=\s*["']proto[23]["']/m.test(body) || /^\s*message\s+\w+\s*\{/m.test(body) || /^\s*rpc\s+\w+\s*\(/m.test(body)) return CONTRACT_PROTO
    if (/^(asyncapi|openapi|swagger)\s*:/m.test(body)) return CONTRACT_YAML
    if (/^\s*(type|schema|input|enum|interface|extend\s+\w+|query|mutation|subscription|fragment)\b[^\n{]*\{/m.test(body)
      || /^\s*(scalar|union|directive)\s+@?\w+/m.test(body)) return CONTRACT_GRAPHQL
  }
  return CONTRACT_BY_PROTOCOL[(protocol ?? "").toLowerCase()] ?? CONTRACT_TEXT
}

// 편집 가능한 Monaco 코드 에디터 — 글꼴 text-sm(14px), 우하단 그립으로 높이 조절. (읽기 전용은 CodeViewer)
export function CodeEditor({ value, onChange, language, ariaLabel, placeholder, height = 180, minHeight = 96, maxHeight = 600 }: {
  value: string; onChange: (v: string) => void; language: string; ariaLabel: string; placeholder?: string
  height?: number; minHeight?: number; maxHeight?: number | string
}) {
  return (
    <div className="resize-y overflow-hidden rounded-md border" style={{ height, minHeight, maxHeight }}>
      <MonacoEditor
        height="100%"
        language={language}
        theme="light"
        value={value}
        onChange={(v) => onChange(v ?? "")}
        options={{
          minimap: { enabled: false }, fontSize: MONACO_FONT_SIZE, lineNumbers: "on",
          scrollBeyondLastLine: false, automaticLayout: true, tabSize: 2, wordWrap: "on",
          padding: { top: 6, bottom: 6 }, placeholder, ariaLabel,
        }}
      />
    </div>
  )
}

export function InvokeResultPanel({ result }: { result: InvokeResult }) {
  const ok = result.status >= 200 && result.status < 300
  const kind = result.body ? detectBodyKind(result.body, result.headers) : null
  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex items-center gap-2 text-sm">
        <Badge className={ok ? "bg-emerald-600 text-white" : "bg-red-600 text-white"}>{result.status || "ERR"}</Badge>
        <span className="text-muted-foreground">{result.latency_ms}ms</span>
        {result.error && <span className="text-destructive">{result.error}</span>}
      </div>
      <div>
        <div className="mb-1 flex items-center gap-2">
          <p className="text-xs text-muted-foreground">응답 본문</p>
          {kind && <Badge variant="outline" className="h-5 px-1.5 text-[11px] font-normal">{kind.label}</Badge>}
        </div>
        {kind
          ? <CodeViewer text={kind.text} language={kind.language} maxHeight={480} />
          : <p className="rounded bg-muted/30 px-2 py-1.5 text-sm text-muted-foreground">(빈 응답)</p>}
      </div>
    </div>
  )
}

/** 날짜를 'yyyy-MM-dd HH:mm' 형식으로 포맷. */
export function fmtDateTime(value: string | null | undefined): string {
  if (!value) return "-"
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return "-"
  const p = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
const METHOD_COLOR: Record<string, string> = {
  GET: "bg-sky-600 text-white", POST: "bg-emerald-600 text-white", PUT: "bg-amber-600 text-white",
  PATCH: "bg-orange-600 text-white", DELETE: "bg-red-600 text-white",
  // GraphQL 오퍼레이션 유형
  query: "bg-sky-600 text-white", mutation: "bg-amber-600 text-white", subscription: "bg-violet-600 text-white",
}

// 태그 배지 입력 — 입력 후 Enter→배지 추가, X→삭제. 저장 포맷은 콤마 구분 문자열.
export function TagsInput({ id, value, onChange }: { id?: string; value: string; onChange: (next: string) => void }) {
  const [text, setText] = useState("")
  const tags = value.split(",").map((t) => t.trim()).filter(Boolean)
  const commit = (raw: string) => {
    const incoming = raw.split(",").map((t) => t.trim()).filter(Boolean)
    if (incoming.length === 0) return
    const next = [...tags]
    for (const t of incoming) if (!next.includes(t)) next.push(t)
    onChange(next.join(", "))
    setText("")
  }
  const remove = (t: string) => onChange(tags.filter((x) => x !== t).join(", "))
  return (
    <div className="flex w-full flex-wrap items-center gap-1">
      {tags.map((t) => (
        <Badge key={t} variant="secondary" className="gap-1 text-xs">
          {t}
          <button type="button" className="hover:opacity-70" onClick={() => remove(t)} aria-label={`${t} 제거`}><X className="h-3 w-3" /></button>
        </Badge>
      ))}
      <Input
        id={id}
        className="h-8 min-w-[100px] flex-1 text-sm"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(text) }
          else if (e.key === "Backspace" && text === "" && tags.length > 0) remove(tags[tags.length - 1]!)
        }}
        onBlur={() => { if (text.trim()) commit(text) }}
        placeholder="입력 후 Enter"
      />
    </div>
  )
}

export function MethodBadge({ method }: { method: string }) {
  return <span className={cn("inline-flex min-w-14 justify-center whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-[11px] font-bold", METHOD_COLOR[method] ?? "bg-zinc-600 text-white")}>{method.toUpperCase()}</span>
}

// 읽기 전용 JSON 뷰어 — 스크롤바·테두리 없이 내용 높이에 맞춰 표시, 클립보드 복사 버튼.
const JSON_VIEWER_OPTIONS: NonNullable<EditorProps["options"]> = {
  readOnly: true,
  domReadOnly: true,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  scrollbar: { vertical: "hidden", horizontal: "hidden", handleMouseWheel: false, alwaysConsumeMouseWheel: false },
  wordWrap: "on",
  lineNumbers: "off",
  folding: false,
  fontSize: MONACO_FONT_SIZE,
  fontFamily: "D2Coding, Menlo, Consolas, monospace",
  renderLineHighlight: "none",
  overviewRulerLanes: 0,
  overviewRulerBorder: false,
  hideCursorInOverviewRuler: true,
  contextmenu: false,
  guides: { indentation: false },
  automaticLayout: true,
  padding: { top: 6, bottom: 6 },
}

// 읽기 전용 코드 뷰어. 기본은 스크롤바·테두리 없이 내용 높이에 맞춰(bottom fit) 표시.
// scroll 모드: 뷰포트 하단까지 채우고(내부 스크롤바) + 줄 번호(원본 스펙처럼 큰 문서용). 공통으로 클립보드 복사 버튼.
// maxHeight: 내용 높이에 맞추되 이 값(px)을 넘으면 고정하고 내부 스크롤바 표시(호출 응답처럼 길이를 모르는 본문용).
export function CodeViewer({ text, language = "json", emptyLabel = "없음", lineNumbers = false, scroll = false, bottomGap = 24, maxHeight }: {
  text: string | null | undefined; language?: string; emptyLabel?: string; lineNumbers?: boolean; scroll?: boolean; bottomGap?: number; maxHeight?: number
}) {
  const [height, setHeight] = useState(40)
  const [copied, setCopied] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [autoH, setAutoH] = useState(400)
  // scroll 모드: 에디터 상단 위치를 측정해 뷰포트 하단까지 정확히 채운다(페이지 스크롤 방지, 내부 스크롤만).
  useEffect(() => {
    if (!scroll) return
    const calc = () => {
      const el = wrapRef.current
      if (!el) return
      setAutoH(Math.max(240, window.innerHeight - el.getBoundingClientRect().top - bottomGap))
    }
    calc()
    window.addEventListener("resize", calc)
    return () => window.removeEventListener("resize", calc)
  }, [scroll, bottomGap])
  if (text === null || text === undefined || text === "") return <span className="text-sm text-black">{emptyLabel}</span>

  const options: NonNullable<EditorProps["options"]> = {
    ...JSON_VIEWER_OPTIONS,
    lineNumbers: lineNumbers ? "on" : "off",
    // scroll 모드(원본): 접기 활성화 — depth 4 이상은 자동으로 접고, 접기 컨트롤 항상 노출.
    folding: scroll ? true : JSON_VIEWER_OPTIONS.folding,
    showFoldingControls: scroll ? "always" : undefined,
    scrollbar: scroll
      ? { vertical: "auto", horizontal: "auto", handleMouseWheel: true, alwaysConsumeMouseWheel: true }
      : maxHeight
        // 상·하단 끝에 닿으면 페이지 스크롤로 넘김(alwaysConsumeMouseWheel=false)
        ? { vertical: "auto", horizontal: "hidden", handleMouseWheel: true, alwaysConsumeMouseWheel: false }
        : JSON_VIEWER_OPTIONS.scrollbar,
  }
  const handleMount: OnMount = (editor) => {
    if (scroll) {
      // 접기 범위 계산 후 depth 4 이상 자동 접기.
      setTimeout(() => editor.getAction("editor.foldLevel4")?.run(), 80)
      return  // 컨테이너 높이(뷰포트 기준)를 채움 — 내용에 맞춰 늘리지 않음
    }
    const update = () => setHeight(Math.min(Math.max(editor.getContentHeight(), 24), maxHeight ?? Infinity))
    update()
    editor.onDidContentSizeChange(update)
  }
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      toast.success("클립보드에 복사했습니다.")
      setTimeout(() => setCopied(false), 2000)
    } catch { toast.error("클립보드에 복사하지 못했습니다.") }
  }

  return (
    <div ref={wrapRef} className={cn("relative overflow-hidden rounded bg-muted/30", scroll && "border")} style={{ height: scroll ? autoH : height }}>
      <MonacoEditor language={language} value={text} theme="light" height="100%" options={options} onMount={handleMount} />
      <Button
        type="button"
        variant="secondary"
        size="icon"
        onClick={handleCopy}
        title={copied ? "복사됨" : "복사"}
        aria-label={copied ? "복사됨" : "복사"}
        className="absolute top-1 right-1 z-10 h-7 w-7 shadow-sm"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </Button>
    </div>
  )
}

export function JsonBlock({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="text-sm text-black">없음</span>
  return <CodeViewer text={JSON.stringify(value, null, 2)} />
}
