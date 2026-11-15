import { describe, expect, it } from "vitest"

import { MANAGED_ROLES, type FeatureEntry, type MenuEntry } from "@/lib/permission-registry"
import { countChanges, draftToPayload, initDraft, previewRole, roleState, setRoleForKeys, type Matrix } from "./matrix"

const SU = "argus-superuser"
const U = "argus-user"
const m = (o: Record<string, string[]>): Matrix => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, new Set(v)]))
const NONE: ReadonlySet<string> = new Set()

describe("initDraft", () => {
  it("서버에 없는 key 는 전 역할 허용, 명시 허용형은 미허용, __none__ 은 빈 집합", () => {
    const d = initDraft(["datasets", "datasources.manage", "apis"], { apis: ["__none__"] })
    expect([...d.datasets!]).toEqual([...MANAGED_ROLES])
    expect(d["datasources.manage"]!.size).toBe(0)
    expect(d.apis!.size).toBe(0)
  })

  it("사용자 정의 역할 열을 받고, 보기 전용 표는 미설정이면 빈 집합", () => {
    const roles = [SU, U, "steward"]
    const d = initDraft(["datasets", "apis"], { apis: [U, "steward", "gone"] }, roles)
    expect([...d.datasets!]).toEqual(roles)
    expect([...d.apis!]).toEqual([U, "steward"])
    const v = initDraft(["datasets", "apis"], { apis: ["steward"] }, roles, true)
    expect(v.datasets!.size).toBe(0)
    expect([...v.apis!]).toEqual(["steward"])
    expect(countChanges(m({ a: [] }), m({ a: ["steward"] }), NONE, roles)).toBe(1)
    expect(countChanges(m({ a: [] }), m({ a: ["steward"] }), NONE)).toBe(0)
  })
})

describe("countChanges / draftToPayload", () => {
  it("칸 단위로 다른 수를 세고, 고정 행은 제외", () => {
    const saved = m({ a: [SU, U], b: [], fixed: [] })
    const draft = m({ a: [SU], b: [SU, U], fixed: [SU] })
    expect(countChanges(saved, draft, new Set(["fixed"]))).toBe(3)
    expect(countChanges(saved, saved, NONE)).toBe(0)
    expect(countChanges(null, draft)).toBe(0)
    expect(draftToPayload(draft, new Set(["fixed"]))).toEqual({ a: [SU], b: [SU, U] })
  })
})

describe("roleState / setRoleForKeys", () => {
  it("전부·일부·없음과 일괄 설정 (고정 행 불변)", () => {
    const d = m({ a: [SU], b: [], fixed: [] })
    const fixed = new Set(["fixed"])
    expect(roleState(d, ["a", "b", "fixed"], SU, fixed)).toBe("some")
    const all = setRoleForKeys(d, ["a", "b", "fixed"], SU, true, fixed)
    expect(roleState(all, ["a", "b", "fixed"], SU, fixed)).toBe("all")
    expect(all.fixed!.size).toBe(0)
    expect(d.b!.size).toBe(0) // 원본 불변
    const none = setRoleForKeys(all, ["a", "b"], SU, false, fixed)
    expect(roleState(none, ["a", "b"], SU, fixed)).toBe("none")
    expect(roleState(none, ["fixed"], SU, fixed)).toBe("none")
  })
})

