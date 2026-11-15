import { describe, expect, it } from "vitest"

import type { QualityIndicator } from "@/features/quality/api"
import {
  copyName,
  criteriaText,
  definableCategories,
  definitionFormError,
  definitionPayload,
  typeForCategory,
  type DefinitionForm,
} from "@/features/quality/definitions"

const indicator = (key: string, check_types: string[], definable = true): QualityIndicator => ({
  key, name: key, area: "VALIDITY", area_name: "유효성", check_types, description: "", definable,
})
const form = (over: Partial<DefinitionForm> = {}): DefinitionForm => ({
  name: "여부 Y/N", category: "YN", checkType: "ACCEPTED_VALUES", expected: "Y,N", threshold: "100",
  severity: "WARNING", description: "", ...over,
})

describe("품질 지표 정의", () => {
  it("정의할 수 있는 분류만 고른다", () => {
    const all = [indicator("YN", ["ACCEPTED_VALUES"]), indicator("LOGICAL", ["LOGICAL"], false)]
    expect(definableCategories(all).map((i) => i.key)).toEqual(["YN"])
  })

  it("분류를 바꾸면 유형을 그 분류에 맞춘다", () => {
    const amount = indicator("AMOUNT", ["MIN_VALUE", "MAX_VALUE"])
    expect(typeForCategory(amount, "MAX_VALUE")).toBe("MAX_VALUE")
    expect(typeForCategory(amount, "REGEX")).toBe("MIN_VALUE")
    expect(typeForCategory(undefined, "REGEX")).toBe("REGEX")
  })

  it("진단 기준을 한 줄로 적는다", () => {
    const of = (check_type: string, expected_value: string | null, threshold = 100) =>
      criteriaText({ check_type, expected_value, threshold })
    expect(of("NOT_NULL", null)).toBe("NULL 없음")
    expect(of("NOT_NULL", null, 95)).toBe("NULL 아닌 값 95% 이상")
    expect(of("UNIQUE", null)).toBe("중복 없음")
    expect(of("DATE_FORMAT", "YYYYMMDD")).toBe("날짜 형식 YYYYMMDD")
    expect(of("ACCEPTED_VALUES", "Y, N,")).toBe("허용 값 Y · N")
    expect(of("MIN_VALUE", "0")).toBe("0 이상")
    expect(of("MAX_VALUE", "100")).toBe("100 이하")
    expect(of("WHITESPACE", null)).toBe("앞뒤 공백 · 제어 문자 없음")
    expect(of("WHITESPACE", "#$")).toBe("앞뒤 공백 · 제어 문자 · #$ 없음")
  })

  it("저장하기 전에 빠진 값을 알린다", () => {
    expect(definitionFormError(form())).toBeNull()
    expect(definitionFormError(form({ name: " " }))).toBe("이름을 적으세요.")
    expect(definitionFormError(form({ category: "" }))).toBe("진단 기준을 고르세요.")
    expect(definitionFormError(form({ expected: " " }))).toContain("허용 값")
    expect(definitionFormError(form({ checkType: "MIN_VALUE", expected: "zero" }))).toBe("기준값은 숫자여야 합니다.")
    expect(definitionFormError(form({ checkType: "NOT_NULL", expected: "", threshold: "101" }))).toContain("임계값")
    expect(definitionFormError(form({ checkType: "WHITESPACE", expected: "" }))).toBeNull()
  })

  it("복사본 이름은 겹치지 않게 짓는다", () => {
    expect(copyName("여부 Y/N", ["여부 Y/N"])).toBe("여부 Y/N 복사")
    expect(copyName("여부 Y/N", ["여부 Y/N", "여부 y/n 복사"])).toBe("여부 Y/N 복사 2")
    expect(copyName("여부 Y/N 복사", ["여부 Y/N", "여부 Y/N 복사"])).toBe("여부 Y/N 복사 2")  // 복사본의 복사
    expect(copyName("여부 Y/N 복사 2", ["여부 Y/N 복사", "여부 Y/N 복사 2"])).toBe("여부 Y/N 복사 3")
  })

  it("AI-Ready 더미 값 · 데이터 연속성 기준", () => {
    const of = (check_type: string, expected_value: string | null, threshold = 100) =>
      criteriaText({ check_type, expected_value, threshold })
    expect(of("DUMMY_VALUE", null)).toBe("흔한 더미 값 없음")
    expect(of("DUMMY_VALUE", "999999, N/A", 99)).toBe("더미 값(999999 · N/A) 아닌 값 99% 이상")
    expect(of("CONTINUITY", "DAY")).toBe("빠진 일 없음")
    expect(of("CONTINUITY", "month YYYYMM", 90)).toBe("있는 월 90% 이상 (YYYYMM)")
    const dummy = form({ category: "DUMMY", checkType: "DUMMY_VALUE", expected: "", threshold: "99" })
    expect(definitionFormError(dummy)).toBeNull()
    expect(definitionPayload(dummy)).toMatchObject({ expected_value: null, threshold: 99 })
    const cont = (expected: string) => definitionFormError(form({ category: "CONTINUITY", checkType: "CONTINUITY", expected }))
    expect(cont("")).toContain("기간 단위")
    expect(cont("WEEK")).toContain("HOUR · DAY · MONTH · YEAR")
    expect(cont("day YYYYMMDD")).toBeNull()
  })

  it("유형이 쓰지 않는 값은 보내지 않는다", () => {
    expect(definitionPayload(form({ name: " 여부 ", threshold: "50", description: " " }))).toEqual({
      name: "여부", category: "YN", check_type: "ACCEPTED_VALUES", expected_value: "Y,N", threshold: 100,
      severity: "WARNING", description: null,
    })
    const required = definitionPayload(form({ checkType: "NOT_NULL", category: "REQUIRED", expected: "x", threshold: "95" }))
    expect([required.expected_value, required.threshold]).toEqual([null, 95])
  })
})
