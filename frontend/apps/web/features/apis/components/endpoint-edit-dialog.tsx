"use client"

import { useEffect, useState } from "react"
import { Loader2, Minus, Plus } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"
import { useConfirm } from "@/components/confirm-provider"

import { Button } from "@workspace/ui/components/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { Textarea } from "@workspace/ui/components/textarea"
import { cn } from "@workspace/ui/lib/utils"

import { type ApiEndpoint, type EndpointPayload, createEndpoint, updateEndpoint } from "../api"
import { CodeEditor, TagsInput } from "./apis-detail-shared"

// 요청/응답 스키마 JSON 편집기 — 공용 Monaco 에디터(JSON 하이라이트·괄호 매칭, text-sm).
function JsonSchemaEditor({ value, onChange, placeholder, ariaLabel }: {
  value: string; onChange: (v: string) => void; placeholder: string; ariaLabel: string
}) {
  return <CodeEditor language="json" value={value} onChange={onChange} placeholder={placeholder} ariaLabel={ariaLabel} />
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
export const protocolOps = (p?: string | null) => PROTOCOL_OPS[p || "REST"] ?? PROTOCOL_OPS.REST!
// 표준 gRPC 상태코드(문서화용)
const GRPC_STATUS_CODES = ["OK", "CANCELLED", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED", "NOT_FOUND", "ALREADY_EXISTS", "PERMISSION_DENIED", "UNAUTHENTICATED", "RESOURCE_EXHAUSTED", "FAILED_PRECONDITION", "ABORTED", "OUT_OF_RANGE", "UNIMPLEMENTED", "INTERNAL", "UNAVAILABLE", "DATA_LOSS"]
const PARAM_LOCATIONS = ["query", "path", "header"]
const PARAM_TYPES = ["string", "integer", "number", "boolean", "array", "object"]
// raw: 불러온 원본 파라미터 — 행에서 다루지 않는 키(description·format·items·enum 등)를 저장 시 보존
type ParamRow = { name: string; location: string; type: string; required: boolean; raw?: Record<string, unknown> }
type BodyField = { name: string; type: string; required: boolean; description: string }
type RespRow = { code: string; description: string }

// ---------------------------------------------------------------------------
// 행·필드 편집기로 손실 없이 옮길 수 있는지 판단 — 아니면 JSON 모드로 열어 원본을 그대로 보존한다.
// (행·필드 모드는 type/description 만 다루므로, 그 외 키가 있는 스키마를 행으로 열면 저장 시 사라진다)
// ---------------------------------------------------------------------------
const isPlainObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v)
const onlyKeys = (o: Record<string, unknown>, allowed: string[]) => Object.keys(o).every((k) => allowed.includes(k))

function bodyFitsFields(rb: unknown): boolean {
  if (!isPlainObject(rb) || !onlyKeys(rb, ["type", "properties", "required"])) return false
  if (rb.type !== undefined && rb.type !== "object") return false
  if (!isPlainObject(rb.properties)) return false
  if (rb.required !== undefined && !(Array.isArray(rb.required) && rb.required.every((x) => typeof x === "string"))) return false
  return Object.values(rb.properties).every((p) =>
    isPlainObject(p) && onlyKeys(p, ["type", "description"])
    && (p.type === undefined || (typeof p.type === "string" && PARAM_TYPES.includes(p.type)))
    && (p.description === undefined || typeof p.description === "string"))
}

function responsesFitRows(rs: unknown): boolean {
  return isPlainObject(rs) && Object.values(rs).every((v) =>
    isPlainObject(v) && onlyKeys(v, ["description"]) && (v.description === undefined || typeof v.description === "string"))
}

/** 본문 필드 → JSON 스키마(필드가 없으면 null) */
function fieldsToSchema(fields: BodyField[]): Record<string, unknown> | null {
  const props: Record<string, unknown> = {}
  const required: string[] = []
  for (const f of fields) {
    const nm = f.name.trim()
    if (!nm) continue
    props[nm] = { type: f.type, ...(f.description.trim() ? { description: f.description.trim() } : {}) }
    if (f.required) required.push(nm)
  }
  return Object.keys(props).length ? { type: "object", properties: props, ...(required.length ? { required } : {}) } : null
}

/** JSON 스키마 → 본문 필드(bodyFitsFields 를 통과한 스키마만) */
function schemaToFields(rb: Record<string, unknown>): BodyField[] {
  const props = (rb.properties ?? {}) as Record<string, { type?: string; description?: string }>
  const req = new Set(Array.isArray(rb.required) ? (rb.required as string[]) : [])
  return Object.entries(props).map(([name, p]) => ({ name, type: p?.type || "string", required: req.has(name), description: p?.description || "" }))
}

function rowsToResponses(rows: RespRow[]): Record<string, unknown> | null {
  const r: Record<string, unknown> = {}
  for (const row of rows) { const c = row.code.trim(); if (c) r[c] = { description: row.description.trim() } }
  return Object.keys(r).length ? r : null
}

function responsesToRows(rs: Record<string, unknown>): RespRow[] {
  return Object.entries(rs as Record<string, { description?: string }>).map(([code, v]) => ({ code, description: v?.description || "" }))
}

/** 경로 템플릿의 {name} 목록 */
const pathTemplateParams = (path: string) => [...path.matchAll(/\{([^{}/]+)\}/g)].map((m) => m[1]!.trim()).filter(Boolean)

type FormState = {
  method: string; extra: Record<string, string>; statusCodes: string[]; path: string; summary: string; description: string; tags: string
  params: ParamRow[]; reqMode: "fields" | "json"; bodyFields: BodyField[]; reqBody: string; respMode: "rows" | "json"; respRows: RespRow[]; resp: string
}
// 변경 여부 비교용 — 원본 파라미터(raw)는 편집 대상이 아니므로 제외
const formKey = (f: FormState) => JSON.stringify({ ...f, params: f.params.map(({ raw: _raw, ...rest }) => rest) })

/** 파라미터 행 → 저장 형식. 원본(raw)의 다른 키는 보존하고, 타입이 바뀐 경우에만 스키마를 새로 만든다. */
function paramToPayload(p: ParamRow): Record<string, unknown> {
  const raw = p.raw ?? {}
  const base = { ...raw, name: p.name.trim(), in: p.location, required: p.required }
  // Swagger 2 형식(스키마 없이 최상위 type)은 그 형식을 유지
  if ("type" in raw && !("schema" in raw)) return { ...base, type: p.type }
  const rawSchema = isPlainObject(raw.schema) ? raw.schema : {}
  return { ...base, schema: rawSchema.type === p.type ? rawSchema : { type: p.type } }
}

export function EndpointEditDialog({ apiName, protocol, endpoint, open, onOpenChange, onSaved }: {
  apiName: string; protocol?: string | null; endpoint: ApiEndpoint | null; open: boolean; onOpenChange: (o: boolean) => void; onSaved: () => Promise<void> | void
}) {
  const askConfirm = useConfirm()
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
  // 열 때의 폼 값 — 닫을 때 변경 여부(dirty) 확인용
  const [initialKey, setInitialKey] = useState("")

  // 다이얼로그가 열릴 때 대상 엔드포인트 값으로 초기화(추가는 빈 값).
  useEffect(() => {
    if (!open) return
    let init: FormState
    if (endpoint) {
      const ex = (endpoint.extra || {}) as Record<string, unknown>
      // 요청 바디: 필드 빌더로 손실 없이 표현되는 단순 object 스키마면 필드 모드, 아니면 JSON 모드(원본 보존).
      const rb = endpoint.request_body
      const fitsFields = !!rb && bodyFitsFields(rb)
      // 응답: 코드→{description} 만 있으면 행으로, 스키마(content 등)가 있으면 JSON 모드(원본 보존).
      const rs = endpoint.responses
      const fitsRows = !!rs && responsesFitRows(rs)
      init = {
        method: endpoint.method || ops.typeOptions[0] || "GET",
        extra: Object.fromEntries(ops.extras.map((e) => [e.key, ex[e.key] != null ? String(ex[e.key]) : ""])),
        statusCodes: Array.isArray(ex.status_codes) ? (ex.status_codes as unknown[]).map(String) : [],
        path: endpoint.path || "",
        summary: endpoint.summary || "",
        description: endpoint.description || "",
        tags: (endpoint.tags || []).join(", "),
        params: (endpoint.parameters || []).map((p) => {
          const pp = p as { name?: string; in?: string; required?: boolean; type?: string; schema?: { type?: string } }
          return { name: pp.name || "", location: pp.in || "query", type: pp.schema?.type || pp.type || "string", required: !!pp.required, raw: p as Record<string, unknown> }
        }),
        reqMode: rb && !fitsFields ? "json" : "fields",
        bodyFields: rb && fitsFields ? schemaToFields(rb) : [],
        reqBody: rb && !fitsFields ? JSON.stringify(rb, null, 2) : "",
        respMode: rs && !fitsRows ? "json" : "rows",
        respRows: rs && fitsRows ? responsesToRows(rs) : [],
        resp: rs && !fitsRows ? JSON.stringify(rs, null, 2) : "",
      }
    } else {
      init = {
        method: ops.typeOptions[0] || "GET", extra: {}, statusCodes: [], path: "", summary: "", description: "", tags: "", params: [],
        reqMode: "fields", bodyFields: [], reqBody: "", respMode: "rows", respRows: [], resp: "",
      }
    }
    setMethod(init.method); setExtra(init.extra); setStatusCodes(init.statusCodes); setPath(init.path); setSummary(init.summary)
    setDescription(init.description); setTags(init.tags); setParams(init.params)
    setReqMode(init.reqMode); setBodyFields(init.bodyFields); setReqBody(init.reqBody)
    setRespMode(init.respMode); setRespRows(init.respRows); setResp(init.resp)
    setInitialKey(formKey(init))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, endpoint])

  const dirty = open && initialKey !== "" && formKey({
    method, extra, statusCodes, path, summary, description, tags, params, reqMode, bodyFields, reqBody, respMode, respRows, resp,
  }) !== initialKey
  const requestClose = async () => {
    if (busy) return  // 저장 중에는 닫지 않는다(결과를 놓치지 않도록)
    if (dirty && !(await askConfirm({
      title: "변경 내용 버리기",
      description: "저장하지 않은 변경 내용이 사라집니다. 닫을까요?",
      confirmText: "닫기",
      destructive: true,
    }))) return
    onOpenChange(false)
  }

  // 필드 ↔ JSON 전환 — 지금 입력한 내용을 옮긴다(옮길 수 없으면 전환하지 않음)
  const switchReqMode = (to: "fields" | "json") => {
    if (to === reqMode) return
    if (to === "json") {
      const schema = fieldsToSchema(bodyFields)
      setReqBody(schema ? JSON.stringify(schema, null, 2) : "")
      setReqMode("json")
      return
    }
    if (!reqBody.trim()) { setBodyFields([]); setReqMode("fields"); return }
    let parsed: unknown
    try { parsed = JSON.parse(reqBody) } catch { toast.error("JSON 이 올바르지 않아 필드로 바꿀 수 없습니다."); return }
    if (!bodyFitsFields(parsed)) {
      toast.error("필드 편집기는 type·description 만 있는 단순 object 스키마만 다룰 수 있습니다. JSON 으로 계속 편집하세요.")
      return
    }
    setBodyFields(schemaToFields(parsed as Record<string, unknown>))
    setReqMode("fields")
  }
  const switchRespMode = (to: "rows" | "json") => {
    if (to === respMode) return
    if (to === "json") {
      const r = rowsToResponses(respRows)
      setResp(r ? JSON.stringify(r, null, 2) : "")
      setRespMode("json")
      return
    }
    if (!resp.trim()) { setRespRows([]); setRespMode("rows"); return }
    let parsed: unknown
    try { parsed = JSON.parse(resp) } catch { toast.error("JSON 이 올바르지 않아 행으로 바꿀 수 없습니다."); return }
    if (!responsesFitRows(parsed)) {
      toast.error("행 편집기는 상태코드별 description 만 다룰 수 있습니다(스키마·content 가 있음). JSON 으로 계속 편집하세요.")
      return
    }
    setRespRows(responsesToRows(parsed as Record<string, unknown>))
    setRespMode("rows")
  }

  const save = async () => {
    if (!path.trim()) { toast.error(`${ops.idLabel}을(를) 입력하세요.`); return }
    let request_body: Record<string, unknown> | null = null
    let responses: Record<string, unknown> | null = null
    // 요청 바디
    if (reqMode === "fields") {
      request_body = fieldsToSchema(bodyFields)
    } else {
      try { if (reqBody.trim()) request_body = JSON.parse(reqBody) } catch { toast.error("요청 바디 스키마가 올바른 JSON이 아닙니다."); return }
    }
    // 응답
    if (respMode === "rows") {
      responses = rowsToResponses(respRows)
    } else {
      try { if (resp.trim()) responses = JSON.parse(resp) } catch { toast.error("응답 스키마가 올바른 JSON이 아닙니다."); return }
    }
    // REST: 경로의 {name} 중 파라미터에 없는 것은 필수 path 파라미터로 자동 추가(호출 화면에 입력란이 생기도록)
    let finalParams = params
    if (ops.params === "rest") {
      const have = new Set(params.filter((p) => p.location === "path").map((p) => p.name.trim()))
      const added = pathTemplateParams(path).filter((n, i, a) => !have.has(n) && a.indexOf(n) === i)
      if (added.length > 0) {
        finalParams = [...params, ...added.map((name) => ({ name, location: "path", type: "string", required: true }))]
        setParams(finalParams)
        toast.info(`경로의 파라미터를 추가했습니다: ${added.join(", ")}`)
      }
    }
    const extraObj: Record<string, unknown> = {}
    for (const e of ops.extras) { const v = (extra[e.key] || "").trim(); if (v) extraObj[e.key] = v }
    if (isGrpc && statusCodes.length) extraObj.status_codes = statusCodes
    const payload: EndpointPayload = {
      method, path: path.trim(),
      summary: summary.trim() || null,
      description: description.trim() || null,
      tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      parameters: finalParams.filter((p) => p.name.trim()).map(paramToPayload),
      request_body, responses,
      extra: Object.keys(extraObj).length ? extraObj : null,
    }
    setBusy(true)
    try {
      if (endpoint) await updateEndpoint(apiName, endpoint.id, payload)
      else await createEndpoint(apiName, payload)
    } catch (e) {
      notifyError(e, endpoint ? "엔드포인트를 수정하지 못했습니다." : "엔드포인트를 추가하지 못했습니다.")
      setBusy(false)
      return
    }
    setBusy(false)
    onOpenChange(false)
    toast.success(endpoint ? "엔드포인트를 수정했습니다." : "엔드포인트를 추가했습니다.")
    try { await onSaved() }
    catch { toast.warning("저장했지만 목록을 새로고침하지 못했습니다.") }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) void requestClose() }}>
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
                  <button type="button" onClick={() => switchReqMode("fields")} className={cn("px-2 py-1 text-xs", reqMode === "fields" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground")}>필드</button>
                  <button type="button" onClick={() => switchReqMode("json")} className={cn("px-2 py-1 text-xs", reqMode === "json" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground")}>JSON</button>
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
              <JsonSchemaEditor ariaLabel="요청 바디 JSON 스키마" placeholder='{ "type": "object", "properties": { ... } }' value={reqBody} onChange={setReqBody} />
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
                  <button type="button" onClick={() => switchRespMode("rows")} className={cn("px-2 py-1 text-xs", respMode === "rows" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground")}>행</button>
                  <button type="button" onClick={() => switchRespMode("json")} className={cn("px-2 py-1 text-xs", respMode === "json" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground")}>JSON</button>
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
              <JsonSchemaEditor ariaLabel="응답 JSON" placeholder='{ "200": { "description": "ok" } }' value={resp} onChange={setResp} />
            )}
          </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => { void requestClose() }} disabled={busy}>취소</Button>
          <Button onClick={save} disabled={busy}>{busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}저장</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
