// 데이터셋 화면으로 가는 링크와 목록 조건 ↔ 주소(쿼리스트링) 변환.
//
// - 상세: `/dashboard/datasets/{id}?tab=<탭>` — 알림·리니지·품질 경고가 알맞은 탭으로 연다(TODO-072).
// - 목록: 검색어·필터·페이지를 주소에 남겨, 링크로 걸러 들어오거나 상세에서 돌아와도 같은 목록이 보인다(TODO-073).
//   태그·용어·분류 링크(TODO-075·076)도 이 형식으로 목록을 연다.

import { DATASET_TAB_TOPICS } from "@/features/help/topics"

export const DATASET_LIST_PATH = "/dashboard/datasets"

/** 데이터셋 상세 탭 이름 — 도움말 주제 표와 같은 목록을 쓴다. */
export type DatasetTab = keyof typeof DATASET_TAB_TOPICS

export function isDatasetTab(value: string | null | undefined): value is DatasetTab {
  return !!value && value !== "overview" && Object.prototype.hasOwnProperty.call(DATASET_TAB_TOPICS, value)
}

/** 데이터셋 상세 주소. 탭을 주지 않거나 개요면 탭 없이. */
export function datasetHref(id: number, tab?: DatasetTab | null): string {
  return isDatasetTab(tab) ? `${DATASET_LIST_PATH}/${id}?tab=${tab}` : `${DATASET_LIST_PATH}/${id}`
}

/** 목록 조회 조건. 다중 선택 값은 배열, 나머지는 빈 문자열이면 조건 없음. */
export type DatasetListState = {
  q: string
  datasource: string[]
  origin: string[]
  status: string[]
  /** 태그 id */
  tag: string[]
  /** 용어집 용어 id */
  term: string
  /** 분류 id(하위 분류 포함) */
  category: string
  /** 분류가 속한 분류 체계 id — 분류 이름 표시용 */
  taxonomy: string
  org: string
  system: string
  page: number
  size: number
}

export const DEFAULT_PAGE_SIZE = 20
const PAGE_SIZES = [10, 20, 30, 40, 50]

export const EMPTY_LIST_STATE: DatasetListState = {
  q: "",
  datasource: [],
  origin: [],
  status: [],
  tag: [],
  term: "",
  category: "",
  taxonomy: "",
  org: "",
  system: "",
  page: 1,
  size: DEFAULT_PAGE_SIZE,
}

const csv = (v: string | null) => (v ? v.split(",").map((s) => s.trim()).filter(Boolean) : [])
const id = (v: string | null) => (v && /^\d+$/.test(v.trim()) ? v.trim() : "")
const ids = (v: string | null) => csv(v).filter((s) => /^\d+$/.test(s))

/** 주소의 쿼리스트링 → 목록 조건. 알 수 없거나 잘못된 값은 버린다. */
export function parseDatasetListParams(params: URLSearchParams | { get(name: string): string | null }): DatasetListState {
  const page = Number(params.get("page"))
  const size = Number(params.get("size"))
  return {
    q: params.get("q")?.trim() ?? "",
    datasource: csv(params.get("datasource")),
    origin: csv(params.get("origin")),
    status: csv(params.get("status")),
    tag: ids(params.get("tag")),
    term: id(params.get("term")),
    category: id(params.get("category")),
    taxonomy: id(params.get("taxonomy")),
    org: id(params.get("org")),
    system: id(params.get("system")),
    page: Number.isInteger(page) && page > 1 ? page : 1,
    size: PAGE_SIZES.includes(size) ? size : DEFAULT_PAGE_SIZE,
  }
}

/** 목록 조건 → 쿼리스트링(앞의 `?` 없이). 기본값은 넣지 않아 주소를 짧게 유지한다. */
export function datasetListQuery(state: Partial<DatasetListState>): string {
  const s = { ...EMPTY_LIST_STATE, ...state }
  const q = new URLSearchParams()
  if (s.q.trim()) q.set("q", s.q.trim())
  if (s.datasource.length) q.set("datasource", s.datasource.join(","))
  if (s.origin.length) q.set("origin", s.origin.join(","))
  if (s.status.length) q.set("status", s.status.join(","))
  if (s.tag.length) q.set("tag", s.tag.join(","))
  if (s.term) q.set("term", s.term)
  if (s.category) q.set("category", s.category)
  if (s.category && s.taxonomy) q.set("taxonomy", s.taxonomy)
  if (s.org) q.set("org", s.org)
  if (s.system) q.set("system", s.system)
  if (s.page > 1) q.set("page", String(s.page))
  if (s.size !== DEFAULT_PAGE_SIZE) q.set("size", String(s.size))
  return q.toString()
}

/** 조건을 건 데이터셋 목록 주소. 예: `datasetListHref({ tag: ["3"] })`. */
export function datasetListHref(state: Partial<DatasetListState>): string {
  const q = datasetListQuery(state)
  return q ? `${DATASET_LIST_PATH}?${q}` : DATASET_LIST_PATH
}

/** 필터(검색어 포함)가 하나라도 걸려 있는지 — 페이지·크기는 보지 않는다. */
export function hasDatasetListFilter(state: DatasetListState): boolean {
  return !!(
    state.q || state.datasource.length || state.origin.length || state.status.length || state.tag.length ||
    state.term || state.category || state.org || state.system
  )
}
