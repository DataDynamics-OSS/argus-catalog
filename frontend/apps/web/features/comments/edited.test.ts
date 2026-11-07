import { describe, expect, it } from "vitest"
import { isCommentEdited } from "./edited"

describe("isCommentEdited", () => {
  it("작성 시각과 같으면(또는 1초 이내) 수정 안 됨", () => {
    expect(isCommentEdited({ created_at: "2026-11-07T10:00:00Z", updated_at: "2026-11-07T10:00:00.400Z" })).toBe(false)
  })
  it("작성 후 수정되면 true", () => {
    expect(isCommentEdited({ created_at: "2026-11-07T10:00:00Z", updated_at: "2026-11-07T10:05:00Z" })).toBe(true)
  })
  it("잘못된 시각은 false", () => {
    expect(isCommentEdited({ created_at: "x", updated_at: "2026-11-07T10:05:00Z" })).toBe(false)
  })
})
