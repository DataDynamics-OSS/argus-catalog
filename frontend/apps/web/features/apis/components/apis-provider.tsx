"use client"

import React, { useCallback, useEffect, useRef, useState } from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"

import { type ApiListFilters, type ApiStats, type ApiSummary, fetchApiStats, fetchApis } from "../api"
import {
  type ApisListState, type ApisViewMode, DEFAULT_FILTERS, readListState, writeListState,
} from "./apis-url-state"
import { errorMessage } from "@/lib/notify"

type DialogType = "add" | null

type ApisContextType = {
  apis: ApiSummary[]
  total: number
  page: number
  pageSize: number
  /** 조회 진행 중(최초·재조회 모두) */
  isLoading: boolean
  /** 한 번이라도 목록을 받았는지 — 최초 로딩(스켈레톤)과 재조회(기존 목록 유지)를 구분 */
  hasLoaded: boolean
  /** 마지막 조회 오류 — 성공하면 null */
  error: string | null
  retry: () => void
  setPage: (p: number) => void
  setPageSize: (s: number) => void
  /** 검색 조건 일부 변경(나머지는 유지) — 1페이지부터 다시 조회 */
  filters: ApiListFilters
  setFilters: (patch: Partial<ApiListFilters>) => void
  /** 정렬을 제외한 검색 조건 초기화 */
  resetFilters: () => void
  refreshApis: () => Promise<void>
  /** 상단 지표(전체 기준) — 필터 옵션(카테고리·프로토콜)으로도 사용 */
  stats: ApiStats | null
  open: DialogType
  setOpen: (d: DialogType) => void
  /** 상세로 열린 API — 주소의 ?api= 값(뒤로 가기·링크 공유 지원) */
  selectedApiName: string | null
  setSelectedApiName: (name: string | null) => void
  viewMode: ApisViewMode
  setViewMode: (v: ApisViewMode) => void
  listScrollRef: React.MutableRefObject<number>
}

const ApisContext = React.createContext<ApisContextType | null>(null)

