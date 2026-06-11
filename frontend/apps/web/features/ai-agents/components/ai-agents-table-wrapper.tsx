"use client"

import { useEffect } from "react"
import { useSearchParams } from "next/navigation"

import { useAIAgents } from "./ai-agents-provider"
import { AIAgentsDetail } from "./ai-agents-detail"
import { AIAgentsTable } from "./ai-agents-table"

export function AIAgentsTableWrapper() {
  const { agents, isLoading, selectedAgentName, setSelectedAgentName } = useAIAgents()
  const params = useSearchParams()

  // 검색 결과 등 외부에서 ?agent=<name> 으로 진입하면 해당 에이전트 상세를 연다(최초 1회).
  useEffect(() => {
    const a = params.get("agent")
    if (a) setSelectedAgentName(a)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (selectedAgentName) {
    return <AIAgentsDetail agentName={selectedAgentName} />
  }

  return <AIAgentsTable data={agents} isLoading={isLoading} />
}
