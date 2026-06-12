"use client"

import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"

/** 페이지당 행 수 선택지 — sidebar 데이터셋 목록과 동일. */
export const PAGE_SIZE_OPTIONS = [10, 20, 30, 40, 50]

/** 페이지 번호 목록(말줄임 포함) — sidebar 데이터셋 페이지네이션과 동일 규칙. */
export function getPageNumbers(
  currentPage: number,
  totalPages: number
): (number | "...")[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1)
  }
  if (currentPage <= 4) {
    return [1, 2, 3, 4, 5, "...", totalPages]
  }
  if (currentPage >= totalPages - 3) {
    return [
      1,
      "...",
      totalPages - 4,
      totalPages - 3,
      totalPages - 2,
      totalPages - 1,
      totalPages,
    ]
  }
  return [1, "...", currentPage - 1, currentPage, currentPage + 1, "...", totalPages]
}

/**
 * 그리드 페이지네이션(컨트롤드) — DataTablePagination 과 동일한 UI.
 * 페이지당 행 수 선택 + 현재/전체 페이지 + 처음/이전/번호/다음/마지막.
 * ``page`` 는 0-base. 페더레이션 탐색·분류 체계 등에서 공용으로 쓴다.
 */
export function GridPagination({
  page,
  pageCount,
  pageSize,
  onPage,
  onPageSize,
  pageSizeOptions = PAGE_SIZE_OPTIONS,
}: {
  page: number // 0-base
  pageCount: number
  pageSize: number
  onPage: (p: number) => void
  onPageSize: (s: number) => void
  pageSizeOptions?: number[]
}) {
  const currentPage = page + 1
  const pageNumbers = getPageNumbers(currentPage, pageCount)
  const canPrev = page > 0
  const canNext = page < pageCount - 1
  return (
    <div className="mt-3 flex flex-col-reverse items-center justify-between gap-4 overflow-clip px-2 sm:flex-row">
      <div className="flex items-center gap-2">
        <Select value={`${pageSize}`} onValueChange={(v) => onPageSize(Number(v))}>
          <SelectTrigger className="h-8 w-[70px]">
            <SelectValue placeholder={pageSize} />
          </SelectTrigger>
          <SelectContent side="top">
            {pageSizeOptions.map((s) => (
              <SelectItem key={s} value={`${s}`}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="hidden text-sm font-medium sm:block">페이지당 행 수</p>
      </div>

      <div className="flex items-center space-x-2">
        <div className="flex w-[100px] items-center justify-center text-sm font-medium">
          {currentPage} / {pageCount} 페이지
        </div>
        <Button
          variant="outline"
          className="size-8 p-0"
          onClick={() => onPage(0)}
          disabled={!canPrev}
        >
          <span className="sr-only">첫 페이지로</span>
          <ChevronsLeft className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          className="size-8 p-0"
          onClick={() => onPage(page - 1)}
          disabled={!canPrev}
        >
          <span className="sr-only">이전 페이지</span>
          <ChevronLeft className="h-4 w-4" />
        </Button>

        {pageNumbers.map((n, i) => (
          <div key={`${n}-${i}`} className="flex items-center">
            {n === "..." ? (
              <span className="px-1 text-sm text-muted-foreground">...</span>
            ) : (
              <Button
                variant={currentPage === n ? "default" : "outline"}
                className="h-8 min-w-8 px-2"
                onClick={() => onPage((n as number) - 1)}
              >
                {n}
              </Button>
            )}
          </div>
        ))}

        <Button
          variant="outline"
          className="size-8 p-0"
          onClick={() => onPage(page + 1)}
          disabled={!canNext}
        >
          <span className="sr-only">다음 페이지</span>
          <ChevronRight className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          className="size-8 p-0"
          onClick={() => onPage(pageCount - 1)}
          disabled={!canNext}
        >
          <span className="sr-only">마지막 페이지로</span>
          <ChevronsRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}
