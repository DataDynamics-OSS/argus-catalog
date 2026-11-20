import { describe, expect, it } from "vitest"

import { adminMenuOpenTo, FEATURE_REGISTRY, MENU_REGISTRY, SETTINGS_DELEGATE_FEATURES, urlToMenuKey } from "./permission-registry"

describe("permission-registry", () => {
  it("관리 메뉴 4종(메타데이터 수집 포함)과 모델 파일 메뉴 2종은 adminOnly (백엔드 ADMIN_MENUS 와 일치)", () => {
    const admin = MENU_REGISTRY.filter((m) => m.adminOnly).map((m) => m.key).sort()
    expect(admin).toEqual(["audit", "mlflow-files", "oci-files", "permissions", "quality-runs", "settings", "sync-runs", "users"])
  })

  it("기능 key 는 중복 없음", () => {
    const keys = FEATURE_REGISTRY.map((f) => f.key)
    expect(new Set(keys).size).toBe(keys.length)
  })

  it("기능의 menuKey 는 존재하는 메뉴", () => {
    const menus = new Set(MENU_REGISTRY.map((m) => m.key))
    for (const f of FEATURE_REGISTRY) expect(menus.has(f.menuKey)).toBe(true)
  })

  it("메타데이터 수집은 수집 권한(datasources.sync)을 허용받으면 비관리자에게도 열린다 (백엔드 ADMIN_MENU_GRANTS 와 일치)", () => {
    const granted = (keys: string[]) => (f: string) => keys.includes(f)
    expect(MENU_REGISTRY.filter((m) => m.grantedBy).map((m) => [m.key, m.grantedBy])).toEqual([
      ["users", ["users.manage"]],
      ["sync-runs", ["datasources.sync"]],
      ["quality-runs", ["quality.manage"]],
      ["settings", [...SETTINGS_DELEGATE_FEATURES]],
      ["audit", ["audit.view"]],
    ])
    expect(adminMenuOpenTo("sync-runs", granted(["datasources.sync"]))).toBe(true)
    expect(adminMenuOpenTo("sync-runs", granted([]))).toBe(false)
    expect(adminMenuOpenTo("users", granted(["datasources.sync"]))).toBe(false)
    expect(adminMenuOpenTo("datasets", granted([]))).toBe(true)
    // 관리 기능 위임(TODO-123) — 사용자 관리는 users.manage, 설정은 settings.* 중 하나, 권한 관리는 늘 관리자만
    expect(adminMenuOpenTo("users", granted(["users.manage"]))).toBe(true)
    expect(adminMenuOpenTo("settings", granted(["settings.mail"]))).toBe(true)
    expect(adminMenuOpenTo("settings", granted(["users.manage"]))).toBe(false)
    expect(adminMenuOpenTo("permissions", granted(["users.manage", ...SETTINGS_DELEGATE_FEATURES]))).toBe(false)
    // 감사 로그(TODO-152) — audit.view 만 받아도 열린다. 사용자 관리는 열리지 않는다
    expect(adminMenuOpenTo("audit", granted(["audit.view"]))).toBe(true)
    expect(adminMenuOpenTo("audit", granted(["users.manage"]))).toBe(false)
    expect(adminMenuOpenTo("users", granted(["audit.view"]))).toBe(false)
  })

  it("url → 메뉴 key 는 가장 긴 prefix", () => {
    expect(urlToMenuKey("/dashboard/apis/favorites")).toBe("apis-favorites")
    expect(urlToMenuKey("/dashboard/apis")).toBe("apis")
    expect(urlToMenuKey("/dashboard/users")).toBe("users")
  })
})
