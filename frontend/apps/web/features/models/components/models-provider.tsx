"use client"

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { type RowSelectionState } from "@tanstack/react-table"

import useDialogState from "@/hooks/use-dialog-state"
import { notifyError } from "@/lib/notify"
import { fetchModels, type PaginatedModels } from "../api"
import { type ModelSummary } from "../data/schema"

/** delete = 소프트 삭제(관리자는 영구 삭제 선택 가능), purge = 삭제된 모델의 영구 삭제 */
type ModelsDialogType = "delete" | "purge"

export type SearchParams = {
  search: string
  status: string
  python_version: string
  flavor: string
}

type ModelsContextType = {
  open: ModelsDialogType | null
  setOpen: (str: ModelsDialogType | null) => void
  currentRow: ModelSummary | null
  setCurrentRow: React.Dispatch<React.SetStateAction<ModelSummary | null>>
  models: ModelSummary[]
  total: number
  page: number
  pageSize: number
  isLoading: boolean
  setPage: (page: number) => void
  setPageSize: (size: number) => void
  searchModels: (params: SearchParams) => void
  refreshModels: () => Promise<void>
  /** Names of selected models (derived from rowSelection). */
  selectedNames: string[]
  /** TanStack row selection state. */
  rowSelection: RowSelectionState
  setRowSelection: React.Dispatch<React.SetStateAction<RowSelectionState>>
  /** Names to delete (set before opening delete dialog). */
  deleteTargetNames: string[]
  setDeleteTargetNames: React.Dispatch<React.SetStateAction<string[]>>
  /** Clear selection after delete. */
  clearSelection: () => void
  /** 마지막으로 적용된 검색·필터 — 상세에서 돌아와 목록이 다시 마운트될 때 입력 UI 를 복원한다. */
  appliedFilters: SearchParams
  /** 목록 스크롤 위치 — 상세 복귀 시 복원용(remount 가로질러 보존). */
  listScrollRef: React.MutableRefObject<number>
  /** 삭제된 모델 보기(관리자) — 켜면 소프트 삭제된 모델만 조회 */
  showDeleted: boolean
  setShowDeleted: (v: boolean) => void
}

const ModelsContext = React.createContext<ModelsContextType | null>(null)

const DEFAULT_PAGE_SIZE = 20

export function ModelsProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const [open, setOpen] = useDialogState<ModelsDialogType>(null)
  const [currentRow, setCurrentRow] = useState<ModelSummary | null>(null)
  const [models, setModels] = useState<ModelSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [isLoading, setIsLoading] = useState(true)
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({})
  const [deleteTargetNames, setDeleteTargetNames] = useState<string[]>([])

  const listScrollRef = useRef(0)
  const [showDeleted, setShowDeletedState] = useState(false)
  const showDeletedRef = useRef(false)

  const appliedFiltersRef = useRef<SearchParams>({
    search: "", status: "", python_version: "", flavor: "",
  })
  // 렌더에서 읽는 사본 (ref 는 핸들러 안에서만 읽는다)
  const [appliedFilters, setAppliedFilters] = useState<SearchParams>({
    search: "", status: "", python_version: "", flavor: "",
  })

  const selectedNames = useMemo(() => {
    return Object.keys(rowSelection)
      .filter((k) => rowSelection[k])
      .map((idx) => models[Number(idx)]?.name)
      .filter(Boolean) as string[]
  }, [rowSelection, models])

  const clearSelection = useCallback(() => {
    setRowSelection({})
    setDeleteTargetNames([])
  }, [])

  // 마지막 요청 번호 — 요청이 겹치면(페이지 크기 변경·검색·페이지 이동 연속) 가장 마지막 요청의 응답만 반영.
  const requestSeqRef = useRef(0)

  const loadModels = useCallback(
    async (params: {
      page: number; pageSize: number;
      search: string; status: string; python_version: string; flavor: string;
    }) => {
      const deleted = showDeletedRef.current
      const seq = ++requestSeqRef.current
      try {
        setIsLoading(true)
        const data: PaginatedModels = await fetchModels({
          page: params.page,
          pageSize: params.pageSize,
          search: params.search || undefined,
          status: params.status || undefined,
          python_version: params.python_version || undefined,
          flavor: params.flavor || undefined,
          deleted: deleted || undefined,
        })
        if (seq !== requestSeqRef.current) return
        setModels(data.items)
        setTotal(data.total)
      } catch (err) {
        if (seq !== requestSeqRef.current) return
        console.error("Failed to fetch models:", err)
        notifyError(err, "모델 목록을 불러오지 못했습니다.")
      } finally {
        if (seq === requestSeqRef.current) setIsLoading(false)
      }
    },
    [],
  )

  // 최초 1회 로드. pageSize 를 의존성에 두면 페이지 크기 변경 시 필터 없는 조회가 한 번 더 나가
  // handleSetPageSize 의 (필터 유지) 조회 결과를 덮어쓸 수 있으므로, 이후 조회는 각 핸들러가 담당한다.
  useEffect(() => {
    loadModels({ page: 1, pageSize: DEFAULT_PAGE_SIZE, search: "", status: "", python_version: "", flavor: "" })
  }, [loadModels])

  const handleSetPage = useCallback(
    (newPage: number) => {
      setPage(newPage)
      const f = appliedFiltersRef.current
      loadModels({ page: newPage, pageSize, ...f })
    },
    [loadModels, pageSize],
  )

  const handleSetPageSize = useCallback(
    (size: number) => {
      setPageSize(size)
      setPage(1)
      const f = appliedFiltersRef.current
      loadModels({ page: 1, pageSize: size, ...f })
    },
    [loadModels],
  )

  const searchModels = useCallback(
    (params: SearchParams) => {
      appliedFiltersRef.current = params
      setAppliedFilters(params)
      setPage(1)
      loadModels({ page: 1, pageSize, ...params })
    },
    [loadModels, pageSize],
  )

  const setShowDeleted = useCallback(
    (v: boolean) => {
      showDeletedRef.current = v
      setShowDeletedState(v)
      setRowSelection({})
      setPage(1)
      loadModels({ page: 1, pageSize, ...appliedFiltersRef.current })
    },
    [loadModels, pageSize],
  )

  const refreshModels = useCallback(async () => {
    const f = appliedFiltersRef.current
    await loadModels({ page, pageSize, ...f })
  }, [loadModels, page, pageSize])

  return (
    <ModelsContext
      value={{
        open,
        setOpen,
        currentRow,
        setCurrentRow,
        models,
        total,
        page,
        pageSize,
        isLoading,
        setPage: handleSetPage,
        setPageSize: handleSetPageSize,
        searchModels,
        refreshModels,
        selectedNames,
        rowSelection,
        setRowSelection,
        deleteTargetNames,
        setDeleteTargetNames,
        clearSelection,
        listScrollRef,
        appliedFilters,
        showDeleted,
        setShowDeleted,
      }}
    >
      {children}
    </ModelsContext>
  )
}

export const useModels = () => {
  const ctx = React.useContext(ModelsContext)
  if (!ctx) {
    throw new Error("useModels must be used within <ModelsProvider>")
  }
  return ctx
}
