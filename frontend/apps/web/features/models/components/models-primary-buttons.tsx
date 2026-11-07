"use client"

import { useState } from "react"
import { ArchiveRestore, Trash2 } from "lucide-react"
import { errorMessage, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import { useAuth } from "@/features/auth"
import { restoreModel } from "../api"
import { useModels } from "./models-provider"

/**
 * 목록 우측 버튼.
 * - 일반 보기: "모델 삭제"(소프트 삭제) — 선택한 모델을 모두 수정할 수 있을 때(관리자·등록자·소유자)만 활성
 * - 삭제된 모델 보기(관리자): "복원" · "영구 삭제"
 */
export function ModelsPrimaryButtons() {
  const { user } = useAuth()
  const {
    setOpen, models, rowSelection, selectedNames, setDeleteTargetNames,
    showDeleted, refreshModels, clearSelection,
  } = useModels()
  const [restoring, setRestoring] = useState(false)

  if (!user) return null

  if (showDeleted) {
    if (!user.is_admin) return null
    const restore = async () => {
      setRestoring(true)
      const failed: string[] = []
      for (const name of selectedNames) {
        try {
          await restoreModel(name)
        } catch (e) {
          failed.push(`${name}: ${errorMessage(e) ?? e}`)
        }
      }
      setRestoring(false)
      const ok = selectedNames.length - failed.length
      if (ok > 0) toast.success(`${ok}개 모델을 복원했습니다.`)
      if (failed.length) toast.error(`복원 실패 — ${failed.join(", ")}`)
      clearSelection()
      await refreshModels()
    }
    return (
      <>
        <Button size="sm" variant="outline" disabled={selectedNames.length === 0 || restoring} onClick={restore}>
          <ArchiveRestore className="mr-1 h-4 w-4" />
          {restoring ? "복원 중..." : "복원"}
        </Button>
        <Button
          size="sm"
          variant="destructive"
          disabled={selectedNames.length === 0 || restoring}
          onClick={() => {
            setDeleteTargetNames(selectedNames)
            setOpen("purge")
          }}
        >
          <Trash2 className="mr-1 h-4 w-4" />
          영구 삭제
        </Button>
      </>
    )
  }

  const selected = Object.keys(rowSelection)
    .filter((k) => rowSelection[k])
    .map((k) => models[Number(k)])
    .filter((m): m is NonNullable<typeof m> => !!m)
  const notEditable = selected.filter(
    (m) => !user.is_admin && ![m.owner, m.created_by].includes(user.username),
  )

  // 선택한 모델이 있을 때만 삭제 버튼을 보인다
  if (selectedNames.length === 0) return null

  return (
    <Button
      size="sm"
      variant="destructive"
      disabled={notEditable.length > 0}
      title={
        notEditable.length > 0
          ? `소유자 또는 관리자만 삭제할 수 있습니다: ${notEditable.map((m) => m.name).join(", ")}`
          : undefined
      }
      onClick={() => {
        setDeleteTargetNames(selectedNames)
        setOpen("delete")
      }}
    >
      <Trash2 className="mr-1 h-4 w-4" />
      {selectedNames.length}개 삭제
    </Button>
  )
}
