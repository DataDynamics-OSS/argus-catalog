"use client"

import { useCallback, useEffect, useState } from "react"
import { AlertTriangle } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { notifyError, toast } from "@/lib/notify"

import { deleteDataset } from "../api"
import { DATASET_DELETE_DESCRIPTION, DatasetDeleteImpact } from "./dataset-delete-impact"
import { useDatasets } from "./datasets-provider"

const CONFIRM_KEYWORD = "삭제"

export function DatasetsDeleteDialog() {
  const { open, setOpen, currentRow, setCurrentRow, refreshDatasets } = useDatasets()
  const [confirmInput, setConfirmInput] = useState("")
  const [deleting, setDeleting] = useState(false)

  // 다이얼로그가 열릴 때마다 입력 초기화
  useEffect(() => {
    if (open === "delete") {
      setConfirmInput("")
    }
  }, [open])

  const close = useCallback(() => {
    setOpen(null)
    setCurrentRow(null)
    setConfirmInput("")
  }, [setOpen, setCurrentRow])

  const handleDelete = useCallback(async () => {
    if (!currentRow) return
    if (confirmInput.trim() !== CONFIRM_KEYWORD) return
    try {
      setDeleting(true)
      await deleteDataset(currentRow.id)
      toast.success(`데이터셋 "${currentRow.name}" 을 삭제했습니다.`)
      close()
      await refreshDatasets()
    } catch (e) {
      notifyError(e, "데이터셋을 삭제하지 못했습니다.")
    } finally {
      setDeleting(false)
    }
  }, [currentRow, confirmInput, close, refreshDatasets])

  const canDelete = confirmInput.trim() === CONFIRM_KEYWORD && !deleting

  return (
    <Dialog open={open === "delete"} onOpenChange={(o) => { if (!o) close() }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-5 w-5" />
            데이터셋 삭제
          </DialogTitle>
          <DialogDescription>
            {DATASET_DELETE_DESCRIPTION}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-1">
          {currentRow && (
            <div className="rounded-md border bg-muted/40 p-3 text-sm">
              <p className="font-medium">{currentRow.name}</p>
              <p className="mt-0.5 text-xs font-mono break-all">
                {currentRow.urn}
              </p>
            </div>
          )}
          {/* 함께 삭제되는 변경 요청·취소되는 결재 건수 (DEF-059) */}
          {open === "delete" && currentRow && <DatasetDeleteImpact datasetId={currentRow.id} />}
          <div className="space-y-1.5">
            <Label htmlFor="confirm-delete" className="text-sm">
              계속하려면 <span className="font-mono font-semibold text-destructive">{CONFIRM_KEYWORD}</span> 를 입력하세요.
            </Label>
            <Input
              id="confirm-delete"
              value={confirmInput}
              onChange={(e) => setConfirmInput(e.target.value)}
              placeholder={CONFIRM_KEYWORD}
              autoComplete="off"
              autoFocus
            />
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="outline" onClick={close} disabled={deleting}>
              취소
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={!canDelete}
            >
              {deleting ? "삭제 중…" : "삭제"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
