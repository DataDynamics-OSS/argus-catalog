"use client"

// Dataset 상세 페이지의 "변경 이력" 탭 — 데이터셋 스키마 스냅샷 목록과 ADD/DROP/MODIFY 변경 내역을 펼쳐 본다.

import { useCallback, useEffect, useState } from "react"
import { ChevronRight, History, Loader2, Minus, Plus, RefreshCw } from "lucide-react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import {
  fetchSchemaHistory,
  type SchemaSnapshot,
  type SchemaChangeEntry,
} from "@/features/datasets/api"

type SchemaHistoryTabProps = {
  datasetId: number
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, "0")
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

function ChangeTypeBadge({ type }: { type: string }) {
  if (type === "ADD") {
    return (
      <span className="inline-flex items-center gap-0.5 text-sm font-semibold text-green-600">
        <Plus className="h-3.5 w-3.5" /> 추가
      </span>
    )
  }
  if (type === "DROP") {
    return (
      <span className="inline-flex items-center gap-0.5 text-sm font-semibold text-red-500">
        <Minus className="h-3.5 w-3.5" /> 삭제
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-0.5 text-sm font-semibold text-orange-500">
      <RefreshCw className="h-3.5 w-3.5" /> 변경
    </span>
  )
}

function ChangeDetail({ change }: { change: SchemaChangeEntry }) {
  // 본문/값 모두 text-sm 으로 키우고 monospace 는 유지 (key=value 정렬 가독성).
  return (
    <div className="flex items-start gap-3 py-1 px-2 text-sm font-[family-name:var(--font-d2coding)]">
      <ChangeTypeBadge type={change.type} />
      <span className="font-medium min-w-[120px]">{change.field}</span>
      <div className="flex-1">
        {change.type === "ADD" && change.after && (
          <span className="text-green-600">
            {Object.entries(change.after).map(([k, v]) => `${k}=${v}`).join(", ")}
          </span>
        )}
        {change.type === "DROP" && change.before && (
          <span className="text-red-500 line-through">
            {Object.entries(change.before).map(([k, v]) => `${k}=${v}`).join(", ")}
          </span>
        )}
        {change.type === "MODIFY" && (
          <div className="flex gap-2">
            {change.before && (
              <span className="text-red-500 line-through">
                {Object.entries(change.before).map(([k, v]) => `${k}=${v}`).join(", ")}
              </span>
            )}
            <span className="text-muted-foreground">&rarr;</span>
            {change.after && (
              <span className="text-green-600">
                {Object.entries(change.after).map(([k, v]) => `${k}=${v}`).join(", ")}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function SnapshotRow({ snapshot }: { snapshot: SchemaSnapshot }) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <tr
        className="hover:bg-muted/30 cursor-pointer"
        onClick={() => setOpen(!open)}
      >
        <td className="px-3 py-2 text-sm whitespace-nowrap">
          <div className="flex items-center gap-1.5">
            <ChevronRight
              className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`}
            />
            <span className="font-[family-name:var(--font-d2coding)]">
              {formatDate(snapshot.synced_at)}
            </span>
          </div>
        </td>
        <td className="px-3 py-2 text-sm text-center">{snapshot.field_count}</td>
        <td className="px-3 py-2 text-sm">{snapshot.change_summary || "-"}</td>
        <td className="px-3 py-2 text-sm text-center text-muted-foreground">
          {snapshot.changes.length}
        </td>
      </tr>
      {open && snapshot.changes.length > 0 && (
        <tr>
          <td colSpan={4} className="p-0">
            <div className="border-t border-b bg-muted/10 py-1">
              {snapshot.changes.map((c, i) => (
                <ChangeDetail key={`${c.field}-${i}`} change={c} />
              ))}
            </div>
          </td>
        </tr>
      )}
      {open && snapshot.changes.length === 0 && (
        <tr>
          <td colSpan={4} className="p-0">
            <div className="border-t border-b bg-muted/10 py-2 px-3">
              <p className="text-xs text-muted-foreground">최초 동기화 — 비교할 이전 상태 없음</p>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export function SchemaHistoryTab({ datasetId }: SchemaHistoryTabProps) {
  const [snapshots, setSnapshots] = useState<SchemaSnapshot[]>([])
  const [total, setTotal] = useState(0)
  const [isLoading, setIsLoading] = useState(true)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await fetchSchemaHistory(datasetId, 1, 20)
      setSnapshots(data.items)
      setTotal(data.total)
    } catch (err) {
      console.error("Failed to load schema history", { datasetId, err })
    } finally {
      setIsLoading(false)
    }
  }, [datasetId])

  useEffect(() => {
    load()
  }, [load])

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between py-3">
        <div>
          <CardTitle className="text-base flex items-center gap-2">
            <History className="h-4 w-4" />
            스키마 변경 이력
          </CardTitle>
          <CardDescription className="text-xs mt-1">
            최근 20개의 스냅샷만 보관됩니다.
          </CardDescription>
        </div>
        <span className="text-xs text-muted-foreground">
          {snapshots.length} / {total} 스냅샷
        </span>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : snapshots.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">
            스키마 변경 이력이 없습니다. 동기화를 실행하면 변경 추적이 시작됩니다.
          </p>
        ) : (
          <div className="border rounded overflow-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/60">
                <tr>
                  <th className="px-3 py-2 text-left font-semibold">동기화 시각</th>
                  <th className="px-3 py-2 text-center font-semibold w-[80px]">필드</th>
                  <th className="px-3 py-2 text-left font-semibold">변경 내역</th>
                  <th className="px-3 py-2 text-center font-semibold w-[80px]">건수</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {snapshots.map((snap) => (
                  <SnapshotRow key={snap.id} snapshot={snap} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
