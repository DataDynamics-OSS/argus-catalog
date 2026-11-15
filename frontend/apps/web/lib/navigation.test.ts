import { describe, expect, it } from "vitest"

import {
  afterLoginPath,
  createNavigationTracker,
  isSameOriginReferrer,
  loginPath,
  safeNextPath,
} from "./navigation"

describe("safeNextPath (DEF-063)", () => {
  it("내부 경로는 그대로", () => {
    expect(safeNextPath("/dashboard/datasets/123")).toBe("/dashboard/datasets/123")
    expect(safeNextPath("/dashboard/search?q=a%20b#x")).toBe("/dashboard/search?q=a%20b#x")
  })

  it("외부·프로토콜 상대·이상한 값은 거부", () => {
    expect(safeNextPath("https://evil.example/")).toBeNull()
    expect(safeNextPath("//evil.example/")).toBeNull()
    expect(safeNextPath("/\\evil.example")).toBeNull()
    expect(safeNextPath("javascript:alert(1)")).toBeNull()
    expect(safeNextPath("/dash\nboard")).toBeNull()
    expect(safeNextPath("/a b")).toBeNull()
    expect(safeNextPath("")).toBeNull()
    expect(safeNextPath(null)).toBeNull()
  })

  it("로그인 화면 자신은 거부", () => {
    expect(safeNextPath("/login")).toBeNull()
    expect(safeNextPath("/login?next=/x")).toBeNull()
    expect(safeNextPath("/loginx")).toBe("/loginx")
  })
})

describe("loginPath / afterLoginPath", () => {
  it("현재 경로를 next 로 담고, 로그인 뒤 그대로 꺼낸다", () => {
    const url = loginPath("/dashboard/datasets/1?tab=schema")
    expect(url).toBe("/login?next=%2Fdashboard%2Fdatasets%2F1%3Ftab%3Dschema")
    expect(afterLoginPath(url.slice("/login".length))).toBe("/dashboard/datasets/1?tab=schema")
  })

  it("루트·로그인·안전하지 않은 경로는 next 없이", () => {
    expect(loginPath("/")).toBe("/login")
    expect(loginPath("/login")).toBe("/login")
    expect(loginPath("//evil")).toBe("/login")
  })

  it("next 가 없거나 안전하지 않으면 대시보드", () => {
    expect(afterLoginPath("")).toBe("/dashboard")
    expect(afterLoginPath("?next=")).toBe("/dashboard")
    expect(afterLoginPath("?next=https%3A%2F%2Fevil")).toBe("/dashboard")
    expect(afterLoginPath("?next=%2F%2Fevil")).toBe("/dashboard")
  })
})

describe("앱 내 이동 기록 (DEF-066)", () => {
  it("첫 화면만 기록되면 이동 없음, 다른 경로가 오면 이동 있음", () => {
    const t = createNavigationTracker()
    expect(t.navigated).toBe(false)
    t.record("/dashboard/datasets/1")
    t.record("/dashboard/datasets/1")
    expect(t.navigated).toBe(false)
    t.record("/dashboard/search")
    expect(t.navigated).toBe(true)
  })

  it("referrer 가 같은 출처일 때만 앱 안", () => {
    expect(isSameOriginReferrer("http://a.test/dashboard", "http://a.test")).toBe(true)
    expect(isSameOriginReferrer("http://b.test/", "http://a.test")).toBe(false)
    expect(isSameOriginReferrer("", "http://a.test")).toBe(false)
    expect(isSameOriginReferrer("not a url", "http://a.test")).toBe(false)
  })
})
