import { describe, expect, it } from "vitest"
import { errorMessage, isNetworkError, NETWORK_ERROR_MESSAGE } from "./notify"

describe("errorMessage — 네트워크 오류 한국어 안내", () => {
  it("브라우저별 fetch 네트워크 오류 원문을 한국어로 바꾼다", () => {
    for (const m of ["Failed to fetch", "NetworkError when attempting to fetch resource.", "Load failed", "The network connection was lost.", "fetch failed"]) {
      expect(isNetworkError(new TypeError(m))).toBe(true)
      expect(errorMessage(new TypeError(m))).toBe(NETWORK_ERROR_MESSAGE)
    }
  })
  it("서버 사유·다른 TypeError 는 그대로 둔다", () => {
    expect(errorMessage(new Error("String should have at most 255 characters"))).toBe("String should have at most 255 characters")
    expect(errorMessage(new TypeError("x is not a function"))).toBe("x is not a function")
    // TypeError 가 아니면(서버가 같은 문구를 보낸 경우 등) 바꾸지 않는다
    expect(isNetworkError(new Error("Failed to fetch"))).toBe(false)
    expect(errorMessage(null)).toBeNull()
  })
})
