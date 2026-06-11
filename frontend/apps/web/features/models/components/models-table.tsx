"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  type SortingState,
  type VisibilityState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table"
import { Search, X } from "lucide-react"

import { cn } from "@workspace/ui/lib/utils"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { DataTablePagination } from "@/components/data-table"
import { type ModelSummary } from "../data/schema"
import { createModelsColumns } from "./models-columns"
import { ModelsPrimaryButtons } from "./models-primary-buttons"
import { useModels } from "./models-provider"

type ModelsTableProps = {
  data: ModelSummary[]
  isLoading?: boolean
}

export function ModelsTable({ data, isLoading }: ModelsTableProps) {
  const {
    total, page, pageSize, setPage, setPageSize, searchModels,
    rowSelection, setRowSelection, setSelectedModelName, listScrollRef,
  } = useModels()

  // 상세 복귀 시 스크롤 위치 복원 + 목록에 있는 동안 위치 추적.
  useEffect(() => {
    const id = requestAnimationFrame(() => window.scrollTo(0, listScrollRef.current))
    const onScroll = () => {
      listScrollRef.current = window.scrollY
    }
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => {
      cancelAnimationFrame(id)
      window.removeEventListener("scroll", onScroll)
    }
  }, [listScrollRef])

  const columns = useMemo(
    () => createModelsColumns((name) => setSelectedModelName(name)),
    [setSelectedModelName],
  )

  const [sorting, setSorting] = useState<SortingState>([])
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({})

  // --- Filter state ---
  const [searchQuery, setSearchQuery] = useState("")
  const [statusFilter, setStatusFilter] = useState("all")
  const [pythonFilter, setPythonFilter] = useState("all")
  const [sklearnFilter, setSklearnFilter] = useState("all")
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Derive unique filter values from data
  const pythonVersions = useMemo(() => {
    const set = new Set<string>()
    data.forEach((m) => { if (m.python_version) set.add(m.python_version) })
    return Array.from(set).sort()
  }, [data])

  const sklearnVersions = useMemo(() => {
    const set = new Set<string>()
    data.forEach((m) => { if (m.sklearn_version) set.add(m.sklearn_version) })
    return Array.from(set).sort()
  }, [data])

  // --- Search helpers ---
  const doSearch = useCallback(() => {
    searchModels({
      search: searchQuery,
      status: statusFilter === "all" ? "" : statusFilter,
      python_version: pythonFilter === "all" ? "" : pythonFilter,
      sklearn_version: sklearnFilter === "all" ? "" : sklearnFilter,
    })
  }, [searchQuery, statusFilter, pythonFilter, sklearnFilter, searchModels])

  // Debounce search on text change (3s)
  function handleSearchChange(value: string) {
    setSearchQuery(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      searchModels({
        search: value,
        status: statusFilter === "all" ? "" : statusFilter,
        python_version: pythonFilter === "all" ? "" : pythonFilter,
        sklearn_version: sklearnFilter === "all" ? "" : sklearnFilter,
      })
    }, 3000)
  }

  function handleSearchKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") {
      if (debounceRef.current) clearTimeout(debounceRef.current)
      doSearch()
    }
  }

  // Trigger search immediately on filter dropdown change
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    doSearch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, pythonFilter, sklearnFilter])

  function handleClear() {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    setSearchQuery("")
    setStatusFilter("all")
    setPythonFilter("all")
    setSklearnFilter("all")
    searchModels({ search: "", status: "", python_version: "", sklearn_version: "" })
  }

  // --- Table ---
  const pageCount = useMemo(
    () => Math.ceil(total / pageSize),
    [total, pageSize],
  )

  const table = useReactTable({
    data,
    columns,
    pageCount,
    state: {
      sorting,
      pagination: { pageIndex: page - 1, pageSize },
      columnVisibility,
      rowSelection,
    },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    manualPagination: true,
    manualFiltering: true,
    onPaginationChange: (updater) => {
      const next =
        typeof updater === "function"
          ? updater({ pageIndex: page - 1, pageSize })
          : updater
      if (next.pageSize !== pageSize) {
        setPageSize(next.pageSize)
      } else {
        setPage(next.pageIndex + 1)
      }
    },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  return (
    <div className={cn("flex flex-1 flex-col gap-4")}>
      {/* Custom Toolbar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-1 items-center gap-2">
          {/* Search input */}
          <div className="relative max-w-xs flex-1">
            <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="MLflow 모델 검색..."
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              className="pl-8 h-9"
            />
          </div>

          {/* Status filter */}
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger size="sm" className="w-[140px]">
              <SelectValue placeholder="상태" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">전체 상태</SelectItem>
              <SelectItem value="READY">READY</SelectItem>
              <SelectItem value="PENDING_REGISTRATION">PENDING</SelectItem>
              <SelectItem value="FAILED_REGISTRATION">FAILED</SelectItem>
            </SelectContent>
          </Select>

          {/* Python filter */}
          <Select value={pythonFilter} onValueChange={setPythonFilter}>
            <SelectTrigger size="sm" className="w-[130px]">
              <SelectValue placeholder="Python" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">전체 Python</SelectItem>
              {pythonVersions.map((v) => (
                <SelectItem key={v} value={v}>{v}</SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* sklearn filter */}
          <Select value={sklearnFilter} onValueChange={setSklearnFilter}>
            <SelectTrigger size="sm" className="w-[130px]">
              <SelectValue placeholder="sklearn" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">전체 sklearn</SelectItem>
              {sklearnVersions.map((v) => (
                <SelectItem key={v} value={v}>{v}</SelectItem>
              ))}
            </SelectContent>
          </Select>

        </div>

        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleClear}>
            <X className="mr-1 h-3.5 w-3.5" />
            초기화
          </Button>
          <ModelsPrimaryButtons />
        </div>
      </div>

      {/* Table */}
      <div className="overflow-hidden rounded-md border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id} className="group/row">
                {headerGroup.headers.map((header) => (
                  <TableHead
                    key={header.id}
                    colSpan={header.colSpan}
                    className={cn(
                      "bg-background group-hover/row:bg-muted",
                      (header.column.columnDef.meta as { className?: string })
                        ?.className,
                    )}
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="h-24 text-center"
                >
                  <p className="text-muted-foreground">모델을 불러오는 중...</p>
                </TableCell>
              </TableRow>
            ) : table.getRowModel().rows?.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id} className="group/row">
                  {row.getVisibleCells().map((cell) => (
                    <TableCell
                      key={cell.id}
                      className={cn(
                        "bg-background group-hover/row:bg-muted",
                        (cell.column.columnDef.meta as { className?: string })
                          ?.className,
                      )}
                    >
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext(),
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="h-24 text-center"
                >
                  모델이 없습니다.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <DataTablePagination table={table} className="mt-auto" />
    </div>
  )
}
