"use client"

import { DashboardHeader } from "@/components/dashboard-header"
import { ApisFavorites } from "@/features/apis/components/apis-favorites"

export default function ApiFavoritesPage() {
  return (
    <>
      <DashboardHeader title="API 즐겨찾기" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <ApisFavorites />
      </div>
    </>
  )
}
