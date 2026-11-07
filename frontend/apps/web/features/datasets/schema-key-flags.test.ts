import { describe, expect, it } from "vitest"

import { applyKeyFlags } from "./schema-key-flags"

const f = (pk: string, uq = "false") => ({ is_primary_key: pk, is_unique: uq, is_indexed: "false" })

describe("applyKeyFlags", () => {
  it("단일 컬럼 PK 는 고유·인덱스로 채운다", () => {
    const [a, b] = applyKeyFlags([f("true"), f("false")])
    expect(a).toMatchObject({ is_unique: "true", is_indexed: "true" })
    expect(b).toMatchObject({ is_unique: "false", is_indexed: "false" })
  })

  it("복합 PK 의 컬럼은 고유로 채우지 않는다 (ERD 1:1 오판 방지 — DS-T14)", () => {
    const rows = applyKeyFlags([f("true"), f("true"), f("false", "true")])
    expect(rows[0]).toMatchObject({ is_unique: "false", is_indexed: "true" })
    expect(rows[1]).toMatchObject({ is_unique: "false", is_indexed: "true" })
    expect(rows[2]).toMatchObject({ is_unique: "true", is_indexed: "true" })
  })

  it("입력 배열을 바꾸지 않는다", () => {
    const rows = [f("true")]
    applyKeyFlags(rows)
    expect(rows[0]!.is_unique).toBe("false")
  })
})
