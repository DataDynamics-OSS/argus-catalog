"use client"

import { Suspense, useMemo } from "react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { FolderOpen } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { DashboardHeader } from "@/components/dashboard-header"
import { LocalFilesystemBrowser } from "@/components/local-filesystem-browser"
import { createFilesystemDataSource } from "@/features/filesystem/api"

function EmptyHint() {
  return (
    <div className="mx-auto max-w-lg space-y-3 text-center text-sm text-muted-foreground">
      <FolderOpen className="mx-auto h-10 w-10 text-muted-foreground/40" />
      <p className="font-medium text-foreground">아직 저장된 MLflow 모델 파일이 없습니다.</p>
      <p>
        MLflow 클라이언트로 모델 버전을 등록하면 <code className="text-xs">&lt;모델 이름&gt;/versions/&lt;버전&gt;</code> 폴더에
        파일이 저장됩니다. 등록 방법은 각 모델 상세의 &apos;사용법&apos; 탭을 참고하세요.
      </p>
      <Button variant="outline" size="sm" asChild>
        <Link href="/dashboard/models">MLflow 모델 목록</Link>
      </Button>
    </div>
  )
}

// ?path=/argus.ml.iris/versions/1 — MLflow 모델 상세(개요·버전 탭)의 "파일 보기" 링크가 여는 위치.
// 경로 키로 다시 마운트해 다른 모델의 링크로 들어와도 그 위치에서 시작한다.
function MlflowFilesBrowser() {
  const params = useSearchParams()
  const initialPath = params.get("path") || "/"
  const dataSource = useMemo(() => createFilesystemDataSource("model-artifacts"), [])

  return (
    <LocalFilesystemBrowser
      key={initialPath}
      initialPath={initialPath}
      dataSource={dataSource}
      emptyState={<EmptyHint />}
      className="flex-1"
    />
  )
}

export default function MlflowFilesPage() {
  return (
    <>
      <DashboardHeader title="MLflow 모델 파일" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <Suspense>
          <MlflowFilesBrowser />
        </Suspense>
      </div>
    </>
  )
}
