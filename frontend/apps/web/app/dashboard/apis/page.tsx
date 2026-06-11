"use client"

import { DashboardHeader } from "@/components/dashboard-header"
import { ApisDialogs } from "@/features/apis/components/apis-dialogs"
import { ApisProvider } from "@/features/apis/components/apis-provider"
import { ApisTableWrapper } from "@/features/apis/components/apis-table-wrapper"

export default function ApisPage() {
  return (
    <ApisProvider>
      <DashboardHeader title="API Catalog" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <ApisTableWrapper />
      </div>
      <ApisDialogs />
    </ApisProvider>
  )
}
