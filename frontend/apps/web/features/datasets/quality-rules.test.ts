import { describe, expect, it } from "vitest"

import { isBinaryColumnType, ruleListMeta } from "./quality-rules"

describe("ruleListMeta", () => {
  it("is_active 가 \"true\" 가 아니면 비활성 — 검사·점수에서 빠지는 규칙을 목록에서 구분 (DQ-T17)", () => {
    expect(ruleListMeta({ check_type: "MAX_VALUE", threshold: 100, is_active: "false" }).inactive).toBe(true)
    expect(ruleListMeta({ check_type: "MAX_VALUE", threshold: 100, is_active: "no" }).inactive).toBe(true)
    expect(ruleListMeta({ check_type: "MAX_VALUE", threshold: 100, is_active: null }).inactive).toBe(true)
    expect(ruleListMeta({ check_type: "MAX_VALUE", threshold: 100, is_active: "true" }).inactive).toBe(false)
  })

  it("임계값은 NOT_NULL·UNIQUE 에서만 표시", () => {
    expect(ruleListMeta({ check_type: "NOT_NULL", threshold: 98.5, is_active: "true" }).threshold).toBe(98.5)
    expect(ruleListMeta({ check_type: "UNIQUE", threshold: 0, is_active: "true" }).threshold).toBe(0)
    expect(ruleListMeta({ check_type: "ROW_COUNT", threshold: 100, is_active: "true" }).threshold).toBeNull()
    expect(ruleListMeta({ check_type: "CUSTOM_SQL", threshold: 100, is_active: "true" }).threshold).toBeNull()
  })
})

describe("isBinaryColumnType", () => {
  it("바이너리 타입은 통계 생략 대상", () => {
    for (const t of ["BINARY", "BLOB", "longblob", "VARBINARY(16)", "BYTEA", "IMAGE"]) {
      expect(isBinaryColumnType(t)).toBe(true)
    }
  })

  it("pandas 2 문자열·전부 NULL 컬럼(OBJECT)은 바이너리가 아님 — 최소/최대·최빈값이 가려지던 문제 (QB-T06)", () => {
    for (const t of ["OBJECT", "STR", "STRING", "INT64", "DATETIME64[US]", "NUMBER", null, ""]) {
      expect(isBinaryColumnType(t)).toBe(false)
    }
  })
})
