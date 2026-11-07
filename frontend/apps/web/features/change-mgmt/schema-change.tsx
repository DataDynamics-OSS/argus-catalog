"use client"

// 변경 요청(CR)의 스키마 변경 명세(schema_after) 편집기 · 뷰어.
//
// schema_after 는 "부분 변경 명세"다 — 백엔드 워커(apply_change)가 현재 스키마에 병합한다.
//   {"fields": [{"field_path","field_type","nullable"}...], "drop": ["col"...]}
// 편집기는 현재 스키마와의 차이(타입·NULL 변경, 신규 컬럼, 삭제)만 이 형식으로 만든다.
// 뷰어는 워커가 받아들이는 4가지 형식(구조화/필드 배열/평면 맵/속성 맵)을 모두 읽는다
// (backend/app/change_mgmt/activities.py `_parse_schema_after` 와 동일 규칙).

import { useMemo } from "react"
import { Plus, RotateCcw, Trash2, Undo2 } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
import { Input } from "@workspace/ui/components/input"
import { cn } from "@workspace/ui/lib/utils"

/** 현재 스키마의 한 컬럼(편집에 필요한 속성만). */
export type BaseField = { field_path: string; field_type: string; nullable: boolean }

/** 편집기 행. orig 가 없으면 신규 컬럼. */
export type SchemaRow = {
  key: string
  orig?: BaseField
  field_path: string
  field_type: string
  nullable: boolean
  drop: boolean
}

export type SchemaChangeSpec = {
  fields: { field_path: string; field_type: string; nullable: "true" | "false" }[]
  drop: string[]
}

export function rowsFromFields(fields: BaseField[]): SchemaRow[] {
  return fields.map((f) => ({ key: `o:${f.field_path}`, orig: f, ...f, drop: false }))
}

function rowState(r: SchemaRow): "new" | "drop" | "modified" | "same" {
  if (!r.orig) return "new"
  if (r.drop) return "drop"
  if (r.field_type.trim() !== r.orig.field_type || r.nullable !== r.orig.nullable) return "modified"
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
    fields.push({ field_path: path, field_type: type, nullable: r.nullable ? "true" : "false" })
  }
  if (fields.length === 0 && drop.length === 0) return { spec: null }
  return { spec: { fields, drop } }
}

/** 변경 내용으로 추정한 변경 유형 — 삭제·타입 변경·NOT NULL 화는 BREAKING. */
export function suggestChangeType(rows: SchemaRow[]): "BREAKING" | "ADDITIVE" | "NON_BREAKING" | null {
  let breaking = false, additive = false, other = false
  for (const r of rows) {
    const st = rowState(r)
    if (st === "drop") breaking = true
    else if (st === "new" && r.field_path.trim()) additive = true
    else if (st === "modified") {
      if (r.field_type.trim() !== r.orig!.field_type || (r.orig!.nullable && !r.nullable)) breaking = true
      else other = true
    }
  }
  if (breaking) return "BREAKING"
  if (other) return "NON_BREAKING"
  if (additive) return "ADDITIVE"
  return null
}

