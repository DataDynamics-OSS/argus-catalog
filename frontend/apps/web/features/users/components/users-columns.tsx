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
import { Lock } from "lucide-react"

import { cn } from "@workspace/ui/lib/utils"
import { Badge } from "@workspace/ui/components/badge"
import { Checkbox } from "@workspace/ui/components/checkbox"
import { DataTableColumnHeader } from "@/components/data-table/column-header"
import { LongText } from "@/components/long-text"
import { callTypes, roles, statusLabels } from "../data/data"
import { type User } from "../data/schema"
import { DataTableRowActions } from "./data-table-row-actions"

/** 로컬 시간 "YYYY-MM-DD HH:mm". */
function formatDateTime(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

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
   * first and last names. 정렬은 서버가 성·이름 순으로 한다.
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
   * 잠긴 계정(로그인 실패 반복)은 "잠김" 배지를 함께 표시한다. 서버 정렬 가능.
   */
  {
    accessorKey: "status",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="상태" className="justify-center" />
    ),
    cell: ({ row }) => {
      const { status, lockedUntil } = row.original
      const badgeColor = callTypes.get(status)
      return (
        <div className="flex justify-center gap-1">
          <Badge variant="outline" className={cn(badgeColor)}>
            {statusLabels[status] ?? status}
          </Badge>
          {lockedUntil && (
            <Badge
              variant="outline"
              className="border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-300"
              title={`로그인 실패가 반복되어 ${formatDateTime(lockedUntil)} 까지 잠겼습니다`}
            >
              <Lock className="size-3" />
              잠김
            </Badge>
          )}
        </div>
      )
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue(id))
    },
    enableHiding: false,
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
   * 마지막 로그인 — 로컬 로그인 성공 또는 SSO 로그인(JIT) 시각. 없으면 "-".
   * 서버 정렬 시 로그인 기록이 없는 사용자는 방향과 무관하게 맨 뒤에 온다.
   */
  {
    accessorKey: "lastLoginAt",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="마지막 로그인" className="justify-center" />
    ),
    cell: ({ row }) => {
      const date = row.original.lastLoginAt
      return (
        <div className="text-center text-sm text-nowrap tabular-nums text-muted-foreground">
          {date ? formatDateTime(date) : "-"}
        </div>
      )
    },
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
   * 서버 정렬 가능 (역할 코드 순), always visible.
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
