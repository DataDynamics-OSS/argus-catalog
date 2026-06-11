"use client"

import { useMemo } from "react"
import { DashboardHeader } from "@/components/dashboard-header"
import { LocalFilesystemBrowser } from "@/components/local-filesystem-browser"
import { createS3DataSource } from "@/features/filesystem/api"

export default function OciFilesPage() {
  const dataSource = useMemo(() => createS3DataSource(), [])

  return (
    <>
      <DashboardHeader title="OCI 모델 파일" />
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
