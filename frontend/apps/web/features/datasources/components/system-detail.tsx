"use client"

import { useId, useState } from "react"

import { Button } from "@workspace/ui/components/button"
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
import type { TopologySystem } from "@/features/topology/api"
import type { FlatOrg } from "@/features/datasources/utils"
import { DatasourcesTable } from "@/features/datasources/components/detail-tables"

export interface SystemSavePayload {
  name: string
  summary: string
  description: string
  owner: string
  status: string
  org_id: number | null
}

// 시스템 인라인 편집 폼 (우측 상세). key={node.id} 로 선택 변경 시 폼이 리셋된다.
export function SystemDetail({
  node,
  flatOrgs,
  onSave,
  onSelectDatasource,
  canManage,
}: {
  node: TopologySystem
  flatOrgs: FlatOrg[]
  canManage: boolean
  onSave: (id: number, payload: SystemSavePayload) => void
  onSelectDatasource: (id: number) => void
}) {
  const [name, setName] = useState(node.name)
  const [summary, setSummary] = useState(node.summary ?? "")
  const [description, setDescription] = useState(node.description ?? "")
  const [owner, setOwner] = useState(node.owner ?? "")
  const [status, setStatus] = useState(node.status)
  const [orgId, setOrgId] = useState<number | null>(node.org_id)
  const [saving, setSaving] = useState(false)
  const idPrefix = useId()

  const dirty =
    name !== node.name ||
    summary !== (node.summary ?? "") ||
    description !== (node.description ?? "") ||
    owner !== (node.owner ?? "") ||
    status !== node.status ||
    orgId !== node.org_id

  const save = async () => {
    if (!name.trim()) return
    setSaving(true)
    await onSave(node.id, {
      name: name.trim(),
      summary,
      description,
      owner,
      status,
      org_id: orgId,
    })
    setSaving(false)
  }

  return (
    <div className="grid gap-4 max-w-xl">
      <h2 className="text-base font-semibold">{node.name}</h2>
      <fieldset disabled={!canManage} className="contents">
      <div className="grid gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor={`${idPrefix}-name`} className="text-sm">이름 <span className="text-destructive">*</span></Label>
          <Input id={`${idPrefix}-name`} value={name} onChange={(e) => setName(e.target.value)} className="h-9 text-sm" />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`${idPrefix}-summary`} className="text-sm">요약</Label>
          <Input
            id={`${idPrefix}-summary`}
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder="한 줄 요약"
            maxLength={200}
            className="h-9 text-sm"
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`${idPrefix}-description`} className="text-sm">설명</Label>
          <Textarea
            id={`${idPrefix}-description`}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="상세 설명"
            rows={4}
            className="text-sm"
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`${idPrefix}-org`} className="text-sm">소속 조직</Label>
          <Select value={orgId ? String(orgId) : "none"} onValueChange={(v) => setOrgId(v === "none" ? null : Number(v))}>
            <SelectTrigger id={`${idPrefix}-org`} className="h-9 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none" className="text-sm">미분류</SelectItem>
              {flatOrgs.map((o) => (
                <SelectItem key={o.id} value={String(o.id)} className="text-sm">
                  {" ".repeat(o.depth * 2)}{o.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor={`${idPrefix}-owner`} className="text-sm">담당자</Label>
            <Input id={`${idPrefix}-owner`} value={owner} onChange={(e) => setOwner(e.target.value)} className="h-9 text-sm" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${idPrefix}-status`} className="text-sm">상태</Label>
            <Select value={status} onValueChange={setStatus}>
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
        </div>
        {canManage && (
          <div className="flex justify-end">
            <Button size="sm" onClick={save} disabled={saving || !name.trim() || !dirty}>
              {saving ? "저장 중..." : "저장"}
            </Button>
          </div>
        )}
      </div>
      </fieldset>

      <div className="grid gap-1.5">
        <Label className="text-sm text-muted-foreground">데이터 소스 ({node.datasources.length})</Label>
        {node.datasources.length > 0 ? (
          <DatasourcesTable rows={node.datasources} onSelect={onSelectDatasource} />
        ) : (
          <p className="text-sm text-muted-foreground">
            데이터 소스가 없습니다. 미분류 데이터 소스를 선택해 이 시스템에 배정하세요.
          </p>
        )}
      </div>
    </div>
  )
}
