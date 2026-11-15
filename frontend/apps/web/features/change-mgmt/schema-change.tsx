"use client"

// 변경 요청(CR)의 스키마 변경 명세(schema_after) 편집기 · 뷰어.
//
// schema_after 는 "부분 변경 명세"다 — 백엔드 워커(apply_change)가 현재 스키마에 병합한다.
//   {"fields": [{"field_path","field_type","nullable", 바뀐 속성…}...], "drop": ["col"...]}
// 편집기는 현재 스키마와의 차이(타입 · 네이티브 타입 · PK · 고유 · 파티션 · 분산 · NULL 변경, 신규 컬럼, 삭제)만
// 이 형식으로 만든다(TODO-132). 인덱스는 원본 DB 가 정하므로 보기만 한다(데이터셋 스키마 편집과 같다).
// 뷰어는 워커가 받아들이는 4가지 형식(구조화/필드 배열/평면 맵/속성 맵)을 모두 읽는다
// (backend/app/change_mgmt/activities.py `_parse_schema_after` 와 동일 규칙).

import { Tip } from "@/components/tip"
import { useMemo } from "react"
import { Check, Plus, RotateCcw, Trash2, Undo2 } from "lucide-react"

import { StatusBadge, type StatusMeta } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
import { Input } from "@workspace/ui/components/input"
import { cn } from "@workspace/ui/lib/utils"

/** 변경 요청에서 바꿀 수 있는 켜고 끄는 속성 — 데이터셋 상세 스키마 표와 같은 이름. 바꾸면 호환이 깨질 수 있다. */
export const FLAG_ATTRS = [
  { key: "is_primary_key", label: "PK" },
  { key: "is_unique", label: "고유" },
  { key: "is_partition_key", label: "파티션" },
  { key: "is_distribution_key", label: "분산" },
] as const
export type FlagKey = (typeof FLAG_ATTRS)[number]["key"]
type Flags = Record<FlagKey, boolean>

/** 현재 스키마의 한 컬럼. 이름 · 타입 · NULL 외에는 없으면 빈 값 · false. */
export type BaseField = { field_path: string; field_type: string; nullable: boolean; native_type?: string; is_indexed?: boolean } & Partial<Flags>

/** 편집기 행. orig 가 없으면 신규 컬럼. */
export type SchemaRow = {
  key: string
  orig?: BaseField
  field_path: string
  field_type: string
  native_type: string
  nullable: boolean
  /** 원본 DB 의 인덱스 — 보기만 */
  is_indexed: boolean
  drop: boolean
} & Flags

export type SchemaChangeSpec = {
  fields: ({ field_path: string; field_type: string; nullable: "true" | "false"; native_type?: string } & Partial<Record<FlagKey, "true" | "false">>)[]
  drop: string[]
}

const flagsOf = (f: Partial<Flags>): Flags =>
  Object.fromEntries(FLAG_ATTRS.map(({ key }) => [key, !!f[key]])) as Flags

/** 행을 원래 값으로(삭제 표시 · 초기화) */
const origValues = (o: BaseField) => ({ field_type: o.field_type, native_type: o.native_type ?? "", nullable: o.nullable, ...flagsOf(o) })

export function rowsFromFields(fields: BaseField[]): SchemaRow[] {
  return fields.map((f) => ({
    key: `o:${f.field_path}`, orig: f, field_path: f.field_path, ...origValues(f), is_indexed: !!f.is_indexed, drop: false,
  }))
}

/** 신규 행 기본값 */
export function newRow(key: string): SchemaRow {
  return { key, field_path: "", field_type: "", native_type: "", nullable: true, is_indexed: false, drop: false, ...flagsOf({}) }
}

const flagChanged = (r: SchemaRow, k: FlagKey) => !!r.orig && r[k] !== !!r.orig[k]
const nativeChanged = (r: SchemaRow) => !!r.orig && r.native_type.trim() !== (r.orig.native_type ?? "")

