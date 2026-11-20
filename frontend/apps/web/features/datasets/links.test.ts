import { describe, expect, it } from "vitest"
import {
  EMPTY_LIST_STATE,
  datasetHref,
  datasetListHref,
  datasetListQuery,
  hasDatasetListFilter,
  isDatasetTab,
  parseDatasetListParams,
} from "./links"

describe("datasetHref", () => {
  it("탭이 있으면 ?tab=, 개요·없음·모르는 탭은 붙이지 않는다", () => {
    expect(datasetHref(7, "history")).toBe("/dashboard/datasets/7?tab=history")
    expect(datasetHref(7, "quality")).toBe("/dashboard/datasets/7?tab=quality")
    expect(datasetHref(7)).toBe("/dashboard/datasets/7")
    expect(datasetHref(7, "overview")).toBe("/dashboard/datasets/7")
    expect(datasetHref(7, "nope" as never)).toBe("/dashboard/datasets/7")
  })
  it("댓글 · 메타데이터 항목을 가리킬 수 있다(TODO-151)", () => {
    expect(datasetHref(7, "comments", { comment: 42 })).toBe("/dashboard/datasets/7?tab=comments&comment=42")
    expect(datasetHref(7, "metadata", { field: "라이선스" }))
      .toBe(`/dashboard/datasets/7?tab=metadata&field=${encodeURIComponent("라이선스")}`)
    expect(datasetHref(7, "comments", { comment: null })).toBe("/dashboard/datasets/7?tab=comments")
    expect(datasetHref(7, undefined, { comment: 1 })).toBe("/dashboard/datasets/7")
  })
  it("isDatasetTab 은 개요를 탭 값으로 보지 않는다", () => {
    expect(isDatasetTab("lineage")).toBe(true)
    expect(isDatasetTab("overview")).toBe(false)
    expect(isDatasetTab("toString")).toBe(false)
    expect(isDatasetTab(null)).toBe(false)
  })
})

describe("목록 조건 ↔ 주소", () => {
  it("빈 조건은 빈 쿼리, 기본 페이지·크기는 넣지 않는다", () => {
    expect(datasetListQuery(EMPTY_LIST_STATE)).toBe("")
    expect(datasetListHref({})).toBe("/dashboard/datasets")
    expect(datasetListHref({ page: 1, size: 20 })).toBe("/dashboard/datasets")
  })
  it("왕복하면 같은 조건", () => {
    const state = {
      ...EMPTY_LIST_STATE,
      q: "주문",
      datasource: ["pg-1", "my-2"],
      origin: ["PROD"],
      status: ["active", "deprecated"],
      tag: ["3", "5"],
      term: "9",
      category: "12",
      taxonomy: "2",
      org: "4",
      system: "6",
      page: 3,
      size: 50,
    }
    const parsed = parseDatasetListParams(new URLSearchParams(datasetListQuery(state)))
    expect(parsed).toEqual(state)
  })
  it("잘못된 값은 버린다", () => {
    const p = parseDatasetListParams(
      new URLSearchParams("tag=3,x,,5&term=abc&category=1;drop&page=-2&size=33&org=7&q=%20%20"),
    )
    expect(p.tag).toEqual(["3", "5"])
    expect(p.term).toBe("")
    expect(p.category).toBe("")
    expect(p.page).toBe(1)
    expect(p.size).toBe(20)
    expect(p.org).toBe("7")
    expect(p.q).toBe("")
  })
  it("분류 체계 id 는 분류가 있을 때만 남긴다", () => {
    expect(datasetListQuery({ taxonomy: "2" })).toBe("")
    expect(datasetListQuery({ category: "12", taxonomy: "2" })).toBe("category=12&taxonomy=2")
  })
  it("hasDatasetListFilter 는 페이지·크기를 보지 않는다", () => {
    expect(hasDatasetListFilter({ ...EMPTY_LIST_STATE, page: 4, size: 50 })).toBe(false)
    expect(hasDatasetListFilter({ ...EMPTY_LIST_STATE, term: "1" })).toBe(true)
  })
})
