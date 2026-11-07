"use client"

import React, { useCallback, useState } from "react"

import useDialogState from "@/hooks/use-dialog-state"

export type McpDialogType = "add-server" | "add-deployment" | "import"
export type McpTab = "servers" | "deployments" | "unregistered" | "control"

export type ServerFilters = { search: string; status: string; provider: string; framework: string }
export type DeploymentFilters = { search: string; environment: string; status: string; mutating: string }

type McpContextType = {
  open: McpDialogType | null
  setOpen: (v: McpDialogType | null) => void
  tab: McpTab
  setTab: (t: McpTab) => void
  /** 상세 뷰 대상 (둘 다 null = 목록). */
  selectedServer: string | null
  setSelectedServer: (n: string | null) => void
  selectedDeployment: string | null
  setSelectedDeployment: (n: string | null) => void
  /** 배포 등록 다이얼로그의 기본 서버(서버 상세에서 진입 시). */
  addDeploymentServer: string | null
  setAddDeploymentServer: (n: string | null) => void
  /** 목록 상태 — 상세 복귀 시 유지. */
  serverFilters: ServerFilters
  setServerFilters: React.Dispatch<React.SetStateAction<ServerFilters>>
  deploymentFilters: DeploymentFilters
  setDeploymentFilters: React.Dispatch<React.SetStateAction<DeploymentFilters>>
  serverPage: number
  setServerPage: (p: number) => void
  deploymentPage: number
  setDeploymentPage: (p: number) => void
  pageSize: number
  setPageSize: (s: number) => void
  /** 목록 재조회 트리거. */
  reloadKey: number
  reload: () => void
}

const McpContext = React.createContext<McpContextType | null>(null)

export function McpServersProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useDialogState<McpDialogType>(null)
  const [tab, setTab] = useState<McpTab>("servers")
  const [selectedServer, setSelectedServerState] = useState<string | null>(null)
  const [selectedDeployment, setSelectedDeploymentState] = useState<string | null>(null)
  const [addDeploymentServer, setAddDeploymentServer] = useState<string | null>(null)
  const [serverFilters, setServerFilters] = useState<ServerFilters>({
    search: "", status: "all", provider: "all", framework: "all",
  })
  const [deploymentFilters, setDeploymentFilters] = useState<DeploymentFilters>({
    search: "", environment: "all", status: "all", mutating: "all",
  })
  const [serverPage, setServerPage] = useState(1)
  const [deploymentPage, setDeploymentPage] = useState(1)
  const [pageSize, setPageSizeState] = useState(20)
  const [reloadKey, setReloadKey] = useState(0)

  // 서버 ↔ 배포 상세는 서로 이동할 수 있으므로 하나만 선택되도록 한다.
  const setSelectedServer = useCallback((n: string | null) => {
    setSelectedDeploymentState(null)
    setSelectedServerState(n)
  }, [])
  const setSelectedDeployment = useCallback((n: string | null) => {
    setSelectedServerState(null)
    setSelectedDeploymentState(n)
  }, [])
  const setPageSize = useCallback((s: number) => {
    setPageSizeState(s)
    setServerPage(1)
    setDeploymentPage(1)
  }, [])
  const reload = useCallback(() => setReloadKey((k) => k + 1), [])

  return (
    <McpContext
      value={{
        open,
        setOpen,
        tab,
        setTab,
        selectedServer,
        setSelectedServer,
        selectedDeployment,
        setSelectedDeployment,
        addDeploymentServer,
        setAddDeploymentServer,
        serverFilters,
        setServerFilters,
        deploymentFilters,
        setDeploymentFilters,
        serverPage,
        setServerPage,
        deploymentPage,
        setDeploymentPage,
        pageSize,
        setPageSize,
        reloadKey,
        reload,
      }}
    >
      {children}
    </McpContext>
  )
}

export const useMcpServers = () => {
  const ctx = React.useContext(McpContext)
  if (!ctx) throw new Error("useMcpServers must be used within <McpServersProvider>")
  return ctx
}
