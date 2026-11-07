"use client"

import { Suspense } from "react"
import { useParams } from "next/navigation"

import { DashboardHeader } from "@/components/dashboard-header"
import { OciModelDetailView } from "@/features/oci-hub/components/oci-model-detail"

/** OCI 모델 상세 — `/dashboard/oci-hub/{모델 이름}` (북마크·공유·브라우저 뒤로 가기 지원). */
export default function OciModelDetailPage() {
  const params = useParams<{ name: string }>()
  const raw = params?.name ?? ""
  let name = raw
  try {
    name = decodeURIComponent(raw)
  } catch {
    /* 잘못 인코딩된 주소는 원문 그대로 조회(404 로 안내) */
  }

  return (
    <>
      <DashboardHeader title="OCI 모델 허브" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <Suspense>
          <OciModelDetailView key={name} name={name} />
        </Suspense>
      </div>
    </>
  )
}
