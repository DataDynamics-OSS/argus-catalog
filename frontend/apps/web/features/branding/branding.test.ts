import { describe, expect, it } from "vitest"

import { brandLink, DEFAULT_BRAND_HREF } from "./branding"

describe("brandLink", () => {
  it("비면 데이터셋 카드 보기", () => {
    expect(brandLink("")).toEqual({ href: DEFAULT_BRAND_HREF, external: false })
    expect(brandLink("  ")).toEqual({ href: DEFAULT_BRAND_HREF, external: false })
    expect(brandLink(undefined)).toEqual({ href: DEFAULT_BRAND_HREF, external: false })
  })
  it("웹 주소는 새 탭, 화면 경로는 안에서", () => {
    expect(brandLink(" https://www.data-dynamics.io ")).toEqual({ href: "https://www.data-dynamics.io", external: true })
    expect(brandLink("/dashboard/apis")).toEqual({ href: "/dashboard/apis", external: false })
  })
  it("안전하지 않은 값은 기본 주소", () => {
    expect(brandLink("javascript:alert(1)").href).toBe(DEFAULT_BRAND_HREF)
    expect(brandLink("//evil.example").href).toBe(DEFAULT_BRAND_HREF)
  })
})
