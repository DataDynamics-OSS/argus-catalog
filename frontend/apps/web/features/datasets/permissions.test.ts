import { describe, expect, it } from "vitest"

import { canManageDataset, datasetOwnerLabels, isDatasetOwner, ownerUsername } from "./permissions"

describe("canManageDataset — 삭제 · 소유자 추가 · 삭제 (TODO-154)", () => {
  it("관리자는 남의 데이터셋도 편집 가능", () => {
    expect(canManageDataset({ is_admin: true, username: "admin" }, "mjkim")).toBe(true)
  })

  it("생성자 본인은 편집 가능", () => {
    expect(canManageDataset({ is_admin: false, username: "mjkim" }, "mjkim")).toBe(true)
  })

  it("남의 데이터셋은 삭제 · 소유자 관리 불가", () => {
    expect(canManageDataset({ is_admin: false, username: "mjkim" }, "admin")).toBe(false)
  })

  it("비로그인·생성자 정보 없음은 편집 불가", () => {
    expect(canManageDataset(null, "admin")).toBe(false)
    expect(canManageDataset({ is_admin: false, username: "mjkim" }, null)).toBe(false)
    // username 이 비어 있어도 created_by 가 빈 값과 일치했다고 보지 않는다
    expect(canManageDataset({ is_admin: false, username: "" }, "")).toBe(false)
  })
})

describe("isDatasetOwner — 일반 편집 (DEF-138, TODO-154)", () => {
  const ds = { created_by: "alice", owners: [{ owner_name: "홍길동 (bob)" }, { owner_name: "carol" }] }

  it("생성자 · 소유자 목록(두 저장 형식) · 관리자는 소유자", () => {
    expect(isDatasetOwner({ username: "alice" }, ds)).toBe(true)
    expect(isDatasetOwner({ username: "bob" }, ds)).toBe(true)
    expect(isDatasetOwner({ username: "carol" }, ds)).toBe(true)
    expect(isDatasetOwner({ is_admin: true, username: "root" }, ds)).toBe(true)
  })

  it("소유자 목록 사용자는 편집하지만 삭제 · 소유자 관리는 못 한다", () => {
    expect(isDatasetOwner({ username: "bob" }, ds)).toBe(true)
    expect(canManageDataset({ username: "bob" }, ds.created_by)).toBe(false)
    expect(canManageDataset({ username: "alice" }, ds.created_by)).toBe(true)
  })

  it("그 밖의 사용자 · 비로그인은 아님 — 메타데이터 탭 입력·저장 버튼 비노출 기준 (DS-T05)", () => {
    expect(isDatasetOwner({ username: "mallory" }, ds)).toBe(false)
    expect(isDatasetOwner({ username: "" }, { created_by: "", owners: [{ owner_name: "" }] })).toBe(false)
    expect(isDatasetOwner(null, ds)).toBe(false)
  })

  it("표시용 이름은 목록에 없는 생성자를 덧붙인다", () => {
    expect(ownerUsername("홍길동 (bob)")).toBe("bob")
    expect(datasetOwnerLabels(ds)).toEqual(["홍길동 (bob)", "carol", "alice (생성자)"])
    expect(datasetOwnerLabels({ created_by: "bob", owners: ds.owners })).toEqual(["홍길동 (bob)", "carol"])
    expect(datasetOwnerLabels({ created_by: null, owners: [] })).toEqual([])
  })
})
