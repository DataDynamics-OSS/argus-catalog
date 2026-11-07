"use client"

import { Suspense, useMemo } from "react"
import { useSearchParams } from "next/navigation"
import Link from "next/link"
import { Package } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { DashboardHeader } from "@/components/dashboard-header"
import { LocalFilesystemBrowser } from "@/components/local-filesystem-browser"
import { createS3DataSource } from "@/features/filesystem/api"

function EmptyHint() {
  return (
    <div className="mx-auto max-w-lg space-y-3 text-center text-sm text-muted-foreground">
      <Package className="mx-auto h-10 w-10 text-muted-foreground/40" />
      <p className="font-medium text-foreground">아직 저장된 OCI 모델 파일이 없습니다.</p>
      <p>
        OCI 모델 허브에서 모델을 올리거나 HuggingFace 에서 가져오면 버전별 파일이 이 저장소에 쌓입니다.
        파일은 모델 상세의 &apos;파일&apos; 탭에서도 볼 수 있습니다.
      </p>
      <Button variant="outline" size="sm" asChild>
        <Link href="/dashboard/oci-hub?tab=models">OCI 모델 목록</Link>
      </Button>
    </div>
  )
}

// ?path=/<모델>/v<버전>/ — 그 위치에서 연다(MLflow 모델 파일과 같은 규칙). 경로 키로 다시 마운트한다.
function OciFilesBrowser() {
  const params = useSearchParams()
  const initialPath = params.get("path") || "/"
  const dataSource = useMemo(() => createS3DataSource(), [])

  return (
    <LocalFilesystemBrowser
      key={initialPath}
      initialPath={initialPath}
      dataSource={dataSource}
      readOnly
      emptyState={<EmptyHint />}
      className="flex-1"
    />
  )
}

// OCI 모델 저장소(S3) 는 조회·내려받기만 지원한다 — 변경은 SDK·API 로 모델 버전 단위로 한다.
export default function OciFilesPage() {
  return (
    <>
      <DashboardHeader title="OCI 모델 파일" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <Suspense>
          <OciFilesBrowser />
        </Suspense>
      </div>
    </>
  )
}
