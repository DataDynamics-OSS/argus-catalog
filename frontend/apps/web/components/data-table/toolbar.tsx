"use client"

import { type Table } from "@tanstack/react-table"
import { Search, X } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { DataTableFacetedFilter } from "./faceted-filter"

type DataTableToolbarProps<TData> = {
  table: Table<TData>
  searchPlaceholder?: string
  searchKey?: string
  filters?: {
    columnId: string
    title: string
    options: {
      label: string
      value: string
      icon?: React.ComponentType<{ className?: string }>
      badgeClassName?: string
    }[]
  }[]
  onSearch?: () => void
  onClear?: () => void
  extraActions?: React.ReactNode
}

export function DataTableToolbar<TData>({
  table,
  searchPlaceholder = "검색...",
  searchKey,
  filters = [],
  onSearch,
  onClear,
  extraActions,
}: DataTableToolbarProps<TData>) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex flex-1 flex-col-reverse items-start gap-y-2 sm:flex-row sm:items-center sm:space-x-2">
        {searchKey ? (
          <Input
            placeholder={searchPlaceholder}
            value={
              (table.getColumn(searchKey)?.getFilterValue() as string) ?? ""
            }
            onChange={(event) =>
              table.getColumn(searchKey)?.setFilterValue(event.target.value)
            }
            onKeyDown={(event) => {
              if (event.key === "Enter" && onSearch) {
                onSearch()
              }
            }}
            className="h-8 w-[150px] lg:w-[250px]"
          />
        ) : (
          <Input
            placeholder={searchPlaceholder}
            value={table.getState().globalFilter ?? ""}
            onChange={(event) => table.setGlobalFilter(event.target.value)}
            className="h-8 w-[150px] lg:w-[250px]"
          />
        )}
        <div className="flex gap-x-2">
          {filters.map((filter) => {
            const column = table.getColumn(filter.columnId)
            if (!column) return null
            return (
              <DataTableFacetedFilter
                key={filter.columnId}
                column={column}
                title={filter.title}
                options={filter.options}
              />
            )
          })}
          {onClear && (
            <Button
              variant="outline"
              size="sm"
              onClick={onClear}
              className="h-8 px-3"
            >
              <X className="mr-1 h-4 w-4" />
              초기화
            </Button>
          )}
          {onSearch && (
            <Button
              variant="outline"
              size="sm"
              onClick={onSearch}
              className="h-8 px-3"
            >
              <Search className="mr-1 h-4 w-4" />
              검색
            </Button>
          )}
        </div>
      </div>
      {extraActions && (
        <div className="flex items-center gap-x-2">{extraActions}</div>
      )}
    </div>
  )
}