export function ApisProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()

  // 최초 상태는 주소에서 — 이후 변경은 각 핸들러가 주소에 반영(replace: 기록을 쌓지 않음).
  const [initial] = useState(() => readListState(new URLSearchParams(searchParams.toString())))
  const [filters, setFiltersState] = useState<ApiListFilters>(initial.filters)
  const [page, setPageState] = useState(initial.page)
  const [pageSize, setPageSizeState] = useState(initial.pageSize)
  const [viewMode, setViewModeState] = useState<ApisViewMode>(initial.view)
  // 비동기 콜백·디바운스에서 최신 값을 쓰도록 ref 로도 보관
  const stateRef = useRef<ApisListState>(initial)

  const [apis, setApis] = useState<ApiSummary[]>([])
  const [total, setTotal] = useState(0)
  const [isLoading, setIsLoading] = useState(true)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<DialogType>(null)
  const [stats, setStats] = useState<ApiStats | null>(null)
  const [statsVersion, setStatsVersion] = useState(0)
  const listScrollRef = useRef(0)

  const selectedApiName = searchParams.get("api")

  const urlFor = useCallback((sp: URLSearchParams) => {
    const qs = sp.toString()
    return qs ? `${pathname}?${qs}` : pathname
  }, [pathname])

  const applyState = useCallback((next: ApisListState) => {
    stateRef.current = next
    setFiltersState(next.filters)
    setPageState(next.page)
    setPageSizeState(next.pageSize)
    setViewModeState(next.view)
    router.replace(urlFor(writeListState(new URLSearchParams(window.location.search), next)), { scroll: false })
  }, [router, urlFor])

  // 마지막 요청 번호 — 요청이 겹치면 가장 마지막 요청의 응답만 반영.
  const requestSeqRef = useRef(0)

  const load = useCallback(async () => {
    const seq = ++requestSeqRef.current
    setIsLoading(true)
    try {
      // 삭제 등으로 현재 페이지가 범위를 벗어나면(빈 페이지) 마지막 페이지로 한 번 더 조회
      for (let attempt = 0; attempt < 2; attempt++) {
        const st = stateRef.current
        const data = await fetchApis({ ...st.filters, page: st.page, pageSize: st.pageSize })
        if (seq !== requestSeqRef.current) return
        const lastPage = Math.max(1, Math.ceil(data.total / st.pageSize))
        if (attempt === 0 && data.items.length === 0 && data.total > 0 && st.page > lastPage) {
          applyState({ ...st, page: lastPage })
          continue
        }
        setApis(data.items)
        setTotal(data.total)
        setError(null)
        setHasLoaded(true)
        return
      }
    } catch (err) {
      if (seq !== requestSeqRef.current) return
      setError(errorMessage(err) ?? "API 목록을 불러오지 못했습니다.")
    } finally {
      if (seq === requestSeqRef.current) setIsLoading(false)
    }
  }, [applyState])

  // 최초 1회 로드 — 이후 조회는 각 핸들러가 담당.
  useEffect(() => { void load() }, [load])

  // 상단 지표 — 최초와 등록·수정·삭제 후(statsVersion) 재조회. 실패 시 null("-" 표시).
  useEffect(() => {
    let active = true
    fetchApiStats()
      .then((s) => { if (active) setStats(s) })
      .catch(() => { if (active) setStats(null) })
    return () => { active = false }
  }, [statsVersion])

  const setPage = useCallback((p: number) => {
    applyState({ ...stateRef.current, page: p })
    void load()
  }, [applyState, load])

  const setPageSize = useCallback((s: number) => {
    applyState({ ...stateRef.current, page: 1, pageSize: s })
    void load()
  }, [applyState, load])

  const setFilters = useCallback((patch: Partial<ApiListFilters>) => {
    const cur = stateRef.current
    applyState({ ...cur, page: 1, filters: { ...cur.filters, ...patch } })
    void load()
  }, [applyState, load])

  const resetFilters = useCallback(() => {
    const cur = stateRef.current
    applyState({ ...cur, page: 1, filters: { ...DEFAULT_FILTERS, sort: cur.filters.sort } })
    void load()
  }, [applyState, load])

  const setViewMode = useCallback((v: ApisViewMode) => {
    applyState({ ...stateRef.current, view: v })
  }, [applyState])

  const refreshApis = useCallback(async () => {
    setStatsVersion((v) => v + 1)
    await load()
  }, [load])

  const retry = useCallback(() => { void load() }, [load])

  // 상세 열기는 주소에 기록(push) — 브라우저 뒤로 가기로 목록 복귀. 목록에서 연 경우 닫기도 뒤로 가기로 처리해
  // 기록이 쌓이지 않게 하고, 외부 링크(?api=)로 바로 들어온 경우엔 api 키만 지운다.
  const openedByPushRef = useRef(false)
  const setSelectedApiName = useCallback((name: string | null) => {
    const sp = new URLSearchParams(window.location.search)
    // 상세 탭(?tab=)·관측 기간(?days=)은 열린 API 에만 해당
    sp.delete("tab")
    sp.delete("days")
    if (name) {
      sp.set("api", name)
      router.push(urlFor(sp), { scroll: false })
      openedByPushRef.current = true
      return
    }
    if (openedByPushRef.current) {
      openedByPushRef.current = false
      router.back()
      return
    }
    sp.delete("api")
    router.replace(urlFor(sp), { scroll: false })
  }, [router, urlFor])

  return (
    <ApisContext
      value={{
        apis, total, page, pageSize, isLoading, hasLoaded, error, retry,
        setPage, setPageSize, filters, setFilters, resetFilters, refreshApis, stats,
        open, setOpen, selectedApiName, setSelectedApiName,
        viewMode, setViewMode, listScrollRef,
      }}
    >
      {children}
    </ApisContext>
  )
}

export const useApis = () => {
  const ctx = React.useContext(ApisContext)
  if (!ctx) throw new Error("useApis must be used within <ApisProvider>")
  return ctx
}
