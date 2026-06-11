"use client"

import { LayoutGrid, Table2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { useDatasets } from "./datasets-provider"

/**
 * 데이터셋 목록을 테이블/카드로 전환하는 작은 토글.
 * 선택된 모드는 ``DatasetsProvider`` 의 ``viewMode`` 와 localStorage 로 영속화된다.
 */
export function ViewModeToggle() {
  const { viewMode, setViewMode } = useDatasets()
  return (
    <div className="inline-flex items-center rounded-md border bg-background p-0.5">
      <Button
        type="button"
        variant={viewMode === "table" ? "secondary" : "ghost"}
        size="sm"
        aria-label="테이블 뷰"
        title="테이블 뷰"
        className="h-7 w-7 p-0"
        onClick={() => setViewMode("table")}
      >
        <Table2 className="h-4 w-4" />
      </Button>
      <Button
        type="button"
        variant={viewMode === "card" ? "secondary" : "ghost"}
        size="sm"
        aria-label="카드 뷰"
        title="카드 뷰"
        className="h-7 w-7 p-0"
        onClick={() => setViewMode("card")}
      >
        <LayoutGrid className="h-4 w-4" />
      </Button>
    </div>
  )
}
