import { describe, expect, it, vi } from "vitest"

vi.mock("@/features/auth/auth-fetch", () => ({ authFetch: vi.fn(), throwOnError: vi.fn() }))

import { cleanQuery, LIST_QUERY_KEYS } from "./api"

describe("cleanQuery — 목록 질의 정리(저장된 보기·요청 공통)", () => {
  it("빈 값·false 를 빼고 문자열 앞뒤 공백을 지운다", () => {
    expect(
      cleanQuery({ search: "  환불 ", status: "", department: "고객지원팀", incomplete: false, tag: undefined }),
    ).toEqual({ search: "환불", department: "고객지원팀" })
  })

  it("true 와 정렬 값은 남긴다", () => {
    expect(cleanQuery({ incomplete: true, sort: "-completeness", owner: "me" })).toEqual({
      incomplete: true, sort: "-completeness", owner: "me",
    })
  })

  it("알려진 질의 키만 남긴다", () => {
    const q = cleanQuery({ risk: "high", colour: "red" } as never)
    expect(q).toEqual({ risk: "high" })
    expect(LIST_QUERY_KEYS).toContain("owner_state")
  })
})
