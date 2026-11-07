import { describe, expect, it, vi } from "vitest"

vi.mock("@/features/auth/auth-fetch", () => ({ authFetch: vi.fn() }))

import { actionLabel, detailLines } from "./admin-audit-sheet"

describe("admin audit formatting", () => {
  it("작업 이름 → 표시 이름", () => {
    expect(actionLabel("user.role")).toBe("역할 변경")
    expect(actionLabel("settings.llm")).toBe("LLM / AI 설정")
    expect(actionLabel("settings.foo")).toBe("foo 설정")
    expect(actionLabel("unknown.x")).toBe("unknown.x")
  })

  it("changes 는 '항목: 이전 → 이후' 로", () => {
    expect(detailLines({ changes: { llm_model: { old: "a", new: "b" }, apis: { old: "기본", new: ["argus-user"] } } }))
      .toEqual(["llm_model: a → b", "apis: 기본 → argus-user"])
  })

  it("old/new 단건과 일반 객체", () => {
    expect(detailLines({ old: "argus-user", new: "argus-admin" })).toEqual(["argus-user → argus-admin"])
    expect(detailLines({ transfer_to: "heir", owned_assets: [] , x: null })).toEqual(["transfer_to: heir"])
    expect(detailLines(null)).toEqual([])
  })
})
