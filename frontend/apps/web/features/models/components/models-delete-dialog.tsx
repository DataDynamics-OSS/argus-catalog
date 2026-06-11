"use client"

import { useCallback, useState } from "react"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { hardDeleteModels } from "../api"
import { useModels } from "./models-provider"

const CONFIRM_TEXT = "DELETE MODELS"

export function ModelsDeleteDialog() {
  const { open, setOpen, deleteTargetNames, refreshModels, clearSelection } = useModels()
  const [confirmInput, setConfirmInput] = useState("")
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isConfirmed = confirmInput === CONFIRM_TEXT
  const count = deleteTargetNames.length

  const handleDelete = useCallback(async () => {
    if (!isConfirmed || count === 0) return
    setDeleting(true)
    setError(null)
    try {
      await hardDeleteModels(deleteTargetNames)
      setConfirmInput("")
      setOpen(null)
      clearSelection()
      await refreshModels()
    } catch (e) {
      setError(e instanceof Error ? e.message : "모델 삭제에 실패했습니다.")
    } finally {
      setDeleting(false)
    }
  }, [isConfirmed, count, deleteTargetNames, setOpen, clearSelection, refreshModels])

  function handleOpenChange(v: boolean) {
    if (!v) {
      setConfirmInput("")
      setError(null)
    }
    if (!v) setOpen(null)
  }

  return (
    <Dialog open={open === "delete"} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>모델 삭제</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <p className="text-sm">
            <strong>{count}</strong>개의 모델을 선택하셨습니다.
            정말 삭제하시겠습니까? 삭제는 되돌릴 수 없습니다.
          </p>

          <div className="rounded-md border p-3 max-h-32 overflow-y-auto">
            <ul className="text-sm text-muted-foreground space-y-1">
              {deleteTargetNames.map((name) => (
                <li key={name} className="truncate">{name}</li>
              ))}
            </ul>
          </div>

          <div className="grid gap-2">
            <p className="text-sm text-muted-foreground">
              확인을 위해 <strong>{CONFIRM_TEXT}</strong> 를 입력하세요:
            </p>
            <Input
              value={confirmInput}
              onChange={(e) => setConfirmInput(e.target.value)}
              placeholder={CONFIRM_TEXT}
              disabled={deleting}
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={deleting}
            >
              취소
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={!isConfirmed || deleting}
            >
              {deleting ? "삭제 중..." : "삭제"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
