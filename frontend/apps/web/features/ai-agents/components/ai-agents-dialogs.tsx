"use client"

import { AIAgentsAddDialog } from "./ai-agents-add-dialog"
import { AIAgentsBulkDialog } from "./ai-agents-bulk-dialog"
import { AIAgentsDeleteDialog } from "./ai-agents-delete-dialog"
import { AIAgentsImportDialog } from "./ai-agents-import-dialog"

export function AIAgentsDialogs() {
  return (
    <>
      <AIAgentsAddDialog />
      <AIAgentsDeleteDialog />
      <AIAgentsImportDialog />
      <AIAgentsBulkDialog />
    </>
  )
}
