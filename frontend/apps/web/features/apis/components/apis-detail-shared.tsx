"use client"

import { type ReactNode, useState } from "react"
import dynamic from "next/dynamic"
import { AlertTriangle, Loader2 } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { StatusBadge } from "@workspace/ui/components/status-badge"
import { methodTone } from "./apis-constants"
import { cn } from "@workspace/ui/lib/utils"

import { MONACO_FONT_SIZE } from "@/lib/monaco"

import type { InvokeResult } from "../api"
import { FitCodeViewer } from "@/components/fit-code-viewer"

// 읽기 전용 코드 뷰어는 공용 components/fit-code-viewer.tsx 로 옮김 — 이 파일을 쓰던 곳을 위해 같은 이름으로 내보낸다
export { FitCodeViewer as CodeViewer, JsonBlock } from "@/components/fit-code-viewer"

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
export function TabLoading({ label = "불러오는 중…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-4 text-sm">
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
          다시 시도
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
        <StatusBadge tone={ok ? "success" : "danger"} className="font-mono">{result.status || "ERR"}</StatusBadge>
        <span >{result.latency_ms}ms</span>
        {result.error && <span className="text-destructive">{result.error}</span>}
      </div>
      <div>
        <div className="mb-1 flex items-center gap-2">
          <p className="text-xs">응답 본문</p>
          {kind && <Badge size="sm" variant="outline" className="font-normal">{kind.label}</Badge>}
        </div>
        {kind
          ? <FitCodeViewer text={kind.text} language={kind.language} maxHeight={480} />
          : <p className="rounded bg-muted/30 px-2 py-1.5 text-sm">(빈 응답)</p>}
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
// 메서드 톤(TODO-107) — 읽기는 파랑, 만들기는 초록, 바꾸기는 주황, 지우기는 빨강. 진한 단색 대신 옅은 칠.


export function MethodBadge({ method }: { method: string }) {
  return (
    <StatusBadge tone={methodTone(method)} className="min-w-14 font-mono font-semibold">
      {method.toUpperCase()}
    </StatusBadge>
  )
}
