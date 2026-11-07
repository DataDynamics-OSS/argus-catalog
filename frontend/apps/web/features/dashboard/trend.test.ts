import { describe, expect, it } from "vitest"

import { fillDaily, fillHourly, localDateKey } from "./trend"

describe("fillHourly", () => {
  const now = new Date("2026-11-07T07:35:00Z")

  it("현재 시 포함 정확히 24개 버킷을 시간순으로 0 채움", () => {
    const out = fillHourly([
      { date: "2026-11-06T11:00:00+00:00", count: 16 },
      { date: "2026-11-07T07:00:00+00:00", count: 5 },
    ], now)
    expect(out).toHaveLength(24)
    expect(new Date(out[0]!.date).toISOString()).toBe("2026-11-06T08:00:00.000Z")
    expect(new Date(out[23]!.date).toISOString()).toBe("2026-11-07T07:00:00.000Z")
    expect(out[23]!.count).toBe(5)
    expect(out.find((p) => p.date === "2026-11-06T11:00:00.000Z")?.count).toBe(16)
    expect(out.reduce((s, p) => s + p.count, 0)).toBe(21)
    expect(out.filter((p) => p.count === 0)).toHaveLength(22)
  })

  it("창 밖 버킷은 버린다", () => {
    const out = fillHourly([{ date: "2026-11-06T07:00:00+00:00", count: 3 }], now)
    expect(out.reduce((s, p) => s + p.count, 0)).toBe(0)
  })
})

describe("fillDaily", () => {
  it("오늘 포함 N일, 빈 날은 0", () => {
    const now = new Date(2026, 10, 7, 16, 0)
    const out = fillDaily([
      { date: "2026-11-06", count: 16 },
      { date: "2026-11-07", count: 5 },
    ], 7, now)
    expect(out.map((p) => p.date)).toEqual([
      "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07",
    ])
    expect(out.map((p) => p.count)).toEqual([0, 0, 0, 0, 0, 16, 5])
  })

  it("30일 창은 월 경계를 넘는다", () => {
    const out = fillDaily([{ date: "2026-10-09", count: 2 }], 30, new Date(2026, 10, 7))
    expect(out).toHaveLength(30)
    expect(out[0]).toEqual({ date: "2026-10-09", count: 2 })
    expect(out[29]!.date).toBe(localDateKey(new Date(2026, 10, 7)))
  })
})
