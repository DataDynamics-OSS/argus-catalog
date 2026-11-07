"use client"

import { Suspense } from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { DashboardHeader } from "@/components/dashboard-header"
import { OciModelList } from "@/features/oci-hub/components/oci-model-list"
import { OciHubDashboard } from "@/features/oci-hub/oci-hub-dashboard"

// 탭·검색 조건을 주소(?tab=models&q=...)에 남겨 상세에서 돌아오거나 링크를 공유해도 같은 화면이 열린다.
function OciHubTabs() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const tab = params.get("tab") === "models" ? "models" : "dashboard"

  return (
    <Tabs
      value={tab}
      onValueChange={(v) => router.replace(v === "dashboard" ? pathname : `${pathname}?tab=${v}`, { scroll: false })}
    >
      <TabsList variant="line">
        <TabsTrigger value="dashboard" className="text-base">대시보드</TabsTrigger>
        <TabsTrigger value="models" className="text-base">모델</TabsTrigger>
      </TabsList>
      <TabsContent value="dashboard" className="mt-4">
        <OciHubDashboard />
      </TabsContent>
      <TabsContent value="models" className="mt-4">
        {tab === "models" && <OciModelList />}
      </TabsContent>
    </Tabs>
  )
}

export default function OciHubPage() {
  return (
    <>
      <DashboardHeader title="OCI 모델 허브" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <Suspense>
          <OciHubTabs />
        </Suspense>
      </div>
    </>
  )
}
