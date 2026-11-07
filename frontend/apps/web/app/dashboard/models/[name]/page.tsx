"use client"

import { Suspense } from "react"
import { useParams, useRouter } from "next/navigation"

import { DashboardHeader } from "@/components/dashboard-header"
import { ModelsDetail } from "@/features/models/components/models-detail"

/** MLflow 모델 상세 — `/dashboard/models/{모델 이름}` (북마크·공유·브라우저 뒤로 가기 지원). */
export default function ModelDetailPage() {
  const params = useParams<{ name: string }>()
  const router = useRouter()
  const raw = params?.name ?? ""
  let name = raw
  try {
    name = decodeURIComponent(raw)
  } catch {
    /* 잘못 인코딩된 주소는 원문 그대로 조회(404 로 안내) */
  }

  return (
    <>
      <DashboardHeader title="MLflow 모델" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <Suspense>
          <ModelsDetail modelName={name} onBack={() => router.push("/dashboard/models?tab=models")} />
        </Suspense>
      </div>
    </>
  )
}
