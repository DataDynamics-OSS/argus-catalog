/**
 * Users Data Table component.
 *
 * Renders the main users table with server-side pagination and filtering.
 * Built on TanStack React Table v8, this component integrates:
 *
 * - **Server-side pagination**: Page changes trigger backend API calls via the
 *   UsersProvider context. The `manualPagination` flag tells TanStack Table
 *   that pagination is handled externally.
 * - **Server-side filtering**: Filters (status, role, search text) are collected
 *   from the toolbar and sent to the backend when the Search button is clicked.
 *   The `manualFiltering` flag prevents client-side filtering.
 * - **Client-side sorting**: Column sorting is handled locally on the current
 *   page's data using TanStack Table's built-in sort models.
 * - **Row selection**: Checkbox-based multi-select with "select all" support.
 *   Selected rows are synced to the UsersProvider context for bulk actions.
 * - **Toolbar**: Search input, faceted filters (Status, Role), and action buttons.
 * - **Bulk actions bar**: Appears when rows are selected, showing available
 *   bulk operations (activate, deactivate, etc.).
 */

"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  type ColumnFiltersState,
  type SortingState,
  type VisibilityState,
  flexRender,
  getCoreRowModel,
  getFacetedRowModel,
  getFacetedUniqueValues,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table"

import { cn } from "@workspace/ui/lib/utils"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import {
  DataTableBulkActions,
  DataTablePagination,
  DataTableToolbar,
} from "@/components/data-table"
import { fetchOrganizations } from "../api"
import { roles } from "../data/data"
import { type User } from "../data/schema"
import { usersColumns as columns } from "./users-columns"
import { UsersPrimaryButtons } from "./users-primary-buttons"
import { useUsers } from "./users-provider"

type UsersTableProps = {
  /** Array of User objects to display (current page from the backend). */
  data: User[]
  /** Whether the data is currently being fetched from the backend. */
  isLoading?: boolean
}

