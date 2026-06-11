"use client"

import { DashboardHeader } from "@/components/dashboard-header"
import { DatasetsDialogs } from "@/features/datasets/components/datasets-dialogs"
import { DatasetsProvider } from "@/features/datasets/components/datasets-provider"
import { DatasetsTableWrapper } from "@/features/datasets/components/datasets-table-wrapper"

export default function DatasetsPage() {
  return (
    <DatasetsProvider>
      <DashboardHeader title="데이터셋" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <DatasetsTableWrapper />
      </div>
      <DatasetsDialogs />
    </DatasetsProvider>
  )
}