describe("previewRole", () => {
  const menus: MenuEntry[] = [
    { key: "dashboard", label: "대시보드", group: "g", url: "/dashboard" },
    { key: "datasets", label: "데이터셋", group: "g", url: "/dashboard/datasets" },
    { key: "users", label: "사용자", group: "관리", url: "/dashboard/users", adminOnly: true },
  ]
  const features: FeatureEntry[] = [
    { key: "datasets.sample-view", label: "샘플", menuKey: "datasets", description: "" },
    { key: "datasets.ddl-view", label: "DDL", menuKey: "datasets", description: "" },
    { key: "ai.assistant", label: "AI", menuKey: "dashboard", description: "" },
  ]
  it("메뉴 차단·관리 메뉴·기능 차단·메뉴 막혀 못 쓰는 기능을 구분", () => {
    const menuDraft = m({ dashboard: [], datasets: [SU], users: [SU] })
    const featDraft = m({ "datasets.sample-view": [U], "datasets.ddl-view": [SU, U], "ai.assistant": [U] })
    const su = previewRole(SU, menuDraft, featDraft, menus, features)
    expect(su.visibleMenus.map((x) => x.key)).toEqual(["datasets"])
    expect(su.blockedMenus.map((x) => x.key)).toEqual(["dashboard", "users"])
    expect(su.allowedFeatures.map((x) => x.key)).toEqual(["datasets.ddl-view"])
    expect(su.blockedFeatures.map((x) => x.key)).toEqual(["datasets.sample-view", "ai.assistant"])
    const u = previewRole(U, menuDraft, featDraft, menus, features)
    expect(u.unreachableFeatures.map((x) => x.key)).toEqual(["datasets.sample-view", "datasets.ddl-view"])
    // AI 어시스턴트는 대시보드 메뉴가 막혀도 쓸 수 있다
    expect(u.allowedFeatures.map((x) => x.key)).toEqual(["ai.assistant"])
  })

  it("기능 권한으로 여는 관리 메뉴(메타데이터 수집)는 그 기능을 허용받은 역할에게만 보인다", () => {
    const withSync: MenuEntry[] = [
      ...menus,
      { key: "sync-runs", label: "메타데이터 수집", group: "관리", url: "/dashboard/sync-runs", adminOnly: true, grantedBy: ["datasources.sync"] },
    ]
    const featDraft = m({ "datasources.sync": [U] })
    expect(previewRole(U, m({}), featDraft, withSync, []).visibleMenus.map((x) => x.key)).toEqual(["sync-runs"])
    expect(previewRole(SU, m({}), featDraft, withSync, []).visibleMenus.map((x) => x.key)).toEqual([])
  })
  it("보기 전용 메뉴의 편집 기능은 못 쓰고, 조회 기능(view)과 위임 키는 그대로", () => {
    const vMenus: MenuEntry[] = [
      { key: "datasets", label: "데이터셋", group: "g", url: "/dashboard/datasets" },
      { key: "quality-runs", label: "데이터 품질", group: "관리", url: "/dashboard/quality-runs", adminOnly: true, grantedBy: ["quality.manage"] },
    ]
    const vFeatures: FeatureEntry[] = [
      { key: "datasets.sample-view", label: "샘플", menuKey: "datasets", description: "", view: true },
      { key: "datasets.edit", label: "수정", menuKey: "datasets", description: "" },
      { key: "quality.manage", label: "품질 관리", menuKey: "datasets", description: "", defaultDeny: true },
    ]
    const feat = m({ "datasets.sample-view": [U], "datasets.edit": [U], "quality.manage": [U] })
    const p = previewRole(U, m({ datasets: [U] }), feat, vMenus, vFeatures, m({ datasets: [U] }))
    expect(p.readonlyMenus.map((x) => x.key)).toEqual(["datasets"])
    expect(p.viewBlockedFeatures.map((x) => x.key)).toEqual(["datasets.edit"])
    expect(p.allowedFeatures.map((x) => x.key)).toEqual(["datasets.sample-view", "quality.manage"])
    // 메뉴가 막혀도 위임 키는 관리 메뉴를 열기 때문에 '메뉴 막힘'으로 보지 않는다
    const blocked = previewRole(U, m({ datasets: [] }), feat, vMenus, vFeatures)
    expect(blocked.unreachableFeatures.map((x) => x.key)).toEqual(["datasets.sample-view", "datasets.edit"])
    expect(blocked.visibleMenus.map((x) => x.key)).toEqual(["quality-runs"])
  })
})
