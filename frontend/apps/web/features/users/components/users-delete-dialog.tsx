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
 *
 * 소유 자산(데이터셋·API·에이전트·MCP·모델 등 편집 권한이 아이디로 묶인 자산)이 있으면 넘겨받을
 * 활성 사용자를 골라야 삭제할 수 있다 — 같은 아이디로 새 계정을 만들 때 권한이 이어지지 않도록.
 */

"use client"

import { useEffect, useState } from "react"
import { AlertTriangle } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@workspace/ui/components/alert"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { deleteUser, fetchOwnedAssets, fetchUsers, type OwnedAssets } from "../api"
import { type User } from "../data/schema"
import { useUsers } from "./users-provider"
import { errorMessage } from "@/lib/notify"

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
  const [owned, setOwned] = useState<OwnedAssets | null>(null)
  const [heirs, setHeirs] = useState<User[]>([])
  const [heir, setHeir] = useState("")

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setOwned(null)
    setHeir("")
    fetchOwnedAssets(currentRow.id)
      .then((o) => {
        if (cancelled) return
        setOwned(o)
        if (o.total > 0) {
          fetchUsers({ status: "active", pageSize: 0 })
            .then((r) => { if (!cancelled) setHeirs(r.items.filter((u) => u.id !== currentRow.id)) })
            .catch(() => {})
        }
      })
      .catch(() => { if (!cancelled) setOwned({ username: currentRow.username, total: 0, items: [] }) })
    return () => { cancelled = true }
  }, [open, currentRow.id, currentRow.username])

  const needsHeir = (owned?.total ?? 0) > 0
  const ready = owned !== null && value.trim() === currentRow.username && (!needsHeir || !!heir)

  const handleDelete = async () => {
    if (!ready) return
    setError(null)

    try {
      await deleteUser(currentRow.id, needsHeir ? heir : undefined)
      await refreshUsers()
      onOpenChange(false)
    } catch (err) {
      const msg = errorMessage(err) ?? "사용자를 삭제하지 못했습니다."
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
      disabled={!ready}
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

          {/* 소유 자산 이관 */}
          {needsHeir && owned && (
            <div className="space-y-2 rounded-md border p-3 text-sm">
              <p>
                이 사용자가 소유한 자산이 있습니다. 삭제하려면 넘겨받을 사용자를 고르세요.
              </p>
              <ul className="list-inside list-disc text-muted-foreground">
                {owned.items.filter((i) => i.count > 0).map((i) => (
                  <li key={i.key}>{i.label} {i.count}건</li>
                ))}
              </ul>
              <Select value={heir} onValueChange={setHeir}>
                <SelectTrigger aria-label="자산을 넘겨받을 사용자">
                  <SelectValue placeholder="자산을 넘겨받을 사용자" />
                </SelectTrigger>
                <SelectContent>
                  {heirs.map((u) => (
                    <SelectItem key={u.id} value={u.username}>
                      {u.lastName}{u.firstName} ({u.username})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

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
