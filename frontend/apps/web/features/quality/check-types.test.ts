import { describe, expect, it } from "vitest"

import type { QualityIndicator } from "@/features/quality/api"
import {
  categoryOfRule, CHECK_TYPE_LABEL, checkTypeLabel, domainForCategory, groupByArea, pathSkipReason, ruleCategories,
  skippedOnPath, UNCLASSIFIED,
} from "@/features/quality/check-types"
import { DEFINITION_TYPE_META } from "@/features/quality/definitions"

const ind = (key: string, area: string, check_types: string[], domain: string | null = null): QualityIndicator => ({
  key, name: key, area, area_name: area, check_types, description: "", domain,
})
const indicators = [
  ind("REQUIRED", "COMPLETENESS", ["NOT_NULL"]),
  ind("DATE", "VALIDITY", ["DATE_FORMAT", "REGEX", "FUNCTION"], "DATE"),
  ind("YN", "VALIDITY", ["ACCEPTED_VALUES", "REGEX", "FUNCTION"], "YN"),
  ind("CODE", "VALIDITY", ["ACCEPTED_VALUES", "REGEX", "FUNCTION"], "CODE"),
  ind("NUMBER", "VALIDITY", ["REGEX", "FUNCTION"], "NUMBER"),
  ind("VOLUME", "OTHER", ["ROW_COUNT"]),
]

describe("정밀 진단 경로에서 빠지는 유형", () => {
  const direct = ["NOT_NULL", "REFERENTIAL", "CUSTOM_PYTHON", "FRESHNESS"]
  it("경로가 평가하지 않는 유형만 — 경로를 모르면 아님", () => {
    expect(skippedOnPath("CUSTOM_SQL", direct)).toBe(true)
    expect(skippedOnPath("CUSTOM_PYTHON", direct)).toBe(false)
    expect(skippedOnPath("FRESHNESS", direct)).toBe(false)
    expect(skippedOnPath("FRESHNESS", ["NOT_NULL"])).toBe(true)
    expect(skippedOnPath("CUSTOM_SQL", [])).toBe(false)
    expect(skippedOnPath("CUSTOM_SQL", null)).toBe(false)
  })
  it("이유는 경로에 따라", () => {
    expect(pathSkipReason("DIRECT", "FUNCTION")).toContain("Trino 에서만")
    expect(pathSkipReason("TRINO", "CUSTOM_PYTHON")).toContain("직접 읽을 수 있는 유형")
  })
})

describe("규칙 유형 이름", () => {
  it("품질 지표 대화상자와 규칙 대화상자가 같은 이름을 쓴다", () => {
    for (const [type, meta] of Object.entries(DEFINITION_TYPE_META)) expect(meta.label).toBe(CHECK_TYPE_LABEL[type])
    expect(checkTypeLabel("MIN_VALUE")).toBe("하한 (이상)")
    expect(checkTypeLabel("UNKNOWN_TYPE")).toBe("UNKNOWN_TYPE")
    expect(checkTypeLabel(null)).toBe("-")
  })
})

describe("규칙 대화상자의 분류", () => {
  it("서버 분류 뒤에 분류 없음을 붙이고 영역별로 묶는다", () => {
    const categories = ruleCategories(indicators)
    expect(categories.at(-1)).toBe(UNCLASSIFIED)
    expect(groupByArea(categories).map((a) => a.key)).toEqual(["COMPLETENESS", "VALIDITY", "OTHER", "UNCLASSIFIED"])
  })

  it("규칙의 분류 — 서버가 정한 분류, 없으면 유형으로", () => {
    const categories = ruleCategories(indicators)
    expect(categoryOfRule({ check_type: "ACCEPTED_VALUES", indicator: "YN" }, categories)).toBe("YN")
    expect(categoryOfRule({ check_type: "NOT_NULL", indicator: null }, categories)).toBe("REQUIRED")
    expect(categoryOfRule({ check_type: "CUSTOM_SQL", indicator: null }, categories)).toBe(UNCLASSIFIED.key)
  })

  it("같은 유형을 나눠 갖는 분류만 도메인을 보낸다", () => {
    const find = (k: string) => indicators.find((i) => i.key === k)
    expect(domainForCategory(find("YN"), "ACCEPTED_VALUES", indicators)).toBe("YN")
    expect(domainForCategory(find("DATE"), "REGEX", indicators)).toBe("DATE")
    expect(domainForCategory(find("DATE"), "DATE_FORMAT", indicators)).toBeNull()  // 날짜 형식은 날짜뿐
    expect(domainForCategory(find("REQUIRED"), "NOT_NULL", indicators)).toBeNull()
    expect(domainForCategory(UNCLASSIFIED, "FRESHNESS", indicators)).toBeNull()
  })
})
