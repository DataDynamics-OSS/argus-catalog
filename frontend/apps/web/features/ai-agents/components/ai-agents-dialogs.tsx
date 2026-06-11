"use client"

import { AIAgentsAddDialog } from "./ai-agents-add-dialog"
import { AIAgentsDeleteDialog } from "./ai-agents-delete-dialog"

export function AIAgentsDialogs() {
  return (
    <>
      <AIAgentsAddDialog />
      <AIAgentsDeleteDialog />
    </>
  )
}
