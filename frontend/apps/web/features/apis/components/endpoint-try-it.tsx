"use client"

import { useCallback, useEffect, useState } from "react"
import { AlertTriangle, Loader2, Minus, Play, Plus, RotateCcw, Wand2 } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"
import { useConfirm } from "@/components/confirm-provider"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Tabs, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { cn } from "@workspace/ui/lib/utils"

import {
  type ApiDetail, type ApiEndpoint, type ApiInvocationRecord, type InvokeResult, fetchEndpointInvocations, invokeApi,
} from "../api"
import { MONACO_FONT_SIZE } from "@/lib/monaco"
import { CodeViewer, InvokeResultPanel, MonacoEditor, fmtDateTime, paramDataType } from "./apis-detail-shared"
import { requestBodyExample } from "./request-example"

// ---------------------------------------------------------------------------
// 헤더 입력 행 — REST·GraphQL·SOAP 호출이 함께 쓴다. 스펙의 in: header 파라미터는 미리 채워 두고 필수 표시.
// ---------------------------------------------------------------------------
type HeaderRow = { key: string; value: string; required?: boolean; description?: string; fromSpec?: boolean }
const EMPTY_HEADER_ROW: HeaderRow = { key: "", value: "" }

/** 스펙에 선언된 헤더 파라미터 → 입력 행(없으면 빈 행 하나) */
function specHeaderRows(endpoint: ApiEndpoint): HeaderRow[] {
  const rows = (endpoint.parameters ?? [])
    .map((p) => p as { name?: string; in?: string; required?: boolean; description?: string })
    .filter((p) => p.in === "header" && p.name)
    .map((p) => ({ key: p.name!, value: "", required: !!p.required, description: p.description, fromSpec: true }))
  return rows.length > 0 ? rows : [EMPTY_HEADER_ROW]
}

/** 입력 행 → 헤더 객체. 값이 빈 선택 헤더(스펙에서 미리 채운 행)는 보내지 않는다. */
function headerRowsToObject(rows: HeaderRow[]): Record<string, string> {
  const o: Record<string, string> = {}
  for (const r of rows) {
    const k = r.key.trim()
    if (!k) continue
    if (r.value === "" && r.fromSpec) continue
    o[k] = r.value
  }
  return o
}

const hasHeader = (h: Record<string, string>, name: string) => Object.keys(h).some((k) => k.toLowerCase() === name.toLowerCase())

