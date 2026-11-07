import { describe, expect, it } from "vitest"
import { parseSchemaAfter } from "./schema-change"

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
