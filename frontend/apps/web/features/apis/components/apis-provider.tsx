"use client"

import React, { useCallback, useEffect, useRef, useState } from "react"

import { fetchApis, type ApiSummary, type PaginatedApis } from "../api"

type DialogType = "add" | null

type SearchParams = { search: string; status: string; category: string }

type ApisContextType = {
  apis: ApiSummary[]
  total: number
  page: number
  pageSize: number
  isLoading: boolean
  setPage: (p: number) => void
  setPageSize: (s: number) => void
  searchApis: (params: SearchParams) => void
  refreshApis: () => Promise<void>
  open: DialogType
  setOpen: (d: DialogType) => void
  selectedApiName: string | null
  setSelectedApiName: React.Dispatch<React.SetStateAction<string | null>>
  // 목록 UI state (상세 복귀 시 유지)
  viewMode: "table" | "card"
  setViewMode: (v: "table" | "card") => void
  searchQuery: string
  setSearchQuery: (v: string) => void
  statusFilter: string
  setStatusFilter: (v: string) => void
  listScrollRef: React.MutableRefObject<number>
}

const ApisContext = React.createContext<ApisContextType | null>(null)

export function ApisProvider({ children }: { children: React.ReactNode }) {
  const [apis, setApis] = useState<ApiSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [isLoading, setIsLoading] = useState(true)
  const [open, setOpen] = useState<DialogType>(null)
  const [selectedApiName, setSelectedApiName] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<"table" | "card">("table")
  const [searchQuery, setSearchQuery] = useState("")
  const [statusFilter, setStatusFilter] = useState("all")
  const listScrollRef = useRef(0)
  const appliedFiltersRef = useRef<SearchParams>({ search: "", status: "", category: "" })

  const loadApis = useCallback(
    async (params: { page: number; pageSize: number } & SearchParams) => {
      try {
        setIsLoading(true)
        const data: PaginatedApis = await fetchApis({
          page: params.page,
          pageSize: params.pageSize,
          search: params.search || undefined,
          status: params.status || undefined,
          category: params.category || undefined,
        })
        setApis(data.items)
        setTotal(data.total)
      } catch (err) {
        console.error("Failed to fetch APIs:", err)
      } finally {
        setIsLoading(false)
      }
    },
    [],
  )

  useEffect(() => {
    loadApis({ page: 1, pageSize, search: "", status: "", category: "" })
  }, [loadApis, pageSize])

  const handleSetPage = useCallback(
    (newPage: number) => {
      setPage(newPage)
      loadApis({ page: newPage, pageSize, ...appliedFiltersRef.current })
    },
    [loadApis, pageSize],
  )

  const handleSetPageSize = useCallback(
    (size: number) => {
      setPageSize(size)
      setPage(1)
      loadApis({ page: 1, pageSize: size, ...appliedFiltersRef.current })
    },
    [loadApis],
  )

  const searchApis = useCallback(
    (params: SearchParams) => {
      appliedFiltersRef.current = params
      setPage(1)
      loadApis({ page: 1, pageSize, ...params })
    },
    [loadApis, pageSize],
  )

  const refreshApis = useCallback(async () => {
    await loadApis({ page, pageSize, ...appliedFiltersRef.current })
  }, [loadApis, page, pageSize])

  return (
    <ApisContext
      value={{
        apis, total, page, pageSize, isLoading,
        setPage: handleSetPage, setPageSize: handleSetPageSize,
        searchApis, refreshApis, open, setOpen,
        selectedApiName, setSelectedApiName,
        viewMode, setViewMode, searchQuery, setSearchQuery, statusFilter, setStatusFilter,
        listScrollRef,
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
