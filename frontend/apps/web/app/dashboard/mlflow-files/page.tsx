"use client"

import { useMemo } from "react"
import { DashboardHeader } from "@/components/dashboard-header"
import { LocalFilesystemBrowser } from "@/components/local-filesystem-browser"
import { createFilesystemDataSource } from "@/features/filesystem/api"

export default function MlflowFilesPage() {
  const dataSource = useMemo(() => createFilesystemDataSource("model-artifacts"), [])

  return (
    <>
      <DashboardHeader title="MLflow 모델 파일" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <LocalFilesystemBrowser
          initialPath="/"
          dataSource={dataSource}
          className="flex-1"
        />
      </div>
    </>
  )
}
