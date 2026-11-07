"use client"

import { DashboardHeader } from "@/components/dashboard-header"

import { McpDialogs } from "@/features/mcp-servers/components/mcp-dialogs"
import { McpServersProvider } from "@/features/mcp-servers/components/mcp-provider"
import { McpTableWrapper } from "@/features/mcp-servers/components/mcp-table-wrapper"

export default function McpServersPage() {
  return (
    <McpServersProvider>
      <DashboardHeader title="MCP Server" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <McpTableWrapper />
      </div>
      <McpDialogs />
    </McpServersProvider>
  )
}
