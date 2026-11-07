import { describe, expect, it } from "vitest"

import { canManageDataset } from "./permissions"

describe("canManageDataset", () => {
  it("관리자는 남의 데이터셋도 편집 가능", () => {
    expect(canManageDataset({ is_admin: true, username: "admin" }, "mjkim")).toBe(true)
  })

  it("생성자 본인은 편집 가능", () => {
    expect(canManageDataset({ is_admin: false, username: "mjkim" }, "mjkim")).toBe(true)
  })

  it("남의 데이터셋은 편집 불가 — 메타데이터 탭 입력·저장 버튼 비노출 기준 (DS-T05)", () => {
    expect(canManageDataset({ is_admin: false, username: "mjkim" }, "admin")).toBe(false)
  })

  it("비로그인·생성자 정보 없음은 편집 불가", () => {
    expect(canManageDataset(null, "admin")).toBe(false)
    expect(canManageDataset({ is_admin: false, username: "mjkim" }, null)).toBe(false)
    // username 이 비어 있어도 created_by 가 빈 값과 일치했다고 보지 않는다
    expect(canManageDataset({ is_admin: false, username: "" }, "")).toBe(false)
  })
})
