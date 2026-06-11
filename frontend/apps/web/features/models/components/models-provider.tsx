"use client"

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { type RowSelectionState } from "@tanstack/react-table"

import useDialogState from "@/hooks/use-dialog-state"
import { fetchModels, type PaginatedModels } from "../api"
import { type ModelSummary } from "../data/schema"

type ModelsDialogType = "add" | "edit" | "delete"

type SearchParams = {
  search: string
  status: string
  python_version: string
  sklearn_version: string
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
  /** Currently selected model name for detail view (null = show grid). */
  selectedModelName: string | null
  setSelectedModelName: React.Dispatch<React.SetStateAction<string | null>>
  /** 목록 스크롤 위치 — 상세 복귀 시 복원용(remount 가로질러 보존). */
  listScrollRef: React.MutableRefObject<number>
}

const ModelsContext = React.createContext<ModelsContextType | null>(null)

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
  const [pageSize, setPageSize] = useState(20)
  const [isLoading, setIsLoading] = useState(true)
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({})
  const [deleteTargetNames, setDeleteTargetNames] = useState<string[]>([])
  const [selectedModelName, setSelectedModelName] = useState<string | null>(null)

  const listScrollRef = useRef(0)

  const appliedFiltersRef = useRef<SearchParams>({
    search: "", status: "", python_version: "", sklearn_version: "",
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

  const loadModels = useCallback(
    async (params: {
      page: number; pageSize: number;
      search: string; status: string; python_version: string; sklearn_version: string;
    }) => {
      try {
        setIsLoading(true)
        const data: PaginatedModels = await fetchModels({
          page: params.page,
          pageSize: params.pageSize,
          search: params.search || undefined,
          status: params.status || undefined,
          python_version: params.python_version || undefined,
          sklearn_version: params.sklearn_version || undefined,
        })
        setModels(data.items)
        setTotal(data.total)
      } catch (err) {
        console.error("Failed to fetch models:", err)
      } finally {
        setIsLoading(false)
      }
    },
    [],
  )

  useEffect(() => {
    loadModels({ page: 1, pageSize, search: "", status: "", python_version: "", sklearn_version: "" })
  }, [loadModels, pageSize])

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
      setPage(1)
      loadModels({ page: 1, pageSize, ...params })
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
        selectedModelName,
        setSelectedModelName,
        listScrollRef,
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