function HeaderRowsInput({ rows, onChange, invalid, hint }: {
  rows: HeaderRow[]
  onChange: (rows: HeaderRow[]) => void
  /** 값이 비어 실행을 막은 필수 헤더 이름 */
  invalid?: Set<string>
  hint?: string
}) {
  const set = (i: number, patch: Partial<HeaderRow>) => onChange(rows.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-muted-foreground">헤더 {hint ?? "(인증 토큰 등)"}</p>
        <Button size="sm" variant="outline" className="h-7" title="헤더 추가" aria-label="헤더 추가" onClick={() => onChange([...rows, EMPTY_HEADER_ROW])}><Plus className="h-3.5 w-3.5" /></Button>
      </div>
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-2">
          <div className="relative w-full">
            <Input className={cn("h-8 text-sm", row.required && "pr-5")} placeholder="헤더 이름 (예: Authorization)" value={row.key} onChange={(e) => set(i, { key: e.target.value })} title={row.description || undefined} />
            {row.required && <span className="absolute right-2 top-1/2 -translate-y-1/2 text-red-600" title="필수">*</span>}
          </div>
          <Input
            className="h-8 text-sm"
            placeholder={row.description || "값"}
            value={row.value}
            aria-invalid={invalid?.has(row.key.trim()) && !row.value.trim() ? true : undefined}
            onChange={(e) => set(i, { value: e.target.value })}
          />
          <Button size="icon" variant="ghost" className="h-8 w-8 shrink-0" onClick={() => onChange(rows.length > 1 ? rows.filter((_, j) => j !== i) : [EMPTY_HEADER_ROW])} title="삭제" aria-label="헤더 삭제"><Minus className="h-4 w-4" /></Button>
        </div>
      ))}
    </div>
  )
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
  if (hasBody) wgetLines.push(`  --body-data='${bodyText.replace(/'/g, "'\\''")}' \\`)
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
export function EndpointTryIt({ api, endpoint, server, credId }: {
  api: ApiDetail; endpoint: ApiEndpoint; server: string; credId: string
}) {
  const askConfirm = useConfirm()
  const isWrite = ["POST", "PUT", "PATCH", "DELETE"].includes(endpoint.method)
  // 요청 바디 예시(스펙 example → 스키마 샘플) — 행을 펼칠 때 한 번 계산
  const [example] = useState(() => (isWrite ? requestBodyExample(endpoint, api.raw_spec) : null))
  const [pathParams, setPathParams] = useState<Record<string, string>>({})
  const [queryParams, setQueryParams] = useState<Record<string, string>>({})
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>(() => specHeaderRows(endpoint))
  // POST·PUT·PATCH 는 예시로 미리 채우고, DELETE 는 바디가 드물어 버튼으로만
  const [bodyText, setBodyText] = useState(() => (example && endpoint.method !== "DELETE" ? example.text : ""))
  // 필수 값이 비어 실행을 막았을 때 표시할 항목(위치:이름)
  const [missing, setMissing] = useState<Set<string>>(new Set())
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<InvokeResult | null>(null)
  const [history, setHistory] = useState<ApiInvocationRecord[]>([])

  const loadHistory = useCallback(() => {
    fetchEndpointInvocations(api.name, endpoint.method, endpoint.path, 10).then(setHistory).catch(() => {})
  }, [api.name, endpoint.method, endpoint.path])
  useEffect(() => { loadHistory() }, [loadHistory])

  const headersObj = (): Record<string, string> => headerRowsToObject(headerRows)

  // 이력 → 폼 재주입(불러오기). 마스킹된 헤더 값(***)은 그대로 들어가므로 직접 갱신 필요.
  const loadFromHistory = (rec: ApiInvocationRecord) => {
    const ri = rec.request_input || {}
    setPathParams(ri.path_params ?? {})
    setQueryParams(ri.query_params ?? {})
    // 스펙 헤더 행(필수 표시)은 유지하고 값만 채운 뒤, 나머지 헤더를 덧붙인다
    const saved: Record<string, string> = { ...(ri.headers ?? {}) }
    const base = specHeaderRows(endpoint).filter((r) => r.key).map((r) => {
      const k = Object.keys(saved).find((x) => x.toLowerCase() === r.key.toLowerCase())
      if (k === undefined) return r
      const v = saved[k] ?? ""
      delete saved[k]
      return { ...r, value: v }
    })
    const hs = [...base, ...Object.entries(saved).map(([key, value]) => ({ key, value }))]
    setHeaderRows(hs.length > 0 ? hs : [EMPTY_HEADER_ROW])
    setMissing(new Set())
    setBodyText(ri.body ?? "")
    toast.success("과거 입력값을 불러왔습니다.")
  }

  const params = endpoint.parameters ?? []
  const pathP = params.filter((p) => (p as { in?: string }).in === "path")
  const queryP = params.filter((p) => (p as { in?: string }).in === "query")

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
    // 필수 값 확인 — 경로 파라미터는 항상 필수(비면 빈 경로 조각이 된다)
    const miss = new Set<string>()
    for (const p of pathP) { const nm = (p as { name?: string }).name ?? ""; if (!(pathParams[nm] ?? "").trim()) miss.add(`path:${nm}`) }
    for (const p of queryP) {
      const pp = p as { name?: string; required?: boolean }
      if (pp.required && !(queryParams[pp.name ?? ""] ?? "").trim()) miss.add(`query:${pp.name ?? ""}`)
    }
    for (const r of headerRows) if (r.required && r.key.trim() && !r.value.trim()) miss.add(`header:${r.key.trim()}`)
    setMissing(miss)
    if (miss.size > 0) {
      toast.error(`필수 값을 입력하세요: ${[...miss].map((k) => k.slice(k.indexOf(":") + 1)).join(", ")}`)
      return
    }
    const headers: Record<string, string> = headersObj()
    let body: unknown
    if (isWrite && bodyText.trim()) {
      try { body = JSON.parse(bodyText) } catch { body = bodyText }
      if (!hasHeader(headers, "Content-Type")) headers["Content-Type"] = example?.mime ?? "application/json"
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
      notifyError(e, "엔드포인트를 호출하지 못했습니다.")
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
                  const dataType = paramDataType(pp)
                  const invalid = missing.has(`${where}:${nm}`) && !val.trim()
                  return (
                    <tr key={`${where}:${nm}`} className="border-t align-top">
                      <td className="px-3 py-2"><span className="font-mono text-sm text-black">{nm}</span>{(pp.required || isPath) && <span className="ml-1 text-red-600" title="필수">*</span>}</td>
                      <td className="px-3 py-2"><Input className="h-8 w-full text-sm" placeholder={nm} value={val} aria-invalid={invalid ? true : undefined} onChange={(e) => setVal(e.target.value)} /></td>
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
      <HeaderRowsInput
        rows={headerRows}
        onChange={setHeaderRows}
        invalid={new Set([...missing].filter((k) => k.startsWith("header:")).map((k) => k.slice(7)))}
      />
      {isWrite && (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-medium text-muted-foreground">
              요청 바디 ({example?.mime ?? "JSON"}){example && endpoint.method !== "DELETE" ? " — 스펙 예시로 미리 채움" : ""}
            </p>
            {example && (
              <Button
                size="sm" variant="outline" className="h-7"
                title="스펙의 예시(없으면 스키마로 만든 샘플)로 바디를 채웁니다"
                onClick={async () => {
                  if (bodyText.trim() && bodyText !== example.text && !(await askConfirm({
                    title: "예시로 바꾸기",
                    description: "입력한 바디를 예시로 바꿀까요?",
                    confirmText: "바꾸기",
                  }))) return
                  setBodyText(example.text)
                }}
              >
                <Wand2 className="mr-1 h-3.5 w-3.5" />예시로 채우기
              </Button>
            )}
          </div>
          {/* resize-y: 우하단 그립으로 높이 조절. 에디터는 컨테이너를 100% 채우고 자동 재배치. */}
          <div className="resize-y overflow-hidden rounded-md border" style={{ height: 180, minHeight: 96, maxHeight: 600 }}>
            <MonacoEditor
              height="100%"
              language="json"
              theme="light"
              value={bodyText}
              onChange={(v) => setBodyText(v ?? "")}
              options={{
                minimap: { enabled: false }, fontSize: MONACO_FONT_SIZE, lineNumbers: "on",
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

      {result && <InvokeResultPanel result={result} />}

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
export function GraphQLTryIt({ api, endpoint, server, credId }: {
  api: ApiDetail; endpoint: ApiEndpoint; server: string; credId: string
}) {
  const args = (endpoint.parameters || []).map((p) => (p as { name?: string }).name).filter(Boolean) as string[]
  const argInline = args.length ? `(${args.map((a) => `${a}: `).join(", ")})` : ""
  const skeleton = `${endpoint.method} {\n  ${endpoint.path}${argInline} {\n    \n  }\n}\n`
  const [query, setQuery] = useState(skeleton)
  const [vars, setVars] = useState("")
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>([EMPTY_HEADER_ROW])
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
        headers: { "Content-Type": "application/json", ...headerRowsToObject(headerRows) },
        body: { query, ...(variables !== undefined ? { variables } : {}) },
        api_name: api.name,
        credential_id: credId !== "none" ? Number(credId) : undefined,
        endpoint_method: endpoint.method, endpoint_path: endpoint.path,
      }))
    } catch (e) { notifyError(e, "엔드포인트를 호출하지 못했습니다.") } finally { setRunning(false) }
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
            options={{ minimap: { enabled: false }, fontSize: MONACO_FONT_SIZE, lineNumbers: "on", scrollBeyondLastLine: false, automaticLayout: true, tabSize: 2, wordWrap: "on", padding: { top: 6, bottom: 6 } }}
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
            options={{ minimap: { enabled: false }, fontSize: MONACO_FONT_SIZE, lineNumbers: "on", scrollBeyondLastLine: false, automaticLayout: true, tabSize: 2, wordWrap: "on", padding: { top: 6, bottom: 6 } }}
          />
        </div>
      </div>
      {!isSubscription && <HeaderRowsInput rows={headerRows} onChange={setHeaderRows} hint="(인증 토큰 등 · Content-Type 은 application/json 기본)" />}
      <div className="flex items-center gap-2">
        <code className="flex-1 truncate rounded bg-muted px-2 py-1 text-sm" title={server}>POST {server || "<Base URL>"}</code>
        <Button size="sm" onClick={run} disabled={running || !server || isSubscription} title={isSubscription ? "구독은 WebSocket 기반이라 실행 불가" : undefined}>{running ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Play className="mr-1 h-4 w-4" />} 실행</Button>
      </div>
      {result && <InvokeResultPanel result={result} />}
    </div>
  )
}

// SOAP 호출 — POST(XML Envelope) + SOAPAction 헤더로 전송.
export function SoapTryIt({ api, endpoint, server, credId }: {
  api: ApiDetail; endpoint: ApiEndpoint; server: string; credId: string
}) {
  const ex = (endpoint.extra || {}) as { soap_action?: string; soap_version?: string }
  const is12 = ex.soap_version === "1.2"
  const ns = is12 ? "http://www.w3.org/2003/05/soap-envelope" : "http://schemas.xmlsoap.org/soap/envelope/"
  const skeleton = `<?xml version="1.0" encoding="UTF-8"?>\n<soap:Envelope xmlns:soap="${ns}">\n  <soap:Body>\n    <!-- ${endpoint.path} 요청 메시지 -->\n  </soap:Body>\n</soap:Envelope>\n`
  const [body, setBody] = useState(skeleton)
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>([EMPTY_HEADER_ROW])
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<InvokeResult | null>(null)

  const run = async () => {
    if (!server) { toast.error("상단에서 서버(Base URL)를 선택/입력하세요."); return }
    const custom = headerRowsToObject(headerRows)
    const headers: Record<string, string> = {}
    // 직접 입력한 Content-Type·SOAPAction 이 있으면 그것을 쓴다
    if (!hasHeader(custom, "Content-Type")) headers["Content-Type"] = is12 ? "application/soap+xml; charset=utf-8" : "text/xml; charset=utf-8"
    if (!is12 && ex.soap_action && !hasHeader(custom, "SOAPAction")) headers["SOAPAction"] = `"${ex.soap_action}"`
    Object.assign(headers, custom)
    setRunning(true)
    try {
      setResult(await invokeApi({
        method: "POST", url: server, headers, body,
        api_name: api.name,
        credential_id: credId !== "none" ? Number(credId) : undefined,
        endpoint_method: endpoint.method, endpoint_path: endpoint.path,
      }))
    } catch (e) { notifyError(e, "엔드포인트를 호출하지 못했습니다.") } finally { setRunning(false) }
  }

  return (
    <div className="grid gap-3">
      <p className="text-xs text-muted-foreground">SOAP {ex.soap_version || "1.1"} — 단일 엔드포인트(POST {server || "<Base URL>"})로 XML Envelope 를 전송합니다.{ex.soap_action ? ` SOAPAction: ${ex.soap_action}` : ""}</p>
      <div className="space-y-1">
        <p className="text-xs font-medium text-muted-foreground">요청 XML (SOAP Envelope)</p>
        <div className="resize-y overflow-hidden rounded-md border" style={{ height: 220, minHeight: 120, maxHeight: 600 }}>
          <MonacoEditor
            height="100%" language="xml" theme="light" value={body} onChange={(v) => setBody(v ?? "")}
            options={{ minimap: { enabled: false }, fontSize: MONACO_FONT_SIZE, lineNumbers: "on", scrollBeyondLastLine: false, automaticLayout: true, tabSize: 2, wordWrap: "on", padding: { top: 6, bottom: 6 } }}
          />
        </div>
      </div>
      <HeaderRowsInput rows={headerRows} onChange={setHeaderRows} hint="(인증 토큰 등 · Content-Type·SOAPAction 은 자동 설정, 직접 입력 시 우선)" />
      <div className="flex items-center gap-2">
        <code className="flex-1 truncate rounded bg-muted px-2 py-1 text-sm" title={server}>POST {server || "<Base URL>"}</code>
        <Button size="sm" onClick={run} disabled={running || !server}>{running ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Play className="mr-1 h-4 w-4" />} 실행</Button>
      </div>
      {result && <InvokeResultPanel result={result} />}
    </div>
  )
}
