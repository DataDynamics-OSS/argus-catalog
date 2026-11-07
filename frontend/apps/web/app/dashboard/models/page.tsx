"use client"

import { Suspense } from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"

import { DashboardHeader } from "@/components/dashboard-header"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { ModelsDashboard } from "@/features/models/components/models-dashboard"
import { ModelsDialogs } from "@/features/models/components/models-dialogs"
import { ModelsTableWrapper } from "@/features/models/components/models-table-wrapper"

// 탭은 ?tab=models 로 URL 에 남겨 상세에서 돌아오거나 링크를 공유해도 같은 탭이 열린다.
function ModelsTabs() {
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
        <ModelsDashboard />
      </TabsContent>
      <TabsContent value="models" className="mt-4">
        <ModelsTableWrapper />
      </TabsContent>
    </Tabs>
  )
}

export default function ModelsPage() {
  return (
    <>
      <DashboardHeader title="MLflow 모델" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <Suspense>
          <ModelsTabs />
        </Suspense>
      </div>
      <ModelsDialogs />
    </>
  )
}
