"use client"

import { useCallback, useState } from "react"
import { errorMessage, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { useAuth } from "@/features/auth"
import { deleteModel, hardDeleteModels } from "../api"
import { useModels } from "./models-provider"

const CONFIRM_TEXT = "DELETE MODELS"

/**
 * 모델 삭제 대화상자.
 * - open === "delete": 기본은 소프트 삭제(목록에서 숨김 — 버전·파일 보존, 관리자가 복원 가능).
 *   관리자는 "영구 삭제" 를 체크해 DB 기록과 아티팩트 파일까지 지울 수 있다.
 * - open === "purge" : 삭제된 모델 보기에서 영구 삭제 (관리자).
 * 영구 삭제는 되돌릴 수 없으므로 확인 문구 입력을 요구한다.
 */
export function ModelsDeleteDialog() {
  const { user } = useAuth()
  const { open, setOpen, deleteTargetNames, refreshModels, clearSelection } = useModels()
  const [confirmInput, setConfirmInput] = useState("")
  const [hardChecked, setHardChecked] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isOpen = open === "delete" || open === "purge"
  const hard = open === "purge" || (hardChecked && !!user?.is_admin)
  const count = deleteTargetNames.length
  const canSubmit = count > 0 && (!hard || confirmInput === CONFIRM_TEXT)

  const reset = () => {
    setConfirmInput("")
    setHardChecked(false)
    setError(null)
  }

  const handleDelete = useCallback(async () => {
    if (!canSubmit) return
    setDeleting(true)
    setError(null)
    try {
      if (hard) {
        const res = await hardDeleteModels(deleteTargetNames)
        toast.success(`${res.deleted.length}개 모델을 영구 삭제했습니다.`)
        if (res.not_found.length) toast.warning(`찾을 수 없음: ${res.not_found.join(", ")}`)
      } else {
        const failed: string[] = []
        for (const name of deleteTargetNames) {
          try {
            await deleteModel(name)
          } catch (e) {
            failed.push(`${name}: ${errorMessage(e) ?? e}`)
          }
        }
        const ok = count - failed.length
        if (ok > 0) toast.success(`${ok}개 모델을 삭제했습니다. 관리자는 '삭제된 모델'에서 복원할 수 있습니다.`)
        if (failed.length) {
          setError(`일부 모델을 삭제하지 못했습니다: ${failed.join(", ")}`)
          await refreshModels()
          return
        }
      }
      reset()
      setOpen(null)
      clearSelection()
      await refreshModels()
    } catch (e) {
      setError(errorMessage(e) ?? "모델을 삭제하지 못했습니다.")
    } finally {
      setDeleting(false)
    }
  }, [canSubmit, hard, deleteTargetNames, count, setOpen, clearSelection, refreshModels])

  function handleOpenChange(v: boolean) {
    if (!v) {
      reset()
      setOpen(null)
    }
  }

  return (
    <Dialog open={isOpen} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{open === "purge" ? "모델 영구 삭제" : "모델 삭제"}</DialogTitle>
          <DialogDescription>
            {hard
              ? "DB 기록(버전·메타데이터)과 저장소의 아티팩트 파일을 모두 지웁니다. 되돌릴 수 없습니다."
              : "목록에서 숨깁니다. 버전과 아티팩트 파일은 남아 있고, 관리자가 '삭제된 모델'에서 복원할 수 있습니다."}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <p className="text-sm">
            <strong>{count}</strong>개의 모델을 선택하셨습니다.
          </p>

          <div className="rounded-md border p-3 max-h-32 overflow-y-auto">
            <ul className="text-sm text-muted-foreground space-y-1">
              {deleteTargetNames.map((name) => (
                <li key={name} className="truncate">{name}</li>
              ))}
            </ul>
          </div>

          {open === "delete" && user?.is_admin && (
            <div className="flex items-start gap-2">
              <Checkbox
                id="model-hard-delete"
                checked={hardChecked}
                onCheckedChange={(v) => {
                  setHardChecked(!!v)
                  setConfirmInput("")
                }}
                disabled={deleting}
              />
              <Label htmlFor="model-hard-delete" className="text-sm font-normal leading-snug">
                영구 삭제 (관리자) — 아티팩트 파일까지 지우고 복원할 수 없습니다
              </Label>
            </div>
          )}

          {hard && (
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
          )}

          {error && <p className="text-sm text-destructive">{error}</p>}

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => handleOpenChange(false)} disabled={deleting}>
              취소
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={!canSubmit || deleting}>
              {deleting ? "삭제 중..." : hard ? "영구 삭제" : "삭제"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
