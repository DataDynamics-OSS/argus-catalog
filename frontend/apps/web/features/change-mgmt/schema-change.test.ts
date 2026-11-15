import { describe, expect, it } from "vitest"
import { buildSchemaChange, newRow, parseSchemaAfter, parseSchemaBefore, rowsFromFields, rowsWithSpec, schemaChangeSummary, suggestChangeType } from "./schema-change"

describe("parseSchemaAfter", () => {
  it("삭제만 있는 명세는 drop 으로 (예전에는 'drop' 컬럼 추가로 보였다 — IT CHG-T11)", () => {
    expect(parseSchemaAfter('{"drop":["phone"]}')).toEqual({ upserts: [], drops: ["phone"] })
  })
  it("구조화·평면 맵은 그대로", () => {
    expect(parseSchemaAfter('{"fields":[{"field_path":"email","field_type":"VARCHAR(255)"}],"drop":["x"]}')?.drops).toEqual(["x"])
    expect(parseSchemaAfter('{"created_at":"TIMESTAMP"}')).toEqual({ upserts: [{ field_path: "created_at", field_type: "TIMESTAMP" }], drops: [] })
    expect(parseSchemaAfter('{"drop":"INT"}')?.upserts[0]?.field_path).toBe("drop")
  })
})

describe("schemaChangeSummary", () => {
  const before = '{"fields":[{"name":"id","type":"INT"},{"name":"email","type":"TEXT"}]}'
  it("추가·수정·삭제를 센다", () => {
    expect(schemaChangeSummary('{"fields":[{"field_path":"email","field_type":"VARCHAR"},{"field_path":"x","field_type":"INT"}],"drop":["id"]}', before))
      .toEqual({ added: 1, modified: 1, dropped: 1 })
  })
  it("before 가 없으면 모두 수정, 변경 없으면 null", () => {
    expect(schemaChangeSummary('{"x":"INT"}', null)).toEqual({ added: 0, modified: 1, dropped: 0 })
    expect(schemaChangeSummary(null, before)).toBeNull()
    expect(schemaChangeSummary('{"fields":[],"drop":[]}', before)).toBeNull()
  })
})

describe("rowsWithSpec", () => {
  const base = rowsFromFields([
    { field_path: "id", field_type: "INT", nullable: false },
    { field_path: "email", field_type: "TEXT", nullable: true },
  ])
  it("저장된 명세를 편집 행에 다시 입히면 같은 명세가 나온다", () => {
    const raw = '{"fields":[{"field_path":"email","field_type":"VARCHAR(255)","nullable":"false"},{"field_path":"x","field_type":"INT","nullable":"true"}],"drop":["id"]}'
    const rows = rowsWithSpec(base, raw)
    expect(buildSchemaChange(rows).spec).toEqual(JSON.parse(raw))
    expect(base[1]!.field_type).toBe("TEXT") // 원본은 그대로
  })
  it("읽을 수 없는 명세는 원래 행", () => {
    expect(rowsWithSpec(base, "not json")).toBe(base)
  })
})

describe("컬럼 속성(TODO-132) — 네이티브 타입 · PK · 고유 · 파티션 · 분산", () => {
  const base = rowsFromFields([
    { field_path: "dt", field_type: "DATE", nullable: false, native_type: "date", is_partition_key: true, is_indexed: true },
    { field_path: "id", field_type: "INT", nullable: false, is_primary_key: true },
  ])
  it("바뀐 속성만 명세에 담는다", () => {
    const rows = base.map((r) => (r.field_path === "dt" ? { ...r, is_partition_key: false, is_distribution_key: true } : r))
    expect(buildSchemaChange(rows).spec).toEqual({
      fields: [{ field_path: "dt", field_type: "DATE", nullable: "false", is_partition_key: "false", is_distribution_key: "true" }],
      drop: [],
    })
  })
  it("신규 컬럼은 켠 속성 · 네이티브 타입만", () => {
    const rows = [...base, { ...newRow("n:1"), field_path: "k", field_type: "INT", native_type: "int4", is_unique: true }]
    expect(buildSchemaChange(rows).spec?.fields).toEqual([
      { field_path: "k", field_type: "INT", nullable: "true", native_type: "int4", is_unique: "true" },
    ])
  })
  it("PK · 파티션 변경은 BREAKING, 네이티브 타입만 바꾸면 NON_BREAKING", () => {
    expect(suggestChangeType(base.map((r) => (r.field_path === "id" ? { ...r, is_primary_key: false } : r)))).toBe("BREAKING")
    expect(suggestChangeType(base.map((r) => (r.field_path === "dt" ? { ...r, is_partition_key: false } : r)))).toBe("BREAKING")
    expect(suggestChangeType(base.map((r) => (r.field_path === "dt" ? { ...r, native_type: "timestamp" } : r)))).toBe("NON_BREAKING")
  })
  it("저장한 명세를 다시 입히면 같은 명세", () => {
    const raw = '{"fields":[{"field_path":"dt","field_type":"DATE","nullable":"false","is_partition_key":"false","is_distribution_key":"true"}],"drop":[]}'
    expect(buildSchemaChange(rowsWithSpec(base, raw)).spec).toEqual(JSON.parse(raw))
  })
  it("schema_before 예전 형식(primary_key bool)도 PK 로 읽는다", () => {
    const m = parseSchemaBefore('{"fields":[{"name":"id","type":"INT","nullable":false,"primary_key":true,"is_partition_key":"true"}]}')
    expect(m.get("id")).toEqual({ field_path: "id", field_type: "INT", nullable: "false", is_primary_key: "true", is_partition_key: "true" })
  })
})
