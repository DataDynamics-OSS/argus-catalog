"use client"

import { useState } from "react"
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table"
import { LayoutGrid, Table2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { cn } from "@workspace/ui/lib/utils"

import { type DatasetSummary } from "../data/schema"
import { DatasetsCardView } from "./datasets-card-view"
import { datasetsColumns } from "./datasets-columns"

type DatasetsGridProps = {
  data: DatasetSummary[]
  emptyText?: string
  /** 지정 시 행 끝에 "제거" 액션 컬럼을 추가한다(예: 분류 매핑 해제). */
  onRemove?: (id: number) => void
  removeLabel?: string
  /** 체크박스 다중 선택 모드. selectedIds + onToggleSelect 가 함께 주어지면 선택 컬럼을 앞에 붙인다. */
  selectedIds?: Set<number>
  onToggleSelect?: (id: number) => void
  onToggleSelectAll?: (checked: boolean) => void
}

/**
 * 데이터셋 목록과 동일한 그리드(컬럼/셀)를 그대로 재사용하는 프레젠테이셔널 테이블.
 * DatasetsProvider 에 의존하는 actions(드롭다운) 컬럼은 제외하고, 필요 시 onRemove/선택 컬럼을 덧붙인다.
 */
export function DatasetsGrid({
  data,
  emptyText = "데이터셋이 없습니다.",
  onRemove,
  removeLabel = "제거",
  selectedIds,
  onToggleSelect,
  onToggleSelectAll,
}: DatasetsGridProps) {
  const [viewMode, setViewMode] = useState<"table" | "card">("table")

  const selectable = !!(selectedIds && onToggleSelect)
  // 선택 모드에서는 카드 뷰에 체크박스가 없으므로 토글을 숨기고 테이블로 고정.
  const effectiveView = selectable ? "table" : viewMode

  const baseCols = datasetsColumns.filter(
    (c) => (c as { id?: string }).id !== "actions",
  )

  const selectCol: ColumnDef<DatasetSummary> = {
    id: "select",
    header: () => (
      <Checkbox
        checked={data.length > 0 && data.every((d) => selectedIds!.has(d.id))}
        onCheckedChange={(v) => onToggleSelectAll?.(!!v)}
        aria-label="전체 선택"
      />
    ),
    cell: ({ row }) => (
      <Checkbox
        checked={selectedIds!.has(row.original.id)}
        onCheckedChange={() => onToggleSelect!(row.original.id)}
        aria-label="행 선택"
      />
    ),
    meta: { className: "w-[40px] text-center" },
  }

  const removeCol: ColumnDef<DatasetSummary> = {
    id: "remove",
    cell: ({ row }) => (
      <Button
        size="sm"
        variant="ghost"
        className="h-6 text-destructive"
        onClick={() => onRemove?.(row.original.id)}
      >
        {removeLabel}
      </Button>
    ),
    meta: { className: "w-[60px] text-center" },
  }

  const columns: ColumnDef<DatasetSummary>[] = [
    ...(selectable ? [selectCol] : []),
    ...baseCols,
    ...(onRemove ? [removeCol] : []),
  ]

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="flex flex-col gap-2">
      {/* 카드/테이블 뷰 토글 (데이터셋 메뉴와 동일한 모양, 로컬 상태). 선택 모드에서는 숨김. */}
      {!selectable && (
        <div className="flex justify-end">
          <div className="inline-flex items-center rounded-md border bg-background p-0.5">
            <Button
              type="button"
              variant={viewMode === "table" ? "secondary" : "ghost"}
              size="sm"
              aria-label="테이블 뷰"
              title="테이블 뷰"
              className="h-7 w-7 p-0"
              onClick={() => setViewMode("table")}
            >
              <Table2 className="h-4 w-4" />
            </Button>
            <Button
              type="button"
              variant={viewMode === "card" ? "secondary" : "ghost"}
              size="sm"
              aria-label="카드 뷰"
              title="카드 뷰"
              className="h-7 w-7 p-0"
              onClick={() => setViewMode("card")}
            >
              <LayoutGrid className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}

      {effectiveView === "card" ? (
        <DatasetsCardView rows={data} onRemove={onRemove} removeLabel={removeLabel} />
      ) : (
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
                    (header.column.columnDef.meta as { className?: string })?.className,
                  )}
                >
                  {header.isPlaceholder
                    ? null
                    : flexRender(header.column.columnDef.header, header.getContext())}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length ? (
            table.getRowModel().rows.map((row) => (
              <TableRow key={row.id} className="group/row">
                {row.getVisibleCells().map((cell) => (
                  <TableCell
                    key={cell.id}
                    className={cn(
                      "bg-background group-hover/row:bg-muted",
                      (cell.column.columnDef.meta as { className?: string })?.className,
                    )}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell colSpan={columns.length} className="h-24 text-center text-muted-foreground">
                {emptyText}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
      )}
    </div>
  )
}
