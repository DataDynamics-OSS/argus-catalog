// API 목록 화면 상태 ↔ 주소(URL) 쿼리 변환.
// 새로고침·링크 공유 시 검색 조건·페이지·보기 방식이 유지되도록 주소에 담는다(기본값은 생략해 주소를 짧게).
//   q · status · category · protocol · cert · owner · alert=1 · sort · page · size · view=card  (+ api=<이름>: 상세)

import type { ApiListFilters, ApiSort } from "../api"

export type ApisViewMode = "table" | "card"

export const PAGE_SIZE_OPTIONS = [10, 20, 30, 40, 50]
export const DEFAULT_PAGE_SIZE = 20

export const DEFAULT_FILTERS: ApiListFilters = {
  search: "", status: "", category: "", protocol: "", certification: "", owner: "", alert: false, sort: "updated",
}

const SORTS: ApiSort[] = ["updated", "created", "name"]

export type ApisListState = { filters: ApiListFilters; page: number; pageSize: number; view: ApisViewMode }

export function readListState(sp: URLSearchParams): ApisListState {
  const sort = sp.get("sort") as ApiSort | null
  const page = Number(sp.get("page"))
  const size = Number(sp.get("size"))
  return {
    filters: {
      search: sp.get("q") ?? "",
      status: sp.get("status") ?? "",
      category: sp.get("category") ?? "",
      protocol: sp.get("protocol") ?? "",
      certification: sp.get("cert") ?? "",
      owner: sp.get("owner") === "unowned" ? "unowned" : "",
      alert: sp.get("alert") === "1",
      sort: sort && SORTS.includes(sort) ? sort : "updated",
    },
    page: Number.isInteger(page) && page >= 1 ? page : 1,
    pageSize: PAGE_SIZE_OPTIONS.includes(size) ? size : DEFAULT_PAGE_SIZE,
    view: sp.get("view") === "card" ? "card" : "table",
  }
}

/** 목록 상태를 기존 쿼리(api 등 다른 키 보존)에 덮어쓴 새 쿼리. */
export function writeListState(base: URLSearchParams, st: ApisListState): URLSearchParams {
  const sp = new URLSearchParams(base)
  const put = (key: string, value: string | null) => { if (value) sp.set(key, value); else sp.delete(key) }
  const f = st.filters
  put("q", f.search.trim() || null)
  put("status", f.status || null)
  put("category", f.category || null)
  put("protocol", f.protocol || null)
  put("cert", f.certification || null)
  put("owner", f.owner || null)
  put("alert", f.alert ? "1" : null)
  put("sort", f.sort !== "updated" ? f.sort : null)
  put("page", st.page > 1 ? String(st.page) : null)
  put("size", st.pageSize !== DEFAULT_PAGE_SIZE ? String(st.pageSize) : null)
  put("view", st.view === "card" ? "card" : null)
  return sp
}

/** 정렬을 제외한 검색 조건이 하나라도 있는지. */
export function hasActiveFilters(f: ApiListFilters): boolean {
  return !!(f.search.trim() || f.status || f.category || f.protocol || f.certification || f.owner || f.alert)
}
