import { describe, expect, it, vi } from "vitest"

vi.mock("@/features/auth/auth-fetch", () => ({ authFetch: vi.fn() }))

import { sourceNote, summarize, type SourceInfo } from "./setting-sources"

describe("summarize", () => {
  it("출처별 개수·설정 파일과 다름·환경변수 강제", () => {
    const items: Record<string, SourceInfo> = {
      a: { source: "default", description: "" },
      b: { source: "config", description: "" },
      c: { source: "db", description: "", differs_from_file: true, file_value: "x" },
      d: { source: "db", description: "", override: "강제" },
    }
    expect(summarize(items)).toEqual({ counts: { default: 1, config: 1, db: 2 }, differs: 1, overrides: 1 })
  })
})

describe("sourceNote", () => {
  it("비밀값은 설정 파일 값을 보여 주지 않음", () => {
    expect(sourceNote({ source: "db", description: "", differs_from_file: true, file_value: "http://a" }))
      .toBe('설정 파일 값 "http://a" 과 다름 — 이 화면 값이 쓰임')
    expect(sourceNote({ source: "db", description: "", secret: true, differs_from_file: true }))
      .toBe("설정 파일 값과 다름 — 이 화면 값이 쓰임")
    expect(sourceNote({ source: "db", description: "", differs_from_file: true, file_value: "" }))
      .toContain("(빈 값)")
    expect(sourceNote({ source: "default", description: "" })).toBe("")
    expect(sourceNote({ source: "config", description: "", override: "환경변수" })).toBe("환경변수")
  })
})
