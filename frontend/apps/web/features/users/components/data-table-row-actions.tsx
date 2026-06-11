/**
 * Data Table Row Actions component (Users).
 *
 * Renders a "..." (more) dropdown menu button in each row of the users table.
 * The menu provides per-row actions that open the corresponding dialog:
 *
 * - **Edit**: Opens the UsersActionDialog in edit mode for this user.
 *   Sets the current row in the provider context so the dialog can
 *   pre-populate the form with the user's existing data.
 *
 * - **Delete**: Opens the UsersDeleteDialog for this user.
 *   Styled with red text to indicate a destructive action.
 *   Sets the current row so the dialog knows which user to delete.
 *
 * The dropdown uses `modal={false}` to prevent focus trapping issues when
 * a dialog is subsequently opened from within the menu.
 */

"use client"

import { type Row } from "@tanstack/react-table"
import { KeyRound, MoreHorizontal, Trash2, UserPen } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import { type User } from "../data/schema"
import { useUsers } from "./users-provider"

type DataTableRowActionsProps = {
  /** The TanStack Table row instance containing the User data. */
  row: Row<User>
}

export function DataTableRowActions({ row }: DataTableRowActionsProps) {
  const { setOpen, setCurrentRow } = useUsers()
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className="flex h-8 w-8 p-0 data-[state=open]:bg-muted"
        >
          <MoreHorizontal className="h-4 w-4" />
          <span className="sr-only">메뉴 열기</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-[160px]">
        {/* Edit action: opens the user edit dialog */}
        <DropdownMenuItem
          onClick={() => {
            setCurrentRow(row.original)
            setOpen("edit")
          }}
        >
          수정
          <span className="ml-auto">
            <UserPen size={16} />
          </span>
        </DropdownMenuItem>
        {/* Change password action: opens the admin password-reset dialog */}
        <DropdownMenuItem
          onClick={() => {
            setCurrentRow(row.original)
            setOpen("changePassword")
          }}
        >
          비밀번호 변경
          <span className="ml-auto">
            <KeyRound size={16} />
          </span>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        {/* Delete action: opens the user delete confirmation dialog */}
        <DropdownMenuItem
          onClick={() => {
            setCurrentRow(row.original)
            setOpen("delete")
          }}
          className="text-red-500!"
        >
          삭제
          <span className="ml-auto">
            <Trash2 size={16} />
          </span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
