import { describe, expect, it } from "vitest"

import { excludesToText, intervalLabel, isExcluded, scopeSummary, scopeToText, textToExcludes, textToScope } from "@/features/datasources/sync-format"

describe("수집 범위 입력란 변환", () => {
  it("한 줄·쉼표 구분, 공백·중복 제거, 점은 경로 구분", () => {
    expect(textToScope(" sales \nhr, sales\n\nsales.emea")).toEqual({
      include_all: false,
      include: [{ path: ["sales"] }, { path: ["hr"] }, { path: ["sales", "emea"] }],
    })
    expect(textToScope("  \n").include).toEqual([])
  })

  it("저장된 범위를 입력란으로", () => {
    expect(scopeToText({ include_all: false, include: [{ path: ["a"] }, { path: ["b", "c"] }] })).toBe("a\nb.c")
    expect(scopeToText({ include_all: true, include: [] })).toBe("")
    expect(scopeToText(null)).toBe("")
  })
})

describe("3단계 범위 — 일부 테이블·제외 패턴", () => {
  it("scopeSummary 는 일부 테이블 수와 제외 패턴을 보여 준다", () => {
    expect(scopeSummary({ include_all: false, include: [{ path: ["a"] }, { path: ["s", "e"], tables: ["x", "y"] }],
      exclude: [{ table_pattern: "tmp_*" }, { path: ["a"], table_pattern: "*_bak" }] }, "데이터베이스"))
      .toBe("a, s.e (테이블 2) · 제외 tmp_*, a:*_bak")
    expect(scopeSummary({ include_all: true, include: [], exclude: [{ table_pattern: "tmp_*" }] }, "카탈로그"))
      .toBe("전체 카탈로그 · 제외 tmp_*")
    expect(scopeSummary(null, "x")).toBe("범위 없음")
  })

  it("scopeToText 는 컨테이너 전체 항목만", () => {
    expect(scopeToText({ include_all: false, include: [{ path: ["a"] }, { path: ["b"], tables: ["t"] }] })).toBe("a")
  })

  it("제외 패턴 입력란 왕복", () => {
    const ex = textToExcludes(" tmp_* , sales.emea:*_bak\n tmp_*, :x, a: ")
    expect(ex).toEqual([{ table_pattern: "tmp_*" }, { path: ["sales", "emea"], table_pattern: "*_bak" }, { table_pattern: ":x" }])
    expect(excludesToText(ex)).toBe("tmp_*, sales.emea:*_bak, :x")
    expect(excludesToText(undefined)).toBe("")
  })
})

describe("isExcluded", () => {
  const ex = textToExcludes("*_list, sales:tmp_?, Bak*")
  it("테이블 이름·상대 이름과 glob 비교(대소문자 무시)", () => {
    expect(isExcluded(ex, ["sakila"], "customer_list")).toBe(true)
    expect(isExcluded(ex, ["sakila"], "customer")).toBe(false)
    expect(isExcluded(ex, ["hive"], "default.film_list")).toBe(true)
    expect(isExcluded(ex, ["x"], "BAKUP")).toBe(true)
  })
  it("경로를 준 패턴은 그 컨테이너에서만, ?는 한 글자", () => {
    expect(isExcluded(ex, ["sales"], "tmp_1")).toBe(true)
    expect(isExcluded(ex, ["sales"], "tmp_12")).toBe(false)
    expect(isExcluded(ex, ["hr"], "tmp_1")).toBe(false)
  })
  it("정규식 특수문자는 글자 그대로", () => {
    expect(isExcluded(textToExcludes("a.b"), ["x"], "axb")).toBe(false)
    expect(isExcluded(textToExcludes("a.b"), ["x"], "a.b")).toBe(true)
  })
})

describe("intervalLabel", () => {
  it("일·시간·분 단위", () => {
    expect(intervalLabel(15)).toBe("15분")
    expect(intervalLabel(90)).toBe("90분")
    expect(intervalLabel(180)).toBe("3시간")
    expect(intervalLabel(1440)).toBe("1일")
    expect(intervalLabel(10080)).toBe("7일")
    expect(intervalLabel(null)).toBe("-")
  })
})
