// ---------------------------------------------------------------------------
// 요청 바디 예시 — 스펙의 example(s) 를 우선 쓰고, 없으면 스키마로 샘플을 만든다.
// OpenAPI 3 requestBody({content:{mime:{schema, example(s)}}}), Swagger 2 `in: body` 파라미터,
// 수동 등록의 평문 JSON 스키마({type: object, properties})를 모두 처리하고 $ref 는 원본 스펙에서 푼다.
// ---------------------------------------------------------------------------
import type { ApiEndpoint } from "../api"

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v)

const MAX_DEPTH = 6

/** 로컬 $ref(#/components/schemas/X, #/definitions/X 등)를 원본 스펙에서 찾는다 */
function resolveRef(root: Obj | null, ref: string): unknown {
  if (!root || !ref.startsWith("#/")) return undefined
  let cur: unknown = root
  for (const raw of ref.slice(2).split("/")) {
    const key = raw.replace(/~1/g, "/").replace(/~0/g, "~")
    if (!isObj(cur)) return undefined
    cur = cur[key]
  }
  return cur
}

function deref(root: Obj | null, v: unknown, seen: Set<string>): unknown {
  let cur = v
  // $ref 사슬 따라가기(순환 방지)
  while (isObj(cur) && typeof cur.$ref === "string") {
    const ref = cur.$ref
    if (seen.has(ref)) return undefined
    seen.add(ref)
    cur = resolveRef(root, ref)
  }
  return cur
}

function sampleForString(s: Obj): string {
  const fmt = String(s.format ?? "")
  if (fmt === "date-time") return "2026-01-01T00:00:00Z"
  if (fmt === "date") return "2026-01-01"
  if (fmt === "email") return "user@example.com"
  if (fmt === "uuid") return "00000000-0000-0000-0000-000000000000"
  if (fmt === "uri" || fmt === "url") return "https://example.com"
  return "string"
}

/** 스키마 → 샘플 값. 순환·깊이 제한을 넘으면 undefined(필드 생략). */
export function sampleFromSchema(root: Obj | null, schema: unknown, depth = 0, seen: Set<string> = new Set()): unknown {
  if (depth > MAX_DEPTH) return undefined
  const localSeen = new Set(seen)
  const s = deref(root, schema, localSeen)
  if (!isObj(s)) return undefined
  if (s.example !== undefined) return s.example
  if (s.default !== undefined) return s.default
  if (Array.isArray(s.enum) && s.enum.length > 0) return s.enum[0]
  if (s.const !== undefined) return s.const
  for (const k of ["allOf"] as const) {
    if (Array.isArray(s[k])) {
      const merged: Obj = {}
      for (const part of s[k] as unknown[]) {
        const v = sampleFromSchema(root, part, depth + 1, localSeen)
        if (isObj(v)) Object.assign(merged, v)
      }
      return merged
    }
  }
  for (const k of ["oneOf", "anyOf"] as const) {
    if (Array.isArray(s[k]) && (s[k] as unknown[]).length > 0) return sampleFromSchema(root, (s[k] as unknown[])[0], depth + 1, localSeen)
  }
  const type = Array.isArray(s.type) ? String(s.type.find((t) => t !== "null") ?? "") : String(s.type ?? "")
  if (type === "object" || (!type && isObj(s.properties))) {
    const out: Obj = {}
    const props = isObj(s.properties) ? s.properties : {}
    for (const [name, ps] of Object.entries(props)) {
      const ds = deref(root, ps, new Set(localSeen))
      if (isObj(ds) && ds.readOnly === true) continue  // 응답 전용 필드는 요청에서 제외
      const v = sampleFromSchema(root, ps, depth + 1, localSeen)
      if (v !== undefined) out[name] = v
    }
    return out
  }
  if (type === "array") {
    const v = sampleFromSchema(root, s.items, depth + 1, localSeen)
    return v === undefined ? [] : [v]
  }
  if (type === "integer") return 0
  if (type === "number") return 0
  if (type === "boolean") return false
  if (type === "string") return sampleForString(s)
  return undefined
}

const JSON_MIME = /json/i

/** 미디어 타입 객체 → 예시 값(example → examples 첫 항목 → 스키마 example → 스키마 샘플) */
function exampleFromMedia(root: Obj | null, media: Obj): unknown {
  if (media.example !== undefined) return media.example
  if (isObj(media.examples)) {
    const first = deref(root, Object.values(media.examples)[0], new Set())
    if (isObj(first) && first.value !== undefined) return first.value
  }
  return sampleFromSchema(root, media.schema)
}

export type RequestExample = { text: string; mime: string | null }

const toText = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v, null, 2))

/** 엔드포인트의 요청 바디 예시(없으면 null). rawSpec 은 $ref 해석용 원본 스펙(JSON). */
export function requestBodyExample(endpoint: ApiEndpoint, rawSpec: string | null): RequestExample | null {
  let root: Obj | null = null
  if (rawSpec) {
    try { const r = JSON.parse(rawSpec) as unknown; root = isObj(r) ? r : null } catch { root = null }
  }
  const rb = deref(root, endpoint.request_body, new Set())

  // OpenAPI 3 requestBody
  if (isObj(rb) && isObj(rb.content)) {
    const entries = Object.entries(rb.content)
    const [mime, media] = entries.find(([m]) => JSON_MIME.test(m)) ?? entries[0] ?? []
    if (!mime || !isObj(media)) return null
    const v = exampleFromMedia(root, media)
    return v === undefined ? null : { text: toText(v), mime }
  }
  // 수동 등록 — 평문 JSON 스키마
  if (isObj(rb) && (rb.type !== undefined || isObj(rb.properties))) {
    const v = sampleFromSchema(root, rb)
    return v === undefined ? null : { text: toText(v), mime: null }
  }
  // Swagger 2 — in: body 파라미터
  const bodyParam = (endpoint.parameters ?? []).find((p) => (p as { in?: string }).in === "body") as Obj | undefined
  if (bodyParam) {
    const v = bodyParam.example !== undefined ? bodyParam.example : sampleFromSchema(root, bodyParam.schema)
    return v === undefined ? null : { text: toText(v), mime: null }
  }
  return null
}
