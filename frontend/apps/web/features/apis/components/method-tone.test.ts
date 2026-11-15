import { describe, expect, it } from "vitest"

import { methodTone } from "./apis-constants"

describe("methodTone", () => {
  it("이벤트 · gRPC · SOAP 유형도 색이 있다(대소문자 무관)", () => {
    expect(methodTone("PUBLISH")).toBe("success")
    expect(methodTone("publish")).toBe("success")
    expect(methodTone("SUBSCRIBE")).toBe("info")
    expect(methodTone("EVENT")).toBe("warning")
    expect(methodTone("SERVER_STREAMING")).toBe("success")
    expect(methodTone("operation")).toBe("info")
    expect(methodTone("subscription")).toBe("success")
  })
  it("REST 메서드는 그대로", () => {
    expect(methodTone("GET")).toBe("info")
    expect(methodTone("delete")).toBe("danger")
    expect(methodTone("HEAD")).toBe("muted")
  })
  it("모르는 유형만 무채색", () => {
    expect(methodTone("custom")).toBe("neutral")
  })
})
