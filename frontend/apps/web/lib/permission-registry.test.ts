import { describe, expect, it } from "vitest"

import { FEATURE_REGISTRY, MENU_REGISTRY, urlToMenuKey } from "./permission-registry"

describe("permission-registry", () => {
  it("관리 메뉴 3종과 모델 파일 메뉴 2종은 adminOnly (백엔드 ADMIN_MENUS 와 일치)", () => {
    const admin = MENU_REGISTRY.filter((m) => m.adminOnly).map((m) => m.key).sort()
    expect(admin).toEqual(["mlflow-files", "oci-files", "permissions", "settings", "users"])
  })

  it("기능 key 는 중복 없음", () => {
    const keys = FEATURE_REGISTRY.map((f) => f.key)
    expect(new Set(keys).size).toBe(keys.length)
  })

  it("기능의 menuKey 는 존재하는 메뉴", () => {
    const menus = new Set(MENU_REGISTRY.map((m) => m.key))
    for (const f of FEATURE_REGISTRY) expect(menus.has(f.menuKey)).toBe(true)
  })

  it("url → 메뉴 key 는 가장 긴 prefix", () => {
    expect(urlToMenuKey("/dashboard/apis/favorites")).toBe("apis-favorites")
    expect(urlToMenuKey("/dashboard/apis")).toBe("apis")
    expect(urlToMenuKey("/dashboard/users")).toBe("users")
  })
})
