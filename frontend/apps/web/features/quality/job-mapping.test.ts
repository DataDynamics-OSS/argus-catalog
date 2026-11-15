import { describe, expect, it } from "vitest"

import {
  definitionsOf, mappedDefinitionIds, mappingChanges, mappingFromRules, mappingSize, toggleMapping,
} from "./job-mapping"

const rules = [
  { id: 1, column_name: "postal_code", definition_id: 10 },
  { id: 2, column_name: "Phone", definition_id: 11 },
  { id: 3, column_name: "phone", definition_id: 5 },
  { id: 4, column_name: "address", definition_id: null },   // 직접 만든 규칙
  { id: 5, column_name: null, definition_id: 7 },           // 컬럼 없는 규칙
]
const columns = ["address_id", "address", "postal_code", "Phone"]

describe("컬럼 ↔ 품질 지표 매핑", () => {
  it("지표가 할당된 컬럼 규칙만 매핑이 된다 — 컬럼 이름은 대소문자를 가리지 않는다", () => {
    const m = mappingFromRules(rules)
    expect(definitionsOf(m, "postal_code")).toEqual([10])
    expect(definitionsOf(m, "PHONE")).toEqual([5, 11])
    expect(definitionsOf(m, "address")).toEqual([])
    expect(mappedDefinitionIds(m)).toEqual([5, 10, 11])
    expect(mappingSize(m)).toBe(3)
  })

  it("바꾸지 않았으면 할 일이 없다", () => {
    const c = mappingChanges(rules, mappingFromRules(rules), columns)
    expect(c).toEqual({ assign: [], unassign: [], added: 0, removed: 0 })
  })

  it("더한 것은 할당, 뺀 것은 그 규칙을 해제 — 직접 만든 규칙은 건드리지 않는다", () => {
    let m = mappingFromRules(rules)
    m = toggleMapping(m, "address_id", 1, true)
    m = toggleMapping(m, "address", 1, true)
    m = toggleMapping(m, "Phone", 5, false)
    m = toggleMapping(m, "postal_code", 10, false)
    expect(definitionsOf(m, "postal_code")).toEqual([])
    const c = mappingChanges(rules, m, columns)
    expect(c.assign).toEqual([{ definitionId: 1, columns: ["address_id", "address"] }])
    expect(c.unassign).toEqual([{ definitionId: 5, ruleIds: [3] }, { definitionId: 10, ruleIds: [1] }])
    expect([c.added, c.removed]).toEqual([2, 2])
    expect(mappedDefinitionIds(m)).toEqual([1, 11])
  })

  it("toggleMapping 은 원래 매핑을 바꾸지 않는다", () => {
    const m = mappingFromRules(rules)
    toggleMapping(m, "postal_code", 10, false)
    expect(definitionsOf(m, "postal_code")).toEqual([10])
  })
})
