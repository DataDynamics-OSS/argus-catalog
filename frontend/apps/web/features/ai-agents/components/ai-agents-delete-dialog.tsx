"use client"

import { useCallback, useState } from "react"

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@workspace/ui/components/alert-dialog"
import { Button } from "@workspace/ui/components/button"

import { deleteAIAgent } from "../api"
import { useAIAgents } from "./ai-agents-provider"

export function AIAgentsDeleteDialog() {
  const {
    open,
    setOpen,
    deleteTargetName,
    setDeleteTargetName,
    setSelectedAgentName,
    refreshAgents,
  } = useAIAgents()
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleDelete = useCallback(async () => {
    if (!deleteTargetName) return
    setDeleting(true)
    setError(null)
    try {
      await deleteAIAgent(deleteTargetName)
      setOpen(null)
      setDeleteTargetName(null)
      setSelectedAgentName(null)
      await refreshAgents()
    } catch (e) {
      setError(e instanceof Error ? e.message : "삭제에 실패했습니다.")
    } finally {
      setDeleting(false)
    }
  }, [deleteTargetName, setOpen, setDeleteTargetName, setSelectedAgentName, refreshAgents])

  return (
    <AlertDialog open={open === "delete"} onOpenChange={() => setOpen(null)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>AI Agent 삭제</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="font-mono">{deleteTargetName}</span> 에이전트를 삭제합니다. 이
            작업은 되돌릴 수 없으며 연결된 도구·MCP·리니지·버전도 함께 삭제됩니다.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>취소</AlertDialogCancel>
          <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
            {deleting ? "삭제 중..." : "삭제"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
