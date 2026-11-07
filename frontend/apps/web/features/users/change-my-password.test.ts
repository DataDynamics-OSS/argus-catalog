// 계정 설정의 본인 비밀번호 변경 — 서버가 돌려준 새 토큰 쌍을 호출자에게 넘겨야 한다.
// (예전에는 반환값을 버려, 이어지는 프로필 저장이 이전 토큰으로 401 → 강제 로그아웃·프로필 미저장)
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { changeMyPassword } from "./api"

const TOKENS = {
  access_token: "new-access",
  refresh_token: "new-refresh",
  token_type: "bearer",
  expires_in: 28800,
  refresh_expires_in: 604800,
}

describe("changeMyPassword", () => {
  beforeEach(() => {
    const store = new Map<string, string>([
      ["argus_tokens", JSON.stringify({ access_token: "old-access", refresh_token: "old-refresh", expires_at: 0 })],
    ])
    vi.stubGlobal("sessionStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    })
  })
  afterEach(() => vi.unstubAllGlobals())

  it("성공하면 새 토큰 쌍을 반환한다", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(TOKENS), { status: 200 }))
    vi.stubGlobal("fetch", fetchMock)
    await expect(changeMyPassword("itauth5678", "itauth9012")).resolves.toEqual(TOKENS)
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe("/api/v1/auth/change-password")
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer old-access")
  })

  it("현재 비밀번호 오류는 서버 문구로 실패한다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "현재 비밀번호가 올바르지 않습니다." }), { status: 400 })))
    await expect(changeMyPassword("wrong", "itauth9012")).rejects.toThrow("현재 비밀번호가 올바르지 않습니다.")
  })
})
