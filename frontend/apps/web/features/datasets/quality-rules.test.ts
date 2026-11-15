import { describe, expect, it } from "vitest"

import {
  DATE_FORMAT_CHOICES, domainOptions, domainToSave, isBinaryColumnType, listedDateFormat, ruleListMeta,
} from "./quality-rules"

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

describe("규칙의 도메인(품질 지표) 선택지", () => {
  const indicators = [
    { key: "REQUIRED", name: "필수값", check_types: ["NOT_NULL"], domain: null },
    { key: "YN", name: "여부", check_types: ["ACCEPTED_VALUES"], domain: "YN" },
    { key: "CODE", name: "코드", check_types: ["ACCEPTED_VALUES"], domain: "CODE" },
    { key: "AMOUNT", name: "금액", check_types: ["MIN_VALUE", "MAX_VALUE"], domain: "AMOUNT" },
    { key: "RATE", name: "율", check_types: ["MIN_VALUE", "MAX_VALUE"], domain: "RATE" },
    { key: "DATE", name: "날짜", check_types: ["DATE_FORMAT"], domain: "DATE" },
  ]

  it("같은 유형을 나눠 갖는 지표만 선택지가 된다", () => {
    expect(domainOptions("ACCEPTED_VALUES", indicators)).toEqual([
      { value: "YN", label: "여부" }, { value: "CODE", label: "코드" }])
    expect(domainOptions("MAX_VALUE", indicators)).toEqual([
      { value: "AMOUNT", label: "금액" }, { value: "RATE", label: "율" }])
    expect(domainOptions("DATE_FORMAT", indicators)).toEqual([])  // 분류가 하나뿐이면 고를 것이 없다
    expect(domainOptions("NOT_NULL", indicators)).toEqual([])
    expect(domainOptions("ACCEPTED_VALUES", [])).toEqual([])
  })

  it("자동이거나 이 유형의 선택지가 아니면 저장하지 않는다(null)", () => {
    expect(domainToSave("ACCEPTED_VALUES", "YN", indicators)).toBe("YN")
    expect(domainToSave("ACCEPTED_VALUES", "", indicators)).toBeNull()
    expect(domainToSave("MIN_VALUE", "YN", indicators)).toBeNull()  // 유형을 바꾸기 전에 고른 값
    expect(domainToSave("NOT_NULL", "YN", indicators)).toBeNull()
  })
})

describe("날짜 형식 선택지", () => {
  it("목록에 있는 형식은 대소문자·앞뒤 공백을 다듬어 찾고, 없으면 직접 입력(null)", () => {
    expect(listedDateFormat(" yyyy-mm-dd ")).toBe("YYYY-MM-DD")
    expect(listedDateFormat("YYYYMMDD")).toBe("YYYYMMDD")
    expect(listedDateFormat("DD/MM/YYYY")).toBeNull()
    expect(listedDateFormat("")).toBeNull()
  })

  it("형식마다 그 형식의 값 예가 있고 겹치지 않는다", () => {
    expect(new Set(DATE_FORMAT_CHOICES.map((c) => c.value)).size).toBe(DATE_FORMAT_CHOICES.length)
    expect(DATE_FORMAT_CHOICES.every((c) => c.example.length > 0)).toBe(true)
  })
})
