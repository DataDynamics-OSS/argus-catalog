/**
 * User Status Change (Activate/Deactivate) Dialog component.
 *
 * A confirmation dialog for bulk-activating or bulk-deactivating user accounts.
 * The dialog is reused for both operations — the `type` prop determines the
 * action text and which API function is called.
 *
 * - **Activate**: Sets selected users' status to "active", allowing them to log in.
 * - **Deactivate**: Sets selected users' status to "inactive", preventing login.
 *
 * The operation is performed on all users in the `selectedUsers` array by
 * calling the corresponding API function (activateUser/deactivateUser) for
 * each user in parallel using `Promise.all()`.
 *
 * After the operation completes, the user list is refreshed from the backend
 * to reflect the updated statuses.
 */

"use client"

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@workspace/ui/components/alert-dialog"
import { Button } from "@workspace/ui/components/button"
import { toast } from "sonner"
import { activateUser, deactivateUser } from "../api"
import { type User } from "../data/schema"
import { useUsers } from "./users-provider"

type UsersStatusDialogProps = {
  /** Whether the dialog is open. */
  open: boolean
  /** Callback to open or close the dialog. */
  onOpenChange: (open: boolean) => void
  /** Array of users selected for the bulk status change. */
  selectedUsers: User[]
  /** The type of status change operation. */
  type: "activate" | "deactivate"
}

export function UsersStatusDialog({
  open,
  onOpenChange,
  selectedUsers,
  type,
}: UsersStatusDialogProps) {
  const isActivate = type === "activate"
  const { refreshUsers } = useUsers()

  /**
   * Handle the status change confirmation.
   *
   * Determines the correct API function based on the `type` prop, then calls
   * it for each selected user in parallel. After all calls complete (or fail),
   * refreshes the user list and closes the dialog.
   */
  const handleConfirm = async () => {
    try {
      const fn = isActivate ? activateUser : deactivateUser
      await Promise.all(selectedUsers.map((u) => fn(u.id)))
      await refreshUsers()
      onOpenChange(false)
    } catch (err) {
      const action = isActivate ? "활성화" : "비활성화"
      const msg = err instanceof Error ? err.message : `사용자 ${action}에 실패했습니다.`
      toast.error(msg)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader className="text-start">
          <AlertDialogTitle>
            {isActivate ? "사용자 활성화" : "사용자 비활성화"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {isActivate
              ? `선택한 ${selectedUsers.length}명의 사용자를 활성화하시겠습니까?`
              : `선택한 ${selectedUsers.length}명의 사용자를 비활성화하시겠습니까?`}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter className="flex-row justify-end gap-2 sm:flex-row">
          <Button className="flex-1 sm:flex-none" onClick={handleConfirm}>
            확인
          </Button>
          <Button
            variant="outline"
            className="flex-1 sm:flex-none"
            onClick={() => onOpenChange(false)}
          >
            취소
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
