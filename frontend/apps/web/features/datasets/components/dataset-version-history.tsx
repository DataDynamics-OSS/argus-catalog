"use client"

/**
 * 데이터 버전 이력 (TODO-096 4단계, 가이드라인 체크리스트 C32) — 메타데이터 탭 '버전' 칸 옆 '이력' 단추.
 *
 * '버전'·'버전 노트'를 저장하면 서버가 이력을 남긴다(가장 최근 이력과 버전이 같으면 그 줄의 노트를 고침).
 * 잘못 남긴 줄은 소유자(생성자 · 소유자 목록)·관리자가 지울 수 있다 — 데이터셋의 현재 버전 값은 바뀌지 않는다.
 */

import { Tip } from "@/components/tip"
import { useCallback, useEffect, useState } from "react"
import { History, Loader2, Trash2 } from "lucide-react"

import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@workspace/ui/components/alert-dialog"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@workspace/ui/components/table"

import { errorMessage, notifyError, notifySuccess } from "@/lib/notify"

import { deleteDatasetVersion, fetchDatasetVersions, type DatasetVersionEntry } from "../api"

function formatTime(v: string | null): string {
  if (!v) return ""
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" })
}

export function DatasetVersionHistoryButton({ datasetId, canEdit }: { datasetId: number; canEdit: boolean }) {
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<DatasetVersionEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [removing, setRemoving] = useState<DatasetVersionEntry | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      setRows(await fetchDatasetVersions(datasetId))
    } catch (e) {
      setError(errorMessage(e) ?? "버전 이력을 불러오지 못했습니다.")
    }
  }, [datasetId])

  useEffect(() => { if (open) load() }, [open, load])

  const remove = async (row: DatasetVersionEntry) => {
    setRemoving(null)
    setBusy(row.id)
    try {
      await deleteDatasetVersion(datasetId, row.id)
      notifySuccess("버전 이력을 지웠습니다.")
      await load()
    } catch (e) {
      notifyError(e, "버전 이력을 지우지 못했습니다.")
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <Button type="button" size="sm" variant="outline" className="h-8 shrink-0" onClick={() => setOpen(true)}>
        <History className="mr-1 h-3.5 w-3.5" /> 이력
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>데이터 버전 이력</DialogTitle>
            <DialogDescription>
              &apos;버전&apos;·&apos;버전 노트&apos;를 저장할 때마다 남습니다. 같은 버전의 노트를 고치면 그 줄이 바뀝니다. 데이터 카드에도 이 표가 들어갑니다.
            </DialogDescription>
          </DialogHeader>
          {error && <div className="text-sm text-destructive">{error}</div>}
          {rows === null && !error && <Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" />}
          {rows && rows.length === 0 && (
            <div className="rounded-md border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">
              아직 이력이 없습니다. 메타데이터 탭에서 버전과 버전 노트를 적고 저장하세요.
            </div>
          )}
          {rows && rows.length > 0 && (
            <div className="max-h-[60vh] overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-28">버전</TableHead>
                    <TableHead>변경 사항</TableHead>
                    <TableHead className="w-40">담당자 · 시각</TableHead>
                    {canEdit && <TableHead className="w-10" />}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r, i) => (
                    <TableRow key={r.id} className="align-top">
                      <TableCell className="font-mono text-sm">
                        {r.version}
                        {i === 0 && <div className="text-[11px]">최근</div>}
                      </TableCell>
                      <TableCell className="whitespace-pre-wrap break-words text-sm">
                        {r.notes || <span className="text-muted-foreground">-</span>}
                      </TableCell>
                      <TableCell className="text-xs">
                        <div>{r.created_by ?? "-"}</div>
                        <div >{formatTime(r.created_at)}</div>
                      </TableCell>
                      {canEdit && (
                        <TableCell>
                          <Tip label={`${r.version} 이력 지우기`}><Button size="icon" variant="ghost" className="h-7 w-7" aria-label={`${r.version} 이력 지우기`}
                            disabled={busy !== null} onClick={() => setRemoving(r)}>
                            {busy === r.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                          </Button></Tip>
                        </TableCell>
                      )}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!removing} onOpenChange={(o) => { if (!o) setRemoving(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>버전 이력을 지울까요?</AlertDialogTitle>
            <AlertDialogDescription>
              &apos;{removing?.version}&apos; 줄을 지웁니다. 데이터셋의 현재 버전 값은 바뀌지 않습니다.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction onClick={() => removing && remove(removing)}>지우기</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
