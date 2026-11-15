"use client"

import { Suspense } from "react"

import { DashboardHeader } from "@/components/dashboard-header"
import { DatasetsDialogs } from "@/features/datasets/components/datasets-dialogs"
import { DatasetsProvider } from "@/features/datasets/components/datasets-provider"
import { DatasetsTableWrapper } from "@/features/datasets/components/datasets-table-wrapper"

// 목록 조건은 주소(?q=&datasource=&tag=&page=…)에 남는다 — useSearchParams 를 쓰므로 Suspense 로 감싼다.
export default function DatasetsPage() {
  return (
    <Suspense>
      <DatasetsPageContent />
    </Suspense>
  )
}

function DatasetsPageContent() {
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
