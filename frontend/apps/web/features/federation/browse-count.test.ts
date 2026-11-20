import { describe, expect, it } from "vitest"

import { instanceTreeCount } from "./browse-count"

describe("instanceTreeCount (DEF-177)", () => {
  it("필터가 없으면 미러 데이터셋 수", () => {
    expect(instanceTreeCount(2, "", null)).toBe(2)
    expect(instanceTreeCount(2, "", 2)).toBe(2)
  })
  it("필터가 있으면 필터 결과 수", () => {
    expect(instanceTreeCount(2, "mart", 1)).toBe(1)
    expect(instanceTreeCount(2, "zzz", 0)).toBe(0)
  })
  it("필터 결과가 아직 없으면 필터 전 건수를 보이지 않는다", () => {
    expect(instanceTreeCount(2, "mart", null)).toBeNull()
    expect(instanceTreeCount(2, "mart", undefined)).toBeNull()
  })
})
