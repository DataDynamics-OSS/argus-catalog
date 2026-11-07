import { describe, expect, it } from "vitest"

import { remoteItemVisible } from "./detail-visibility"

describe("remoteItemVisible", () => {
  it("정보가 없으면(하위 호환) 보인다", () => {
    expect(remoteItemVisible("sample", null, null)).toBe(true)
    expect(remoteItemVisible("lineage", undefined, undefined)).toBe(true)
  })
  it("peer 가 노출하지 않으면 숨긴다", () => {
    expect(remoteItemVisible("sample", null, ["description", "schema", "lineage"])).toBe(false)
    expect(remoteItemVisible("lineage", null, ["description", "sample"])).toBe(false)
    expect(remoteItemVisible("sample", null, ["sample"])).toBe(true)
  })
  it("소비자 표시 선택에 없으면 숨긴다", () => {
    expect(remoteItemVisible("sample", ["description"], null)).toBe(false)
    expect(remoteItemVisible("sample", ["sample"], ["sample", "lineage"])).toBe(true)
  })
  it("두 조건의 교집합", () => {
    expect(remoteItemVisible("lineage", ["lineage"], ["sample"])).toBe(false)
    expect(remoteItemVisible("lineage", [], ["lineage"])).toBe(false)
  })
})
