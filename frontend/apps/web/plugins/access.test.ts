import { describe, expect, it, vi } from "vitest"

import type { MenuGroup } from "@/types/menu"

// 테스트용 플러그인 메뉴 — 권한 레지스트리(permission-registry)가 이 값으로 plugin.<id> 행을 만든다.
const menus: MenuGroup[] = [
  {
    id: "acme",
    label: "ACME",
    items: [
      { id: "acme-billing", title: "정산", url: "/dashboard/x/acme/billing", icon: "Receipt" },
      { id: "acme-audit", title: "감사", url: "/dashboard/x/acme/audit", icon: "Shield", adminOnly: true },
    ],
  },
]

vi.mock("@/plugins/registry", () => ({ pluginMenus: menus, pluginScreens: {}, pluginIcons: {} }))

const { MENU_GROUP_ORDER, MENU_REGISTRY, urlToMenuKey } = await import("@/lib/permission-registry")
const { canAccessPluginScreen, pluginScreenUrl } = await import("./access")

const allowAll = () => true
const deny = (...keys: string[]) => (key: string | null) => !key || !keys.includes(key)

describe("권한 레지스트리 — 플러그인 메뉴", () => {
  it("플러그인 메뉴 아이템마다 plugin.<id> 행을 추가한다", () => {
    expect(MENU_REGISTRY.filter((m) => m.key.startsWith("plugin."))).toEqual([
      { key: "plugin.acme-billing", label: "정산", group: "ACME", url: "/dashboard/x/acme/billing" },
      { key: "plugin.acme-audit", label: "감사", group: "ACME", url: "/dashboard/x/acme/audit" },
    ])
  })

  it("플러그인 URL 을 'dashboard' 가 아니라 자기 키로 매핑한다", () => {
    expect(urlToMenuKey("/dashboard/x/acme/billing")).toBe("plugin.acme-billing")
    expect(urlToMenuKey("/dashboard/datasets")).toBe("datasets")
  })

  it("메뉴에 없는 플러그인 URL 은 기존처럼 가장 긴 prefix(dashboard)로 매핑한다", () => {
    expect(urlToMenuKey("/dashboard/x/acme/hidden")).toBe("dashboard")
  })

  it("권한 화면 그룹 순서에서 플러그인 그룹을 '관리' 앞에 둔다", () => {
    expect(MENU_GROUP_ORDER.slice(-2)).toEqual(["ACME", "관리"])
  })
})

describe("canAccessPluginScreen", () => {
  const billing = pluginScreenUrl("acme/billing")
  const audit = pluginScreenUrl("acme/audit")

  it("slug 경로를 /dashboard/x/<slug> 로 만든다", () => {
    expect(billing).toBe("/dashboard/x/acme/billing")
  })

  it("제한이 없으면 일반 사용자도 접근할 수 있다", () => {
    expect(canAccessPluginScreen(billing, menus, false, allowAll)).toBe(true)
  })

  it("권한 매트릭스에서 내 역할이 막힌 메뉴는 차단한다", () => {
    expect(canAccessPluginScreen(billing, menus, false, deny("plugin.acme-billing"))).toBe(false)
  })

  it("다른 메뉴가 막혀도 영향을 받지 않는다", () => {
    expect(canAccessPluginScreen(billing, menus, false, deny("plugin.acme-audit"))).toBe(true)
  })

  it("adminOnly 메뉴는 관리자만 접근할 수 있다", () => {
    expect(canAccessPluginScreen(audit, menus, false, allowAll)).toBe(false)
    expect(canAccessPluginScreen(audit, menus, true, allowAll)).toBe(true)
  })

  it("메뉴 아이템이 없는 화면은 dashboard 메뉴 권한을 따른다", () => {
    const hidden = pluginScreenUrl("acme/hidden")
    expect(canAccessPluginScreen(hidden, menus, false, allowAll)).toBe(true)
    expect(canAccessPluginScreen(hidden, menus, false, deny("dashboard"))).toBe(false)
  })
})
