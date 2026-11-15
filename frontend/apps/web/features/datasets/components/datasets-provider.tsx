"use client"

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"

import useDialogState from "@/hooks/use-dialog-state"
import { notifyError } from "@/lib/notify"
import { fetchDatasets, type PaginatedDatasets } from "../api"
import { type DatasetSummary } from "../data/schema"
import { type DatasetListState, datasetListQuery, parseDatasetListParams } from "../links"

type DatasetsDialogType = "add" | "edit" | "delete"

export type DatasetsViewMode = "table" | "card"

const VIEW_MODE_KEY = "argus.datasets.viewMode"

/** 목록 화면이 바꾸는 조건 — 페이지·크기를 뺀 나머지. */
export type DatasetsFilters = Omit<DatasetListState, "page" | "size">

type DatasetsContextType = {
  open: DatasetsDialogType | null
  setOpen: (str: DatasetsDialogType | null) => void
  currentRow: DatasetSummary | null
  setCurrentRow: React.Dispatch<React.SetStateAction<DatasetSummary | null>>
  datasets: DatasetSummary[]
  total: number
  page: number
  pageSize: number
  isLoading: boolean
  setPage: (page: number) => void
  setPageSize: (size: number) => void
  /** 지금 주소에 걸린 조회 조건 */
  filters: DatasetsFilters
  /** 조건을 바꿔 1페이지부터 다시 조회(주소도 바뀐다) */
  searchDatasets: (params: DatasetsFilters) => void
  refreshDatasets: () => Promise<void>
  viewMode: DatasetsViewMode
  setViewMode: (m: DatasetsViewMode) => void
}

const DatasetsContext = React.createContext<DatasetsContextType | null>(null)

export function DatasetsProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const [open, setOpen] = useDialogState<DatasetsDialogType>(null)
  const [currentRow, setCurrentRow] = useState<DatasetSummary | null>(null)
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [total, setTotal] = useState(0)
  const [isLoading, setIsLoading] = useState(true)
  // 뷰 모드 — localStorage 영속화. 초기값은 mount 시점에 읽음(SSR/CSR mismatch 방지).
  const [viewMode, setViewModeState] = useState<DatasetsViewMode>("table")
  useEffect(() => {
    if (typeof window === "undefined") return
    const stored = window.localStorage.getItem(VIEW_MODE_KEY)
    if (stored === "card" || stored === "table") setViewModeState(stored)
  }, [])
  const setViewMode = useCallback((m: DatasetsViewMode) => {
    setViewModeState(m)
    if (typeof window !== "undefined") {
      window.localStorage.setItem(VIEW_MODE_KEY, m)
    }
  }, [])

  // 주소의 ?view=card|table 이 있으면 그 보기로 연다(사이드바 브랜드의 기본 링크 — TODO-145). 고른 보기는 기억한다.
  const viewParam = useSearchParams().get("view")
  useEffect(() => {
    if (viewParam === "card" || viewParam === "table") setViewMode(viewParam)
  }, [viewParam, setViewMode])

  // 조회 조건은 주소(쿼리스트링)가 원본이다 — 링크로 걸러 들어오거나 상세에서 돌아와도 같은 목록(TODO-073).
  // 화면 조작은 주소만 바꾸고, 주소가 바뀌면 다시 조회한다.
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const queryKey = searchParams.toString()
  const listState = useMemo(() => parseDatasetListParams(new URLSearchParams(queryKey)), [queryKey])
  const { page, size: pageSize } = listState
  const filters = useMemo<DatasetsFilters>(() => {
    const { page: _p, size: _s, ...rest } = listState
    return rest
  }, [listState])

  // 마지막 요청 번호 — 요청이 겹치면(주소가 연달아 바뀜) 가장 마지막 요청의 응답만 반영.
  const requestSeqRef = useRef(0)

  const loadDatasets = useCallback(async (state: DatasetListState, card: boolean) => {
    const seq = ++requestSeqRef.current
    const join = (v: string[]) => (v.length ? v.join(",") : undefined)
    try {
      setIsLoading(true)
      const data: PaginatedDatasets = await fetchDatasets({
        page: state.page,
        pageSize: state.size,
        search: state.q || undefined,
        // 다중 선택은 쉼표로 이어 보낸다 — 백엔드가 여러 값을 받는다(DEF-067)
        datasource: join(state.datasource),
        origin: join(state.origin),
        status: join(state.status),
        tagIds: join(state.tag),
        termId: state.term ? Number(state.term) : undefined,
        categoryId: state.category ? Number(state.category) : undefined,
        orgId: state.org ? Number(state.org) : undefined,
        systemId: state.system ? Number(state.system) : undefined,
        // 카드 보기면 컬럼 미리보기도 받는다(TODO-111)
        card,
      })
      if (seq !== requestSeqRef.current) return
      setDatasets(data.items)
      setTotal(data.total)
    } catch (err) {
      if (seq !== requestSeqRef.current) return
      console.error("Failed to fetch datasets:", err)
      notifyError(err, "데이터셋 목록을 불러오지 못했습니다.")
    } finally {
      if (seq === requestSeqRef.current) setIsLoading(false)
    }
  }, [])

  // 보기 방식을 바꿔도 다시 조회한다 — 카드 보기에만 필요한 필드가 있다
  const wantCard = viewMode === "card"
  useEffect(() => {
    loadDatasets(listState, wantCard)
  }, [listState, wantCard, loadDatasets])

  // 새 조건을 주소에 쓴다. 주소가 그대로면(같은 조건으로 다시 검색) 바로 다시 조회한다.
  const navigate = useCallback(
    (next: DatasetListState) => {
      const q = datasetListQuery(next)
      if (q === queryKey) {
        loadDatasets(next, wantCard)
        return
      }
      router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false })
    },
    [queryKey, pathname, router, loadDatasets, wantCard]
  )

  const handleSetPage = useCallback(
    (newPage: number) => navigate({ ...listState, page: newPage }),
    [navigate, listState]
  )

  const handleSetPageSize = useCallback(
    (size: number) => navigate({ ...listState, size, page: 1 }),
    [navigate, listState]
  )

  const searchDatasets = useCallback(
    (params: DatasetsFilters) => navigate({ ...params, page: 1, size: pageSize }),
    [navigate, pageSize]
  )

  const refreshDatasets = useCallback(async () => {
    await loadDatasets(listState, wantCard)
  }, [loadDatasets, listState, wantCard])

  return (
    <DatasetsContext
      value={{
        open,
        setOpen,
        currentRow,
        setCurrentRow,
        datasets,
        total,
        page,
        pageSize,
        isLoading,
        setPage: handleSetPage,
        setPageSize: handleSetPageSize,
        filters,
        searchDatasets,
        refreshDatasets,
        viewMode,
        setViewMode,
      }}
    >
      {children}
    </DatasetsContext>
  )
}

export const useDatasets = () => {
  const ctx = React.useContext(DatasetsContext)
  if (!ctx) {
    throw new Error("useDatasets must be used within <DatasetsProvider>")
  }
  return ctx
}
