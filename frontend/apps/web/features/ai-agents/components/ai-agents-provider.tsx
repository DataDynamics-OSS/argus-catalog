"use client"

import React, { useCallback, useEffect, useRef, useState } from "react"

import useDialogState from "@/hooks/use-dialog-state"
import { notifyError } from "@/lib/notify"
import { cleanQuery, fetchAIAgents, type AgentListQuery, type PaginatedAIAgents } from "../api"
import { type AIAgentSummary } from "../data/schema"

type AIAgentsDialogType = "add" | "delete" | "import" | "bulk"

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
  /** 현재 목록 질의(필터·정렬) */
  query: AgentListQuery
  /** 질의를 바꾸고 1페이지부터 다시 조회. viewId 는 저장된 보기에서 고른 경우 그 id(직접 바꾸면 null) */
  applyQuery: (q: AgentListQuery, viewId?: string | null) => void
  /** 지금 질의를 만든 저장된 보기 id */
  activeViewId: string | null
  /** 일괄 변경 대상으로 고른 에이전트 이름 */
  selectedNames: string[]
  setSelectedNames: React.Dispatch<React.SetStateAction<string[]>>
  refreshAgents: () => Promise<void>
  /** 목록 UI state — 상세 진입 후 복귀 시에도 유지되도록 provider 에서 보관. */
  viewMode: "table" | "card"
  setViewMode: (v: "table" | "card") => void
  /** 검색 입력 칸 값(입력 중 — 적용은 applyQuery) */
  searchQuery: string
  setSearchQuery: (v: string) => void
  /** 목록 스크롤 위치 — 상세 복귀 시 복원용(remount 가로질러 보존). */
  listScrollRef: React.MutableRefObject<number>
  /** 삭제 대상 이름 (삭제 다이얼로그 오픈 전 설정). */
  deleteTargetName: string | null
  setDeleteTargetName: React.Dispatch<React.SetStateAction<string | null>>
  /** 목록 탭 — 에이전트 / 미등록 에이전트 (상세 복귀 시 유지). */
  listTab: "agents" | "discoveries"
  setListTab: (v: "agents" | "discoveries") => void
  /** 지표 카드 다시 조회용 — 발견 처리 등 전체 수와 무관한 변경 후 올린다. */
  statsKey: number
  bumpStats: () => void
  /** 상세 뷰 대상 이름 (null = 목록). */
  selectedAgentName: string | null
  setSelectedAgentName: React.Dispatch<React.SetStateAction<string | null>>
}

const AIAgentsContext = React.createContext<AIAgentsContextType | null>(null)

const DEFAULT_PAGE_SIZE = 20

export function AIAgentsProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useDialogState<AIAgentsDialogType>(null)
  const [agents, setAgents] = useState<AIAgentSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [isLoading, setIsLoading] = useState(true)
  const [deleteTargetName, setDeleteTargetName] = useState<string | null>(null)
  const [selectedAgentName, setSelectedAgentName] = useState<string | null>(null)
  // 목록 UI state (뷰 모드/검색어/상태 필터) — 상세 복귀 시 유지
  const [viewMode, setViewMode] = useState<"table" | "card">("table")
  const [searchQuery, setSearchQuery] = useState("")
  const [query, setQuery] = useState<AgentListQuery>({})
  const [activeViewId, setActiveViewId] = useState<string | null>(null)
  const [selectedNames, setSelectedNames] = useState<string[]>([])
  const [listTab, setListTab] = useState<"agents" | "discoveries">("agents")
  const [statsKey, setStatsKey] = useState(0)
  const bumpStats = useCallback(() => setStatsKey((k) => k + 1), [])

  const listScrollRef = useRef(0)

  const appliedFiltersRef = useRef<AgentListQuery>({})

  // 마지막 요청 번호 — 요청이 겹치면(페이지 크기 변경·검색·페이지 이동 연속) 가장 마지막 요청의 응답만 반영.
  const requestSeqRef = useRef(0)

  const loadAgents = useCallback(
    async (params: { page: number; pageSize: number } & AgentListQuery) => {
      const seq = ++requestSeqRef.current
      try {
        setIsLoading(true)
        const data: PaginatedAIAgents = await fetchAIAgents(params)
        if (seq !== requestSeqRef.current) return
        setAgents(data.items)
        setTotal(data.total)
      } catch (err) {
        if (seq !== requestSeqRef.current) return
        console.error("Failed to fetch AI agents:", err)
        notifyError(err, "AI 에이전트 목록을 불러오지 못했습니다.")
      } finally {
        if (seq === requestSeqRef.current) setIsLoading(false)
      }
    },
    [],
  )

  // 최초 1회 로드. pageSize 를 의존성에 두면 페이지 크기 변경 시 필터 없는 조회가 한 번 더 나가
  // handleSetPageSize 의 (필터 유지) 조회 결과를 덮어쓸 수 있으므로, 이후 조회는 각 핸들러가 담당한다.
  useEffect(() => {
    loadAgents({ page: 1, pageSize: DEFAULT_PAGE_SIZE })
  }, [loadAgents])

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

  const applyQuery = useCallback(
    (q: AgentListQuery, viewId: string | null = null) => {
      const clean = cleanQuery(q)
      appliedFiltersRef.current = clean
      setQuery(clean)
      setActiveViewId(viewId)
      setSearchQuery(clean.search ?? "")
      setSelectedNames([])
      setPage(1)
      loadAgents({ page: 1, pageSize, ...clean })
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
        query,
        applyQuery,
        activeViewId,
        selectedNames,
        setSelectedNames,
        refreshAgents,
        viewMode,
        setViewMode,
        searchQuery,
        setSearchQuery,
        listScrollRef,
        deleteTargetName,
        setDeleteTargetName,
        listTab,
        setListTab,
        statsKey,
        bumpStats,
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
