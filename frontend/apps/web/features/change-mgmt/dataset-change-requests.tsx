"use client"

/**
 * 데이터셋 상세의 "변경 요청" 탭 — 해당 데이터셋의 변경 요청 목록과
 * "변경 요청 만들기" 진입 버튼을 제공한다. 새 요청은 대상 데이터셋이 미리
 * 채워지도록 ?dataset_id= 로 생성 화면에 진입한다.
 */

import { useCallback, useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { Plus, RefreshCw } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { usePermissions } from "@/features/permissions/use-permissions"
import { Badge } from "@workspace/ui/components/badge"
import {
  type ChangeRequest,
  listChangeRequests,
} from "@/features/change-mgmt/api"
import {
  CHANGE_TYPE_LABEL,
  PriorityBadge,
  StatusBadge,
  useUserNames,
} from "@/features/change-mgmt/labels"
import { errorMessage } from "@/lib/notify"

function fmtDate(s?: string | null): string {
  if (!s) return "—"
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return "—"
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function DatasetChangeRequests({ datasetId }: { datasetId: number }) {
  // 기능 권한 changes.create — 서버도 같은 키로 403.
  // 버튼이 여는 화면(/dashboard/changes/*)의 메뉴 권한도 함께 본다 — 닫힌 메뉴로 보내 차단 화면을 띄우지 않게(DEF-064)
  const { canUseFeature, isMenuAllowed } = usePermissions()
  const canCreate = isMenuAllowed("changes") && canUseFeature("changes.create")
  // 상세는 변경 관리 또는 내 결재함 메뉴로 열 수 있다(MenuRouteGuard 와 같은 기준)
  const canOpen = isMenuAllowed("changes") || isMenuAllowed("my-approvals")
  const router = useRouter()
  // 요청자를 다른 변경 관리 화면처럼 '이름 (username)' 으로 (예전에는 username 만)
  const { approverLabel } = useUserNames()
  const [items, setItems] = useState<ChangeRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setItems(await listChangeRequests({ dataset_id: datasetId }))
    } catch (e) {
      setError(errorMessage(e) ?? "변경 요청 목록을 불러오지 못했습니다.")
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
        <p className="text-sm">이 데이터셋의 변경 요청 {items.length}건</p>
        <div className="flex gap-1">
          <Button variant="outline" size="sm" onClick={() => void reload()} disabled={loading}>
            <RefreshCw className={`mr-1 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> 새로고침
          </Button>
          {canCreate && (
            <Button
              size="sm"
              onClick={() => router.push(`/dashboard/changes/new?dataset_id=${datasetId}`)}
            >
              <Plus className="mr-1 h-4 w-4" /> 변경 요청 만들기
            </Button>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </div>
      )}

      {loading && items.length === 0 ? (
        <div className="text-sm text-muted-foreground">불러오는 중…</div>
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
              disabled={!canOpen}
              className="flex flex-col gap-1 rounded-lg border p-3 text-left transition-[box-shadow,border-color] enabled:hover:border-primary/40 enabled:hover:shadow-md disabled:cursor-default"
              onClick={() => router.push(`/dashboard/changes/${cr.id}`)}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs">{cr.cr_code}</span>
                <span className="font-medium">{cr.title}</span>
                <StatusBadge status={cr.status} />
                <Badge variant="outline">{CHANGE_TYPE_LABEL[cr.change_type]}</Badge>
                <PriorityBadge priority={cr.priority} />
              </div>
              <div className="flex flex-wrap items-center gap-3 text-xs">
                <span>요청자: {approverLabel(cr.requested_by)}</span>
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
