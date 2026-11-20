import { describe, expect, it } from "vitest"

import { normalizeSchemaRow } from "./schema-edit-fields"

describe("normalizeSchemaRow (DEF-DSET-001)", () => {
  it("그리드가 비운 칸(null · undefined)을 빈 문자열로 맞춘다", () => {
    const row = normalizeSchemaRow({
      key: "k", field_path: "customer_id", display_name: null, field_type: "INT", native_type: undefined,
      description: null, unit: null, calculation: undefined, nullable: "true",
    })
    expect(row.display_name).toBe("")
    expect(row.description).toBe("")
    expect(row.native_type).toBe("")
    expect(row.unit).toBe("")
    expect(row.calculation).toBe("")
    expect(row.description.trim()).toBe("")
  })

  it("값이 있는 칸과 다른 칸은 그대로 둔다", () => {
    const row = normalizeSchemaRow({ field_path: "amount", display_name: "금액", field_type: "DECIMAL", nullable: "false", ordinal: 3 })
    expect(row).toMatchObject({ field_path: "amount", display_name: "금액", field_type: "DECIMAL", nullable: "false", ordinal: 3 })
  })
})
