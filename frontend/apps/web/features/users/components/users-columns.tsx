/**
 * TanStack Table column definitions for the Users table.
 *
 * Defines how each column in the users data table is rendered, sorted, and filtered.
 * Columns include: selection checkbox, username, full name, email, phone number,
 * status badge, creation date, role with icon, and row actions.
 *
 * This array is passed to `useReactTable()` in `users-table.tsx` and controls
 * the entire table layout and behavior.
 */

"use client"

import { type ColumnDef } from "@tanstack/react-table"

import { cn } from "@workspace/ui/lib/utils"
import { Badge } from "@workspace/ui/components/badge"
import { Checkbox } from "@workspace/ui/components/checkbox"
import { DataTableColumnHeader } from "@/components/data-table/column-header"
import { LongText } from "@/components/long-text"
import { callTypes, roles, statusLabels } from "../data/data"
import { type User } from "../data/schema"
import { DataTableRowActions } from "./data-table-row-actions"

export const usersColumns: ColumnDef<User>[] = [
  /**
   * Row selection checkbox column.
   *
   * Header: "Select all" checkbox that toggles selection for all rows on the page.
   *         Shows an indeterminate state when only some rows are selected.
   * Cell:   Individual row checkbox. Click events are stopped from propagating
   *         to prevent the row click handler from interfering.
   * Fixed width (48px), always visible (cannot be hidden), not sortable.
   */
  {
    id: "select",
    header: ({ table }) => (
      <Checkbox
        checked={
          table.getIsAllPageRowsSelected() ||
          (table.getIsSomePageRowsSelected() && "indeterminate")
        }
        onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
        aria-label="전체 선택"
        className="translate-y-[2px]"
      />
    ),
    meta: {
      className: cn("w-12 max-w-12 max-md:sticky start-0 z-10 rounded-tl-[inherit]"),
    },
    cell: ({ row }) => (
      <div onClick={(e) => e.stopPropagation()}>
        <Checkbox
          checked={row.getIsSelected()}
          onCheckedChange={(value) => row.toggleSelected(!!value)}
          aria-label="행 선택"
          className="translate-y-[2px]"
        />
      </div>
    ),
    enableSorting: false,
    enableHiding: false,
  },
  /**
   * Username column.
   *
   * Displays the user's unique login identifier. Sortable via column header.
   * Uses LongText component to truncate long usernames with ellipsis.
   * Always visible (cannot be hidden).
   */
  {
    accessorKey: "username",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="사용자명" className="justify-center" />
    ),
    cell: ({ row }) => (
      <LongText className="max-w-36 text-center">{row.getValue("username")}</LongText>
    ),
    enableHiding: false,
  },
  /**
   * Full Name column (computed from firstName + lastName).
   *
   * This is a virtual column (no direct accessorKey) that combines the user's
   * first and last names. Custom sorting function compares the concatenated
   * full names alphabetically using locale-aware comparison.
   */
  {
    id: "fullName",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="이름" className="justify-center" />
    ),
    cell: ({ row }) => {
      const { firstName, lastName } = row.original
      // 한국 이름 표기 관례에 맞춰 성+이름(공백 없음). sidebar-user.tsx 의
      // displayName 과 동일한 방식.
      const fullName = `${lastName}${firstName}`
      return <LongText className="max-w-36 text-center">{fullName}</LongText>
    },
    sortingFn: (rowA, rowB) => {
      const a = `${rowA.original.lastName}${rowA.original.firstName}`
      const b = `${rowB.original.lastName}${rowB.original.firstName}`
      return a.localeCompare(b)
    },
    meta: { className: "w-36" },
  },
  /**
   * Email column.
   *
   * Displays the user's email address. Sortable and always visible.
   * Uses text-nowrap to prevent the email from wrapping to multiple lines.
   */
  {
    accessorKey: "email",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="이메일" className="justify-center" />
    ),
    cell: ({ row }) => (
      <div className="text-center text-nowrap">{row.getValue("email")}</div>
    ),
    enableHiding: false,
  },
  /**
   * 소속(조직) column. 비어 있으면 "-".
   */
  {
    accessorKey: "organization",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="소속" className="justify-center" />
    ),
    cell: ({ row }) => (
      <div className="text-center">{(row.getValue("organization") as string) || "-"}</div>
    ),
    enableSorting: false,
  },
  /**
   * 소속 부서 column. 비어 있으면 "-".
   */
  {
    accessorKey: "department",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="소속 부서" className="justify-center" />
    ),
    cell: ({ row }) => (
      <div className="text-center">{(row.getValue("department") as string) || "-"}</div>
    ),
    enableSorting: false,
  },
  /**
   * Phone Number column.
   *
   * Displays the user's contact phone number. Not sortable since phone number
   * sorting is rarely meaningful. Can be hidden via column visibility options.
   */
  {
    accessorKey: "phoneNumber",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="전화번호" className="justify-center" />
    ),
    cell: ({ row }) => <div className="text-center">{row.getValue("phoneNumber")}</div>,
    enableSorting: false,
  },
  /**
   * Status column.
   *
   * Renders a colored Badge component showing "active" or "inactive".
   * Badge color is determined by the `callTypes` map from `data.ts`:
   *   - active   → primary color (blue/brand)
   *   - inactive → destructive color (red)
   *
   * Supports faceted filtering: the toolbar can filter by selected status values.
   * Custom filterFn checks if the row's status is included in the selected values.
   * Always visible, not sortable.
   */
  {
    accessorKey: "status",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="상태" className="justify-center" />
    ),
    cell: ({ row }) => {
      const { status } = row.original
      const badgeColor = callTypes.get(status)
      return (
        <div className="flex justify-center">
          <Badge variant="outline" className={cn(badgeColor)}>
            {statusLabels[status] ?? status}
          </Badge>
        </div>
      )
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue(id))
    },
    enableHiding: false,
    enableSorting: false,
  },
  /**
   * Created At column.
   *
   * Displays the account creation date in ISO format (YYYY-MM-DD).
   * Sortable to allow ordering users by registration date.
   * Always visible.
   */
  {
    accessorKey: "createdAt",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="생성일" className="justify-center" />
    ),
    cell: ({ row }) => {
      const date = row.getValue("createdAt") as Date
      const formatted = date.toISOString().slice(0, 10)
      return <div className="text-center text-sm text-nowrap">{formatted}</div>
    },
    enableHiding: false,
  },
  /**
   * Role column.
   *
   * Displays the user's role with an associated Lucide icon:
   *   - Admin → UserCheck icon
   *   - User  → Users icon
   *
   * The role definition is looked up from the `roles` array in `data.ts`.
   * Supports faceted filtering to show only admins or only regular users.
   * Not sortable, always visible.
   */
  {
    accessorKey: "role",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="역할" className="justify-center" />
    ),
    cell: ({ row }) => {
      const { role } = row.original
      const userType = roles.find(({ value }) => value === role)

      if (!userType) {
        return null
      }

      return (
        <div className="flex items-center justify-center">
          <span className="text-sm">{userType.label}</span>
        </div>
      )
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue(id))
    },
    enableSorting: false,
    enableHiding: false,
  },
  /**
   * Row Actions column.
   *
   * Renders a dropdown menu with per-row actions (Edit, Delete).
   * The DataTableRowActions component handles opening the appropriate dialog
   * and setting the current row context in the provider.
   */
  {
    id: "actions",
    cell: DataTableRowActions,
  },
]
