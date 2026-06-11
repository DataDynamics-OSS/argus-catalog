"use client"

import { DashboardHeader } from "@/components/dashboard-header"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { ModelsDashboard } from "@/features/models/components/models-dashboard"
import { ModelsDialogs } from "@/features/models/components/models-dialogs"
import { ModelsProvider } from "@/features/models/components/models-provider"
import { ModelsTableWrapper } from "@/features/models/components/models-table-wrapper"

export default function ModelsPage() {
  return (
    <ModelsProvider>
      <DashboardHeader title="MLflow 모델" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <Tabs defaultValue="dashboard">
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
      </div>
      <ModelsDialogs />
    </ModelsProvider>
  )
}