const STATE_BADGE: Record<string, { label: string; className: string }> = {
  new: { label: "추가", className: "border-emerald-500/50 text-emerald-600 dark:text-emerald-400" },
  drop: { label: "삭제", className: "border-destructive/50 text-destructive" },
  modified: { label: "수정", className: "border-amber-500/50 text-amber-600 dark:text-amber-400" },
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
          <thead className="sticky top-0 bg-muted/60 text-xs text-muted-foreground">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium">컬럼</th>
              <th className="px-2 py-1.5 text-left font-medium">타입</th>
              <th className="w-14 px-2 py-1.5 text-center font-medium">NULL</th>
              <th className="w-16 px-2 py-1.5 text-center font-medium">변경</th>
              <th className="w-10 px-2 py-1.5" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const st = rowState(r)
              const badge = STATE_BADGE[st]
              return (
                <tr key={r.key} className={cn("border-t", st === "drop" && "bg-destructive/5 text-muted-foreground line-through")}>
                  <td className="px-2 py-1">
                    {r.orig ? (
                      <span className="font-mono text-xs">{r.field_path}</span>
                    ) : (
                      <Input
                        value={r.field_path}
                        onChange={(e) => update(r.key, { field_path: e.target.value })}
                        placeholder="new_column"
                        className="h-7 font-mono text-xs"
                      />
                    )}
                  </td>
                  <td className="px-2 py-1">
                    <Input
                      value={r.field_type}
                      disabled={r.drop}
                      onChange={(e) => update(r.key, { field_type: e.target.value })}
                      placeholder="VARCHAR(100)"
                      className="h-7 font-mono text-xs"
                    />
                    {st === "modified" && r.orig && r.field_type.trim() !== r.orig.field_type && (
                      <span className="text-[11px] text-muted-foreground">기존 {r.orig.field_type}</span>
                    )}
                  </td>
                  <td className="px-2 py-1 text-center">
                    <Checkbox
                      checked={r.nullable}
                      disabled={r.drop}
                      onCheckedChange={(v) => update(r.key, { nullable: v === true })}
                      aria-label={`${r.field_path} NULL 허용`}
                    />
                  </td>
                  <td className="px-2 py-1 text-center">
                    {badge && <Badge variant="outline" className={cn("no-underline", badge.className)}>{badge.label}</Badge>}
                  </td>
                  <td className="px-1 py-1 text-center">
                    {r.orig ? (
                      <Button
                        size="sm" variant="ghost" className="h-7 w-7 p-0"
                        onClick={() =>
                          r.drop
                            ? update(r.key, { drop: false })
                            : update(r.key, { drop: true, field_type: r.orig!.field_type, nullable: r.orig!.nullable })
                        }
                        aria-label={r.drop ? "삭제 취소" : "컬럼 삭제"}
                        title={r.drop ? "삭제 취소" : "컬럼 삭제"}
                      >
                        {r.drop ? <Undo2 className="h-3.5 w-3.5" /> : <Trash2 className="h-3.5 w-3.5 text-destructive" />}
                      </Button>
                    ) : (
                      <Button
                        size="sm" variant="ghost" className="h-7 w-7 p-0"
                        onClick={() => onChange(rows.filter((x) => x.key !== r.key))}
                        aria-label="신규 컬럼 제거"
                      >
                        <Trash2 className="h-3.5 w-3.5 text-destructive" />
                      </Button>
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
            onChange([...rows, { key: `n:${Date.now()}:${rows.length}`, field_path: "", field_type: "", nullable: true, drop: false }])
          }
        >
          <Plus className="mr-1 h-3.5 w-3.5" /> 컬럼 추가
        </Button>
        {onReset && (
          <Button size="sm" variant="ghost" className="h-7" onClick={onReset} disabled={changed === 0}>
            <RotateCcw className="mr-1 h-3.5 w-3.5" /> 초기화
          </Button>
        )}
        <span className="ml-auto text-xs text-muted-foreground">변경 {changed}건</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 뷰어 (CR 상세)
// ---------------------------------------------------------------------------

type ViewField = { field_path: string; field_type: string; nullable?: string }

/** schema_after 문자열을 (upsert, drop) 으로 해석. 백엔드 `_parse_schema_after` 와 같은 4가지 형식. */
export function parseSchemaAfter(raw: string | null | undefined): { upserts: ViewField[]; drops: string[] } | null {
  if (!raw) return null
  let data: unknown
  try { data = JSON.parse(raw) } catch { return null }
  const norm = (path: string, v: unknown): ViewField => {
    if (v && typeof v === "object") {
      const o = v as Record<string, unknown>
      return { field_path: path, field_type: String(o.field_type ?? o.type ?? "STRING"), nullable: o.nullable == null ? undefined : String(o.nullable) }
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

/** schema_before 해석 — 백엔드 자동 채움 형식 `{"fields":[{"name","type","nullable":bool}]}` 과
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
      nullable: o.nullable == null ? undefined : String(o.nullable),
    })
  }
  return m
}

export function SchemaChangeView({ raw, before }: { raw: string | null | undefined; before?: string | null }) {
  const parsed = useMemo(() => parseSchemaAfter(raw), [raw])
  // schema_before(생성 시점 스키마)가 있으면 추가/수정을 구분하고 기존 타입을 함께 보여준다.
  const prev = useMemo(() => parseSchemaBefore(before), [before])
  if (!raw) return <p className="text-sm text-muted-foreground">스키마 변경 없음 (메타데이터만 변경)</p>
  if (!parsed) return <pre className="whitespace-pre-wrap rounded bg-muted/40 p-2 font-mono text-xs">{raw}</pre>
  if (parsed.upserts.length === 0 && parsed.drops.length === 0)
    return <p className="text-sm text-muted-foreground">스키마 변경 없음</p>
  return (
    <div className="overflow-auto rounded-md border">
      <table className="w-full text-sm">
        <thead className="bg-muted/60 text-xs text-muted-foreground">
          <tr>
            <th className="px-2 py-1.5 text-left font-medium">컬럼</th>
            <th className="px-2 py-1.5 text-left font-medium">변경 전 타입</th>
            <th className="px-2 py-1.5 text-left font-medium">변경 후 타입</th>
            <th className="w-16 px-2 py-1.5 text-center font-medium">NULL</th>
            <th className="w-16 px-2 py-1.5 text-center font-medium">변경</th>
          </tr>
        </thead>
        <tbody>
          {parsed.upserts.map((f) => {
            const old = prev.get(f.field_path)
            // schema_before 가 없으면(API 로 만든 CR 등) 추가/수정을 구분할 수 없다.
            const kind = !before ? "추가·수정" : old ? "수정" : "추가"
            const badge = kind === "추가" ? STATE_BADGE.new! : STATE_BADGE.modified!
            const nul = (v?: string) => (v == null ? "" : v === "true" ? " · NULL" : " · NOT NULL")
            return (
              <tr key={`u:${f.field_path}`} className="border-t">
                <td className="px-2 py-1 font-mono text-xs">{f.field_path}</td>
                <td className="px-2 py-1 font-mono text-xs text-muted-foreground">{old ? `${old.field_type}${nul(old.nullable)}` : "—"}</td>
                <td className="px-2 py-1 font-mono text-xs">{f.field_type}</td>
                <td className="px-2 py-1 text-center text-xs">{f.nullable == null ? "—" : f.nullable === "true" ? "Y" : "N"}</td>
                <td className="px-2 py-1 text-center">
                  <Badge variant="outline" className={badge.className}>{kind}</Badge>
                </td>
              </tr>
            )
          })}
          {parsed.drops.map((d) => (
            <tr key={`d:${d}`} className="border-t bg-destructive/5">
              <td className="px-2 py-1 font-mono text-xs line-through">{d}</td>
              <td className="px-2 py-1 font-mono text-xs text-muted-foreground">{prev.get(d)?.field_type ?? "—"}</td>
              <td className="px-2 py-1 text-xs text-muted-foreground">—</td>
              <td className="px-2 py-1 text-center text-xs">—</td>
              <td className="px-2 py-1 text-center">
                <Badge variant="outline" className={STATE_BADGE.drop!.className}>삭제</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
