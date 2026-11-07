"use client"

import { useEffect } from "react"
import { useSearchParams } from "next/navigation"

import { McpDeploymentDetail } from "./mcp-deployment-detail"
import { McpList } from "./mcp-list"
import { useMcpServers } from "./mcp-provider"
import { McpServerDetail } from "./mcp-server-detail"

export function McpTableWrapper() {
  const { selectedServer, selectedDeployment, setSelectedServer, setSelectedDeployment } = useMcpServers()
  const params = useSearchParams()

  // 외부(검색·AI Agent 상세)에서 ?server=<name> / ?deployment=<name> 으로 진입하면 상세를 연다(최초 1회).
  useEffect(() => {
    const d = params.get("deployment")
    const s = params.get("server")
    if (d) setSelectedDeployment(d)
    else if (s) setSelectedServer(s)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (selectedDeployment) return <McpDeploymentDetail name={selectedDeployment} />
  if (selectedServer) return <McpServerDetail name={selectedServer} />
  return <McpList />
}
