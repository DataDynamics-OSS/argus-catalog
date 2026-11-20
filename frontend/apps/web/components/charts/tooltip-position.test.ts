import { describe, expect, it } from "vitest"

import { tooltipLeft } from "./tooltip-position"

describe("tooltipLeft", () => {
  it("자리가 있으면 포인터 오른쪽", () => {
    expect(tooltipLeft(100, 200, 600)).toBe(110)
  })

  it("오른쪽 끝을 넘으면 포인터 왼쪽으로 뒤집는다", () => {
    expect(tooltipLeft(500, 200, 600)).toBe(290)
  })

  it("양쪽 다 모자라면 상자 오른쪽 끝에 맞춘다 (좁은 카드, DEF-178)", () => {
    // 1440 폭 대시보드의 좁은 카드 — 차트 249px, 툴팁 229px
    const left = tooltipLeft(140, 229, 249)
    expect(left).toBe(20)
    expect(left + 229).toBeLessThanOrEqual(249)
  })

  it("툴팁이 상자보다 넓으면 왼쪽 끝에 붙인다", () => {
    expect(tooltipLeft(50, 256, 240)).toBe(0)
  })
})
