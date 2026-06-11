"use client"

/**
 * 데이터셋 상세의 "변경 요청" 탭 — 해당 데이터셋의 변경 요청 목록과
 * "새 변경 요청" 진입 버튼을 제공한다. 새 요청은 대상 데이터셋이 미리
 * 채워지도록 ?dataset_id= 로 생성 화면에 진입한다.
 */

import { useCallback, useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { FilePlus2, RefreshCw } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Badge } from "@workspace/ui/components/badge"
import {
  type ChangeRequest,
  listChangeRequests,
} from "@/features/change-mgmt/api"
import {
  CHANGE_TYPE_LABEL,
  PRIORITY_LABEL,
  StatusBadge,
} from "@/features/change-mgmt/labels"

function fmtDate(s?: string | null): string {
  if (!s) return "—"
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return "—"
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function DatasetChangeRequests({ datasetId }: { datasetId: number }) {
  const router = useRouter()
  const [items, setItems] = useState<ChangeRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setItems(await listChangeRequests({ dataset_id: datasetId }))
    } catch (e) {
      setError(e instanceof Error ? e.message : "변경 요청 목록 조회 실패")
    } finally {
      setLoading(false)
    }
  }, [datasetId])

  useEffect(() => {
    void reload()
  }, [reload])

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">이 데이터셋의 변경 요청 {items.length}건</p>
        <div className="flex gap-1">
          <Button variant="outline" size="sm" onClick={() => void reload()} disabled={loading}>
            <RefreshCw className={`mr-1 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> 새로고침
          </Button>
          <Button
            size="sm"
            onClick={() => router.push(`/dashboard/changes/new?dataset_id=${datasetId}`)}
          >
            <FilePlus2 className="mr-1 h-4 w-4" /> 새 변경 요청
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </div>
      )}

      {loading && items.length === 0 ? (
        <div className="text-sm text-muted-foreground">불러오는 중...</div>
      ) : items.length === 0 ? (
        <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
          이 데이터셋에 대한 변경 요청이 없습니다.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {items.map((cr) => (
            <button
              key={cr.id}
              type="button"
              className="flex flex-col gap-1 rounded-lg border p-3 text-left hover:bg-muted/40"
              onClick={() => router.push(`/dashboard/changes/${cr.id}`)}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-muted-foreground">{cr.cr_code}</span>
                <span className="font-medium">{cr.title}</span>
                <StatusBadge status={cr.status} />
                <Badge variant="outline">{CHANGE_TYPE_LABEL[cr.change_type]}</Badge>
                <Badge variant="outline">{PRIORITY_LABEL[cr.priority]}</Badge>
              </div>
              <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                <span>요청자: {cr.requested_by}</span>
                <span>생성: {fmtDate(cr.created_at)}</span>
                {cr.scheduled_at && <span>적용 예정: {fmtDate(cr.scheduled_at)}</span>}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
