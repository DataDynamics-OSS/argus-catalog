"use client"

import { ConfirmDialog } from "@/components/confirm-dialog"

type DeleteDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedPaths: string[]
  onConfirm: () => void
  isLoading?: boolean
}

export function DeleteDialog({
  open,
  onOpenChange,
  selectedPaths,
  onConfirm,
  isLoading,
}: DeleteDialogProps) {
  const count = selectedPaths.length
  const folderCount = selectedPaths.filter((k) => k.endsWith("/")).length
  const fileCount = count - folderCount

  const parts: string[] = []
  if (fileCount > 0) parts.push(`파일 ${fileCount}개`)
  if (folderCount > 0) parts.push(`폴더 ${folderCount}개`)
  const summary = parts.join(" 및 ")

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title="삭제"
      desc={
        <span className="text-sm">
          {summary}를 정말 삭제하시겠습니까?
          {folderCount > 0 && (
            <span className="block mt-1 text-destructive">
              폴더를 삭제하면 하위 파일과 디렉터리도 모두 함께 제거됩니다.
            </span>
          )}
          <span className="block mt-2 text-muted-foreground">
            이 작업은 되돌릴 수 없습니다.
          </span>
        </span>
      }
      destructive
      confirmText={isLoading ? "삭제 중..." : "삭제"}
      handleConfirm={onConfirm}
      isLoading={isLoading}
    />
  )
}
