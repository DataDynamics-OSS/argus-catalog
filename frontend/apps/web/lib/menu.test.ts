import { describe, expect, it } from "vitest"

import type { MenuGroup } from "@/types/menu"
import { mergeMenus } from "./menu"

const item = (id: string) => ({ id, title: id, url: `/dashboard/${id}`, icon: "Circle" })

const base: MenuGroup[] = [
  { id: "catalog", label: "데이터 카탈로그", items: [item("datasets")] },
  { id: "governance", label: "거버넌스", items: [item("tags")] },
  { id: "admin", label: "관리", items: [item("users")] },
]

const ids = (groups: MenuGroup[]) => groups.map((g) => g.id)

describe("mergeMenus", () => {
  it("플러그인 메뉴가 없으면 베이스를 그대로 반환한다", () => {
    expect(mergeMenus(base, [])).toBe(base)
  })

  it("새 그룹은 관리(admin) 그룹 바로 앞에 삽입해 관리 그룹을 마지막에 유지한다", () => {
    const merged = mergeMenus(base, [
      { id: "acme", label: "ACME", items: [item("acme-billing")] },
      { id: "beta", label: "BETA", items: [item("beta-report")] },
    ])
    expect(ids(merged)).toEqual(["catalog", "governance", "acme", "beta", "admin"])
  })

  it("기존 그룹 id 와 같으면 그 그룹 끝에 아이템을 덧붙인다", () => {
    const merged = mergeMenus(base, [{ id: "governance", label: "거버넌스", items: [item("acme-policy")] }])
    expect(ids(merged)).toEqual(["catalog", "governance", "admin"])
    expect(merged[1]!.items.map((i) => i.id)).toEqual(["tags", "acme-policy"])
  })

  it("여러 플러그인이 같은 새 그룹 id 를 쓰면 한 그룹으로 합친다", () => {
    const merged = mergeMenus(base, [
      { id: "custom", label: "고객 전용", items: [item("a")] },
      { id: "custom", label: "고객 전용", items: [item("b")] },
    ])
    expect(ids(merged)).toEqual(["catalog", "governance", "custom", "admin"])
    expect(merged[2]!.items.map((i) => i.id)).toEqual(["a", "b"])
  })

  it("관리 그룹이 없으면 맨 끝에 추가한다", () => {
    const merged = mergeMenus(base.slice(0, 2), [{ id: "acme", label: "ACME", items: [item("x")] }])
    expect(ids(merged)).toEqual(["catalog", "governance", "acme"])
  })

  it("베이스와 플러그인 입력을 변경하지 않는다", () => {
    const extra: MenuGroup[] = [
      { id: "governance", label: "거버넌스", items: [item("p")] },
      { id: "acme", label: "ACME", items: [item("q")] },
    ]
    const before = JSON.stringify({ base, extra })
    mergeMenus(base, extra)
    expect(JSON.stringify({ base, extra })).toBe(before)
  })
})