function rowState(r: SchemaRow): "new" | "drop" | "modified" | "same" {
  if (!r.orig) return "new"
  if (r.drop) return "drop"
  if (r.field_type.trim() !== r.orig.field_type || r.nullable !== r.orig.nullable || nativeChanged(r)
    || FLAG_ATTRS.some(({ key }) => flagChanged(r, key))) return "modified"
  return "same"
}

/** 편집 행 → 부분 변경 명세. 변경이 없으면 null. 오류가 있으면 error. */
export function buildSchemaChange(rows: SchemaRow[]): { spec: SchemaChangeSpec | null; error?: string } {
  const fields: SchemaChangeSpec["fields"] = []
  const drop: string[] = []
  const names = new Set(rows.filter((r) => r.orig && !r.drop).map((r) => r.field_path))
  for (const r of rows) {
    const st = rowState(r)
    if (st === "same") continue
    if (st === "drop") { drop.push(r.orig!.field_path); continue }
    const path = r.field_path.trim()
    const type = r.field_type.trim()
    if (st === "new") {
      if (!path && !type) continue // 빈 신규 행은 무시
      if (!path) return { spec: null, error: "신규 컬럼 이름을 입력하세요." }
      if (names.has(path)) return { spec: null, error: `컬럼 '${path}' 이(가) 이미 있습니다.` }
      names.add(path)
    }
    if (!type) return { spec: null, error: `컬럼 '${path}' 의 타입을 입력하세요.` }
    const f: SchemaChangeSpec["fields"][number] = { field_path: path, field_type: type, nullable: r.nullable ? "true" : "false" }
    // 그 밖의 속성은 바뀐 것만(신규 컬럼은 값이 있는 것만) 담는다
    const native = r.native_type.trim()
    if (r.orig ? nativeChanged(r) : native) f.native_type = native
    for (const { key } of FLAG_ATTRS) {
      if (r.orig ? flagChanged(r, key) : r[key]) f[key] = r[key] ? "true" : "false"
    }
    fields.push(f)
  }
  if (fields.length === 0 && drop.length === 0) return { spec: null }
  return { spec: { fields, drop } }
}

/** 변경 내용으로 추정한 변경 유형 — 삭제 · 타입 변경 · NOT NULL 화 · PK · 고유 · 파티션 · 분산 변경은 BREAKING.
 *  NULL 허용 · 네이티브 타입만 바꾸면 NON_BREAKING. */
export function suggestChangeType(rows: SchemaRow[]): "BREAKING" | "ADDITIVE" | "NON_BREAKING" | null {
  let breaking = false, additive = false, other = false
  for (const r of rows) {
    const st = rowState(r)
    if (st === "drop") breaking = true
    else if (st === "new" && r.field_path.trim()) additive = true
    else if (st === "modified") {
      if (r.field_type.trim() !== r.orig!.field_type || (r.orig!.nullable && !r.nullable)
        || FLAG_ATTRS.some(({ key }) => flagChanged(r, key))) breaking = true
      else other = true
    }
  }
  if (breaking) return "BREAKING"
  if (other) return "NON_BREAKING"
  if (additive) return "ADDITIVE"
  return null
}

/** 필드 변경 표시(TODO-113) — 추가 초록 · 수정 주황 · 삭제 빨강. 예전엔 팔레트 색 테두리를 직접 칠했다. */
const STATE_BADGE: Record<string, StatusMeta> = {
  new: { label: "추가", tone: "success" },
  drop: { label: "삭제", tone: "danger" },
  modified: { label: "수정", tone: "warning" },
}

