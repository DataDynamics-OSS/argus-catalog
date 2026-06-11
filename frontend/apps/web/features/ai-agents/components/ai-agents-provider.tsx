"use client"

import React, { useCallback, useEffect, useRef, useState } from "react"

import useDialogState from "@/hooks/use-dialog-state"
import { fetchAIAgents, type PaginatedAIAgents } from "../api"
import { type AIAgentSummary } from "../data/schema"

type AIAgentsDialogType = "add" | "delete"

type SearchParams = {
  search: string
  status: string
  framework: string
  category: string
}

type AIAgentsContextType = {
  open: AIAgentsDialogType | null
  setOpen: (str: AIAgentsDialogType | null) => void
  agents: AIAgentSummary[]
  total: number
  page: number
  pageSize: number
  isLoading: boolean
  setPage: (page: number) => void
  setPageSize: (size: number) => void
  searchAgents: (params: SearchParams) => void
  refreshAgents: () => Promise<void>
  /** 목록 UI state — 상세 진입 후 복귀 시에도 유지되도록 provider 에서 보관. */
  viewMode: "table" | "card"
  setViewMode: (v: "table" | "card") => void
  searchQuery: string
  setSearchQuery: (v: string) => void
  statusFilter: string
  setStatusFilter: (v: string) => void
  /** 목록 스크롤 위치 — 상세 복귀 시 복원용(remount 가로질러 보존). */
  listScrollRef: React.MutableRefObject<number>
  /** 삭제 대상 이름 (삭제 다이얼로그 오픈 전 설정). */
  deleteTargetName: string | null
  setDeleteTargetName: React.Dispatch<React.SetStateAction<string | null>>
  /** 상세 뷰 대상 이름 (null = 목록). */
  selectedAgentName: string | null
  setSelectedAgentName: React.Dispatch<React.SetStateAction<string | null>>
}

const AIAgentsContext = React.createContext<AIAgentsContextType | null>(null)

export function AIAgentsProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useDialogState<AIAgentsDialogType>(null)
  const [agents, setAgents] = useState<AIAgentSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [isLoading, setIsLoading] = useState(true)
  const [deleteTargetName, setDeleteTargetName] = useState<string | null>(null)
  const [selectedAgentName, setSelectedAgentName] = useState<string | null>(null)
  // 목록 UI state (뷰 모드/검색어/상태 필터) — 상세 복귀 시 유지
  const [viewMode, setViewMode] = useState<"table" | "card">("table")
  const [searchQuery, setSearchQuery] = useState("")
  const [statusFilter, setStatusFilter] = useState("all")

  const listScrollRef = useRef(0)

  const appliedFiltersRef = useRef<SearchParams>({
    search: "",
    status: "",
    framework: "",
    category: "",
  })

  const loadAgents = useCallback(
    async (params: { page: number; pageSize: number } & SearchParams) => {
      try {
        setIsLoading(true)
        const data: PaginatedAIAgents = await fetchAIAgents({
          page: params.page,
          pageSize: params.pageSize,
          search: params.search || undefined,
          status: params.status || undefined,
          framework: params.framework || undefined,
          category: params.category || undefined,
        })
        setAgents(data.items)
        setTotal(data.total)
      } catch (err) {
        console.error("Failed to fetch AI agents:", err)
      } finally {
        setIsLoading(false)
      }
    },
    [],
  )

  useEffect(() => {
    loadAgents({
      page: 1,
      pageSize,
      search: "",
      status: "",
      framework: "",
      category: "",
    })
  }, [loadAgents, pageSize])

  const handleSetPage = useCallback(
    (newPage: number) => {
      setPage(newPage)
      loadAgents({ page: newPage, pageSize, ...appliedFiltersRef.current })
    },
    [loadAgents, pageSize],
  )

  const handleSetPageSize = useCallback(
    (size: number) => {
      setPageSize(size)
      setPage(1)
      loadAgents({ page: 1, pageSize: size, ...appliedFiltersRef.current })
    },
    [loadAgents],
  )

  const searchAgents = useCallback(
    (params: SearchParams) => {
      appliedFiltersRef.current = params
      setPage(1)
      loadAgents({ page: 1, pageSize, ...params })
    },
    [loadAgents, pageSize],
  )

  const refreshAgents = useCallback(async () => {
    await loadAgents({ page, pageSize, ...appliedFiltersRef.current })
  }, [loadAgents, page, pageSize])

  return (
    <AIAgentsContext
      value={{
        open,
        setOpen,
        agents,
        total,
        page,
        pageSize,
        isLoading,
        setPage: handleSetPage,
        setPageSize: handleSetPageSize,
        searchAgents,
        refreshAgents,
        viewMode,
        setViewMode,
        searchQuery,
        setSearchQuery,
        statusFilter,
        setStatusFilter,
        listScrollRef,
        deleteTargetName,
        setDeleteTargetName,
        selectedAgentName,
        setSelectedAgentName,
      }}
    >
      {children}
    </AIAgentsContext>
  )
}

export const useAIAgents = () => {
  const ctx = React.useContext(AIAgentsContext)
  if (!ctx) {
    throw new Error("useAIAgents must be used within <AIAgentsProvider>")
  }
  return ctx
}
