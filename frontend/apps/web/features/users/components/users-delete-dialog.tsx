/**
 * User Delete Confirmation Dialog component.
 *
 * A destructive confirmation dialog that requires the admin to type the target
 * user's username before the delete operation is allowed. This two-step
 * confirmation prevents accidental deletion of user accounts.
 *
 * Flow:
 * 1. Dialog opens showing the target user's username and role.
 * 2. Admin must type the exact username into the confirmation input.
 * 3. The "Delete" button is disabled until the typed value matches.
 * 4. On confirmation, calls `deleteUser()` API and refreshes the user list.
 *
 * The dialog also displays a prominent warning alert to emphasize that
 * the deletion is permanent and cannot be undone.
 */

"use client"

import { useState } from "react"
import { AlertTriangle } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@workspace/ui/components/alert"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { deleteUser } from "../api"
import { type User } from "../data/schema"
import { useUsers } from "./users-provider"

type UsersDeleteDialogProps = {
  /** Whether the dialog is open. */
  open: boolean
  /** Callback to open or close the dialog. */
  onOpenChange: (open: boolean) => void
  /** The user to be deleted. */
  currentRow: User
}

export function UsersDeleteDialog({
  open,
  onOpenChange,
  currentRow,
}: UsersDeleteDialogProps) {
  /** Tracks the admin's typed confirmation text (must match the username). */
  const [value, setValue] = useState("")
  const { refreshUsers } = useUsers()

  /**
   * Handle the delete confirmation.
   *
   * Only proceeds if the typed value exactly matches the target user's username.
   * Calls the deleteUser API, refreshes the list, and closes the dialog.
   */
  const [error, setError] = useState<string | null>(null)

  const handleDelete = async () => {
    if (value.trim() !== currentRow.username) return
    setError(null)

    try {
      await deleteUser(currentRow.id)
      await refreshUsers()
      onOpenChange(false)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "사용자 삭제에 실패했습니다."
      setError(msg)
    }
  }

  // 역할 코드 → 화면 표시용 한글 이름
  const roleLabel =
    currentRow.role === "argus-admin"
      ? "관리자"
      : currentRow.role === "argus-superuser"
        ? "슈퍼유저"
        : "사용자"

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      handleConfirm={handleDelete}
      disabled={value.trim() !== currentRow.username}
      title={
        <span className="text-destructive">
          <AlertTriangle
            className="me-1 inline-block stroke-destructive"
            size={18}
          />{" "}
          사용자 삭제
        </span>
      }
      desc={
        <div className="space-y-4">
          <p className="mb-2">
            <span className="font-bold">{currentRow.username}</span> 사용자를
            정말 삭제하시겠습니까?
            <br />
            <span className="font-bold">{roleLabel}</span> 역할을 가진 사용자가
            시스템에서 영구히 제거됩니다. 이 작업은 되돌릴 수 없습니다.
          </p>

          {/* 사용자명 확인 입력 */}
          <Label className="my-2">
            <span className="min-w-[4.5rem] shrink-0">사용자명:</span>
            <Input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="확인을 위해 사용자명을 입력하세요."
            />
          </Label>

          {/* 에러 메시지 */}
          {error && (
            <Alert variant="destructive">
              <AlertTitle>오류</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {/* 비가역 작업 경고 */}
          {!error && (
            <Alert variant="destructive">
              <AlertTitle>주의!</AlertTitle>
              <AlertDescription>
                이 작업은 되돌릴 수 없으니 신중히 진행해주세요.
              </AlertDescription>
            </Alert>
          )}
        </div>
      }
      confirmText="삭제"
      destructive
    />
  )
}