export function SchemaChangeEditor({
  rows,
  onChange,
  onReset,
}: {
  rows: SchemaRow[]
  onChange: (rows: SchemaRow[]) => void
  onReset?: () => void
}) {
  const update = (key: string, patch: Partial<SchemaRow>) =>
    onChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  const changed = rows.filter((r) => rowState(r) !== "same" && !(rowState(r) === "new" && !r.field_path && !r.field_type)).length

  return (
    <div className="flex flex-col gap-2">
      <div className="max-h-[360px] overflow-auto rounded-md border">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10 bg-muted text-xs text-muted-foreground">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium">컬럼</th>
              <th className="px-2 py-1.5 text-left font-medium">타입</th>
              <th className="px-2 py-1.5 text-left font-medium">네이티브 타입</th>
              {FLAG_ATTRS.slice(0, 2).map(({ key, label }) => <th key={key} className="w-12 px-1 py-1.5 text-center font-medium">{label}</th>)}
              <th className="w-12 px-1 py-1.5 text-center font-medium">인덱스</th>
              {FLAG_ATTRS.slice(2).map(({ key, label }) => <th key={key} className="w-12 px-1 py-1.5 text-center font-medium">{label}</th>)}
              <th className="w-12 px-1 py-1.5 text-center font-medium">NULL</th>
              <th className="w-16 px-2 py-1.5 text-center font-medium">변경</th>
              <th className="w-10 px-2 py-1.5" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const st = rowState(r)
              const badge = STATE_BADGE[st]
              const name = r.field_path || "신규 컬럼"
              // 바뀐 칸은 옅은 주황 배경 — 기존 값은 툴팁 · 아래 글자로
              const changedCell = (on: boolean) => cn("px-1 py-1 text-center", on && "bg-warning/10")
              const flagCell = (key: FlagKey, label: string) => (
                <td key={key} className={changedCell(flagChanged(r, key) && !r.drop)}>
                  <Checkbox
                    checked={r[key]}
                    disabled={r.drop}
                    onCheckedChange={(v) => update(r.key, { [key]: v === true } as Partial<SchemaRow>)}
                    aria-label={`${name} ${label}`}
                  />
                </td>
              )
              return (
                <tr key={r.key} className={cn("border-t", st === "drop" && "bg-destructive/5 text-muted-foreground line-through")}>
                  <td className="px-2 py-1">
                    {r.orig ? (
                      <span className="font-mono text-sm">{r.field_path}</span>
                    ) : (
                      <Input
                        value={r.field_path}
                        onChange={(e) => update(r.key, { field_path: e.target.value })}
                        placeholder="new_column"
                        aria-label="신규 컬럼 이름"
                        className="h-7 font-mono text-sm"
                      />
                    )}
                  </td>
                  <td className="px-2 py-1">
                    <Input
                      value={r.field_type}
                      disabled={r.drop}
                      onChange={(e) => update(r.key, { field_type: e.target.value })}
                      placeholder="VARCHAR(100)"
                      aria-label={`${name} 타입`}
                      className="h-7 font-mono text-sm"
                    />
                    {st === "modified" && r.orig && r.field_type.trim() !== r.orig.field_type && (
                      <span className="text-[11px]">기존 {r.orig.field_type}</span>
                    )}
                  </td>
                  <td className="px-2 py-1">
                    <Input
                      value={r.native_type}
                      disabled={r.drop}
                      onChange={(e) => update(r.key, { native_type: e.target.value })}
                      placeholder="varchar(100)"
                      aria-label={`${name} 네이티브 타입`}
                      className="h-7 font-mono text-sm"
                    />
                    {st === "modified" && nativeChanged(r) && (
                      <span className="text-[11px]">기존 {r.orig!.native_type || "없음"}</span>
                    )}
                  </td>
                  {FLAG_ATTRS.slice(0, 2).map(({ key, label }) => flagCell(key, label))}
                  <td className="px-1 py-1 text-center">
                    {r.is_indexed && <Check className="mx-auto h-4 w-4 text-muted-foreground" aria-label="인덱스 있음" />}
                  </td>
                  {FLAG_ATTRS.slice(2).map(({ key, label }) => flagCell(key, label))}
                  <td className={changedCell(!!r.orig && r.nullable !== r.orig.nullable && !r.drop)}>
                    <Checkbox
                      checked={r.nullable}
                      disabled={r.drop}
                      onCheckedChange={(v) => update(r.key, { nullable: v === true })}
                      aria-label={`${name} NULL 허용`}
                    />
                  </td>
                  <td className="px-2 py-1 text-center">
                    {badge && <StatusBadge {...badge} className="no-underline" />}
                  </td>
                  <td className="px-1 py-1 text-center">
                    {r.orig ? (
                      <Tip label={r.drop ? "삭제 취소" : "컬럼 삭제"}><Button
                        size="sm" variant="ghost" className="h-7 w-7 p-0"
                        onClick={() =>
                          r.drop
                            ? update(r.key, { drop: false })
                            : update(r.key, { drop: true, ...origValues(r.orig!) })
                        }
                        aria-label={r.drop ? "삭제 취소" : "컬럼 삭제"}
                      >
                        {r.drop ? <Undo2 className="h-3.5 w-3.5" /> : <Trash2 className="h-3.5 w-3.5 text-destructive" />}
                      </Button></Tip>
                    ) : (
                      <Tip label="신규 컬럼 제거"><Button
                        size="sm" variant="ghost" className="h-7 w-7 p-0"
                        onClick={() => onChange(rows.filter((x) => x.key !== r.key))}
                        aria-label="신규 컬럼 제거"
                      >
                        <Trash2 className="h-3.5 w-3.5 text-destructive" />
                      </Button></Tip>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-2">
        <Button
          size="sm" variant="outline" className="h-7"
          onClick={() =>
            onChange([...rows, newRow(`n:${Date.now()}:${rows.length}`)])
          }
        >
          <Plus className="mr-1 h-3.5 w-3.5" /> 컬럼 추가
        </Button>
        {onReset && (
          <Button size="sm" variant="ghost" className="h-7" onClick={onReset} disabled={changed === 0}>
            <RotateCcw className="mr-1 h-3.5 w-3.5" /> 초기화
          </Button>
        )}
        <span className="ml-auto text-xs">변경 {changed}건</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 뷰어 (CR 상세)
// ---------------------------------------------------------------------------

/** 뷰어용 필드 — 속성 값은 "true"/"false" 문자열(없으면 명세에 없던 것). */
type ViewField = { field_path: string; field_type: string; nullable?: string; native_type?: string; is_indexed?: string }
  & Partial<Record<FlagKey, string>>

const VIEW_ATTRS = ["native_type", "is_indexed", ...FLAG_ATTRS.map((a) => a.key)] as const

/** 명세 · 기록의 한 필드에서 속성 값만 꺼낸다 — bool 은 "true"/"false" 로, 없는 키는 넣지 않는다. */
function attrsOf(o: Record<string, unknown>): Partial<ViewField> {
  const out: Record<string, string> = {}
  for (const k of VIEW_ATTRS) if (o[k] != null && o[k] !== "") out[k] = String(o[k])
  if (out.is_primary_key == null && typeof o.primary_key === "boolean") out.is_primary_key = String(o.primary_key)
  return out
}

/** schema_after 문자열을 (upsert, drop) 으로 해석. 백엔드 `_parse_schema_after` 와 같은 4가지 형식. */
export function parseSchemaAfter(raw: string | null | undefined): { upserts: ViewField[]; drops: string[] } | null {
  if (!raw) return null
  let data: unknown
  try { data = JSON.parse(raw) } catch { return null }
  const norm = (path: string, v: unknown): ViewField => {
    if (v && typeof v === "object") {
      const o = v as Record<string, unknown>
      return {
        field_path: path, field_type: String(o.field_type ?? o.type ?? "STRING"),
        ...(o.nullable == null ? {} : { nullable: String(o.nullable) }), ...attrsOf(o),
      }
    }
    return { field_path: path, field_type: String(v) }
  }
  const fromArray = (arr: unknown[]) =>
    arr.filter((f): f is Record<string, unknown> => !!f && typeof f === "object" && !!(f as Record<string, unknown>).field_path)
      .map((f) => norm(String(f.field_path), f))
  if (Array.isArray(data)) return { upserts: fromArray(data), drops: [] }
  if (data && typeof data === "object") {
    const o = data as Record<string, unknown>
    // 삭제만 있는 명세({"drop": [...]})도 구조화 형식 — 예전에는 "drop" 컬럼 추가로 보였다(IT CHG-T11)
    if ("fields" in o || Array.isArray(o.drop)) {
      return {
        upserts: Array.isArray(o.fields) ? fromArray(o.fields) : [],
        drops: Array.isArray(o.drop) ? o.drop.map(String) : [],
      }
    }
    return { upserts: Object.entries(o).map(([k, v]) => norm(k, v)), drops: [] }
  }
  return null
}

/** schema_before 해석 — 백엔드 자동 채움 형식 `{"fields":[{"name","type","nullable":bool,"primary_key":bool, is_*}]}` 과
 *  필드 배열/구조화 형식(`field_path`/`field_type`)을 모두 읽는다. */
export function parseSchemaBefore(raw: string | null | undefined): Map<string, ViewField> {
  const m = new Map<string, ViewField>()
  if (!raw) return m
  let data: unknown
  try { data = JSON.parse(raw) } catch { return m }
  const list = Array.isArray(data)
    ? data
    : data && typeof data === "object" && Array.isArray((data as { fields?: unknown }).fields)
      ? (data as { fields: unknown[] }).fields
      : []
  for (const f of list) {
    if (!f || typeof f !== "object") continue
    const o = f as Record<string, unknown>
    const path = o.field_path ?? o.name
    if (!path) continue
    m.set(String(path), {
      field_path: String(path),
      field_type: String(o.field_type ?? o.type ?? ""),
      ...(o.nullable == null ? {} : { nullable: String(o.nullable) }),
      ...attrsOf(o),
    })
  }
  return m
}

/** 한 필드를 "타입 · 네이티브 · NULL · PK · …" 조각으로 — 변경 전 · 후를 같은 모양으로 비교한다. */
function describe(f: ViewField): string[] {
  const out = [f.field_type]
  if (f.native_type && f.native_type !== f.field_type) out.push(`네이티브 ${f.native_type}`)
  if (f.nullable != null) out.push(f.nullable === "true" ? "NULL" : "NOT NULL")
  for (const { key, label } of FLAG_ATTRS) if (f[key] === "true") out.push(label)
  if (f.is_indexed === "true") out.push("인덱스")
  return out
}

/** 변경 전 · 후 조각 — 다른 쪽에 없는 조각은 굵게(바뀐 것). */
function Parts({ parts, other }: { parts: string[]; other?: string[] }) {
  return (
    <span className="font-mono">
      {parts.map((p, i) => (
        <span key={p}>
          {i > 0 && " · "}
          <span className={cn(other && !other.includes(p) && "font-semibold text-warning")}>{p}</span>
        </span>
      ))}
    </span>
  )
}

export function SchemaChangeView({ raw, before }: { raw: string | null | undefined; before?: string | null }) {
  const parsed = useMemo(() => parseSchemaAfter(raw), [raw])
  // schema_before(생성 시점 스키마)가 있으면 추가/수정을 구분하고 변경 전 속성을 함께 보여준다.
  const prev = useMemo(() => parseSchemaBefore(before), [before])
  if (!raw) return <p className="text-sm text-muted-foreground">스키마 변경 없음 (메타데이터만 변경)</p>
  if (!parsed) return <pre className="whitespace-pre-wrap rounded bg-muted/40 p-2 font-mono text-xs">{raw}</pre>
  if (parsed.upserts.length === 0 && parsed.drops.length === 0)
    return <p className="text-sm text-muted-foreground">스키마 변경 없음</p>
  return (
    <div className="overflow-auto rounded-md border">
      <table className="w-full text-sm">
        <thead className="bg-muted text-xs text-muted-foreground">
          <tr>
            <th className="px-2 py-1.5 text-left font-medium">컬럼</th>
            <th className="px-2 py-1.5 text-left font-medium">변경 전</th>
            <th className="px-2 py-1.5 text-left font-medium">변경 후</th>
            <th className="w-16 px-2 py-1.5 text-center font-medium">변경</th>
          </tr>
        </thead>
        <tbody>
          {parsed.upserts.map((f) => {
            const old = prev.get(f.field_path)
            // schema_before 가 없으면(API 로 만든 CR 등) 추가/수정을 구분할 수 없다.
            const kind = !before ? "추가·수정" : old ? "수정" : "추가"
            const badge: StatusMeta = kind === "추가" ? STATE_BADGE.new! : kind === "수정" ? STATE_BADGE.modified! : { label: kind, tone: "neutral" }
            // 명세는 바뀐 속성만 담으므로 변경 후 = 변경 전 위에 명세를 덮은 것
            const after: ViewField = old ? { ...old, ...f } : f
            const beforeParts = old ? describe(old) : undefined
            const afterParts = describe(after)
            return (
              <tr key={`u:${f.field_path}`} className="border-t">
                <td className="px-2 py-1 font-mono">{f.field_path}</td>
                <td className="px-2 py-1">{beforeParts ? <Parts parts={beforeParts} /> : "—"}</td>
                <td className="px-2 py-1"><Parts parts={afterParts} other={beforeParts} /></td>
                <td className="px-2 py-1 text-center">
                  <StatusBadge {...badge} />
                </td>
              </tr>
            )
          })}
          {parsed.drops.map((d) => {
            const old = prev.get(d)
            return (
              <tr key={`d:${d}`} className="border-t bg-destructive/5">
                <td className="px-2 py-1 font-mono line-through">{d}</td>
                <td className="px-2 py-1">{old ? <Parts parts={describe(old)} /> : "—"}</td>
                <td className="px-2 py-1 text-muted-foreground">—</td>
                <td className="px-2 py-1 text-center">
                  <StatusBadge {...STATE_BADGE.drop!} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** 변경 요약 — 추가·수정·삭제 컬럼 수(결재함 카드용). 스키마 변경이 없거나 읽을 수 없으면 null.
 *  schema_before 가 없으면 추가와 수정을 구분할 수 없어 모두 "수정" 으로 센다. */
export function schemaChangeSummary(
  raw: string | null | undefined,
  before?: string | null,
): { added: number; modified: number; dropped: number } | null {
  const parsed = parseSchemaAfter(raw)
  if (!parsed || (parsed.upserts.length === 0 && parsed.drops.length === 0)) return null
  const prev = parseSchemaBefore(before)
  const added = before ? parsed.upserts.filter((f) => !prev.has(f.field_path)).length : 0
  return { added, modified: parsed.upserts.length - added, dropped: parsed.drops.length }
}

/** 초안 수정 — 현재 스키마 편집 행에 저장된 변경 명세(schema_after)를 다시 입힌다.
 *  있는 컬럼은 명세에 있는 속성(타입 · 네이티브 타입 · NULL · PK · 고유 · 파티션 · 분산)을 바꾸고,
 *  없는 컬럼은 신규 행으로, drop 은 삭제 표시. */
export function rowsWithSpec(base: SchemaRow[], raw: string | null | undefined): SchemaRow[] {
  const parsed = parseSchemaAfter(raw)
  if (!parsed) return base
  const rows = base.map((r) => ({ ...r }))
  parsed.upserts.forEach((f, i) => {
    const row = rows.find((r) => r.orig?.field_path === f.field_path)
    const nullable = f.nullable == null ? undefined : f.nullable === "true"
    const target = row ?? { ...newRow(`n:spec:${i}`), field_path: f.field_path }
    target.field_type = f.field_type
    if (nullable !== undefined) target.nullable = nullable
    if (f.native_type != null) target.native_type = f.native_type
    for (const { key } of FLAG_ATTRS) if (f[key] != null) target[key] = f[key] === "true"
    if (!row) rows.push(target)
  })
  for (const d of parsed.drops) {
    const row = rows.find((r) => r.orig?.field_path === d)
    if (row) row.drop = true
  }
  return rows
}
