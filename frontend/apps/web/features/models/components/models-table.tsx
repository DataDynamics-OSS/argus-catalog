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
import { Archive, Search, X } from "lucide-react"
import { useRouter } from "next/navigation"
import { toast } from "@/lib/notify"

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
import { useAuth } from "@/features/auth"
import { type ModelSummary } from "../data/schema"
import { createModelsColumns } from "./models-columns"
import { ModelsPrimaryButtons } from "./models-primary-buttons"
import { ModelsRegisterGuide } from "./models-register-guide"
import { useModels } from "./models-provider"

type ModelsTableProps = {
  data: ModelSummary[]
  isLoading?: boolean
}

export function ModelsTable({ data, isLoading }: ModelsTableProps) {
  const {
    total, page, pageSize, setPage, setPageSize, searchModels,
    rowSelection, setRowSelection, listScrollRef, appliedFilters,
    showDeleted, setShowDeleted,
  } = useModels()
  const { user } = useAuth()
  const router = useRouter()
  const openDetail = useCallback(
    (name: string) => {
      // 삭제된 모델은 상세 API 가 404 — 복원 후 열 수 있다
      if (showDeleted) {
        toast.info("삭제된 모델입니다. 복원한 뒤 상세를 볼 수 있습니다.")
        return
      }
      router.push(`/dashboard/models/${encodeURIComponent(name)}`)
    },
    [router, showDeleted],
  )

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

  const columns = useMemo(() => createModelsColumns(openDetail), [openDetail])

  const [sorting, setSorting] = useState<SortingState>([])
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({})
  const [guideOpen, setGuideOpen] = useState(false)

  // --- Filter state ---
  // 초기값은 provider 에 마지막으로 적용된 필터 — 상세에서 돌아와도 입력 UI 와 목록이 일치한다.
  const [searchQuery, setSearchQuery] = useState(appliedFilters.search)
  const [statusFilter, setStatusFilter] = useState(appliedFilters.status || "all")
  const [flavorFilter, setFlavorFilter] = useState(appliedFilters.flavor || "all")
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Derive unique filter values from data
  const flavors = useMemo(() => {
    const set = new Set<string>()
    data.forEach((m) => { if (m.flavor) set.add(m.flavor) })
    if (flavorFilter !== "all") set.add(flavorFilter)
    return Array.from(set).sort()
  }, [data, flavorFilter])

  // --- Search helpers ---
  const doSearch = useCallback(() => {
    searchModels({
      search: searchQuery,
      status: statusFilter === "all" ? "" : statusFilter,
      python_version: "",
      flavor: flavorFilter === "all" ? "" : flavorFilter,
    })
  }, [searchQuery, statusFilter, flavorFilter, searchModels])

  // Debounce search on text change (3s)
  function handleSearchChange(value: string) {
    setSearchQuery(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      searchModels({
        search: value,
        status: statusFilter === "all" ? "" : statusFilter,
        python_version: "",
        flavor: flavorFilter === "all" ? "" : flavorFilter,
      })
    }, 3000)
  }

  function handleSearchKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") {
      if (debounceRef.current) clearTimeout(debounceRef.current)
      doSearch()
    }
  }

  // 드롭다운 필터 변경 시 즉시 검색. 첫 마운트(상세에서 복귀 포함)는 provider 가 이미 같은
  // 조건으로 불러온 상태라 다시 조회하지 않는다 — 다시 조회하면 페이지 위치가 1로 초기화된다.
  const mountedRef = useRef(false)
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true
      return
    }
    if (debounceRef.current) clearTimeout(debounceRef.current)
    doSearch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, flavorFilter])

  function handleClear() {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    setSearchQuery("")
    setStatusFilter("all")
    setFlavorFilter("all")
    searchModels({ search: "", status: "", python_version: "", flavor: "" })
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
              placeholder="모델 이름 검색"
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              className="pl-8 h-9"
            />
          </div>

          {/* Status filter */}
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger size="sm" className="w-[130px]">
              <SelectValue placeholder="상태" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">전체 상태</SelectItem>
              <SelectItem value="READY">준비됨</SelectItem>
              <SelectItem value="PENDING_REGISTRATION">등록 중</SelectItem>
              <SelectItem value="FAILED_REGISTRATION">등록 실패</SelectItem>
            </SelectContent>
          </Select>

          {/* 프레임워크(MLflow flavor) filter */}
          <Select value={flavorFilter} onValueChange={setFlavorFilter}>
            <SelectTrigger size="sm" className="w-[150px]">
              <SelectValue placeholder="프레임워크" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">전체 프레임워크</SelectItem>
              {flavors.map((v) => (
                <SelectItem key={v} value={v}>{v}</SelectItem>
              ))}
            </SelectContent>
          </Select>

        </div>

        <div className="flex items-center gap-2">
          {(searchQuery || statusFilter !== "all" || flavorFilter !== "all") && (
            <Button variant="ghost" size="sm" onClick={handleClear}>
              <X className="mr-1 h-3.5 w-3.5" />
              초기화
            </Button>
          )}
          {user?.is_admin && (
            <Button
              variant={showDeleted ? "secondary" : "outline"}
              size="sm"
              aria-pressed={showDeleted}
              onClick={() => setShowDeleted(!showDeleted)}
              title="소프트 삭제된 모델을 보고 복원하거나 영구 삭제합니다"
            >
              <Archive className="mr-1 h-3.5 w-3.5" />
              {showDeleted ? "삭제된 모델 닫기" : "삭제된 모델"}
            </Button>
          )}
          <ModelsPrimaryButtons />
        </div>
      </div>

      {showDeleted && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          삭제된 모델을 보고 있습니다. 선택한 뒤 <strong>복원</strong>하거나 <strong>영구 삭제</strong>(아티팩트 파일 포함)할 수 있습니다.
        </div>
      )}

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
                // 행 아무 곳이나 누르면 상세로 이동 (체크박스·⋯ 메뉴·이름 버튼은 이벤트 전파를 막는다)
                <TableRow
                  key={row.id}
                  className="group/row cursor-pointer"
                  onClick={() => openDetail(row.original.name)}
                >
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
                  {showDeleted ? "삭제된 모델이 없습니다." : (
                    <div className="space-y-1">
                      <p>모델이 없습니다.</p>
                      <p className="text-xs text-muted-foreground">
                        모델은 MLflow 클라이언트로 학습 결과를 기록할 때 등록됩니다.{" "}
                        <button
                          type="button"
                          className="font-medium text-primary underline-offset-4 hover:underline"
                          onClick={() => setGuideOpen(true)}
                        >
                          등록 방법 보기
                        </button>
                      </p>
                    </div>
                  )}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <DataTablePagination table={table} className="mt-auto" />
      <ModelsRegisterGuide open={guideOpen} onOpenChange={setGuideOpen} />
    </div>
  )
}
