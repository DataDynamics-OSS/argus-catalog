"use client"

import { Suspense } from "react"
import { Loader2 } from "lucide-react"
import { useSearchParams } from "next/navigation"

import { DashboardHeader } from "@/components/dashboard-header"
import { FederationDatasetDetailView } from "@/features/federation/dataset-detail-view"

function DetailFromQuery() {
  const params = useSearchParams()
  const urn = params.get("urn") ?? ""
  return <FederationDatasetDetailView urn={urn} />
}

export default function FederationDatasetDetailPage() {
  return (
    <>
      <DashboardHeader title="페더레이션 · 데이터셋 상세" />
      <div className="flex min-h-0 flex-1 flex-col gap-4 p-4">
        <Suspense
          fallback={
            <div className="flex items-center gap-2 py-16 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> 불러오는 중...
            </div>
          }
        >
          <DetailFromQuery />
        </Suspense>
      </div>
    </>
  )
}
