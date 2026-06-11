"use client"

import { type Table } from "@tanstack/react-table"
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react"

import { cn } from "@workspace/ui/lib/utils"
import { Button } from "@workspace/ui/components/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"

type DataTablePaginationProps<TData> = {
  table: Table<TData>
  className?: string
}

function getPageNumbers(
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
  return [
    1,
    "...",
    currentPage - 1,
    currentPage,
    currentPage + 1,
    "...",
    totalPages,
  ]
}

export function DataTablePagination<TData>({
  table,
  className,
}: DataTablePaginationProps<TData>) {
  const currentPage = table.getState().pagination.pageIndex + 1
  const totalPages = table.getPageCount()
  const pageNumbers = getPageNumbers(currentPage, totalPages)

  return (
    <div
      className={cn(
        "flex items-center justify-between overflow-clip px-2",
        "flex-col-reverse gap-4 sm:flex-row",
        className
      )}
    >
      <div className="flex items-center gap-2">
        <Select
          value={`${table.getState().pagination.pageSize}`}
          onValueChange={(value) => {
            table.setPageSize(Number(value))
          }}
        >
          <SelectTrigger className="h-8 w-[70px]">
            <SelectValue placeholder={table.getState().pagination.pageSize} />
          </SelectTrigger>
          <SelectContent side="top">
            {[10, 20, 30, 40, 50].map((pageSize) => (
              <SelectItem key={pageSize} value={`${pageSize}`}>
                {pageSize}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="hidden text-sm font-medium sm:block">페이지당 행 수</p>
      </div>

      <div className="flex items-center space-x-2">
        <div className="flex w-[100px] items-center justify-center text-sm font-medium">
          {currentPage} / {totalPages} 페이지
        </div>
        <Button
          variant="outline"
          className="size-8 p-0"
          onClick={() => table.setPageIndex(0)}
          disabled={!table.getCanPreviousPage()}
        >
          <span className="sr-only">첫 페이지로</span>
          <ChevronsLeft className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          className="size-8 p-0"
          onClick={() => table.previousPage()}
          disabled={!table.getCanPreviousPage()}
        >
          <span className="sr-only">이전 페이지</span>
          <ChevronLeft className="h-4 w-4" />
        </Button>

        {pageNumbers.map((pageNumber, index) => (
          <div key={`${pageNumber}-${index}`} className="flex items-center">
            {pageNumber === "..." ? (
              <span className="px-1 text-sm text-muted-foreground">...</span>
            ) : (
              <Button
                variant={currentPage === pageNumber ? "default" : "outline"}
                className="h-8 min-w-8 px-2"
                onClick={() =>
                  table.setPageIndex((pageNumber as number) - 1)
                }
              >
                {pageNumber}
              </Button>
            )}
          </div>
        ))}

        <Button
          variant="outline"
          className="size-8 p-0"
          onClick={() => table.nextPage()}
          disabled={!table.getCanNextPage()}
        >
          <span className="sr-only">다음 페이지</span>
          <ChevronRight className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          className="size-8 p-0"
          onClick={() => table.setPageIndex(table.getPageCount() - 1)}
          disabled={!table.getCanNextPage()}
        >
          <span className="sr-only">마지막 페이지로</span>
          <ChevronsRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}
