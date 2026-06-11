"use client"

import React, { useCallback, useEffect, useRef, useState } from "react"

import useDialogState from "@/hooks/use-dialog-state"
import { fetchDatasets, type PaginatedDatasets } from "../api"
import { type DatasetSummary } from "../data/schema"

type DatasetsDialogType = "add" | "edit" | "delete"

export type DatasetsViewMode = "table" | "card"

const VIEW_MODE_KEY = "argus.datasets.viewMode"

type SearchParams = {
  search: string
  datasource: string
  origin: string
  status: string
  tag: string
  orgId: string
  systemId: string
}

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
  searchDatasets: (params: SearchParams) => void
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
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
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

  const appliedFiltersRef = useRef<SearchParams>({
    search: "",
    datasource: "",
    origin: "",
    status: "",
    tag: "",
    orgId: "",
    systemId: "",
  })

  const loadDatasets = useCallback(
    async (params: {
      page: number
      pageSize: number
      search: string
      datasource: string
      origin: string
      status: string
      tag: string
      orgId: string
      systemId: string
    }) => {
      try {
        setIsLoading(true)
        const data: PaginatedDatasets = await fetchDatasets({
          page: params.page,
          pageSize: params.pageSize,
          search: params.search || undefined,
          datasource: params.datasource || undefined,
          origin: params.origin || undefined,
          status: params.status || undefined,
          tag: params.tag || undefined,
          orgId: params.orgId ? Number(params.orgId) : undefined,
          systemId: params.systemId ? Number(params.systemId) : undefined,
        })
        setDatasets(data.items)
        setTotal(data.total)
      } catch (err) {
        console.error("Failed to fetch datasets:", err)
      } finally {
        setIsLoading(false)
      }
    },
    []
  )

  useEffect(() => {
    loadDatasets({
      page: 1,
      pageSize,
      search: "",
      datasource: "",
      origin: "",
      status: "",
      tag: "",
      orgId: "",
      systemId: "",
    })
  }, [loadDatasets, pageSize])

  const handleSetPage = useCallback(
    (newPage: number) => {
      setPage(newPage)
      const f = appliedFiltersRef.current
      loadDatasets({ page: newPage, pageSize, ...f })
    },
    [loadDatasets, pageSize]
  )

  const handleSetPageSize = useCallback(
    (size: number) => {
      setPageSize(size)
      setPage(1)
      const f = appliedFiltersRef.current
      loadDatasets({ page: 1, pageSize: size, ...f })
    },
    [loadDatasets]
  )

  const searchDatasets = useCallback(
    (params: SearchParams) => {
      appliedFiltersRef.current = params
      setPage(1)
      loadDatasets({ page: 1, pageSize, ...params })
    },
    [loadDatasets, pageSize]
  )

  const refreshDatasets = useCallback(async () => {
    const f = appliedFiltersRef.current
    await loadDatasets({ page, pageSize, ...f })
  }, [loadDatasets, page, pageSize])

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
