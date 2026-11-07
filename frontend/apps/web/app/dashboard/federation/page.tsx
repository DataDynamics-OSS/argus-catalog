"use client"

import { Suspense, useCallback } from "react"
import {
  Activity,
  FolderTree,
  Loader2,
  Network,
  Search,
  Share2,
} from "lucide-react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"

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

const PUBLIC_TABS = ["search", "browse"]
const ADMIN_TABS = ["instances", "observability", "export-policy"]

/**
 * 탭·검색어를 URL(?tab=&q=)에 보관 — 상세 화면에서 뒤로 오거나 새로고침·링크
 * 공유 시에도 같은 탭과 검색 결과로 돌아온다.
 */
function FederationTabs() {
  const { user } = useAuth()
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()

  const allowed = user?.is_admin ? [...PUBLIC_TABS, ...ADMIN_TABS] : PUBLIC_TABS
  const requested = params.get("tab") ?? "search"
  const tab = allowed.includes(requested) ? requested : "search"
  const q = params.get("q") ?? ""

  const setParams = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(params.toString())
      for (const [k, v] of Object.entries(patch)) {
        if (v) next.set(k, v)
        else next.delete(k)
      }
      const qs = next.toString()
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false })
    },
    [params, pathname, router]
  )

  return (
    <Tabs
      value={tab}
      onValueChange={(v) => setParams({ tab: v === "search" ? null : v })}
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
      <TabsContent value="search" className="mt-4 flex min-h-0 flex-1 flex-col">
        <FederationSearchPanel
          initialQuery={q}
          onQueryCommit={(next) => setParams({ q: next || null })}
        />
      </TabsContent>
      <TabsContent value="browse" className="mt-4 flex min-h-0 flex-1 flex-col">
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
  )
}

export default function FederationPage() {
  return (
    <>
      <DashboardHeader title="페더레이션" />
      <div className="flex min-h-0 flex-1 flex-col gap-4 p-4">
        <Suspense
          fallback={
            <div className="flex items-center gap-2 py-16 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> 불러오는 중...
            </div>
          }
        >
          <FederationTabs />
        </Suspense>
      </div>
    </>
  )
}
