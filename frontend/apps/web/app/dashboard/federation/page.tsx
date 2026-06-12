"use client"

import { useState } from "react"
import { Activity, FolderTree, Network, Search, Share2 } from "lucide-react"

import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@workspace/ui/components/tabs"
import { DashboardHeader } from "@/components/dashboard-header"
import { FederationBrowsePanel } from "@/features/federation/browse-panel"
import { FederationExportPolicyPanel } from "@/features/federation/export-policy-panel"
import { FederationInstancesPanel } from "@/features/federation/instances-panel"
import { FederationObservabilityPanel } from "@/features/federation/observability-panel"
import { FederationSearchPanel } from "@/features/federation/search-panel"
import { useAuth } from "@/features/auth"

export default function FederationPage() {
  const { user } = useAuth()
  const [tab, setTab] = useState("search")

  return (
    <>
      <DashboardHeader title="페더레이션" />
      <div className="flex min-h-0 flex-1 flex-col gap-4 p-4">
        <Tabs
          value={tab}
          onValueChange={setTab}
          className="flex min-h-0 flex-1 flex-col"
        >
          <TabsList>
            <TabsTrigger value="search">
              <Search className="mr-1 h-3.5 w-3.5" /> 통합 검색
            </TabsTrigger>
            <TabsTrigger value="browse">
              <FolderTree className="mr-1 h-3.5 w-3.5" /> 탐색
            </TabsTrigger>
            {user?.is_admin && (
              <TabsTrigger value="instances">
                <Network className="mr-1 h-3.5 w-3.5" /> 인스턴스 관리
              </TabsTrigger>
            )}
            {user?.is_admin && (
              <TabsTrigger value="observability">
                <Activity className="mr-1 h-3.5 w-3.5" /> 관측성
              </TabsTrigger>
            )}
            {user?.is_admin && (
              <TabsTrigger value="export-policy">
                <Share2 className="mr-1 h-3.5 w-3.5" /> 노출 설정
              </TabsTrigger>
            )}
          </TabsList>
          <TabsContent
            value="search"
            className="mt-4 flex min-h-0 flex-1 flex-col"
          >
            <FederationSearchPanel />
          </TabsContent>
          <TabsContent
            value="browse"
            className="mt-4 flex min-h-0 flex-1 flex-col"
          >
            <FederationBrowsePanel />
          </TabsContent>
          {user?.is_admin && (
            <TabsContent
              value="instances"
              className="mt-4 flex min-h-0 flex-1 flex-col"
            >
              <FederationInstancesPanel />
            </TabsContent>
          )}
          {user?.is_admin && (
            <TabsContent
              value="observability"
              className="mt-4 flex min-h-0 flex-1 flex-col"
            >
              <FederationObservabilityPanel />
            </TabsContent>
          )}
          {user?.is_admin && (
            <TabsContent
              value="export-policy"
              className="mt-4 flex min-h-0 flex-1 flex-col"
            >
              <FederationExportPolicyPanel />
            </TabsContent>
          )}
        </Tabs>
      </div>
    </>
  )
}
