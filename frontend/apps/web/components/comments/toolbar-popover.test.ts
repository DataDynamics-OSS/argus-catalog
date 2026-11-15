import { describe, expect, it } from "vitest"
import { placePopover } from "./toolbar-popover"

const vp = { width: 1200, height: 800 }

describe("placePopover", () => {
  it("아래 공간이 넉넉하면 버튼 아래", () => {
    expect(placePopover({ top: 100, bottom: 128, left: 50 }, { width: 360, height: 300 }, vp))
      .toEqual({ top: 132, left: 50 })
  })
  it("아래가 모자라고 위가 넉넉하면 버튼 위로", () => {
    // 아래 공간 800-700-4-8=88 < 300, 위 공간 672-4-8=660
    expect(placePopover({ top: 672, bottom: 700, left: 50 }, { width: 360, height: 300 }, vp))
      .toEqual({ top: 672 - 4 - 300, left: 50 })
  })
  it("양쪽 다 모자라면 넓은 쪽에 붙이고 높이를 줄인다", () => {
    const small = { width: 1200, height: 400 }
    // 아래 400-228-12=160, 위 200-12=188 → 위
    expect(placePopover({ top: 200, bottom: 228, left: 50 }, { width: 360, height: 500 }, small))
      .toEqual({ top: 200 - 4 - 188, left: 50, maxHeight: 188 })
    // 아래 400-128-12=260, 위 100-12=88 → 아래
    expect(placePopover({ top: 100, bottom: 128, left: 50 }, { width: 360, height: 500 }, small))
      .toEqual({ top: 132, left: 50, maxHeight: 260 })
  })
  it("가로는 화면 안으로 맞춘다", () => {
    expect(placePopover({ top: 100, bottom: 128, left: 1100 }, { width: 360, height: 100 }, vp).left).toBe(1200 - 360 - 8)
    expect(placePopover({ top: 100, bottom: 128, left: 2 }, { width: 360, height: 100 }, vp).left).toBe(8)
  })
})