export function UsersTable({ data, isLoading }: UsersTableProps) {
  const {
    setSelectedUsers,
    total,
    page,
    pageSize,
    setPage,
    setPageSize,
    searchUsers,
  } = useUsers()

  // Local table state managed by TanStack Table.
  const [rowSelection, setRowSelection] = useState({})
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({})
  const [sorting, setSorting] = useState<SortingState>([])
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([])
  // 소속 드롭다운 필터 옵션 (distinct 소속 목록)
  const [organizations, setOrganizations] = useState<string[]>([])
  useEffect(() => {
    fetchOrganizations().then(setOrganizations).catch(() => setOrganizations([]))
  }, [])

  /** Compute total page count from the backend's total record count. */
  const pageCount = useMemo(() => Math.ceil(total / pageSize), [total, pageSize])

  /**
   * TanStack Table instance configuration.
   *
   * Key settings:
   * - manualPagination: Pagination is handled by the backend; TanStack Table
   *   only tracks the current page index and size.
   * - manualFiltering: Filtering is handled by the backend; column filter state
   *   is used only to build the query parameters for the API call.
   * - onPaginationChange: Bridges TanStack Table's pagination updater with our
   *   provider's setPage/setPageSize. Detects whether the page or page size
   *   changed and calls the appropriate handler.
   */
  const table = useReactTable({
    data,
    columns,
    pageCount,
    state: {
      sorting,
      pagination: { pageIndex: page - 1, pageSize },
      rowSelection,
      columnFilters,
      columnVisibility,
    },
    enableRowSelection: true,
    manualPagination: true,
    manualFiltering: true,
    onPaginationChange: (updater) => {
      const next = typeof updater === "function"
        ? updater({ pageIndex: page - 1, pageSize })
        : updater
      if (next.pageSize !== pageSize) {
        setPageSize(next.pageSize)
      } else {
        setPage(next.pageIndex + 1)
      }
    },
    onColumnFiltersChange: setColumnFilters,
    onRowSelectionChange: setRowSelection,
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFacetedRowModel: getFacetedRowModel(),
    getFacetedUniqueValues: getFacetedUniqueValues(),
  })

  /**
   * Sync table row selection state to the provider's selectedUsers.
   *
   * Whenever the local rowSelection changes, extract the original User objects
   * from the selected rows and push them to the provider context. This allows
   * bulk action dialogs to know which users are selected.
   */
  useEffect(() => {
    setSelectedUsers(table.getSelectedRowModel().rows.map((row) => row.original))
  }, [rowSelection, setSelectedUsers, table])

  /**
   * Gather current filter values from column filters and trigger a server-side search.
   *
   * Extracts the status, role, and search text from TanStack Table's column filter
   * state, formats them for the backend API, and calls searchUsers() on the provider.
   *
   * Special handling:
   * - Status: Only sends a single value (not multi-select for backend compatibility).
   * - Role: Capitalizes the first letter to match the backend enum ("admin" -> "Admin").
   * - Search: Taken from the "username" column filter (used as a general search field).
   */
  const handleSearch = useCallback(() => {
    const statusVal = columnFilters.find((f) => f.id === "status")?.value
    const roleVal = columnFilters.find((f) => f.id === "role")?.value
    const orgVal = columnFilters.find((f) => f.id === "organization")?.value
    const searchVal = columnFilters.find((f) => f.id === "username")?.value

    let status: string | null = null
    if (Array.isArray(statusVal) && statusVal.length === 1) {
      status = statusVal[0]
    }

    let role = ""
    if (Array.isArray(roleVal) && roleVal.length === 1) {
      role = roleVal[0] as string
    }

    let organization = ""
    if (Array.isArray(orgVal) && orgVal.length === 1) {
      organization = orgVal[0] as string
    }

    const search = typeof searchVal === "string" ? searchVal : ""

    searchUsers({ status, role, search, organization })
  }, [columnFilters, searchUsers])

  /** Reset all column filters and the global filter to their default (empty) state. */
  const handleClear = useCallback(() => {
    table.resetColumnFilters()
    table.setGlobalFilter("")
  }, [table])

  return (
    <div className={cn("flex flex-1 flex-col gap-4")}>
      {/* Toolbar: search input, faceted filters (Status, Role), view options */}
      <DataTableToolbar
        table={table}
        searchPlaceholder="이름·이메일·소속 검색..."
        searchKey="username"
        filters={[
          {
            columnId: "status",
            title: "상태",
            options: [
              { label: "활성", value: "active" },
              { label: "비활성", value: "inactive" },
            ],
          },
          {
            columnId: "role",
            title: "역할",
            options: roles.map((role) => ({ ...role })),
          },
          {
            columnId: "organization",
            title: "소속",
            options: organizations.map((o) => ({ label: o, value: o })),
          },
        ]}
        onSearch={handleSearch}
        onClear={handleClear}
        extraActions={<UsersPrimaryButtons />}
      />

      {/* Main table with header and body */}
      <div className="overflow-hidden rounded-md border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id} className="group/row">
                {headerGroup.headers.map((header) => {
                  return (
                    <TableHead
                      key={header.id}
                      colSpan={header.colSpan}
                      className={cn(
                        "bg-background group-hover/row:bg-muted group-data-[state=selected]/row:bg-muted",
                        (header.column.columnDef.meta as { className?: string })
                          ?.className,
                        (
                          header.column.columnDef.meta as {
                            thClassName?: string
                          }
                        )?.thClassName
                      )}
                    >
                      {header.isPlaceholder
                        ? null
                        : flexRender(
                            header.column.columnDef.header,
                            header.getContext()
                          )}
                    </TableHead>
                  )
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {/* Loading state: show a centered loading message */}
            {isLoading ? (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="h-24 text-center"
                >
                  <p className="text-muted-foreground">Loading users...</p>
                </TableCell>
              </TableRow>
            ) : table.getRowModel().rows?.length ? (
              /* Data rows: render each row with selection state and hover styles */
              table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  data-state={row.getIsSelected() && "selected"}
                  className="group/row cursor-pointer"
                  onClick={() => row.toggleSelected()}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell
                      key={cell.id}
                      className={cn(
                        "bg-background group-hover/row:bg-muted group-data-[state=selected]/row:bg-muted",
                        (cell.column.columnDef.meta as { className?: string })
                          ?.className,
                        (
                          cell.column.columnDef.meta as {
                            tdClassName?: string
                          }
                        )?.tdClassName
                      )}
                    >
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext()
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              /* Empty state: no matching results */
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="h-24 text-center"
                >
                  No results.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {/* Pagination controls: page navigation and page size selector */}
      <DataTablePagination table={table} className="mt-auto" />

      {/* Bulk actions bar: appears when one or more rows are selected */}
      <DataTableBulkActions table={table} entityName="사용자" />
    </div>
  )
}
