"use client"

import { useEffect, useState } from "react"
import { errorMessage, notifyError, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import type { Datasource } from "@/features/datasets/data/schema"
import {
  deleteDatasource,
  EMPTY_IMPACT,
  fetchDeleteImpact,
  type DeleteImpact,
} from "@/features/datasources/api"

/**
 * 데이터 소스 삭제 다이얼로그 — 삭제 영향을 조회해 보여 주고 "DELETE <이름>" 입력으로 확정한다.
 * 열 때마다 부모가 key 를 바꿔 다시 마운트하므로 입력/영향 상태는 항상 초기값에서 시작한다.
 */
export function DeleteDatasourceDialog({
  open,
  onOpenChange,
  target,
  onDeleted,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  target: Datasource | null
  /** 삭제 성공 후(다이얼로그를 닫은 뒤) 호출 — 선택 해제·재조회 */
  onDeleted: () => Promise<void>
}) {
  const [deleteConfirmInput, setDeleteConfirmInput] = useState("")
  const [deleting, setDeleting] = useState(false)
  const [deleteImpact, setDeleteImpact] = useState<DeleteImpact>(EMPTY_IMPACT)
  const [deleteImpactLoading, setDeleteImpactLoading] = useState(target !== null)
  const [deleteImpactError, setDeleteImpactError] = useState<string | null>(null)

  const targetId = target?.id
  useEffect(() => {
    if (targetId == null) return
    let active = true
    fetchDeleteImpact(targetId)
      .then((impact) => {
        if (active) setDeleteImpact(impact)
      })
      .catch((err) => {
        // 영향을 모르면 삭제를 막는다(데이터셋 수를 0 으로 오인해 진행하지 않도록)
        if (active) setDeleteImpactError(errorMessage(err) ?? "삭제 영향을 확인하지 못했습니다.")
      })
      .finally(() => {
        if (active) setDeleteImpactLoading(false)
      })
    return () => {
      active = false
    }
  }, [targetId])

  const handleDeleteConfirm = async () => {
    if (!target) return
    setDeleting(true)
    try {
      // 데이터셋이 있으면 force=true 로 cascade 삭제 (다이얼로그에 영향 표시됨)
      await deleteDatasource(target.id, deleteImpact.dataset_count > 0)
      toast.success(`데이터 소스 "${target.name}" 을 삭제했습니다.`)
      onOpenChange(false)
      await onDeleted()
    } catch (err) {
      notifyError(err, "데이터 소스를 삭제하지 못했습니다.")
    } finally {
      setDeleting(false)
    }
  }

  const deleteExpectedInput = target ? `DELETE ${target.name}` : ""

  return (
    <Dialog open={open} onOpenChange={(o) => !deleting && onOpenChange(o)}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>데이터 소스 삭제</DialogTitle>
          <DialogDescription>
            이 작업은 되돌릴 수 없습니다. 삭제를 확정하려면 아래에{" "}
            <span className="font-mono font-semibold text-foreground">{deleteExpectedInput}</span> 을(를) 입력하세요.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          {deleteImpactLoading && (
            <p className="text-xs text-muted-foreground">삭제 영향 확인 중...</p>
          )}
          {deleteImpactError && (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-2.5 text-xs text-destructive">
              삭제 영향을 확인하지 못해 삭제할 수 없습니다: {deleteImpactError}
            </div>
          )}
          {(deleteImpact.alert_rule_count > 0 ||
            deleteImpact.change_request_count > 0 ||
            deleteImpact.model_count > 0 ||
            deleteImpact.mcp_deployment_count > 0) && (
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
              {deleteImpact.alert_rule_count > 0 && (
                <li>이 데이터 소스·데이터셋 대상 알림 규칙 {deleteImpact.alert_rule_count}개가 삭제됩니다.</li>
              )}
              {deleteImpact.change_request_count > 0 && (
                <li>데이터셋의 변경 요청 {deleteImpact.change_request_count}건이 함께 삭제됩니다.</li>
              )}
              {deleteImpact.model_count > 0 && (
                <li>연결된 모델 {deleteImpact.model_count}개는 데이터 소스 연결만 해제됩니다.</li>
              )}
              {deleteImpact.mcp_deployment_count > 0 && (
                <li>연결된 MCP 배포 {deleteImpact.mcp_deployment_count}개는 데이터 소스 연결만 해제됩니다.</li>
              )}
            </ul>
          )}
          {deleteImpact.federation_allowlisted && (
            <p className="text-xs text-amber-600 dark:text-amber-500">
              이 데이터 소스 이름이 페더레이션 노출 허용 목록 설정에 남아 있습니다. 같은 이름으로 다시 만들면 자동으로 노출되니 서버 설정에서도 제거하세요.
            </p>
          )}
          {deleteImpact.removed_dataset_count > 0 && (
            <p className="text-xs text-muted-foreground">
              원본에서 사라진(비활성) 데이터셋 {deleteImpact.removed_dataset_count}개도 함께 정리됩니다.
            </p>
          )}
          {deleteImpact.dataset_count > 0 && (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-2.5 text-xs text-destructive">
              이 데이터 소스의 <span className="font-semibold">데이터셋 {deleteImpact.dataset_count}개</span>
              {deleteImpact.external_lineage_count > 0 && (
                <> 와 이를 참조하는 <span className="font-semibold">타 데이터 소스 리니지 {deleteImpact.external_lineage_count}개</span></>
              )}
              가 함께 삭제됩니다.
            </div>
          )}
          <Input
            placeholder={deleteExpectedInput}
            value={deleteConfirmInput}
            onChange={(e) => setDeleteConfirmInput(e.target.value)}
            className="font-mono text-sm"
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={deleting}>
              취소
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteConfirm}
              disabled={
                deleting ||
                deleteImpactLoading ||
                deleteImpactError !== null ||
                deleteConfirmInput !== deleteExpectedInput
              }
            >
              {deleting ? "삭제 중..." : "삭제"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
