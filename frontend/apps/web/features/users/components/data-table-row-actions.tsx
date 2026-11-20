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
import { KeyRound, LockOpen, MoreHorizontal, Pencil, Trash2 } from "lucide-react"

import { MenuItemContent } from "@/components/menu-item-content"
import { notifyError, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import { useAuth } from "@/features/auth"
import { unlockUser } from "../api"
import { type User } from "../data/schema"
import { useCanAssignRole } from "../use-role-options"
import { useUsers } from "./users-provider"

type DataTableRowActionsProps = {
  /** The TanStack Table row instance containing the User data. */
  row: Row<User>
}

export function DataTableRowActions({ row }: DataTableRowActionsProps) {
  const { setOpen, setCurrentRow, refreshUsers } = useUsers()
  // 본인 계정은 삭제할 수 없다(서버도 400) — 다른 관리자가 처리
  const { user, authType } = useAuth()
  // Keycloak 모드에서는 비밀번호를 Keycloak 에서 관리(서버도 400)
  const local = authType === "local"
  const isSelf = !!user && user.username === row.original.username
  // 사용자 관리를 위임받은 사람(관리자 아님)은 자기가 줄 수 없는 역할(관리자 · 슈퍼유저 등)의 계정을 다룰 수 없다
  // — 본인은 예외(서버도 403, TODO-123 · DEF-163)
  const canAssignRole = useCanAssignRole(!!user?.is_admin, user?.username)
  // 로그인 실패 반복으로 잠긴 계정 — 로컬 인증에서만 잠긴다
  const locked = local && !!row.original.lockedUntil
  const handleUnlock = async () => {
    try {
      await unlockUser(row.original.id)
      toast.success(`${row.original.username} 계정의 잠금을 해제했습니다.`)
      await refreshUsers()
    } catch (e) {
      notifyError(e, "계정 잠금을 해제하지 못했습니다.")
    }
  }
  if (!isSelf && !canAssignRole(row.original.role)) return null
  return (
    // 행을 누르면 선택되므로(users-table) 메뉴 단추 · 항목 클릭이 행으로 올라가지 않게 막는다
    // — 메뉴 항목은 포털에 그려져도 React 이벤트는 이 트리를 따라 올라온다(DEF-173)
    <div onClick={(e) => e.stopPropagation()}>
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className="flex h-8 w-8 p-0 data-[state=open]:bg-muted"
        >
          <MoreHorizontal className="h-4 w-4" />
          <span className="sr-only">{row.original.username} 메뉴</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[160px]">
        {/* Edit action: opens the user edit dialog */}
        <DropdownMenuItem
          onClick={() => {
            setCurrentRow(row.original)
            setOpen("edit")
          }}
        >
          <Pencil /> 편집
        </DropdownMenuItem>
        {/* Change password action: opens the admin password-reset dialog */}
        {local && (
          <DropdownMenuItem
            onClick={() => {
              setCurrentRow(row.original)
              setOpen("changePassword")
            }}
          >
            <KeyRound /> 비밀번호 변경
          </DropdownMenuItem>
        )}
        {locked && (
          <DropdownMenuItem onClick={handleUnlock}>
            <LockOpen /> 잠금 해제
          </DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        {/* Delete action: opens the user delete confirmation dialog */}
        <DropdownMenuItem
          variant="destructive"
          disabled={isSelf}
          onClick={() => {
            setCurrentRow(row.original)
            setOpen("delete")
          }}
        >
          <MenuItemContent icon={Trash2} label="삭제" hint={isSelf ? "본인 계정은 삭제할 수 없습니다." : undefined} />
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
    </div>
  )
}
