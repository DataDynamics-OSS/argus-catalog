"use client"

import { InfoTip } from "@/components/tip"
import { StatusBadge } from "@workspace/ui/components/status-badge"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"

import type { FederationSyncRun } from "./api"
import { syncStatus } from "./status"

/** peer 의 최근 HARVEST 실행 이력(상태·전체/증분·건수·오류·시작 시각). */
export function SyncRunsDialog({
  open,
  onOpenChange,
  instanceName,
  runs,
  loading = false,
  error = null,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  instanceName: string
  runs: FederationSyncRun[]
  loading?: boolean
  error?: string | null
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] max-w-2xl overflow-auto">
        <DialogHeader>
          <DialogTitle>동기화 이력: {instanceName}</DialogTitle>
        </DialogHeader>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>상태</TableHead>
              <TableHead><InfoTip tip="관측 — peer 에서 받은 데이터셋 수 · 갱신 — 미러에 새로 넣거나 바꾼 수 · 임베딩 — 허브 모델로 다시 임베딩한 수 · 정리 — peer 에 더 없어 미러에서 지운 수">관측/갱신/임베딩/정리</InfoTip></TableHead>
              <TableHead>시작</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading || error || runs.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={3}
                  className={
                    error
                      ? "py-6 text-center text-destructive"
                      : "py-6 text-center text-muted-foreground"
                  }
                >
                  {loading
                    ? "불러오는 중…"
                    : error
                      ? `이력을 불러오지 못했습니다: ${error}`
                      : "이력이 없습니다."}
                </TableCell>
              </TableRow>
            ) : (
              runs.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>
                    <StatusBadge {...syncStatus(r.status)} />
                    <span className="ml-1.5 text-xs">
                      {r.is_full ? "전체" : "증분"}
                    </span>
                  </TableCell>
                  <TableCell className="text-sm">
                    {r.datasets_seen} / {r.datasets_upserted} /{" "}
                    {r.datasets_embedded} / {r.datasets_pruned}
                    {r.error && (
                      <div className="text-xs break-all whitespace-normal text-destructive">
                        {r.error}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-xs">
                    {new Date(r.started_at).toLocaleString()}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </DialogContent>
    </Dialog>
  )
}
