"use client"

import { Fragment, useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import dynamic from "next/dynamic"
import type { EditorProps, OnMount } from "@monaco-editor/react"
import {
  AlertTriangle, ArrowLeft, Check, ChevronDown, ChevronRight, ChevronUp, Copy, Eye, EyeOff, Loader2, Minus, Pencil, Play, Plus, RotateCcw, Save, Star, Trash2, X,
} from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { Textarea } from "@workspace/ui/components/textarea"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@workspace/ui/components/tooltip"
import { cn } from "@workspace/ui/lib/utils"

import {
  acknowledgeApiAlert, addApiFavorite, addApiLineage, createApiCredential, deleteApi, deleteApiCredential, deleteApiLineage,
  fetchApiAlerts, fetchApiCredentials, fetchApiDetail, fetchApiFavorites, fetchApiLineage, fetchApiLint, fetchApiStatusHistory, fetchApiUsage,
  createEndpoint, deleteEndpoint, fetchEndpointInvocations, invokeApi, removeApiFavorite, updateApi, updateEndpoint,
  type ApiAlert, type ApiCredential, type ApiDetail, type ApiEndpoint, type ApiInvocationRecord, type ApiLineage, type ApiLint,
  type ApiStatusHistory, type ApiUsage, type EndpointPayload, type InvokeResult,
} from "../api"
import { fetchUsers } from "@/features/users/api"
import { type User } from "@/features/users/data/schema"
import { ApisLineageGraph } from "./apis-lineage-graph"
import { useApis } from "./apis-provider"
import { API_STATUS_LABEL, API_STATUS_VARIANTS, SPEC_FORMAT_LABEL, protocolLabel } from "./apis-table"

const userDisplayName = (u: User) => `${u.lastName ?? ""}${u.firstName ?? ""}`.trim() || u.username

const MonacoEditor = dynamic(() => import("@monaco-editor/react").then((m) => m.default), {
  ssr: false,
  loading: () => <div className="flex h-40 items-center justify-center"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>,
})

const STATUS_ORDER = ["draft", "published", "deprecated", "retired"]

/** 날짜를 'yyyy-MM-dd HH:mm' 형식으로 포맷. */
function fmtDateTime(value: string | null | undefined): string {
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

// 콤보 선택 특수값 + 프리셋/라벨 정의
const COMBO_UNSET = "__unset__"
const COMBO_CUSTOM = "__custom__"
const OPT_API_CATEGORY = [
  "데이터", "결제", "인증/인가", "알림/메시징", "검색", "파일/스토리지",
  "분석/리포팅", "지도/위치", "AI/ML", "관리/운영", "외부 연동",
]
const CERT_LABEL: Record<string, string> = { NONE: "없음", CERTIFIED: "인증됨", IN_REVIEW: "검토 중", DEPRECATED: "지원 중단" }
const TIER_LABEL: Record<string, string> = { GOLD: "골드", SILVER: "실버", BRONZE: "브론즈" }

// 개요 표 key 별 설명 — 점선 밑줄 + hover 툴팁으로 노출.
const KEY_DESC: Record<string, string> = {
  "이름": "API 의 고유 식별 이름(시스템 내 유일, URN 의 기반)",
  "표시명": "사람이 읽기 좋은 API 표시 이름",
  "버전": "API 버전(예: 1.2.0). 새 스펙 업로드 시 갱신",
  "상태": "초안(draft)·게시(published)·사용 중단(deprecated)·폐기(retired)",
  "설명": "API 의 용도·기능에 대한 요약 설명",
  "프로토콜": "통신 프로토콜(REST 등). 스펙에서 자동 판별",
  "스펙 포맷": "스펙 형식 — OpenAPI 3.x / Swagger 2.0 등(스펙에서 자동 판별)",
  "Base URL": "기본 호출 서버 주소. 스펙의 servers/host 에서 자동 추출되며, 직접 입력해 덮어쓰면(파랑) 이후 스펙 업로드 시에도 수동 값이 유지됩니다. 비우면 다시 스펙 자동 추출로 복귀합니다.",
  "카테고리": "용도별 분류(데이터·결제·인증 등)",
  "소유자": "API 책임자(사용자 관리에서 선택). 이름에 마우스를 올리면 소속·부서·Username·Email 표시",
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

// 프리셋 선택 + "사용자 입력" 시 우측 Input 노출.
function ComboInput({ value, onChange, options, placeholder }: { value: string; onChange: (v: string) => void; options: string[]; placeholder?: string }) {
  const [custom, setCustom] = useState(false)
  const isPreset = value !== "" && options.includes(value)
  const showInput = custom || (value !== "" && !isPreset)
  const selectValue = showInput ? COMBO_CUSTOM : value === "" ? COMBO_UNSET : value
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
        <SelectTrigger className={cn("h-8 text-sm", showInput ? "min-w-0 flex-1" : "w-full")}><SelectValue placeholder="미지정" /></SelectTrigger>
        <SelectContent>
          <SelectItem value={COMBO_UNSET} className="text-muted-foreground">미지정</SelectItem>
          {options.map((o) => <SelectItem key={o} value={o}>{o}</SelectItem>)}
          <SelectItem value={COMBO_CUSTOM}>사용자 입력</SelectItem>
        </SelectContent>
      </Select>
      {showInput && <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder ?? "직접 입력"} className="h-8 min-w-0 flex-1 text-sm" />}
    </div>
  )
}

// 소유자 전용 — 사용자 관리의 사용자 목록에서 선택(선택 시 부서 자동 입력). 맨 아래 "사용자 입력"으로 이메일 직접 입력.
function OwnerCombo({ users, value, onSelectUser, onCustomEmail }: {
  users: User[]; value: string; onSelectUser: (u: User) => void; onCustomEmail: (email: string) => void
}) {
  const [custom, setCustom] = useState(false)
  const matched = users.find((u) => u.email === value)
  const showInput = custom || (value !== "" && !matched)
  const selectValue = showInput ? COMBO_CUSTOM : value === "" ? COMBO_UNSET : value
  return (
    <div className="flex items-center gap-2">
      <Select
        value={selectValue}
        onValueChange={(v) => {
          if (v === COMBO_CUSTOM) setCustom(true)
          else if (v === COMBO_UNSET) { setCustom(false); onCustomEmail("") }
          else { setCustom(false); const u = users.find((x) => x.email === v); if (u) onSelectUser(u) }
        }}
      >
        <SelectTrigger className={cn("h-8 text-sm", showInput ? "w-[180px] shrink-0" : "w-full")}><SelectValue placeholder="미지정" /></SelectTrigger>
        <SelectContent>
          <SelectItem value={COMBO_UNSET} className="text-muted-foreground">미지정</SelectItem>
          {users.map((u) => <SelectItem key={u.id} value={u.email}>{userDisplayName(u)} ({u.username})</SelectItem>)}
          <SelectItem value={COMBO_CUSTOM}>사용자 입력</SelectItem>
        </SelectContent>
      </Select>
      {showInput && <Input value={value} onChange={(e) => onCustomEmail(e.target.value)} placeholder="이메일 직접 입력" className="h-8 flex-1 text-sm" />}
    </div>
  )
}

// 태그 배지 입력 — 입력 후 Enter→배지 추가, X→삭제. 저장 포맷은 콤마 구분 문자열.
function TagsInput({ value, onChange }: { value: string; onChange: (next: string) => void }) {
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

function MethodBadge({ method }: { method: string }) {
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
  fontSize: 14,
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
function CodeViewer({ text, language = "json", emptyLabel = "없음", lineNumbers = false, scroll = false, bottomGap = 24 }: {
  text: string | null | undefined; language?: string; emptyLabel?: string; lineNumbers?: boolean; scroll?: boolean; bottomGap?: number
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
      : JSON_VIEWER_OPTIONS.scrollbar,
  }
  const handleMount: OnMount = (editor) => {
    if (scroll) {
      // 접기 범위 계산 후 depth 4 이상 자동 접기.
      setTimeout(() => editor.getAction("editor.foldLevel4")?.run(), 80)
      return  // 컨테이너 높이(뷰포트 기준)를 채움 — 내용에 맞춰 늘리지 않음
    }
    const update = () => setHeight(Math.max(editor.getContentHeight(), 24))
    update()
    editor.onDidContentSizeChange(update)
  }
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      toast.success("클립보드에 복사했습니다.")
      setTimeout(() => setCopied(false), 2000)
    } catch { toast.error("클립보드 복사에 실패했습니다.") }
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

function JsonBlock({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="text-sm text-black">없음</span>
  return <CodeViewer text={JSON.stringify(value, null, 2)} />
}

// ---------------------------------------------------------------------------
// 코드 스니펫 자동 생성 (curl / Python / JavaScript / wget)
// ---------------------------------------------------------------------------
const SNIPPET_LANGS = ["curl", "python", "javascript", "wget"] as const
type SnippetLang = (typeof SNIPPET_LANGS)[number]
const SNIPPET_LABEL: Record<SnippetLang, string> = { curl: "cURL", python: "Python", javascript: "JavaScript", wget: "wget" }
const SNIPPET_MONACO_LANG: Record<SnippetLang, string> = { curl: "shell", wget: "shell", python: "python", javascript: "javascript" }

function buildSnippets(method: string, url: string, headers: Record<string, string>, bodyText: string): Record<SnippetLang, string> {
  const m = method.toUpperCase()
  const hasBody = ["POST", "PUT", "PATCH", "DELETE"].includes(m) && bodyText.trim() !== ""
  const hEntries = Object.entries(headers)
  const hdrJson = JSON.stringify(headers, null, 2)

  const curlLines = [`curl -X ${m} "${url}"`]
  for (const [k, v] of hEntries) curlLines.push(`  -H "${k}: ${v}"`)
  if (hasBody) curlLines.push(`  -d '${bodyText.replace(/'/g, "'\\''")}'`)
  const curl = curlLines.join(" \\\n")

  const wgetLines = [`wget --method=${m} \\`]
  for (const [k, v] of hEntries) wgetLines.push(`  --header="${k}: ${v}" \\`)
  if (hasBody) wgetLines.push(`  --body-data='${bodyText}' \\`)
  wgetLines.push(`  -qO- "${url}"`)
  const wget = wgetLines.join("\n")

  const python = `import requests

resp = requests.request(
    "${m}",
    "${url}",
    headers=${hdrJson},${hasBody ? `\n    data=${JSON.stringify(bodyText)},` : ""}
)
print(resp.status_code)
print(resp.text)`

  const javascript = `const resp = await fetch("${url}", {
  method: "${m}",
  headers: ${hdrJson},${hasBody ? `\n  body: ${JSON.stringify(bodyText)},` : ""}
});
console.log(resp.status, await resp.text());`

  return { curl, python, javascript, wget }
}

function SnippetView({ method, url, headers, bodyText, authNote }: {
  method: string; url: string; headers: Record<string, string>; bodyText: string; authNote?: boolean
}) {
  const [lang, setLang] = useState<SnippetLang>("curl")
  const snippets = buildSnippets(method, url, headers, bodyText)
  const code = snippets[lang]
  return (
    <div className="space-y-2">
      <Tabs value={lang} onValueChange={(v) => setLang(v as SnippetLang)}>
        <TabsList>{SNIPPET_LANGS.map((l) => <TabsTrigger key={l} value={l} className="text-sm">{SNIPPET_LABEL[l]}</TabsTrigger>)}</TabsList>
      </Tabs>
      {authNote && (
        <p className="text-xs text-muted-foreground">※ 저장된 자격증명을 선택한 경우, 시크릿은 서버에서만 주입되어 스니펫에는 포함되지 않습니다. 직접 호출 시 인증 헤더를 추가하세요.</p>
      )}
      <CodeViewer text={code} language={SNIPPET_MONACO_LANG[lang]} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// 엔드포인트 호출(Try-it) — 엔드포인트 고정. 서버/자격증명은 상위(엔드포인트 탭)에서 공유.
// ---------------------------------------------------------------------------
function EndpointTryIt({ api, endpoint, server, credId }: {
  api: ApiDetail; endpoint: ApiEndpoint; server: string; credId: string
}) {
  const [pathParams, setPathParams] = useState<Record<string, string>>({})
  const [queryParams, setQueryParams] = useState<Record<string, string>>({})
  const [headerRows, setHeaderRows] = useState<{ key: string; value: string }[]>([{ key: "", value: "" }])
  const [bodyText, setBodyText] = useState("")
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<InvokeResult | null>(null)
  const [history, setHistory] = useState<ApiInvocationRecord[]>([])

  const loadHistory = useCallback(() => {
    fetchEndpointInvocations(api.name, endpoint.method, endpoint.path, 10).then(setHistory).catch(() => {})
  }, [api.name, endpoint.method, endpoint.path])
  useEffect(() => { loadHistory() }, [loadHistory])

  const headersObj = (): Record<string, string> => {
    const o: Record<string, string> = {}
    for (const r of headerRows) if (r.key.trim()) o[r.key.trim()] = r.value
    return o
  }

  // 이력 → 폼 재주입(불러오기). 마스킹된 헤더 값(***)은 그대로 들어가므로 직접 갱신 필요.
  const loadFromHistory = (rec: ApiInvocationRecord) => {
    const ri = rec.request_input || {}
    setPathParams(ri.path_params ?? {})
    setQueryParams(ri.query_params ?? {})
    const hs = Object.entries(ri.headers ?? {}).map(([key, value]) => ({ key, value }))
    setHeaderRows(hs.length > 0 ? hs : [{ key: "", value: "" }])
    setBodyText(ri.body ?? "")
    toast.success("과거 입력값을 불러왔습니다.")
  }

  const params = endpoint.parameters ?? []
  const pathP = params.filter((p) => (p as { in?: string }).in === "path")
  const queryP = params.filter((p) => (p as { in?: string }).in === "query")
  const isWrite = ["POST", "PUT", "PATCH", "DELETE"].includes(endpoint.method)

  const buildUrl = useCallback(() => {
    let path = endpoint.path
    for (const p of pathP) {
      const nm = (p as { name?: string }).name ?? ""
      path = path.replace(`{${nm}}`, encodeURIComponent(pathParams[nm] ?? ""))
    }
    const qs = queryP
      .map((p) => { const nm = (p as { name?: string }).name ?? ""; return queryParams[nm] ? `${encodeURIComponent(nm)}=${encodeURIComponent(queryParams[nm])}` : "" })
      .filter(Boolean).join("&")
    return `${(server || "").replace(/\/$/, "")}${path}${qs ? `?${qs}` : ""}`
  }, [endpoint, pathP, queryP, pathParams, queryParams, server])

  const run = async () => {
    if (!server) { toast.error("상단에서 서버(Base URL)를 선택/입력하세요."); return }
    const headers: Record<string, string> = headersObj()
    let body: unknown
    if (isWrite && bodyText.trim()) {
      try { body = JSON.parse(bodyText) } catch { body = bodyText }
      if (!headers["Content-Type"] && !headers["content-type"]) headers["Content-Type"] = "application/json"
    }
    setRunning(true)
    try {
      setResult(await invokeApi({
        method: endpoint.method, url: buildUrl(), headers, body,
        api_name: api.name,  // 사용량 로깅 — 자격증명 미선택이어도 기록
        credential_id: credId !== "none" ? Number(credId) : undefined,
        endpoint_method: endpoint.method, endpoint_path: endpoint.path,
        path_params: pathParams, query_params: queryParams,
      }))
      loadHistory()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "호출 실패")
    } finally { setRunning(false) }
  }

  return (
    <div className="grid gap-3">
      {[...pathP, ...queryP].length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">파라미터</p>
          <div className="overflow-hidden rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-muted/40"><tr>
                <th className="w-[160px] px-3 py-2 text-left font-medium text-muted-foreground">파라미터</th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">값</th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">설명</th>
                <th className="w-[80px] px-3 py-2 text-left font-medium text-muted-foreground">위치</th>
                <th className="w-[110px] px-3 py-2 text-left font-medium text-muted-foreground">데이터 타입</th>
              </tr></thead>
              <tbody>
                {[...pathP, ...queryP].map((p) => {
                  const pp = p as { name?: string; in?: string; description?: string; required?: boolean; type?: string; format?: string; schema?: { type?: string; format?: string; items?: { type?: string } } }
                  const nm = pp.name ?? ""
                  const where = pp.in ?? ""
                  const isPath = where === "path"
                  const val = isPath ? (pathParams[nm] ?? "") : (queryParams[nm] ?? "")
                  const setVal = (v: string) => isPath ? setPathParams((s) => ({ ...s, [nm]: v })) : setQueryParams((s) => ({ ...s, [nm]: v }))
                  const base = pp.schema?.type ?? pp.type ?? "-"
                  const items = pp.schema?.items?.type
                  const fmt = pp.schema?.format ?? pp.format
                  const dataType = base === "array" && items ? `array<${items}>` : (fmt ? `${base} (${fmt})` : base)
                  return (
                    <tr key={`${where}:${nm}`} className="border-t align-top">
                      <td className="px-3 py-2"><span className="font-mono text-sm text-black">{nm}</span>{pp.required && <span className="ml-1 text-red-600" title="필수">*</span>}</td>
                      <td className="px-3 py-2"><Input className="h-8 w-full text-sm" placeholder={nm} value={val} onChange={(e) => setVal(e.target.value)} /></td>
                      <td className="px-3 py-2 text-sm text-muted-foreground">{pp.description || "-"}</td>
                      <td className="px-3 py-2"><Badge variant="outline" className="text-sm">{where || "-"}</Badge></td>
                      <td className="px-3 py-2 font-mono text-sm text-muted-foreground">{dataType}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium text-muted-foreground">헤더 (인증 토큰 등)</p>
          <Button size="sm" variant="outline" className="h-7" onClick={() => setHeaderRows((r) => [...r, { key: "", value: "" }])}><Plus className="h-3.5 w-3.5" /></Button>
        </div>
        {headerRows.map((row, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input className="h-8 text-sm" placeholder="헤더 이름 (예: Authorization)" value={row.key} onChange={(e) => setHeaderRows((rs) => rs.map((x, j) => j === i ? { ...x, key: e.target.value } : x))} />
            <Input className="h-8 text-sm" placeholder="값" value={row.value} onChange={(e) => setHeaderRows((rs) => rs.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} />
            <Button size="icon" variant="ghost" className="h-8 w-8 shrink-0" onClick={() => setHeaderRows((rs) => rs.length > 1 ? rs.filter((_, j) => j !== i) : [{ key: "", value: "" }])} title="삭제"><Minus className="h-4 w-4" /></Button>
          </div>
        ))}
      </div>
      {isWrite && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">요청 바디 (JSON)</p>
          {/* resize-y: 우하단 그립으로 높이 조절. 에디터는 컨테이너를 100% 채우고 자동 재배치. */}
          <div className="resize-y overflow-hidden rounded-md border" style={{ height: 180, minHeight: 96, maxHeight: 600 }}>
            <MonacoEditor
              height="100%"
              language="json"
              theme="light"
              value={bodyText}
              onChange={(v) => setBodyText(v ?? "")}
              options={{
                minimap: { enabled: false }, fontSize: 14, lineNumbers: "on",
                scrollBeyondLastLine: false, automaticLayout: true, tabSize: 2,
                wordWrap: "on", padding: { top: 6, bottom: 6 },
              }}
            />
          </div>
        </div>
      )}

      <div className="flex items-center gap-2">
        <code className="flex-1 truncate rounded bg-muted px-2 py-1 text-sm" title={buildUrl()}>{endpoint.method} {buildUrl()}</code>
        <Button size="sm" onClick={run} disabled={running || !server}>
          {running ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Play className="mr-1 h-4 w-4" />} 호출
        </Button>
      </div>

      <div className="space-y-1.5">
        <p className="text-xs font-medium text-muted-foreground">코드 스니펫</p>
        <SnippetView
          method={endpoint.method}
          url={buildUrl()}
          headers={headersObj()}
          bodyText={bodyText}
          authNote={credId !== "none"}
        />
      </div>

      {result && (
        <div className="space-y-2 rounded-md border p-3">
          <div className="flex items-center gap-2 text-sm">
            <Badge className={result.status >= 200 && result.status < 300 ? "bg-emerald-600 text-white" : "bg-red-600 text-white"}>
              {result.status || "ERR"}
            </Badge>
            <span className="text-muted-foreground">{result.latency_ms}ms</span>
            {result.error && <span className="text-destructive">{result.error}</span>}
          </div>
          <div>
            <p className="mb-1 text-xs text-muted-foreground">응답 본문</p>
            <pre className="max-h-80 overflow-auto rounded bg-muted p-2 text-xs">{result.body || "(빈 응답)"}</pre>
          </div>
        </div>
      )}

      {history.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">최근 호출 이력 (내 호출, 입력 파라미터 포함)</p>
          <div className="overflow-hidden rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-muted/40"><tr>
                <th className="w-[150px] px-3 py-2 text-left font-medium text-muted-foreground">시각</th>
                <th className="w-[70px] px-3 py-2 text-left font-medium text-muted-foreground">상태</th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">입력 파라미터</th>
                <th className="w-[90px] px-3 py-2" />
              </tr></thead>
              <tbody>
                {history.map((h) => {
                  const qp = h.request_input?.query_params ?? {}
                  const pp = h.request_input?.path_params ?? {}
                  const summary = [...Object.entries(pp), ...Object.entries(qp)].map(([k, v]) => `${k}=${v}`).join(", ")
                  return (
                    <tr key={h.id} className="border-t align-top">
                      <td className="px-3 py-2 text-xs text-muted-foreground">{fmtDateTime(h.created_at)}</td>
                      <td className="px-3 py-2"><span className={cn("text-sm", h.ok ? "text-emerald-600" : "text-red-600")}>{h.status_code || "ERR"}</span></td>
                      <td className="px-3 py-2 text-sm break-all">{summary || <span className="text-muted-foreground">-</span>}</td>
                      <td className="px-3 py-2 text-right">
                        <Button size="sm" variant="ghost" className="h-7" onClick={() => loadFromHistory(h)} title="입력값 불러오기"><RotateCcw className="mr-1 h-3.5 w-3.5" /> 불러오기</Button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// GraphQL 변수 타입별 기본값 — 자동 구성 시 사용.
function defaultForGqlType(t: string): unknown {
  const base = (t || "").replace(/[!\s]/g, "")
  if (base.startsWith("[")) return []
  if (/^(Int|Float)$/i.test(base)) return 0
  if (/^Boolean$/i.test(base)) return false
  if (/^(ID|String)$/i.test(base)) return ""
  return null
}

// GraphQL 호출 — 단일 엔드포인트(POST base_url)에 { query, variables } 본문 전송.
function GraphQLTryIt({ api, endpoint, server, credId }: {
  api: ApiDetail; endpoint: ApiEndpoint; server: string; credId: string
}) {
  const args = (endpoint.parameters || []).map((p) => (p as { name?: string }).name).filter(Boolean) as string[]
  const argInline = args.length ? `(${args.map((a) => `${a}: `).join(", ")})` : ""
  const skeleton = `${endpoint.method} {\n  ${endpoint.path}${argInline} {\n    \n  }\n}\n`
  const [query, setQuery] = useState(skeleton)
  const [vars, setVars] = useState("")
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<InvokeResult | null>(null)

  // 쿼리의 $변수(선언/사용)를 파싱해 변수 JSON 을 자동 구성(기존 값은 보존).
  const autoVars = () => {
    const types: Record<string, string> = {}
    const declRe = /\$(\w+)\s*:\s*([[\]\w!]+)/g
    let m: RegExpExecArray | null
    while ((m = declRe.exec(query))) { if (m[1]) types[m[1]] = m[2] ?? "" }
    const useRe = /\$(\w+)/g
    while ((m = useRe.exec(query))) { if (m[1] && !(m[1] in types)) types[m[1]] = "" }
    const names = Object.keys(types)
    if (names.length === 0) { toast.error("쿼리에 변수($var)가 없습니다."); return }
    let existing: Record<string, unknown> = {}
    if (vars.trim()) { try { existing = JSON.parse(vars) } catch { /* 무시하고 새로 구성 */ } }
    const merged: Record<string, unknown> = {}
    for (const n of names) merged[n] = (n in existing) ? existing[n] : defaultForGqlType(types[n] ?? "")
    for (const k of Object.keys(existing)) if (!(k in merged)) merged[k] = existing[k]
    setVars(JSON.stringify(merged, null, 2))
    toast.success(`변수 ${names.length}개를 구성했습니다.`)
  }

  const isSubscription = endpoint.method === "subscription"

  const run = async () => {
    if (isSubscription) { toast.error("구독(subscription)은 WebSocket 기반이라 이 콘솔에서 실행할 수 없습니다."); return }
    if (!server) { toast.error("상단에서 서버(Base URL)를 선택/입력하세요."); return }
    let variables: unknown
    if (vars.trim()) { try { variables = JSON.parse(vars) } catch { toast.error("변수(variables)가 올바른 JSON이 아닙니다."); return } }
    setRunning(true)
    try {
      setResult(await invokeApi({
        method: "POST", url: server,
        headers: { "Content-Type": "application/json" },
        body: { query, ...(variables !== undefined ? { variables } : {}) },
        api_name: api.name,
        credential_id: credId !== "none" ? Number(credId) : undefined,
        endpoint_method: endpoint.method, endpoint_path: endpoint.path,
      }))
    } catch (e) { toast.error(e instanceof Error ? e.message : "호출 실패") } finally { setRunning(false) }
  }

  return (
    <div className="grid gap-3">
      {isSubscription ? (
        <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>구독(subscription)은 <b>WebSocket 기반</b>(graphql-ws 등)이라 이 HTTP 콘솔에서는 <b>실행할 수 없습니다</b>. 아래 쿼리는 문서·복사용이며, 실제 구독은 GraphQL 클라이언트(WebSocket)로 연결하세요.</span>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">GraphQL 은 단일 엔드포인트(POST {server || "<Base URL>"})로 쿼리를 전송합니다. 반환 필드를 선택해 채운 뒤 실행하세요.</p>
      )}
      <div className="space-y-1">
        <p className="text-xs font-medium text-muted-foreground">쿼리</p>
        <div className="resize-y overflow-hidden rounded-md border" style={{ height: 200, minHeight: 120, maxHeight: 600 }}>
          <MonacoEditor
            height="100%" language="graphql" theme="light" value={query} onChange={(v) => setQuery(v ?? "")}
            options={{ minimap: { enabled: false }, fontSize: 14, lineNumbers: "on", scrollBeyondLastLine: false, automaticLayout: true, tabSize: 2, wordWrap: "on", padding: { top: 6, bottom: 6 } }}
          />
        </div>
      </div>
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium text-muted-foreground">변수 (variables, JSON · 선택)</p>
          <Button size="sm" variant="outline" className="h-7" onClick={autoVars}><RotateCcw className="mr-1 h-3.5 w-3.5" /> 쿼리 변수 → JSON 구성</Button>
        </div>
        <div className="resize-y overflow-hidden rounded-md border" style={{ height: 120, minHeight: 72, maxHeight: 400 }}>
          <MonacoEditor
            height="100%" language="json" theme="light" value={vars} onChange={(v) => setVars(v ?? "")}
            options={{ minimap: { enabled: false }, fontSize: 14, lineNumbers: "on", scrollBeyondLastLine: false, automaticLayout: true, tabSize: 2, wordWrap: "on", padding: { top: 6, bottom: 6 } }}
          />
        </div>
      </div>
      <div className="flex items-center gap-2">
        <code className="flex-1 truncate rounded bg-muted px-2 py-1 text-sm" title={server}>POST {server || "<Base URL>"}</code>
        <Button size="sm" onClick={run} disabled={running || !server || isSubscription} title={isSubscription ? "구독은 WebSocket 기반이라 실행 불가" : undefined}>{running ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Play className="mr-1 h-4 w-4" />} 실행</Button>
      </div>
      {result && (
        <div className="space-y-2 rounded-md border p-3">
          <div className="flex items-center gap-2 text-sm">
            <Badge className={result.status >= 200 && result.status < 300 ? "bg-emerald-600 text-white" : "bg-red-600 text-white"}>{result.status || "ERR"}</Badge>
            <span className="text-muted-foreground">{result.latency_ms}ms</span>
            {result.error && <span className="text-destructive">{result.error}</span>}
          </div>
          <div><p className="mb-1 text-xs text-muted-foreground">응답 본문</p><pre className="max-h-80 overflow-auto rounded bg-muted p-2 text-xs">{result.body || "(빈 응답)"}</pre></div>
        </div>
      )}
    </div>
  )
}

// SOAP 호출 — POST(XML Envelope) + SOAPAction 헤더로 전송.
function SoapTryIt({ api, endpoint, server, credId }: {
  api: ApiDetail; endpoint: ApiEndpoint; server: string; credId: string
}) {
  const ex = (endpoint.extra || {}) as { soap_action?: string; soap_version?: string }
  const is12 = ex.soap_version === "1.2"
  const ns = is12 ? "http://www.w3.org/2003/05/soap-envelope" : "http://schemas.xmlsoap.org/soap/envelope/"
  const skeleton = `<?xml version="1.0" encoding="UTF-8"?>\n<soap:Envelope xmlns:soap="${ns}">\n  <soap:Body>\n    <!-- ${endpoint.path} 요청 메시지 -->\n  </soap:Body>\n</soap:Envelope>\n`
  const [body, setBody] = useState(skeleton)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<InvokeResult | null>(null)

  const run = async () => {
    if (!server) { toast.error("상단에서 서버(Base URL)를 선택/입력하세요."); return }
    const headers: Record<string, string> = {
      "Content-Type": is12 ? "application/soap+xml; charset=utf-8" : "text/xml; charset=utf-8",
    }
    if (!is12 && ex.soap_action) headers["SOAPAction"] = `"${ex.soap_action}"`
    setRunning(true)
    try {
      setResult(await invokeApi({
        method: "POST", url: server, headers, body,
        api_name: api.name,
        credential_id: credId !== "none" ? Number(credId) : undefined,
        endpoint_method: endpoint.method, endpoint_path: endpoint.path,
      }))
    } catch (e) { toast.error(e instanceof Error ? e.message : "호출 실패") } finally { setRunning(false) }
  }

  return (
    <div className="grid gap-3">
      <p className="text-xs text-muted-foreground">SOAP {ex.soap_version || "1.1"} — 단일 엔드포인트(POST {server || "<Base URL>"})로 XML Envelope 를 전송합니다.{ex.soap_action ? ` SOAPAction: ${ex.soap_action}` : ""}</p>
      <div className="space-y-1">
        <p className="text-xs font-medium text-muted-foreground">요청 XML (SOAP Envelope)</p>
        <div className="resize-y overflow-hidden rounded-md border" style={{ height: 220, minHeight: 120, maxHeight: 600 }}>
          <MonacoEditor
            height="100%" language="xml" theme="light" value={body} onChange={(v) => setBody(v ?? "")}
            options={{ minimap: { enabled: false }, fontSize: 14, lineNumbers: "on", scrollBeyondLastLine: false, automaticLayout: true, tabSize: 2, wordWrap: "on", padding: { top: 6, bottom: 6 } }}
          />
        </div>
      </div>
      <div className="flex items-center gap-2">
        <code className="flex-1 truncate rounded bg-muted px-2 py-1 text-sm" title={server}>POST {server || "<Base URL>"}</code>
        <Button size="sm" onClick={run} disabled={running || !server}>{running ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Play className="mr-1 h-4 w-4" />} 실행</Button>
      </div>
      {result && (
        <div className="space-y-2 rounded-md border p-3">
          <div className="flex items-center gap-2 text-sm">
            <Badge className={result.status >= 200 && result.status < 300 ? "bg-emerald-600 text-white" : "bg-red-600 text-white"}>{result.status || "ERR"}</Badge>
            <span className="text-muted-foreground">{result.latency_ms}ms</span>
            {result.error && <span className="text-destructive">{result.error}</span>}
          </div>
          <div><p className="mb-1 text-xs text-muted-foreground">응답 본문</p><pre className="max-h-80 overflow-auto rounded bg-muted p-2 text-xs">{result.body || "(빈 응답)"}</pre></div>
        </div>
      )}
    </div>
  )
}

// 파라미터 표시 — 이름 / 위치 / 데이터 타입 / 필수 / 설명 표(읽기 전용).
function ParametersView({ parameters }: { parameters: Record<string, unknown>[] | null }) {
  if (!parameters || parameters.length === 0) return <span className="text-sm text-black">없음</span>
  return (
    <div className="overflow-hidden rounded-md border bg-background">
      <table className="w-full text-sm">
        <thead className="bg-muted/40"><tr>
          <th className="px-3 py-2 text-left font-medium text-muted-foreground">파라미터</th>
          <th className="w-[80px] px-3 py-2 text-left font-medium text-muted-foreground">위치</th>
          <th className="w-[120px] px-3 py-2 text-left font-medium text-muted-foreground">데이터 타입</th>
          <th className="px-3 py-2 text-left font-medium text-muted-foreground">설명</th>
        </tr></thead>
        <tbody>
          {parameters.map((p, i) => {
            const pp = p as { name?: string; in?: string; required?: boolean; description?: string; type?: string; format?: string; schema?: { type?: string; format?: string; items?: { type?: string } } }
            const base = pp.schema?.type ?? pp.type ?? "-"
            const items = pp.schema?.items?.type
            const fmt = pp.schema?.format ?? pp.format
            const dataType = base === "array" && items ? `array<${items}>` : (fmt ? `${base} (${fmt})` : base)
            return (
              <tr key={`${pp.in}:${pp.name}:${i}`} className="border-t align-top">
                <td className="px-3 py-2"><span className="font-mono text-sm text-black">{pp.name}</span>{pp.required && <span className="ml-1 text-red-600" title="필수">*</span>}</td>
                <td className="px-3 py-2"><Badge variant="outline" className="text-sm">{pp.in || "-"}</Badge></td>
                <td className="px-3 py-2 font-mono text-sm text-muted-foreground">{dataType}</td>
                <td className="px-3 py-2 text-sm break-words">{pp.description || "-"}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// 응답 표시 — 상태코드 / 설명 / 본문 스키마로 분리(상태코드만/본문만/둘 다 모두 자연스럽게).
function ResponsesView({ responses }: { responses: Record<string, unknown> | null }) {
  if (!responses || typeof responses !== "object" || Object.keys(responses).length === 0) {
    return <span className="text-sm text-black">없음</span>
  }
  const keys = Object.keys(responses)
  // 상태코드(또는 default) 형태가 아니면 원본 JSON 으로 표시(fallback).
  if (!keys.every((k) => /^\d{3}$/.test(k) || k === "default")) {
    return <JsonBlock value={responses} />
  }
  return (
    <div className="overflow-hidden rounded-md border bg-background">
      <table className="w-full text-sm">
        <thead className="bg-muted/40"><tr>
          <th className="w-[90px] px-3 py-2 text-left font-medium text-muted-foreground">상태코드</th>
          <th className="w-[200px] px-3 py-2 text-left font-medium text-muted-foreground">설명</th>
          <th className="px-3 py-2 text-left font-medium text-muted-foreground">본문 스키마</th>
        </tr></thead>
        <tbody>
          {keys.map((code) => {
            const d = (responses[code] && typeof responses[code] === "object" ? responses[code] : {}) as { description?: string; content?: Record<string, { schema?: unknown }>; schema?: unknown }
            let body: unknown = null
            if (d.content && typeof d.content === "object") {
              const first = Object.values(d.content)[0] as { schema?: unknown } | undefined
              body = first?.schema ?? null
            } else if (d.schema !== undefined) { body = d.schema }
            const color = /^2|^3/.test(code) ? "text-emerald-600" : /^4|^5/.test(code) ? "text-red-600" : "text-foreground"
            return (
              <tr key={code} className="border-t align-top">
                <td className="px-3 py-2"><span className={cn("text-sm font-semibold", color)}>{code}</span></td>
                <td className="w-[200px] px-3 py-2 text-sm break-words">{d.description || "-"}</td>
                <td className="px-3 py-2">{body !== null && body !== undefined ? <JsonBlock value={body} /> : <span className="text-sm text-black">없음</span>}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 엔드포인트 목록 (행 확장 → 파라미터/요청/응답)
// ---------------------------------------------------------------------------
const ENDPOINT_UNTAGGED = "__untagged__"

function EndpointRows({ endpoints, open, toggle, keyOf, api, server, credId, favorites, onToggleFav, canEdit, onEdit, onDelete }: {
  endpoints: ApiEndpoint[]
  open: Set<string>
  toggle: (k: string) => void
  keyOf: (e: ApiEndpoint) => string
  api: ApiDetail
  server: string
  credId: string
  favorites: Set<string>
  onToggleFav: (method: string, path: string) => void
  canEdit?: boolean
  onEdit?: (e: ApiEndpoint) => void
  onDelete?: (e: ApiEndpoint) => void
}) {
  return (
    <div className="overflow-hidden rounded-md border">
      <table className="w-full text-sm">
        <tbody>
          {endpoints.map((e) => {
            const k = keyOf(e)
            const isOpen = open.has(k)
            return (
              <Fragment key={k}>
                <tr className="cursor-pointer border-b hover:bg-muted/40" onClick={() => toggle(k)}>
                  <td className="w-6 py-2 pl-3 align-middle text-muted-foreground">{isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}</td>
                  <td className="py-2 pr-3 align-middle">
                    <div className="flex items-center gap-2">
                      <MethodBadge method={e.method} />
                      <span className="text-sm">{e.path}</span>
                      {e.summary && <span className="text-sm text-muted-foreground">— {e.summary}</span>}
                    </div>
                  </td>
                  <td className="py-2 pr-3 align-middle text-right whitespace-nowrap">
                    {canEdit && (
                      <>
                        <button type="button" onClick={(ev) => { ev.stopPropagation(); onEdit?.(e) }} title="수정" aria-label="수정" className="inline-flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-muted"><Pencil className="h-4 w-4" /></button>
                        <button type="button" onClick={(ev) => { ev.stopPropagation(); onDelete?.(e) }} title="삭제" aria-label="삭제" className="inline-flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-muted"><Trash2 className="h-4 w-4" /></button>
                      </>
                    )}
                    {(() => {
                      const fav = favorites.has(`${e.method} ${e.path}`)
                      return (
                        <button
                          type="button"
                          onClick={(ev) => { ev.stopPropagation(); onToggleFav(e.method, e.path) }}
                          title={fav ? "즐겨찾기 해제" : "즐겨찾기"}
                          aria-label={fav ? "즐겨찾기 해제" : "즐겨찾기"}
                          className="inline-flex h-7 w-7 items-center justify-center rounded hover:bg-muted"
                        >
                          <Star className={cn("h-4 w-4", fav ? "fill-amber-400 text-amber-400" : "text-muted-foreground")} />
                        </button>
                      )
                    })()}
                  </td>
                </tr>
                {isOpen && (
                  <tr className="border-b bg-muted/20">
                    <td />
                    <td colSpan={2} className="px-3 py-3">
                      <Tabs defaultValue="doc">
                        <TabsList>
                          <TabsTrigger value="doc">문서</TabsTrigger>
                          {["REST", "GraphQL", "SOAP"].includes(api.protocol || "REST") && <TabsTrigger value="try">호출</TabsTrigger>}
                        </TabsList>
                        <TabsContent value="doc" className="mt-3">
                          <div className="overflow-hidden rounded-md border bg-background">
                            <table className="w-full table-fixed border-collapse text-sm">
                              <colgroup><col className="w-[120px]" /><col /></colgroup>
                              <tbody>
                                {([
                                  { label: "설명", node: <span className="text-sm text-black">{e.description || "없음"}</span> },
                                  ...(e.extra && Object.keys(e.extra).length > 0 ? [{ label: "추가 정보", node: <JsonBlock value={e.extra} /> }] : []),
                                  // 파라미터/인자: REST·GraphQL 만 / 요청 바디·응답(상태코드): REST 만
                                  ...(["REST", "GraphQL"].includes(api.protocol || "REST") ? [{ label: (api.protocol || "REST") === "GraphQL" ? "인자" : "파라미터", node: <ParametersView parameters={e.parameters} /> }] : []),
                                  ...((api.protocol || "REST") === "REST" ? [
                                    { label: "요청 바디", node: <JsonBlock value={e.request_body} /> },
                                    { label: "응답", node: <ResponsesView responses={e.responses} /> },
                                  ] : []),
                                ] as { label: string; node: ReactNode }[]).map((r) => (
                                  <tr key={r.label}>
                                    <th className="border bg-muted/50 px-3 py-2 text-left align-top font-medium text-black">{r.label}</th>
                                    <td className="border px-3 py-2 align-top break-words">{r.node}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </TabsContent>
                        {["REST", "GraphQL", "SOAP"].includes(api.protocol || "REST") && (
                          <TabsContent value="try" className="mt-3">
                            {(api.protocol || "REST") === "GraphQL" ? <GraphQLTryIt api={api} endpoint={e} server={server} credId={credId} />
                              : (api.protocol || "REST") === "SOAP" ? <SoapTryIt api={api} endpoint={e} server={server} credId={credId} />
                              : <EndpointTryIt api={api} endpoint={e} server={server} credId={credId} />}
                          </TabsContent>
                        )}
                      </Tabs>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// 엔드포인트 탭 헤더 — 페이지 설명 + HTTP 메서드 색상 범례 + 모두 접기.
function EndpointsHeader({ hasOpen, onCollapseAll, legendMethods = ["GET", "POST", "PUT", "PATCH", "DELETE"] }: { hasOpen?: boolean; onCollapseAll?: () => void; legendMethods?: string[] }) {
  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {legendMethods.map((m) => (
            <span key={m} className="inline-flex items-center gap-1.5"><MethodBadge method={m} /></span>
          ))}
        </div>
        <details className="rounded-md border bg-muted/20 p-2">
          <summary className="cursor-pointer text-[11px] font-medium text-muted-foreground">범례 도움말 (펼치기)</summary>
          <div className="mt-2 space-y-2 text-xs text-muted-foreground">
            <p>색상 배지는 각 엔드포인트의 <b>HTTP 메서드(REST)</b> 또는 <b>오퍼레이션 유형(GraphQL)</b>을 나타냅니다. 동작의 성격(조회/변경 등)을 색으로 빠르게 구분하기 위한 것입니다.</p>
            <div>
              <p className="font-medium text-foreground">REST 메서드</p>
              <ul className="ml-4 list-disc space-y-0.5">
                <li><b>GET</b> — 리소스 조회(읽기, 부수효과 없음)</li>
                <li><b>POST</b> — 리소스 생성·작업 실행</li>
                <li><b>PUT</b> — 리소스 전체 교체(멱등)</li>
                <li><b>PATCH</b> — 리소스 부분 수정</li>
                <li><b>DELETE</b> — 리소스 삭제</li>
                <li><b>HEAD · OPTIONS</b> — 헤더 조회·사전 요청(CORS 등)</li>
              </ul>
            </div>
            <div>
              <p className="font-medium text-foreground">GraphQL 오퍼레이션</p>
              <ul className="ml-4 list-disc space-y-0.5">
                <li><b>QUERY</b> — 데이터 조회(읽기)</li>
                <li><b>MUTATION</b> — 데이터 변경(생성·수정·삭제)</li>
                <li><b>SUBSCRIPTION</b> — 실시간 구독(WebSocket 기반, HTTP 콘솔로는 실행되지 않을 수 있음)</li>
              </ul>
            </div>
            <div>
              <p className="font-medium text-foreground">gRPC RPC 유형</p>
              <ul className="ml-4 list-disc space-y-0.5">
                <li><b>UNARY</b> — 단일 요청 · 단일 응답</li>
                <li><b>SERVER-STREAMING</b> — 단일 요청 · 응답 스트림(서버→클라이언트)</li>
                <li><b>CLIENT-STREAMING</b> — 요청 스트림 · 단일 응답(클라이언트→서버)</li>
                <li><b>BIDI-STREAMING</b> — 양방향 스트림</li>
              </ul>
            </div>
            <div>
              <p className="font-medium text-foreground">SOAP</p>
              <ul className="ml-4 list-disc space-y-0.5">
                <li><b>OPERATION</b> — WSDL 오퍼레이션(입력/출력 XML 메시지 + SOAPAction)</li>
              </ul>
            </div>
            <div>
              <p className="font-medium text-foreground">Webhook / 이벤트</p>
              <ul className="ml-4 list-disc space-y-0.5">
                <li><b>PUBLISH</b> — 이벤트 발행(API 가 구독자에게 전송)</li>
                <li><b>SUBSCRIBE</b> — 이벤트 수신(외부 이벤트 구독)</li>
              </ul>
            </div>
          </div>
        </details>
      </div>
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          스펙에서 추출한 엔드포인트(메서드·경로)를 카테고리(태그)별로 묶어 보여주는 화면입니다. 각 행을 펼치면 문서(파라미터·요청 바디·응답 스키마)와 호출(Try-it)을 함께 확인할 수 있습니다. 호출에 쓸 서버·자격증명은 아래에서 한 번만 지정하면 모든 엔드포인트에 공유됩니다.
        </p>
        {onCollapseAll && (
          <Button size="sm" variant="outline" className="shrink-0" disabled={!hasOpen} onClick={onCollapseAll}>
            <ChevronUp className="mr-1 h-4 w-4" /> 모두 접기
          </Button>
        )}
      </div>
    </div>
  )
}

// 수동 엔드포인트 추가/편집 다이얼로그 — 기본 필드 + 파라미터(구조적 행) + 요청/응답 스키마(JSON).
const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
// 프로토콜별 오퍼레이션 폼 설정 — 유형(method)·식별자(path) 라벨, 추가 필드(extra), 파라미터/바디/응답 모드.
// params: "rest"=위치별 · "args"=인자 · "none"=없음 / bodyMode: "rest"=요청 바디 · "payload"=페이로드 · "none" / responses: HTTP 상태코드 응답
type ProtoExtra = { key: string; label: string; placeholder?: string; options?: string[] }
type ProtoParams = "rest" | "args" | "none"
type ProtoBody = "rest" | "payload" | "none"
const PROTOCOL_OPS: Record<string, { typeLabel: string; typeOptions: string[]; idLabel: string; idPlaceholder: string; extras: ProtoExtra[]; params: ProtoParams; bodyMode: ProtoBody; responses: boolean }> = {
  REST: { typeLabel: "메서드", typeOptions: HTTP_METHODS, idLabel: "경로(path)", idPlaceholder: "/users/{id}", extras: [], params: "rest", bodyMode: "rest", responses: true },
  GraphQL: { typeLabel: "오퍼레이션 유형", typeOptions: ["query", "mutation", "subscription"], idLabel: "오퍼레이션 이름", idPlaceholder: "getUser", extras: [{ key: "return_type", label: "반환 타입", placeholder: "User!" }], params: "args", bodyMode: "none", responses: false },
  gRPC: { typeLabel: "RPC 유형", typeOptions: ["unary", "server-streaming", "client-streaming", "bidi-streaming"], idLabel: "서비스.메서드", idPlaceholder: "UserService.GetUser", extras: [{ key: "request_message", label: "요청 메시지", placeholder: "GetUserRequest" }, { key: "response_message", label: "응답 메시지", placeholder: "GetUserResponse" }], params: "none", bodyMode: "none", responses: false },
  SOAP: { typeLabel: "오퍼레이션 유형", typeOptions: ["operation"], idLabel: "오퍼레이션 이름", idPlaceholder: "GetUser", extras: [{ key: "soap_action", label: "SOAPAction", placeholder: "urn:GetUser" }, { key: "input_message", label: "입력 메시지", placeholder: "GetUserRequest" }, { key: "output_message", label: "출력 메시지", placeholder: "GetUserResponse" }, { key: "soap_version", label: "SOAP 버전", options: ["1.1", "1.2"] }, { key: "style", label: "스타일", options: ["document", "rpc"] }], params: "none", bodyMode: "none", responses: false },
  Webhook: { typeLabel: "방향", typeOptions: ["publish", "subscribe"], idLabel: "채널/이벤트", idPlaceholder: "order.created", extras: [{ key: "delivery", label: "전송 방식", options: ["HTTP 콜백", "Kafka", "MQTT", "AMQP", "WebSocket"] }, { key: "signature_header", label: "서명 헤더", placeholder: "X-Hub-Signature-256" }], params: "none", bodyMode: "payload", responses: false },
  기타: { typeLabel: "유형", typeOptions: ["operation"], idLabel: "식별자", idPlaceholder: "", extras: [], params: "none", bodyMode: "none", responses: false },
}
const protocolOps = (p?: string | null) => PROTOCOL_OPS[p || "REST"] ?? PROTOCOL_OPS.REST!
// 표준 gRPC 상태코드(문서화용)
const GRPC_STATUS_CODES = ["OK", "CANCELLED", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED", "NOT_FOUND", "ALREADY_EXISTS", "PERMISSION_DENIED", "UNAUTHENTICATED", "RESOURCE_EXHAUSTED", "FAILED_PRECONDITION", "ABORTED", "OUT_OF_RANGE", "UNIMPLEMENTED", "INTERNAL", "UNAVAILABLE", "DATA_LOSS"]
const PARAM_LOCATIONS = ["query", "path", "header"]
const PARAM_TYPES = ["string", "integer", "number", "boolean", "array", "object"]
type ParamRow = { name: string; location: string; type: string; required: boolean }
type BodyField = { name: string; type: string; required: boolean; description: string }
type RespRow = { code: string; description: string }

function EndpointEditDialog({ apiName, protocol, endpoint, open, onOpenChange, onSaved }: {
  apiName: string; protocol?: string | null; endpoint: ApiEndpoint | null; open: boolean; onOpenChange: (o: boolean) => void; onSaved: () => void
}) {
  const ops = protocolOps(protocol)
  const isGrpc = (protocol || "") === "gRPC"
  const [method, setMethod] = useState(ops.typeOptions[0] ?? "GET")
  const [extra, setExtra] = useState<Record<string, string>>({})
  const [statusCodes, setStatusCodes] = useState<string[]>([])
  const [path, setPath] = useState("")
  const [summary, setSummary] = useState("")
  const [description, setDescription] = useState("")
  const [tags, setTags] = useState("")
  const [params, setParams] = useState<ParamRow[]>([])
  const [reqMode, setReqMode] = useState<"fields" | "json">("fields")
  const [bodyFields, setBodyFields] = useState<BodyField[]>([])
  const [reqBody, setReqBody] = useState("")
  const [respMode, setRespMode] = useState<"rows" | "json">("rows")
  const [respRows, setRespRows] = useState<RespRow[]>([])
  const [resp, setResp] = useState("")
  const [busy, setBusy] = useState(false)

  // 다이얼로그가 열릴 때 대상 엔드포인트 값으로 초기화(추가는 빈 값).
  useEffect(() => {
    if (!open) return
    if (endpoint) {
      setMethod(endpoint.method || ops.typeOptions[0] || "GET")
      const ex = (endpoint.extra || {}) as Record<string, unknown>
      setExtra(Object.fromEntries(ops.extras.map((e) => [e.key, ex[e.key] != null ? String(ex[e.key]) : ""])))
      setStatusCodes(Array.isArray(ex.status_codes) ? (ex.status_codes as unknown[]).map(String) : [])
      setPath(endpoint.path || "")
      setSummary(endpoint.summary || "")
      setDescription(endpoint.description || "")
      setTags((endpoint.tags || []).join(", "))
      setParams((endpoint.parameters || []).map((p) => {
        const pp = p as { name?: string; in?: string; required?: boolean; type?: string; schema?: { type?: string } }
        return { name: pp.name || "", location: pp.in || "query", type: pp.schema?.type || pp.type || "string", required: !!pp.required }
      }))
      // 요청 바디: object 스키마면 필드 빌더로, 아니면 JSON 모드.
      const rb = endpoint.request_body as { type?: string; properties?: Record<string, { type?: string; description?: string }>; required?: string[] } | null
      if (rb && rb.properties && typeof rb.properties === "object") {
        const req = new Set(rb.required || [])
        setBodyFields(Object.entries(rb.properties).map(([name, p]) => ({ name, type: p?.type || "string", required: req.has(name), description: p?.description || "" })))
        setReqMode("fields"); setReqBody("")
      } else if (endpoint.request_body) {
        setReqMode("json"); setReqBody(JSON.stringify(endpoint.request_body, null, 2)); setBodyFields([])
      } else {
        setReqMode("fields"); setBodyFields([]); setReqBody("")
      }
      // 응답: 코드→{description} 형태면 행으로, 아니면 JSON.
      const rs = endpoint.responses as Record<string, { description?: string }> | null
      if (rs && typeof rs === "object") {
        setRespRows(Object.entries(rs).map(([code, v]) => ({ code, description: (v && typeof v === "object" ? v.description : "") || "" })))
        setRespMode("rows"); setResp("")
      } else {
        setRespMode("rows"); setRespRows([]); setResp("")
      }
    } else {
      setMethod(ops.typeOptions[0] || "GET"); setExtra({}); setStatusCodes([]); setPath(""); setSummary(""); setDescription(""); setTags(""); setParams([])
      setReqMode("fields"); setBodyFields([]); setReqBody(""); setRespMode("rows"); setRespRows([]); setResp("")
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, endpoint])

  const save = async () => {
    if (!path.trim()) { toast.error("경로(path)를 입력하세요."); return }
    let request_body: Record<string, unknown> | null = null
    let responses: Record<string, unknown> | null = null
    // 요청 바디
    if (reqMode === "fields") {
      const props: Record<string, unknown> = {}
      const required: string[] = []
      for (const f of bodyFields) {
        const nm = f.name.trim()
        if (!nm) continue
        props[nm] = { type: f.type, ...(f.description.trim() ? { description: f.description.trim() } : {}) }
        if (f.required) required.push(nm)
      }
      request_body = Object.keys(props).length ? { type: "object", properties: props, ...(required.length ? { required } : {}) } : null
    } else {
      try { if (reqBody.trim()) request_body = JSON.parse(reqBody) } catch { toast.error("요청 바디 스키마가 올바른 JSON이 아닙니다."); return }
    }
    // 응답
    if (respMode === "rows") {
      const r: Record<string, unknown> = {}
      for (const row of respRows) { const c = row.code.trim(); if (c) r[c] = { description: row.description.trim() } }
      responses = Object.keys(r).length ? r : null
    } else {
      try { if (resp.trim()) responses = JSON.parse(resp) } catch { toast.error("응답 스키마가 올바른 JSON이 아닙니다."); return }
    }
    const extraObj: Record<string, unknown> = {}
    for (const e of ops.extras) { const v = (extra[e.key] || "").trim(); if (v) extraObj[e.key] = v }
    if (isGrpc && statusCodes.length) extraObj.status_codes = statusCodes
    const payload: EndpointPayload = {
      method, path: path.trim(),
      summary: summary.trim() || null,
      description: description.trim() || null,
      tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      parameters: params.filter((p) => p.name.trim()).map((p) => ({ name: p.name.trim(), in: p.location, required: p.required, schema: { type: p.type } })),
      request_body, responses,
      extra: Object.keys(extraObj).length ? extraObj : null,
    }
    setBusy(true)
    try {
      if (endpoint) await updateEndpoint(apiName, endpoint.id, payload)
      else await createEndpoint(apiName, payload)
      onOpenChange(false)
      onSaved()
      toast.success(endpoint ? "엔드포인트를 수정했습니다." : "엔드포인트를 추가했습니다.")
    } catch (e) { toast.error(e instanceof Error ? e.message : "저장 실패") } finally { setBusy(false) }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader><DialogTitle>{endpoint ? "엔드포인트 수정" : "엔드포인트 추가"}</DialogTitle></DialogHeader>
        <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
          <div className="flex gap-2">
            <div className="space-y-1">
              <Label>{ops.typeLabel}</Label>
              <Select value={method} onValueChange={setMethod}>
                <SelectTrigger className="h-8 w-[150px] text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>{ops.typeOptions.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="flex-1 space-y-1">
              <Label>{ops.idLabel}</Label>
              <Input className="h-8 text-sm" placeholder={ops.idPlaceholder} value={path} onChange={(e) => setPath(e.target.value)} />
            </div>
          </div>
          {ops.extras.length > 0 && (
            <div className="grid grid-cols-2 gap-2">
              {ops.extras.map((ex) => (
                <div key={ex.key} className="space-y-1">
                  <Label>{ex.label}</Label>
                  {ex.options ? (
                    <Select value={extra[ex.key] || "none"} onValueChange={(v) => setExtra((s) => ({ ...s, [ex.key]: v === "none" ? "" : v }))}>
                      <SelectTrigger className="h-8 text-sm"><SelectValue placeholder="미지정" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none" className="text-muted-foreground">미지정</SelectItem>
                        {ex.options.map((o) => <SelectItem key={o} value={o}>{o}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  ) : (
                    <Input className="h-8 text-sm" placeholder={ex.placeholder} value={extra[ex.key] ?? ""} onChange={(e) => setExtra((s) => ({ ...s, [ex.key]: e.target.value }))} />
                  )}
                </div>
              ))}
            </div>
          )}
          <div className="space-y-1"><Label>요약</Label><Input className="h-8 text-sm" value={summary} onChange={(e) => setSummary(e.target.value)} /></div>
          <div className="space-y-1"><Label>설명</Label><Textarea className="min-h-16 text-sm" value={description} onChange={(e) => setDescription(e.target.value)} /></div>
          <div className="space-y-1"><Label>태그 (입력 후 Enter)</Label><TagsInput value={tags} onChange={setTags} /></div>

          {ops.params !== "none" && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label>{ops.params === "rest" ? "파라미터" : "인자(arguments)"}</Label>
              <Button size="sm" variant="outline" className="h-7" onClick={() => setParams((r) => [...r, { name: "", location: ops.params === "rest" ? "query" : "argument", type: "string", required: false }])}><Plus className="h-3.5 w-3.5" /></Button>
            </div>
            {params.map((p, i) => (
              <div key={i} className="flex items-center gap-2">
                <Input className="h-8 flex-1 text-sm" placeholder="이름" value={p.name} onChange={(e) => setParams((rs) => rs.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
                {ops.params === "rest" && (
                  <Select value={p.location} onValueChange={(v) => setParams((rs) => rs.map((x, j) => j === i ? { ...x, location: v } : x))}>
                    <SelectTrigger className="h-8 w-[100px] text-sm"><SelectValue /></SelectTrigger>
                    <SelectContent>{PARAM_LOCATIONS.map((l) => <SelectItem key={l} value={l}>{l}</SelectItem>)}</SelectContent>
                  </Select>
                )}
                <Select value={p.type} onValueChange={(v) => setParams((rs) => rs.map((x, j) => j === i ? { ...x, type: v } : x))}>
                  <SelectTrigger className="h-8 w-[110px] text-sm"><SelectValue /></SelectTrigger>
                  <SelectContent>{PARAM_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
                </Select>
                <label className="flex items-center gap-1 text-xs text-muted-foreground">
                  <input type="checkbox" checked={p.required} onChange={(e) => setParams((rs) => rs.map((x, j) => j === i ? { ...x, required: e.target.checked } : x))} /> 필수
                </label>
                <Button size="icon" variant="ghost" className="h-8 w-8 shrink-0" onClick={() => setParams((rs) => rs.filter((_, j) => j !== i))}><Minus className="h-4 w-4" /></Button>
              </div>
            ))}
          </div>
          )}

          {isGrpc && (
            <div className="space-y-1.5">
              <Label>반환 가능 상태코드 (gRPC)</Label>
              <div className="flex flex-wrap gap-1.5">
                {GRPC_STATUS_CODES.map((c) => {
                  const on = statusCodes.includes(c)
                  return (
                    <button key={c} type="button"
                      onClick={() => setStatusCodes((s) => on ? s.filter((x) => x !== c) : [...s, c])}
                      className={cn("rounded border px-2 py-0.5 text-[11px]", on ? "border-primary bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:bg-muted")}>
                      {c}
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {ops.bodyMode !== "none" && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label>{ops.bodyMode === "payload" ? "페이로드 스키마 (선택)" : "요청 바디 (선택)"}</Label>
              <div className="flex items-center gap-2">
                {reqMode === "fields" && <Button size="sm" variant="outline" className="h-7" onClick={() => setBodyFields((r) => [...r, { name: "", type: "string", required: false, description: "" }])}><Plus className="h-3.5 w-3.5" /></Button>}
                <div className="flex overflow-hidden rounded-md border">
                  <button type="button" onClick={() => setReqMode("fields")} className={cn("px-2 py-1 text-xs", reqMode === "fields" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground")}>필드</button>
                  <button type="button" onClick={() => setReqMode("json")} className={cn("px-2 py-1 text-xs", reqMode === "json" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground")}>JSON</button>
                </div>
              </div>
            </div>
            {reqMode === "fields" ? (
              bodyFields.length === 0 ? <p className="text-xs text-muted-foreground">+ 버튼으로 본문 필드를 추가하세요(JSON Schema 자동 생성).</p> : bodyFields.map((f, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Input className="h-8 w-[140px] text-sm" placeholder="필드명" value={f.name} onChange={(e) => setBodyFields((rs) => rs.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
                  <Select value={f.type} onValueChange={(v) => setBodyFields((rs) => rs.map((x, j) => j === i ? { ...x, type: v } : x))}>
                    <SelectTrigger className="h-8 w-[110px] text-sm"><SelectValue /></SelectTrigger>
                    <SelectContent>{PARAM_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
                  </Select>
                  <Input className="h-8 flex-1 text-sm" placeholder="설명" value={f.description} onChange={(e) => setBodyFields((rs) => rs.map((x, j) => j === i ? { ...x, description: e.target.value } : x))} />
                  <label className="flex items-center gap-1 text-xs text-muted-foreground"><input type="checkbox" checked={f.required} onChange={(e) => setBodyFields((rs) => rs.map((x, j) => j === i ? { ...x, required: e.target.checked } : x))} /> 필수</label>
                  <Button size="icon" variant="ghost" className="h-8 w-8 shrink-0" onClick={() => setBodyFields((rs) => rs.filter((_, j) => j !== i))}><Minus className="h-4 w-4" /></Button>
                </div>
              ))
            ) : (
              <Textarea className="min-h-20 font-mono text-xs" placeholder='{ "type": "object", "properties": { ... } }' value={reqBody} onChange={(e) => setReqBody(e.target.value)} />
            )}
          </div>
          )}

          {ops.responses && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label>응답 (선택)</Label>
              <div className="flex items-center gap-2">
                {respMode === "rows" && <Button size="sm" variant="outline" className="h-7" onClick={() => setRespRows((r) => [...r, { code: "", description: "" }])}><Plus className="h-3.5 w-3.5" /></Button>}
                <div className="flex overflow-hidden rounded-md border">
                  <button type="button" onClick={() => setRespMode("rows")} className={cn("px-2 py-1 text-xs", respMode === "rows" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground")}>행</button>
                  <button type="button" onClick={() => setRespMode("json")} className={cn("px-2 py-1 text-xs", respMode === "json" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground")}>JSON</button>
                </div>
              </div>
            </div>
            {respMode === "rows" ? (
              respRows.length === 0 ? <p className="text-xs text-muted-foreground">+ 버튼으로 상태코드(예: 200, 404)와 설명을 추가하세요.</p> : respRows.map((row, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Input className="h-8 w-[100px] text-sm" placeholder="상태코드" value={row.code} onChange={(e) => setRespRows((rs) => rs.map((x, j) => j === i ? { ...x, code: e.target.value } : x))} />
                  <Input className="h-8 flex-1 text-sm" placeholder="설명" value={row.description} onChange={(e) => setRespRows((rs) => rs.map((x, j) => j === i ? { ...x, description: e.target.value } : x))} />
                  <Button size="icon" variant="ghost" className="h-8 w-8 shrink-0" onClick={() => setRespRows((rs) => rs.filter((_, j) => j !== i))}><Minus className="h-4 w-4" /></Button>
                </div>
              ))
            ) : (
              <Textarea className="min-h-20 font-mono text-xs" placeholder='{ "200": { "description": "ok" } }' value={resp} onChange={(e) => setResp(e.target.value)} />
            )}
          </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>취소</Button>
          <Button onClick={save} disabled={busy}>{busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}저장</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function EndpointsTab({ api, credentials, endpointFilter, hideHeader, canEdit, onEndpointsChanged }: {
  api: ApiDetail; credentials: ApiCredential[]; endpointFilter?: Set<string>; hideHeader?: boolean; canEdit?: boolean; onEndpointsChanged?: () => void
}) {
  const [editOpen, setEditOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<ApiEndpoint | null>(null)
  const openAdd = () => { setEditTarget(null); setEditOpen(true) }
  const openEdit = (e: ApiEndpoint) => { setEditTarget(e); setEditOpen(true) }
  const onDelete = async (e: ApiEndpoint) => {
    if (!confirm(`${e.method} ${e.path} 엔드포인트를 삭제하시겠습니까?`)) return
    try { await deleteEndpoint(api.name, e.id); onEndpointsChanged?.(); toast.success("삭제했습니다.") }
    catch (err) { toast.error(err instanceof Error ? err.message : "삭제 실패") }
  }
  const legendMethods = (api.protocol || "REST") === "REST" ? ["GET", "POST", "PUT", "PATCH", "DELETE"] : protocolOps(api.protocol).typeOptions
  const manualBar = canEdit ? (
    <div className="flex justify-end">
      <Button size="sm" onClick={openAdd}><Plus className="mr-1 h-4 w-4" /> 엔드포인트 추가</Button>
    </div>
  ) : null
  const editDialog = canEdit ? (
    <EndpointEditDialog apiName={api.name} protocol={api.protocol} endpoint={editTarget} open={editOpen} onOpenChange={setEditOpen} onSaved={() => onEndpointsChanged?.()} />
  ) : null
  const endpoints = endpointFilter ? api.endpoints.filter((e) => endpointFilter.has(`${e.method} ${e.path}`)) : api.endpoints
  const tagDefs = api.tag_defs ?? []
  const servers = api.servers.length > 0 ? api.servers.map((s) => s.url) : (api.base_url ? [api.base_url] : [])
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [server, setServer] = useState(servers[0] ?? "")
  const [credId, setCredId] = useState<string>("none")
  const [favorites, setFavorites] = useState<Set<string>>(new Set())
  const toggle = (k: string) => setOpen((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n })

  const favKey = (m: string, p: string) => `${m} ${p}`
  useEffect(() => {
    fetchApiFavorites(api.name).then((fs) => setFavorites(new Set(fs.map((f) => favKey(f.method, f.path))))).catch(() => {})
  }, [api.name])
  const toggleFav = async (method: string, path: string) => {
    const k = favKey(method, path)
    const has = favorites.has(k)
    try {
      if (has) await removeApiFavorite(api.name, method, path)
      else await addApiFavorite(api.name, method, path)
      setFavorites((s) => { const n = new Set(s); if (has) n.delete(k); else n.add(k); return n })
    } catch (e) { toast.error(e instanceof Error ? e.message : "즐겨찾기 변경 실패") }
  }

  if (endpoints.length === 0) {
    return <div className="space-y-4">{!hideHeader && <EndpointsHeader legendMethods={legendMethods} />}{manualBar}<p className="text-sm text-muted-foreground">엔드포인트가 없습니다.</p>{editDialog}</div>
  }

  // 호출(Try-it) 공유 컨트롤 — 서버/자격증명은 API 레벨이라 모든 행이 공유한다.
  const controls = (
    <div className="grid gap-3 rounded-md border bg-muted/20 p-3 sm:grid-cols-2">
      <div className="space-y-1.5">
        <p className="text-xs font-medium text-muted-foreground">서버 / Base URL (호출 시 사용)</p>
        {servers.length > 0 ? (
          <Select value={server} onValueChange={setServer}>
            <SelectTrigger className="h-9 text-sm"><SelectValue placeholder="서버 선택" /></SelectTrigger>
            <SelectContent>{servers.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
          </Select>
        ) : (
          <Input className="h-9 text-sm" placeholder="https://api.example.com" value={server} onChange={(e) => setServer(e.target.value)} />
        )}
      </div>
      <div className="space-y-1.5">
        <p className="text-xs font-medium text-muted-foreground">자격증명 (시크릿은 서버 보관·주입)</p>
        <Select value={credId} onValueChange={setCredId}>
          <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="none">사용 안 함(수동 헤더)</SelectItem>
            {credentials.map((c) => <SelectItem key={c.id} value={String(c.id)}>{c.label} ({c.type})</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
    </div>
  )

  // 카테고리(태그)별 그룹화 — tagDefs 순서를 우선, 그 외 태그·미분류는 뒤에 배치.
  const descOf = new Map(tagDefs.map((t) => [t.name, t.description]))
  const order: string[] = tagDefs.map((t) => t.name)
  const groups = new Map<string, ApiEndpoint[]>()
  const push = (tag: string, e: ApiEndpoint) => {
    if (!groups.has(tag)) { groups.set(tag, []); if (!order.includes(tag)) order.push(tag) }
    groups.get(tag)!.push(e)
  }
  for (const e of endpoints) {
    const tags = e.tags && e.tags.length > 0 ? e.tags : [ENDPOINT_UNTAGGED]
    for (const t of tags) push(t, e)
  }
  const visible = order.filter((t) => (groups.get(t)?.length ?? 0) > 0)

  // 태그가 전혀 없으면(미분류 단일 그룹) 그룹 헤더 없이 단순 목록으로 표시.
  if (visible.length === 1 && visible[0] === ENDPOINT_UNTAGGED) {
    return (
      <div className="space-y-4">
        {!hideHeader && <EndpointsHeader hasOpen={open.size > 0} onCollapseAll={() => setOpen(new Set())} legendMethods={legendMethods} />}
        {controls}
        {manualBar}
        <EndpointRows endpoints={endpoints} open={open} toggle={toggle} keyOf={(e) => `${ENDPOINT_UNTAGGED}#${e.id}`} api={api} server={server} credId={credId} favorites={favorites} onToggleFav={toggleFav} canEdit={canEdit} onEdit={openEdit} onDelete={onDelete} />
        {editDialog}
      </div>
    )
  }

  return (
    <TooltipProvider delayDuration={150}>
      <div className="space-y-4">
        {!hideHeader && <EndpointsHeader hasOpen={open.size > 0} onCollapseAll={() => setOpen(new Set())} legendMethods={legendMethods} />}
        {controls}
        {manualBar}
        {visible.map((tag) => {
          const eps = groups.get(tag) ?? []
          const desc = descOf.get(tag)
          const name = tag === ENDPOINT_UNTAGGED ? "기타" : tag
          return (
            <div key={tag} className="space-y-1.5">
              <div className="flex flex-wrap items-baseline gap-x-2">
                {desc ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="cursor-help text-sm font-semibold text-foreground underline decoration-dotted decoration-muted-foreground/50 underline-offset-2">{name}</span>
                    </TooltipTrigger>
                    <TooltipContent side="right" className="max-w-sm whitespace-pre-wrap">{desc}</TooltipContent>
                  </Tooltip>
                ) : (
                  <span className="text-sm font-semibold text-foreground">{name}</span>
                )}
                <span className="text-xs text-muted-foreground">{eps.length}개</span>
              </div>
              <EndpointRows endpoints={eps} open={open} toggle={toggle} keyOf={(e) => `${tag}#${e.id}`} api={api} server={server} credId={credId} favorites={favorites} onToggleFav={toggleFav} canEdit={canEdit} onEdit={openEdit} onDelete={onDelete} />
            </div>
          )
        })}
        {editDialog}
      </div>
    </TooltipProvider>
  )
}

// ---------------------------------------------------------------------------
// 인증 탭 — 보안 스킴 + 저장된 자격증명(암호화 보관) 관리
// ---------------------------------------------------------------------------
const CRED_TYPES = ["apiKey", "bearer", "basic", "oauth2"]
// 저장값은 그대로(apiKey 등) 두고 화면 표시만 친화적 라벨로.
const CRED_TYPE_LABEL: Record<string, string> = { apiKey: "API Key", bearer: "Bearer 토큰", basic: "Basic 인증", oauth2: "OAuth2 토큰" }
const CRED_TYPE_INPUT: Record<string, string> = { apiKey: "API 키 값", bearer: "토큰", basic: "사용자명·비밀번호", oauth2: "액세스 토큰" }
const CRED_TYPE_HELP: Record<string, string> = {
  apiKey: "키 값 1개를 입력합니다. 보안 스킴을 연결하면 스킴 정의(in/name)대로 헤더·쿼리·쿠키에 주입되고, 미연결 시 X-API-Key 헤더로 주입됩니다.",
  bearer: "토큰을 입력하면 Authorization: Bearer <토큰> 헤더로 주입됩니다.",
  basic: "사용자명·비밀번호를 입력하면 Authorization: Basic base64(user:pw) 로 주입됩니다.",
  oauth2: "액세스 토큰을 입력하면 Authorization: Bearer <토큰> 로 주입됩니다.",
}

// 시크릿 입력 — 기본 마스킹(*), eye 아이콘으로 평문 토글.
function SecretInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  const [show, setShow] = useState(false)
  return (
    <div className="relative">
      <Input className="h-8 pr-8 text-sm" type={show ? "text" : "password"} placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />
      <button type="button" onClick={() => setShow((s) => !s)} title={show ? "숨기기" : "표시"} aria-label={show ? "숨기기" : "표시"} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
        {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  )
}

function AuthTab({ api, credentials, onChanged }: { api: ApiDetail; credentials: ApiCredential[]; onChanged: () => void }) {
  const [label, setLabel] = useState("")
  const [type, setType] = useState("apiKey")
  const [schemeName, setSchemeName] = useState<string>("none")
  const [value, setValue] = useState("")
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [busy, setBusy] = useState(false)
  const [selectedCredId, setSelectedCredId] = useState<number | null>(null)
  const selectedCred = credentials.find((c) => c.id === selectedCredId) ?? null

  const add = async () => {
    if (!label.trim()) { toast.error("자격증명 이름을 입력하세요."); return }
    const values: Record<string, unknown> =
      type === "basic" ? { username, password }
      : type === "apiKey" ? { value }
      : { token: value }
    setBusy(true)
    try {
      await createApiCredential(api.name, {
        label: label.trim(),
        type,
        scheme_name: schemeName !== "none" ? schemeName : undefined,
        values,
      })
      setLabel(""); setValue(""); setUsername(""); setPassword("")
      await onChanged()
      toast.success("자격증명을 저장했습니다.")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "저장 실패")
    } finally { setBusy(false) }
  }

  const remove = async (id: number) => {
    try { await deleteApiCredential(api.name, id); await onChanged() }
    catch (e) { toast.error(e instanceof Error ? e.message : "삭제 실패") }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        이 API 호출에 사용할 자격증명(시크릿)을 보관·관리하는 화면입니다. 등록한 시크릿은 서버에 암호화 보관되어 화면·응답에 노출되지 않으며,
        콘솔(Try-it) 탭에서 선택하면 서버가 복호화해 요청에 자동 주입합니다. apiKey 는 보안 스킴을 연결하면 스킴 정의(in/name)에 따라
        헤더·쿼리·쿠키 중 올바른 위치로 주입되고, bearer·oauth2 는 Authorization: Bearer, basic 은 Authorization: Basic 으로 주입됩니다.
      </p>
      {/* 저장된 자격증명 */}
      <Card><CardContent className="space-y-3 pt-4">
        <p className="text-sm font-medium">저장된 자격증명 ({credentials.length})</p>
        <p className="text-xs text-muted-foreground">시크릿은 서버에 암호화 보관되며 화면/응답에 노출되지 않습니다. 콘솔(Try-it)에서 선택해 주입합니다.</p>
        {credentials.length > 0 && (
          <div className="overflow-hidden rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-muted/40"><tr><th className="px-3 py-2 text-left">이름</th><th className="px-3 py-2 text-left">유형</th><th className="px-3 py-2 text-left">스킴</th><th className="w-10" /></tr></thead>
              <tbody>
                {credentials.map((c) => (
                  <tr key={c.id} className={cn("border-t", selectedCredId === c.id && "bg-muted/40")}>
                    <td className="px-3 py-2">
                      <button type="button" className="font-medium text-primary hover:underline" onClick={() => setSelectedCredId((id) => id === c.id ? null : c.id)}>{c.label}</button>
                    </td>
                    <td className="px-3 py-2"><Badge variant="outline" className="text-xs">{CRED_TYPE_LABEL[c.type] ?? c.type}</Badge></td>
                    <td className="px-3 py-2 text-muted-foreground">{c.scheme_name || "-"}</td>
                    <td className="px-3 py-2"><Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => remove(c.id)}><Trash2 className="h-4 w-4" /></Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 선택한 자격증명 상세 — 메타데이터만(시크릿은 보안상 미표시) */}
        {selectedCred && (
          <div className="space-y-2 rounded-md border bg-muted/20 p-3">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium">{selectedCred.label}</p>
              <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setSelectedCredId(null)} title="닫기"><X className="h-4 w-4" /></Button>
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <div><span className="text-muted-foreground">유형: </span>{CRED_TYPE_LABEL[selectedCred.type] ?? selectedCred.type}</div>
              <div><span className="text-muted-foreground">보안 스킴: </span>{selectedCred.scheme_name || "-"}</div>
              <div><span className="text-muted-foreground">생성자: </span>{selectedCred.created_by || "-"}</div>
              <div><span className="text-muted-foreground">생성일: </span>{fmtDateTime(selectedCred.created_at)}</div>
            </div>
            <p className="text-[11px] text-muted-foreground">🔒 보안상 저장된 시크릿(키/비밀번호)은 화면에 표시되지 않습니다. 호출 시 서버에서만 복호화·주입됩니다.</p>
          </div>
        )}

        {/* 추가 폼 */}
        <p className="pt-2 text-sm font-medium">자격증명 추가</p>

        {/* 정적 도움말 — 인증 방식별 의미·주입 방식·입력값 */}
        <details className="rounded-md border bg-muted/20 p-3">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground">인증 방식 도움말 (펼치기)</summary>
          <div className="mt-2 overflow-hidden rounded-md border bg-background">
            <table className="w-full text-sm">
              <thead className="bg-muted/40"><tr>
                <th className="w-[110px] px-3 py-2 text-left font-medium text-muted-foreground">인증 방식</th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">설명 · 주입 방식</th>
                <th className="w-[150px] px-3 py-2 text-left font-medium text-muted-foreground">입력값</th>
              </tr></thead>
              <tbody>
                {CRED_TYPES.map((t) => (
                  <tr key={t} className="border-t align-top">
                    <td className="px-3 py-2 font-medium">{CRED_TYPE_LABEL[t]}</td>
                    <td className="px-3 py-2 text-muted-foreground">{CRED_TYPE_HELP[t]}</td>
                    <td className="px-3 py-2 text-muted-foreground">{CRED_TYPE_INPUT[t]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">이름</p>
            <Input className="h-8 text-sm" placeholder="예: 운영 키" value={label} onChange={(e) => setLabel(e.target.value)} />
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">인증 방식</p>
            <Select value={type} onValueChange={setType}>
              <SelectTrigger className="h-8 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>{CRED_TYPES.map((t) => <SelectItem key={t} value={t}>{CRED_TYPE_LABEL[t]}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">보안 스킴 (선택)</p>
            <Select value={schemeName} onValueChange={setSchemeName}>
              <SelectTrigger className="h-8 text-sm"><SelectValue placeholder="스킴 미지정" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">스킴 미지정</SelectItem>
                {api.security_schemes.map((s) => <SelectItem key={s.id} value={s.scheme_name}>{s.scheme_name} ({s.type})</SelectItem>)}
              </SelectContent>
            </Select>
            <p className="text-[11px] text-muted-foreground">
              스펙(OpenAPI)에 선언된 보안 스킴 목록입니다(아래 “스펙 보안 스킴” 참고). API Key 는 연결 시 스킴의 위치(헤더/쿼리/쿠키)·이름을 따릅니다.
              {api.security_schemes.length === 0 ? " 이 스펙엔 선언된 스킴이 없어 '스킴 미지정'만 선택할 수 있습니다." : " 매핑이 불필요하면 '스킴 미지정'으로 두세요."}
            </p>
          </div>
          <div className="hidden sm:block" />
          {type === "basic" ? (
            <>
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">사용자명</p>
                <Input className="h-8 text-sm" placeholder="username" value={username} onChange={(e) => setUsername(e.target.value)} />
              </div>
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">비밀번호</p>
                <SecretInput value={password} onChange={setPassword} placeholder="password" />
              </div>
            </>
          ) : (
            <div className="space-y-1 sm:col-span-2">
              <p className="text-xs font-medium text-muted-foreground">{CRED_TYPE_INPUT[type]}</p>
              <SecretInput value={value} onChange={setValue} placeholder={CRED_TYPE_INPUT[type]} />
            </div>
          )}
        </div>

        {/* 동적 안내 — 선택한 인증 방식 설명 */}
        <p className="text-xs text-muted-foreground">※ {CRED_TYPE_HELP[type]}</p>

        <div className="flex justify-end">
          <Button size="sm" onClick={add} disabled={busy}>{busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null} 자격증명 추가</Button>
        </div>
      </CardContent></Card>

      {/* 스펙 보안 스킴 */}
      <Card><CardContent className="space-y-2 pt-4">
        <p className="text-sm font-medium">스펙 보안 스킴 ({api.security_schemes.length})</p>
        {api.security_schemes.length === 0 ? (
          <p className="text-sm text-muted-foreground">선언된 인증 스킴이 없습니다.</p>
        ) : api.security_schemes.map((s) => (
          <div key={s.id} className="space-y-1">
            <div className="flex items-center gap-2"><span className="font-medium">{s.scheme_name}</span><Badge variant="outline" className="text-xs">{s.type}</Badge></div>
            <JsonBlock value={s.config} />
          </div>
        ))}
      </CardContent></Card>
    </div>
  )
}


// ---------------------------------------------------------------------------
// 린팅 (스펙 품질 검사 — Spectral 스타일)
// ---------------------------------------------------------------------------

const LINT_SEVERITY: Record<string, { label: string; cls: string }> = {
  error: { label: "오류", cls: "bg-red-600 text-white" },
  warn: { label: "경고", cls: "bg-amber-500 text-white" },
  info: { label: "정보", cls: "bg-slate-400 text-white" },
}

function LintTab({ apiName }: { apiName: string }) {
  const [lint, setLint] = useState<ApiLint | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try { setLint(await fetchApiLint(apiName)) }
    catch (e) { toast.error(e instanceof Error ? e.message : "린팅 조회 실패") }
    finally { setLoading(false) }
  }, [apiName])

  useEffect(() => { load() }, [load])

  const scoreCls = (s: number) => (s >= 90 ? "text-emerald-600" : s >= 70 ? "text-amber-500" : "text-red-600")

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">현재 스펙의 품질을 Spectral(oas 룰셋) 스타일 규칙으로 검사해 오류·경고·정보와 품질 점수(0~100)를 보여주는 화면입니다. info·서버·태그·operationId·응답·경로 파라미터·보안 등 모범사례 위반을 찾아 개선점을 제시합니다.</p>
        <Button size="sm" variant="outline" disabled={loading} onClick={load}>
          {loading ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}다시 검사
        </Button>
      </div>

      {!lint ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-md border px-4 py-3">
              <p className="text-xs text-muted-foreground">품질 점수</p>
              <p className={cn("mt-1 text-2xl font-bold", scoreCls(lint.score))}>{lint.score}<span className="text-sm font-normal text-muted-foreground"> / 100</span></p>
            </div>
            <div className="rounded-md border px-4 py-3"><p className="text-xs text-muted-foreground">오류</p><p className="mt-1 text-xl font-semibold text-red-600">{lint.error_count}</p></div>
            <div className="rounded-md border px-4 py-3"><p className="text-xs text-muted-foreground">경고</p><p className="mt-1 text-xl font-semibold text-amber-500">{lint.warning_count}</p></div>
            <div className="rounded-md border px-4 py-3"><p className="text-xs text-muted-foreground">정보</p><p className="mt-1 text-xl font-semibold text-slate-500">{lint.info_count}</p></div>
          </div>

          {lint.findings.length === 0 ? (
            <p className="text-sm text-emerald-600">발견된 문제가 없습니다. 스펙 품질이 양호합니다.</p>
          ) : (
            <div className="overflow-hidden rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/40"><tr>
                  <th className="w-[70px] px-3 py-2 text-left font-medium text-muted-foreground">심각도</th>
                  <th className="w-[200px] px-3 py-2 text-left font-medium text-muted-foreground">규칙</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">메시지</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">위치</th>
                </tr></thead>
                <tbody>
                  {lint.findings.map((f, i) => {
                    const sev = LINT_SEVERITY[f.severity] ?? LINT_SEVERITY.info!
                    return (
                      <tr key={i} className="border-t align-top">
                        <td className="px-3 py-2"><Badge className={cn("text-[10px]", sev.cls)}>{sev.label}</Badge></td>
                        <td className="px-3 py-2 font-mono text-xs">{f.rule}</td>
                        <td className="px-3 py-2">{f.message}</td>
                        <td className="px-3 py-2 font-mono text-xs text-muted-foreground break-all">{f.location || "-"}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 사용량 관측 (호출 로깅 + 미터링)
// ---------------------------------------------------------------------------

function UsageTab({ apiName }: { apiName: string }) {
  const [days, setDays] = useState("30")
  const [usage, setUsage] = useState<ApiUsage | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async (d: string) => {
    setLoading(true)
    try { setUsage(await fetchApiUsage(apiName, Number(d))) }
    catch (e) { toast.error(e instanceof Error ? e.message : "사용량 조회 실패") }
    finally { setLoading(false) }
  }, [apiName])

  useEffect(() => { load(days) }, [load]) // eslint-disable-line react-hooks/exhaustive-deps

  const stat = (label: string, value: string) => (
    <div className="rounded-md border px-4 py-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-xl font-semibold">{value}</p>
    </div>
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">콘솔(Try-it)을 통한 API 호출을 집계해 호출 수·성공률·지연(평균/p95)·상태 코드 분포·인기 엔드포인트·소비자별 사용량·일별 추이를 보여주는 화면입니다. 외부 게이트웨이 연동 시 실제 트래픽까지 확장할 수 있습니다.</p>
        <div className="flex items-center gap-2">
          {loading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
          <Select value={days} onValueChange={(v) => { setDays(v); load(v) }}>
            <SelectTrigger className="h-8 w-[110px] text-sm"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="7">최근 7일</SelectItem>
              <SelectItem value="30">최근 30일</SelectItem>
              <SelectItem value="90">최근 90일</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {!usage || usage.total_calls === 0 ? (
        <p className="text-sm text-muted-foreground">기간 내 호출 기록이 없습니다. 콘솔(Try-it) 탭에서 API 를 호출하면 사용량이 집계됩니다.</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {stat("총 호출", usage.total_calls.toLocaleString())}
            {stat("성공률", `${usage.success_rate}%`)}
            {stat("평균 지연", `${usage.avg_latency_ms} ms`)}
            {stat("p95 지연", `${usage.p95_latency_ms} ms`)}
          </div>

          <Card><CardContent className="pt-4">
            <p className="mb-2 text-sm font-medium">상태 코드 분포</p>
            <div className="flex flex-wrap gap-2">
              {usage.by_status.map((s) => (
                <Badge key={s.status} variant="outline" className={cn("text-xs", s.status.startsWith("2") || s.status.startsWith("3") ? "text-emerald-600" : "text-red-600")}>
                  {s.status} · {s.count}건
                </Badge>
              ))}
            </div>
          </CardContent></Card>

          <Card><CardContent className="pt-4">
            <p className="mb-2 text-sm font-medium">인기 엔드포인트</p>
            <div className="overflow-hidden rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/40"><tr>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">엔드포인트</th>
                  <th className="w-[90px] px-3 py-2 text-right font-medium text-muted-foreground">호출</th>
                  <th className="w-[110px] px-3 py-2 text-right font-medium text-muted-foreground">평균 지연</th>
                </tr></thead>
                <tbody>
                  {usage.top_endpoints.map((e) => (
                    <tr key={e.endpoint} className="border-t">
                      <td className="px-3 py-2 font-mono text-xs break-all">{e.endpoint}</td>
                      <td className="px-3 py-2 text-right">{e.count}</td>
                      <td className="px-3 py-2 text-right text-muted-foreground">{e.avg_latency_ms} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent></Card>

          <Card><CardContent className="pt-4">
            <p className="mb-2 text-sm font-medium">소비자별 사용량</p>
            <div className="overflow-hidden rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/40"><tr>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">호출자</th>
                  <th className="w-[90px] px-3 py-2 text-right font-medium text-muted-foreground">호출</th>
                </tr></thead>
                <tbody>
                  {usage.top_callers.map((c) => (
                    <tr key={c.name} className="border-t">
                      <td className="px-3 py-2">{c.name}</td>
                      <td className="px-3 py-2 text-right">{c.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent></Card>

          <Card><CardContent className="pt-4">
            <p className="mb-2 text-sm font-medium">최근 호출</p>
            <div className="overflow-hidden rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/40"><tr>
                  <th className="w-[70px] px-3 py-2 text-left font-medium text-muted-foreground">메서드</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">URL</th>
                  <th className="w-[70px] px-3 py-2 text-right font-medium text-muted-foreground">상태</th>
                  <th className="w-[80px] px-3 py-2 text-right font-medium text-muted-foreground">지연</th>
                  <th className="w-[160px] px-3 py-2 text-left font-medium text-muted-foreground">시각</th>
                </tr></thead>
                <tbody>
                  {usage.recent.map((r) => (
                    <tr key={r.id} className="border-t">
                      <td className="px-3 py-2 font-mono text-xs">{r.method}</td>
                      <td className="px-3 py-2 font-mono text-xs break-all">{r.url}</td>
                      <td className={cn("px-3 py-2 text-right", r.ok ? "text-emerald-600" : "text-red-600")}>{r.status_code || "ERR"}</td>
                      <td className="px-3 py-2 text-right text-muted-foreground">{r.latency_ms} ms</td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">{r.created_at ? new Date(r.created_at).toLocaleString() : "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent></Card>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 리니지 (provides / consumes / depends_on)
// ---------------------------------------------------------------------------

const LINEAGE_RELATIONS: { value: string; label: string; caption: string }[] = [
  { value: "provides", label: "제공 (provides)", caption: "이 API 가 데이터·기능을 제공하는 소비처" },
  { value: "consumes", label: "소비 (consumes)", caption: "이 API 가 내부에서 사용·소비하는 대상" },
  { value: "depends_on", label: "의존 (depends_on)", caption: "이 API 가 런타임에 의존하는 대상" },
]
const LINEAGE_TARGET_TYPES: { value: string; label: string }[] = [
  { value: "system", label: "시스템" },
  { value: "dataset", label: "데이터셋" },
  { value: "model", label: "모델" },
  { value: "agent", label: "AI 에이전트" },
  { value: "api", label: "API" },
]
const TARGET_TYPE_LABEL: Record<string, string> = Object.fromEntries(LINEAGE_TARGET_TYPES.map((t) => [t.value, t.label]))

function LineageTab({ apiName, lineage, onChanged }: { apiName: string; lineage: ApiLineage[]; onChanged: () => Promise<void> }) {
  const [relation, setRelation] = useState("consumes")
  const [targetType, setTargetType] = useState("dataset")
  const [targetRef, setTargetRef] = useState("")
  const [targetLabel, setTargetLabel] = useState("")
  const [note, setNote] = useState("")
  const [busy, setBusy] = useState(false)
  const [viewMode, setViewMode] = useState<"table" | "graph">("table")

  const handleAdd = async () => {
    if (!targetRef.trim()) { toast.error("대상 식별자를 입력하세요."); return }
    setBusy(true)
    try {
      await addApiLineage(apiName, {
        relation, target_type: targetType, target_ref: targetRef.trim(),
        target_label: targetLabel.trim() || undefined, note: note.trim() || undefined,
      })
      setTargetRef(""); setTargetLabel(""); setNote("")
      await onChanged()
      toast.success("리니지를 추가했습니다.")
    } catch (e) { toast.error(e instanceof Error ? e.message : "추가 실패") } finally { setBusy(false) }
  }
  const handleDelete = async (id: number) => {
    if (!confirm("이 리니지 관계를 삭제하시겠습니까?")) return
    try { await deleteApiLineage(apiName, id); await onChanged(); toast.success("삭제했습니다.") }
    catch (e) { toast.error(e instanceof Error ? e.message : "삭제 실패") }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          이 API 가 제공(provides)·소비(consumes)·의존(depends_on)하는 시스템·데이터셋·모델·AI 에이전트·다른 API 와의 관계를 기록·관리하는 화면입니다. 관계를 등록하면 영향 범위 파악과 카탈로그 내 연결 추적에 활용됩니다.
        </p>
        <div className="flex shrink-0 overflow-hidden rounded-md border">
          <button type="button" onClick={() => setViewMode("table")} className={cn("px-3 py-1.5 text-xs", viewMode === "table" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:bg-muted")}>표</button>
          <button type="button" onClick={() => setViewMode("graph")} className={cn("px-3 py-1.5 text-xs", viewMode === "graph" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:bg-muted")}>그래프</button>
        </div>
      </div>

      {viewMode === "graph" && <ApisLineageGraph apiName={apiName} lineage={lineage} />}

      {viewMode === "table" && LINEAGE_RELATIONS.map((rel) => {
        const edges = lineage.filter((e) => e.relation === rel.value)
        return (
          <Card key={rel.value}><CardContent className="pt-4">
            <div className="mb-2">
              <p className="text-sm font-medium">{rel.label}</p>
              <p className="text-xs text-muted-foreground">{rel.caption}</p>
            </div>
            {edges.length === 0 ? (
              <p className="text-sm text-muted-foreground">등록된 관계가 없습니다.</p>
            ) : (
              <div className="overflow-hidden rounded-md border">
                <table className="w-full text-sm">
                  <thead className="bg-muted/40"><tr>
                    <th className="w-[110px] px-3 py-2 text-left font-medium text-muted-foreground">유형</th>
                    <th className="px-3 py-2 text-left font-medium text-muted-foreground">대상</th>
                    <th className="px-3 py-2 text-left font-medium text-muted-foreground">비고</th>
                    <th className="w-[60px] px-3 py-2" />
                  </tr></thead>
                  <tbody>
                    {edges.map((e) => (
                      <tr key={e.id} className="border-t">
                        <td className="px-3 py-2 align-top"><Badge variant="outline" className="text-xs">{TARGET_TYPE_LABEL[e.target_type] ?? e.target_type}</Badge></td>
                        <td className="px-3 py-2 align-top">
                          <span className="font-medium">{e.target_label || e.target_ref}</span>
                          {e.target_label && <span className="ml-2 font-mono text-xs text-muted-foreground">{e.target_ref}</span>}
                        </td>
                        <td className="px-3 py-2 align-top text-muted-foreground">{e.note || "-"}</td>
                        <td className="px-3 py-2 align-top text-right">
                          <Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => handleDelete(e.id)} title="삭제"><Trash2 className="h-4 w-4" /></Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent></Card>
        )
      })}

      <Card><CardContent className="pt-4">
        <p className="mb-2 text-sm font-medium">관계 추가</p>

        <details className="mb-3 rounded-md border bg-muted/20 p-3">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground">사용 방법 (펼치기)</summary>
          <div className="mt-2 space-y-2 text-xs text-muted-foreground">
            <p>이 API 기준으로 다른 대상과의 관계 한 건을 등록합니다. 아래 4가지를 채우고 “추가”를 누르세요.</p>
            <ul className="ml-4 list-disc space-y-1">
              <li><b>관계</b> — <b>제공(provides)</b>: 이 API가 데이터·기능을 제공하는 소비처 · <b>소비(consumes)</b>: 이 API가 사용·소비하는 대상 · <b>의존(depends_on)</b>: 런타임에 의존하는 대상</li>
              <li><b>대상 유형</b> — 시스템 / 데이터셋 / 모델 / AI 에이전트 / API 중 선택</li>
              <li><b>대상 식별자(이름/URN)</b> — 대상의 이름이나 URN을 직접 입력 (자유 입력, 필수)</li>
              <li><b>표시명·비고</b> — 선택 입력</li>
            </ul>
            <p>예) 이 API가 <b>orders-dataset</b> 데이터셋을 읽어 쓴다면 → 관계 <b>소비</b>, 대상 유형 <b>데이터셋</b>, 대상 식별자 <b>orders-dataset</b>.</p>
            <p>같은 (관계 + 대상 유형 + 대상 식별자) 조합은 중복 등록되지 않으며, 현재 대상은 자유 입력으로 저장됩니다(자동 연결·검증 없음).</p>
          </div>
        </details>

        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">관계</p>
            <Select value={relation} onValueChange={setRelation}>
              <SelectTrigger className="h-8 w-[150px] text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>{LINEAGE_RELATIONS.map((r) => <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">대상 유형</p>
            <Select value={targetType} onValueChange={setTargetType}>
              <SelectTrigger className="h-8 w-[130px] text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>{LINEAGE_TARGET_TYPES.map((t) => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">대상 식별자(이름/URN)</p>
            <Input className="h-8 w-[220px] text-sm" value={targetRef} onChange={(e) => setTargetRef(e.target.value)} placeholder="예: orders-dataset" />
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">표시명(선택)</p>
            <Input className="h-8 w-[160px] text-sm" value={targetLabel} onChange={(e) => setTargetLabel(e.target.value)} placeholder="주문 데이터셋" />
          </div>
          <div className="flex-1 space-y-1">
            <p className="text-xs text-muted-foreground">비고(선택)</p>
            <Input className="h-8 w-full text-sm" value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          <Button size="sm" disabled={busy} onClick={handleAdd}>{busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Check className="mr-1 h-4 w-4" />}추가</Button>
        </div>
      </CardContent></Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 메인 상세
// ---------------------------------------------------------------------------
export function ApisDetail({ name }: { name: string }) {
  const { setSelectedApiName, refreshApis } = useApis()
  const [api, setApi] = useState<ApiDetail | null>(null)
  const [history, setHistory] = useState<ApiStatusHistory[]>([])
  const [credentials, setCredentials] = useState<ApiCredential[]>([])
  const [alerts, setAlerts] = useState<ApiAlert[]>([])
  const [lineage, setLineage] = useState<ApiLineage[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [error, setError] = useState<string | null>(null)
  // 개요 편집
  const [editingOv, setEditingOv] = useState(false)
  const [savingOv, setSavingOv] = useState(false)
  const [ov, setOv] = useState<Record<string, string>>({})
  const setOvF = (k: string, v: string) => setOv((d) => ({ ...d, [k]: v }))

  const reload = useCallback(async () => {
    setApi(await fetchApiDetail(name))
    setHistory(await fetchApiStatusHistory(name).catch(() => []))
  }, [name])
  const reloadCredentials = useCallback(async () => {
    setCredentials(await fetchApiCredentials(name).catch(() => []))
  }, [name])
  const reloadAlerts = useCallback(async () => {
    setAlerts(await fetchApiAlerts(name, "OPEN").catch(() => []))
  }, [name])
  const reloadLineage = useCallback(async () => {
    setLineage(await fetchApiLineage(name).catch(() => []))
  }, [name])

  useEffect(() => {
    let active = true
    Promise.all([
      fetchApiDetail(name),
      fetchApiStatusHistory(name).catch(() => []),
      fetchApiCredentials(name).catch(() => []),
      fetchApiAlerts(name, "OPEN").catch(() => []),
      fetchApiLineage(name).catch(() => []),
    ])
      .then(([d, h, c, a, l]) => { if (active) { setApi(d); setHistory(h); setCredentials(c); setAlerts(a); setLineage(l) } })
      .catch((e) => active && setError(e instanceof Error ? e.message : "조회 실패"))
    return () => { active = false }
  }, [name])

  const handleAckAlert = async (alertId: number) => {
    try {
      await acknowledgeApiAlert(name, alertId)
      await reloadAlerts()
      toast.success("알림을 확인 처리했습니다.")
    } catch (e) { toast.error(e instanceof Error ? e.message : "확인 처리 실패") }
  }

  const startEditOv = () => {
    if (!api) return
    setOv({
      display_name: api.display_name ?? "", description: api.description ?? "",
      category: api.category ?? "", owner_email: api.owner_email ?? "", department: api.department ?? "",
      certification: api.certification ?? "", tier: api.tier ?? "", base_url: api.base_url ?? "",
      version: api.version ?? "", contract_url: api.contract_url ?? "",
      tags: (api.tags ?? []).join(", "), note: api.note ?? "",
    })
    setEditingOv(true)
    if (users.length === 0) {
      fetchUsers({ pageSize: 0 }).then((r) => setUsers(r.items)).catch(() => {})
    }
  }
  const handleSaveOv = async () => {
    if (!api) return
    const g = (k: string) => ov[k] ?? ""
    const s = (k: string) => (g(k).trim() === "" ? null : g(k).trim())
    setSavingOv(true)
    try {
      await updateApi(api.name, {
        display_name: s("display_name"), description: s("description"),
        category: s("category"), owner_email: s("owner_email"), department: s("department"),
        certification: g("certification") || null, tier: g("tier") || null, base_url: s("base_url"),
        tags: g("tags").split(",").map((t) => t.trim()).filter(Boolean),
        note: s("note"),
        ...(api.source === "manual" && g("version").trim() ? { version: g("version").trim() } : {}),
        ...(api.source === "manual" ? { contract_url: s("contract_url") } : {}),
      })
      await reload()
      await refreshApis()
      setEditingOv(false)
      toast.success("개요를 저장했습니다.")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "저장 실패")
    } finally { setSavingOv(false) }
  }

  const changeStatus = async (next: string) => {
    if (!api || next === api.status) return
    try {
      await updateApi(api.name, { status: next })
      await reload()
      await refreshApis()
      toast.success(`상태를 '${API_STATUS_LABEL[next] ?? next}'(으)로 변경했습니다.`)
    } catch (e) { toast.error(e instanceof Error ? e.message : "상태 변경 실패") }
  }

  const handleDelete = async () => {
    if (!api) return
    if (!confirm(`'${api.display_name || api.name}' API를 삭제하시겠습니까?`)) return
    try {
      await deleteApi(api.name)
      await refreshApis()
      setSelectedApiName(null)
      toast.success("API를 삭제했습니다.")
    } catch (e) { toast.error(e instanceof Error ? e.message : "삭제 실패") }
  }

  if (error) {
    return (
      <div className="flex flex-col gap-4">
        <Button variant="ghost" size="sm" className="w-fit" onClick={() => setSelectedApiName(null)}><ArrowLeft className="mr-1 h-4 w-4" /> API 목록으로</Button>
        <p className="text-sm text-destructive">{error}</p>
      </div>
    )
  }
  if (!api) {
    return (
      <div className="flex flex-col gap-4">
        <Button variant="ghost" size="sm" className="w-fit" onClick={() => setSelectedApiName(null)}><ArrowLeft className="mr-1 h-4 w-4" /> API 목록으로</Button>
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <Button variant="ghost" size="sm" className="mb-2 w-fit -ml-2" onClick={() => setSelectedApiName(null)}><ArrowLeft className="mr-1 h-4 w-4" /> API 목록으로</Button>
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-semibold">{api.display_name || api.name}</h2>
            <Select value={api.status} onValueChange={changeStatus}>
              <SelectTrigger className={cn("h-auto w-auto gap-1 rounded-md border-0 px-2 py-1 text-sm font-medium shadow-none focus:ring-0", API_STATUS_VARIANTS[api.status] ?? API_STATUS_VARIANTS.draft)} title="상태 변경">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>{STATUS_ORDER.map((s) => <SelectItem key={s} value={s}>{API_STATUS_LABEL[s]}</SelectItem>)}</SelectContent>
            </Select>
            <Badge variant="outline" className="text-sm">{api.version}</Badge>
            {api.protocol && <Badge variant="outline" className="text-sm">{protocolLabel(api.protocol)}</Badge>}
            {api.spec_format && <Badge variant="outline" className="text-sm">{SPEC_FORMAT_LABEL[api.spec_format] ?? api.spec_format}</Badge>}
          </div>
          <p className="font-mono text-xs text-muted-foreground">{api.urn}</p>
          {api.description && <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{api.description}</p>}
        </div>
        <Button variant="destructive" size="sm" onClick={handleDelete}><Trash2 className="mr-1 h-4 w-4" /> 삭제</Button>
      </div>

      {alerts.length > 0 && (
        <div className="flex flex-col gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3">
          {alerts.map((al) => (
            <div key={al.id} className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                <div className="text-sm">
                  <span className="font-medium text-destructive">Breaking 변경 감지</span>
                  <span className="ml-2 text-muted-foreground">{new Date(al.created_at).toLocaleString()}</span>
                  <p className="mt-0.5 text-foreground">{al.summary}</p>
                </div>
              </div>
              <Button size="sm" variant="outline" className="shrink-0" onClick={() => handleAckAlert(al.id)}>
                <Check className="mr-1 h-4 w-4" /> 확인
              </Button>
            </div>
          ))}
        </div>
      )}

      <Tabs defaultValue="overview">
        <TabsList className="flex-wrap">
          <TabsTrigger value="overview">개요</TabsTrigger>
          <TabsTrigger value="endpoints">엔드포인트 ({api.endpoints.length})</TabsTrigger>
          <TabsTrigger value="auth">인증 ({api.security_schemes.length})</TabsTrigger>
          <TabsTrigger value="lineage">리니지 ({lineage.length})</TabsTrigger>
          <TabsTrigger value="usage">관측</TabsTrigger>
          <TabsTrigger value="lint">린팅</TabsTrigger>
          <TabsTrigger value="spec">원본</TabsTrigger>
          <TabsTrigger value="history">이력</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4">
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
            <div className="flex shrink-0 gap-2">
              {editingOv ? (
                <>
                  <Button size="sm" variant="outline" disabled={savingOv} onClick={() => setEditingOv(false)}>취소</Button>
                  <Button size="sm" disabled={savingOv} onClick={handleSaveOv}>{savingOv ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}저장</Button>
                </>
              ) : (
                <Button size="sm" variant="outline" onClick={startEditOv}><Pencil className="mr-1 h-4 w-4" /> 편집</Button>
              )}
            </div>
          </div>
          <TooltipProvider delayDuration={150}>
          <div className="overflow-hidden rounded-md border">
            <table className="w-full table-fixed border-collapse text-sm">
              <colgroup><col className="w-[140px]" /><col /><col className="w-[140px]" /><col /></colgroup>
              <tbody>
                {(() => {
                  type OvRow = { label: string; display: string | null; field?: string; editor?: "text" | "textarea" | "cert" | "tier" | "combo" | "owner" | "tags"; source: Source }
                  const isManual = api.source === "manual"
                  const rows: OvRow[] = ([
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
                  ] as OvRow[])
                  const renderVal = (r: OvRow) => (
                    editingOv && r.field ? (
                      r.editor === "textarea" ? (
                        <Textarea className="min-h-16 text-sm" value={ov[r.field] ?? ""} onChange={(e) => setOvF(r.field!, e.target.value)} />
                      ) : r.editor === "owner" ? (
                        <OwnerCombo
                          users={users}
                          value={ov.owner_email ?? ""}
                          onSelectUser={(u) => setOv((d) => ({ ...d, owner_email: u.email, department: u.department ?? d.department ?? "" }))}
                          onCustomEmail={(email) => setOvF("owner_email", email)}
                        />
                      ) : r.editor === "tags" ? (
                        <TagsInput value={ov[r.field] ?? ""} onChange={(v) => setOvF(r.field!, v)} />
                      ) : r.editor === "combo" ? (
                        <ComboInput value={ov[r.field] ?? ""} onChange={(v) => setOvF(r.field!, v)} options={OPT_API_CATEGORY} />
                      ) : r.editor === "cert" ? (
                        <Select value={ov[r.field] || "none"} onValueChange={(v) => setOvF(r.field!, v === "none" ? "" : v)}>
                          <SelectTrigger className="h-8 w-full text-sm"><SelectValue placeholder="미지정" /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="none" className="text-muted-foreground">미지정</SelectItem>
                            {["NONE", "CERTIFIED", "IN_REVIEW", "DEPRECATED"].map((o) => <SelectItem key={o} value={o}>{CERT_LABEL[o] ?? o}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      ) : r.editor === "tier" ? (
                        <Select value={ov[r.field] || "none"} onValueChange={(v) => setOvF(r.field!, v === "none" ? "" : v)}>
                          <SelectTrigger className="h-8 w-full text-sm"><SelectValue placeholder="미지정" /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="none" className="text-muted-foreground">미지정</SelectItem>
                            {["GOLD", "SILVER", "BRONZE"].map((o) => <SelectItem key={o} value={o}>{TIER_LABEL[o] ?? o}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      ) : (
                        <Input className="h-8 w-full text-sm" value={ov[r.field] ?? ""} onChange={(e) => setOvF(r.field!, e.target.value)} placeholder={r.field === "tags" ? "쉼표로 구분" : undefined} />
                      )
                    ) : (r.display === null || r.display === undefined || r.display === "" ? "-" : r.display)
                  )
                  const pairs: (OvRow | undefined)[][] = []
                  for (let i = 0; i < rows.length; i += 2) pairs.push([rows[i], rows[i + 1]])
                  return pairs.map((pair, idx) => (
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
                  ))
                })()}
              </tbody>
            </table>
          </div>
          </TooltipProvider>
        </TabsContent>

        <TabsContent value="endpoints" className="mt-4"><EndpointsTab api={api} credentials={credentials} canEdit={api.source === "manual"} onEndpointsChanged={reload} /></TabsContent>

        <TabsContent value="auth" className="mt-4">
          <AuthTab api={api} credentials={credentials} onChanged={reloadCredentials} />
        </TabsContent>

        <TabsContent value="lineage" className="mt-4">
          <LineageTab apiName={api.name} lineage={lineage} onChanged={reloadLineage} />
        </TabsContent>

        <TabsContent value="usage" className="mt-4">
          <UsageTab apiName={api.name} />
        </TabsContent>

        <TabsContent value="lint" className="mt-4">
          <LintTab apiName={api.name} />
        </TabsContent>

        <TabsContent value="spec" className="mt-4 space-y-4">
          {api.source === "manual" ? (
            <>
              <p className="text-sm text-muted-foreground">
                수동 등록 API 의 계약/스키마 문서(SDL/WSDL/.proto/AsyncAPI 등)입니다. 등록 시 입력한 원문을 표시합니다.
              </p>
              {api.contract_url && (
                <p className="text-sm">문서 URL: <a href={api.contract_url} target="_blank" rel="noreferrer" className="text-primary hover:underline break-all">{api.contract_url}</a></p>
              )}
              <CodeViewer text={api.contract_text || null} language="plaintext" emptyLabel="계약 문서가 없습니다(등록 시 입력)." lineNumbers scroll />
            </>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                업로드한 OpenAPI/Swagger 스펙의 원본(JSON)입니다. 줄 번호·접기를 지원하며 depth 4 이상은 자동으로 접혀 있습니다(거터 화살표로 펼치거나 접을 수 있습니다). 우상단 버튼으로 전체를 클립보드에 복사할 수 있습니다.
              </p>
              <CodeViewer
                text={api.raw_spec ? (() => { try { return JSON.stringify(JSON.parse(api.raw_spec!), null, 2) } catch { return api.raw_spec } })() : null}
                emptyLabel="원본 스펙이 없습니다."
                lineNumbers
                scroll
              />
            </>
          )}
        </TabsContent>

        <TabsContent value="history" className="mt-4 space-y-4">
          <p className="text-sm text-muted-foreground">API 상태(초안·게시·사용 중단·폐기) 변경 이력입니다.</p>
          <Card><CardContent className="pt-4">
            <p className="mb-2 text-sm font-medium">상태 변경 이력 ({history.length})</p>
            <div className="overflow-hidden rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/40"><tr><th className="px-3 py-2 text-left">변경</th><th className="px-3 py-2 text-left">일시</th><th className="px-3 py-2 text-left">변경자</th></tr></thead>
                <tbody>
                  {history.length === 0 ? (
                    <tr><td colSpan={3} className="px-3 py-4 text-center text-muted-foreground">이력 없음</td></tr>
                  ) : history.map((h) => (
                    <tr key={h.id} className="border-t">
                      <td className="px-3 py-2">
                        {h.from_status ? <Badge variant="outline" className="text-xs">{API_STATUS_LABEL[h.from_status] ?? h.from_status}</Badge> : "—"}
                        <span className="mx-1 text-muted-foreground">→</span>
                        <Badge variant="secondary" className="text-xs">{API_STATUS_LABEL[h.to_status] ?? h.to_status}</Badge>
                      </td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">{new Date(h.changed_at).toLocaleString()}</td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">{h.changed_by || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent></Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
