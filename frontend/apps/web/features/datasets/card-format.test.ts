import { describe, expect, it } from "vitest"

import { ageLabel } from "./card-format"

describe("ageLabel", () => {
  const now = new Date("2026-11-15T12:00:00Z").getTime()
  it("하루 안은 오늘, 그 뒤 일 · 개월 · 년", () => {
    expect(ageLabel("2026-11-15T01:00:00Z", now)).toBe("오늘")
    expect(ageLabel(new Date("2026-11-12T12:00:00Z"), now)).toBe("3일 전")
    expect(ageLabel("2026-08-15T12:00:00Z", now)).toBe("3개월 전")
    expect(ageLabel("2024-11-01T12:00:00Z", now)).toBe("2년 전")
  })
  it("값이 없거나 날짜가 아니면 빈 글자", () => {
    expect(ageLabel(null, now)).toBe("")
    expect(ageLabel("not-a-date", now)).toBe("")
  })
})
