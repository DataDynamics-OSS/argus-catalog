"use client"

import { DashboardHeader } from "@/components/dashboard-header"

import { AIAgentsDialogs } from "@/features/ai-agents/components/ai-agents-dialogs"
import { AIAgentsProvider } from "@/features/ai-agents/components/ai-agents-provider"
import { AIAgentsTableWrapper } from "@/features/ai-agents/components/ai-agents-table-wrapper"

export default function AIAgentsPage() {
  return (
    <AIAgentsProvider>
      <DashboardHeader title="AI Agent" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <AIAgentsTableWrapper />
      </div>
      <AIAgentsDialogs />
    </AIAgentsProvider>
  )
}
