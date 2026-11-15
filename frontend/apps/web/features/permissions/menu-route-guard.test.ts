import { describe, expect, it, vi } from "vitest"

// 가드 컴포넌트가 끌어오는 클라이언트 모듈은 key 계산 테스트와 무관 — 가볍게 대체
vi.mock("@/components/dashboard-header", () => ({ DashboardHeader: () => null }))
vi.mock("@/features/auth", () => ({ useAuth: () => ({}) }))
vi.mock("@/features/permissions/use-permissions", () => ({ usePermissions: () => ({}) }))

import { alternateMenuKeys, guardedMenuKey } from "./menu-route-guard"

describe("guardedMenuKey", () => {
  it("메뉴 페이지와 그 하위 경로는 메뉴 key", () => {
    expect(guardedMenuKey("/dashboard/datasets")).toBe("datasets")
    expect(guardedMenuKey("/dashboard/datasets/12")).toBe("datasets")
    expect(guardedMenuKey("/dashboard/settings")).toBe("settings")
  })

  it("대시보드 key 는 /dashboard 정확히만", () => {
    expect(guardedMenuKey("/dashboard")).toBe("dashboard")
    expect(guardedMenuKey("/dashboard/profile")).toBeNull()
  })

  it("플러그인 화면은 호스트 라우트가 검사", () => {
    expect(guardedMenuKey("/dashboard/x/acme/report")).toBeNull()
  })
})

describe("alternateMenuKeys", () => {
  it("변경 요청 상세는 내 결재함 메뉴로도 열린다(DEF-064)", () => {
    expect(guardedMenuKey("/dashboard/changes/42")).toBe("changes")
    expect(alternateMenuKeys("/dashboard/changes/42")).toEqual(["my-approvals"])
  })

  it("새 요청·목록은 변경 관리 메뉴만", () => {
    expect(alternateMenuKeys("/dashboard/changes/new")).toEqual([])
    expect(alternateMenuKeys("/dashboard/changes")).toEqual([])
    expect(alternateMenuKeys("/dashboard/datasets/12")).toEqual([])
  })
})
