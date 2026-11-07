"use client"

import { useId, useState, type Dispatch, type SetStateAction } from "react"
import { notifyError, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { Textarea } from "@workspace/ui/components/textarea"
import { ConfirmDialog } from "@/components/confirm-dialog"
import type { FlatOrg } from "@/features/datasources/utils"
import {
  createOrganization,
  createSystem,
  deleteOrganization,
  deleteSystem,
  updateOrganization,
} from "@/features/topology/api"

// ---------------------------------------------------------------------------
// Organization dialog (create/edit)
// ---------------------------------------------------------------------------

export type OrgDialogState = {
  open: boolean
  mode: "create" | "edit"
  id?: number
  name: string
  parentId: number | null
}

export const INITIAL_ORG_DIALOG: OrgDialogState = { open: false, mode: "create", name: "", parentId: null }

export function OrgDialog({
  state: orgDialog,
  setState: setOrgDialog,
  flatOrgs,
  onReload,
}: {
  state: OrgDialogState
  setState: Dispatch<SetStateAction<OrgDialogState>>
  flatOrgs: FlatOrg[]
  onReload: () => Promise<void>
}) {
  const [orgSaving, setOrgSaving] = useState(false)
  const idPrefix = useId()

  const handleOrgSave = async () => {
    if (!orgDialog.name.trim()) return
    setOrgSaving(true)
    try {
      if (orgDialog.mode === "create") {
        await createOrganization({ name: orgDialog.name.trim(), parent_id: orgDialog.parentId })
        toast.success("조직을 생성했습니다.")
      } else if (orgDialog.id) {
        await updateOrganization(orgDialog.id, { name: orgDialog.name.trim() })
        toast.success("조직을 수정했습니다.")
      }
      setOrgDialog((d) => ({ ...d, open: false }))
      await onReload()
    } catch (err) {
      notifyError(err, "조직을 저장하지 못했습니다.")
    } finally {
      setOrgSaving(false)
    }
  }

  return (
    <Dialog open={orgDialog.open} onOpenChange={(o) => setOrgDialog((d) => ({ ...d, open: o }))}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{orgDialog.mode === "create" ? "조직 추가" : "조직 수정"}</DialogTitle>
          {orgDialog.mode === "create" && orgDialog.parentId && (
            <DialogDescription>
              상위 조직: {flatOrgs.find((o) => o.id === orgDialog.parentId)?.name ?? orgDialog.parentId}
            </DialogDescription>
          )}
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="grid gap-1.5">
            <Label htmlFor={`${idPrefix}-name`} className="text-sm">조직 이름 <span className="text-destructive">*</span></Label>
            <Input
              id={`${idPrefix}-name`}
              value={orgDialog.name}
              onChange={(e) => setOrgDialog((d) => ({ ...d, name: e.target.value }))}
              placeholder="예: 데이터플랫폼본부"
              className="h-9 text-sm"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setOrgDialog((d) => ({ ...d, open: false }))} disabled={orgSaving}>
              취소
            </Button>
            <Button onClick={handleOrgSave} disabled={orgSaving || !orgDialog.name.trim()}>
              {orgSaving ? "저장 중..." : "저장"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// System create dialog (편집은 우측 상세 인라인 폼에서 처리)
// ---------------------------------------------------------------------------

export type SystemDialogState = {
  open: boolean
  name: string
  orgId: number | null
  summary: string
  description: string
  owner: string
  status: string
}

export const INITIAL_SYSTEM_DIALOG: SystemDialogState = {
  open: false,
  name: "",
  orgId: null,
  summary: "",
  description: "",
  owner: "",
  status: "ACTIVE",
}

export function SystemCreateDialog({
  state: sysDialog,
  setState: setSysDialog,
  flatOrgs,
  onReload,
}: {
  state: SystemDialogState
  setState: Dispatch<SetStateAction<SystemDialogState>>
  flatOrgs: FlatOrg[]
  onReload: () => Promise<void>
}) {
  const [sysSaving, setSysSaving] = useState(false)
  const idPrefix = useId()

  const handleSysSave = async () => {
    if (!sysDialog.name.trim()) return
    setSysSaving(true)
    try {
      await createSystem({
        name: sysDialog.name.trim(),
        org_id: sysDialog.orgId,
        summary: sysDialog.summary || undefined,
        description: sysDialog.description || undefined,
        owner: sysDialog.owner || undefined,
        status: sysDialog.status,
      })
      toast.success("시스템을 생성했습니다.")
      setSysDialog((d) => ({ ...d, open: false }))
      await onReload()
    } catch (err) {
      notifyError(err, "시스템을 저장하지 못했습니다.")
    } finally {
      setSysSaving(false)
    }
  }

  return (
    <Dialog open={sysDialog.open} onOpenChange={(o) => setSysDialog((d) => ({ ...d, open: o }))}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>시스템 추가</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="grid gap-1.5">
            <Label htmlFor={`${idPrefix}-name`} className="text-sm">시스템 이름 <span className="text-destructive">*</span></Label>
            <Input
              id={`${idPrefix}-name`}
              value={sysDialog.name}
              onChange={(e) => setSysDialog((d) => ({ ...d, name: e.target.value }))}
              placeholder="예: 고객데이터 시스템"
              className="h-9 text-sm"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${idPrefix}-summary`} className="text-sm">요약</Label>
            <Input
              id={`${idPrefix}-summary`}
              value={sysDialog.summary}
              onChange={(e) => setSysDialog((d) => ({ ...d, summary: e.target.value }))}
              placeholder="한 줄 요약"
              maxLength={200}
              className="h-9 text-sm"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${idPrefix}-description`} className="text-sm">설명</Label>
            <Textarea
              id={`${idPrefix}-description`}
              value={sysDialog.description}
              onChange={(e) => setSysDialog((d) => ({ ...d, description: e.target.value }))}
              placeholder="상세 설명"
              rows={3}
              className="text-sm"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${idPrefix}-org`} className="text-sm">소속 조직</Label>
            <Select
              value={sysDialog.orgId ? String(sysDialog.orgId) : "none"}
              onValueChange={(v) =>
                setSysDialog((d) => ({ ...d, orgId: v === "none" ? null : Number(v) }))
              }
            >
              <SelectTrigger id={`${idPrefix}-org`} className="h-9 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none" className="text-sm">미분류 (조직 미배정)</SelectItem>
                {flatOrgs.map((o) => (
                  <SelectItem key={o.id} value={String(o.id)} className="text-sm">
                    {" ".repeat(o.depth * 2)}{o.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${idPrefix}-owner`} className="text-sm">담당자</Label>
            <Input
              id={`${idPrefix}-owner`}
              value={sysDialog.owner}
              onChange={(e) => setSysDialog((d) => ({ ...d, owner: e.target.value }))}
              placeholder="예: 홍길동"
              className="h-9 text-sm"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${idPrefix}-status`} className="text-sm">상태</Label>
            <Select value={sysDialog.status} onValueChange={(v) => setSysDialog((d) => ({ ...d, status: v }))}>
              <SelectTrigger id={`${idPrefix}-status`} className="h-9 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ACTIVE" className="text-sm">ACTIVE</SelectItem>
                <SelectItem value="INACTIVE" className="text-sm">INACTIVE</SelectItem>
                <SelectItem value="DEPRECATED" className="text-sm">DEPRECATED</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setSysDialog((d) => ({ ...d, open: false }))} disabled={sysSaving}>
              취소
            </Button>
            <Button onClick={handleSysSave} disabled={sysSaving || !sysDialog.name.trim()}>
              {sysSaving ? "저장 중..." : "저장"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Organization / System delete confirm
// ---------------------------------------------------------------------------

export type TopologyDeleteTarget =
  | { kind: "org"; id: number; name: string }
  | { kind: "system"; id: number; name: string; datasourceCount: number }

export function TopologyDeleteConfirm({
  target: topoDelete,
  onClose,
  onDeleted,
  onReload,
}: {
  target: TopologyDeleteTarget | null
  onClose: () => void
  /** 삭제 성공 직후(닫기 전) 호출 — 삭제된 노드가 선택돼 있으면 선택 해제 */
  onDeleted: (target: TopologyDeleteTarget) => void
  onReload: () => Promise<void>
}) {
  const [topoDeleting, setTopoDeleting] = useState(false)

  const handleTopoDeleteConfirm = async () => {
    if (!topoDelete) return
    setTopoDeleting(true)
    try {
      if (topoDelete.kind === "org") {
        await deleteOrganization(topoDelete.id)
        toast.success(`조직 "${topoDelete.name}" 을(를) 삭제했습니다.`)
      } else {
        // 소속 데이터 소스가 있으면 사용자가 확인한 경우에만 force(미분류로 해제 후 삭제)
        await deleteSystem(topoDelete.id, topoDelete.datasourceCount > 0)
        toast.success(`시스템 "${topoDelete.name}" 을(를) 삭제했습니다.`)
      }
      onDeleted(topoDelete)
      onClose()
      await onReload()
    } catch (err) {
      notifyError(err, `${topoDelete.kind === "org" ? "조직" : "시스템"}을 삭제하지 못했습니다.`)
    } finally {
      setTopoDeleting(false)
    }
  }

  return (
    <ConfirmDialog
      open={topoDelete !== null}
      onOpenChange={(o) => !o && !topoDeleting && onClose()}
      title={topoDelete?.kind === "org" ? "조직 삭제" : "시스템 삭제"}
      desc={
        topoDelete?.kind === "system" ? (
          <div className="space-y-2">
            <p>
              시스템 <span className="font-semibold">{topoDelete.name}</span> 을(를) 삭제합니다.
              요약·설명·담당자 정보는 되돌릴 수 없습니다.
            </p>
            {topoDelete.datasourceCount > 0 && (
              <p className="text-destructive">
                소속 데이터 소스 {topoDelete.datasourceCount}개는 삭제되지 않고 미분류로 이동합니다.
              </p>
            )}
          </div>
        ) : (
          <p>
            조직 <span className="font-semibold">{topoDelete?.name}</span> 을(를) 삭제합니다.
            하위 조직이나 시스템이 있으면 삭제되지 않습니다.
          </p>
        )
      }
      confirmText="삭제"
      destructive
      isLoading={topoDeleting}
      handleConfirm={handleTopoDeleteConfirm}
    />
  )
}
