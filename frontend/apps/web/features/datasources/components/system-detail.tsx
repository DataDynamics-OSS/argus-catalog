"use client"

import { Tip } from "@/components/tip"
import { useEffect, useId, useState } from "react"
import { Boxes, Database, MoreHorizontal, Plus, Trash2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
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
import { DetailSection } from "@/components/detail-section"
import type { TopologySystem } from "@/features/topology/api"
import { SystemStatusBadge } from "@/features/topology/components/system-status-badge"
import { SYSTEM_STATUSES, systemStatusLabel } from "@/features/topology/system-status"
import type { FlatOrg } from "@/features/datasources/utils"
import { DatasourcesTable, InfoTable } from "@/features/datasources/components/detail-tables"

export interface SystemSavePayload {
  name: string
  summary: string
  description: string
  owner: string
  status: string
  org_id: number | null
}

/** 저장 결과 — 실패하면 false(폼은 고친 내용을 그대로 둔다) */
export type SaveSystemHandler = (id: number, payload: SystemSavePayload) => Promise<boolean>

/**
 * 시스템 상세(오른쪽). key={node.id} 로 선택이 바뀌면 다시 만든다.
 * - 머리: 이름 · 상태 · 요약, 소속 조직 경로 · 담당자 · 데이터 소스 수 · 데이터셋 합계, 작업 메뉴(데이터 소스 추가 · 삭제)
 * - 기본 정보: 관리 권한이 있으면 폼(고친 내용이 있으면 아래 "저장 안 된 변경" 줄), 없으면 키·값 표
 * - 데이터 소스: 이 시스템에 배정된 데이터 소스 표
 */
export function SystemDetail({
  node,
  flatOrgs,
  onSave,
  onSelectDatasource,
  onAddDatasource,
  onDelete,
  onDirtyChange,
  canManage,
}: {
  node: TopologySystem
  flatOrgs: FlatOrg[]
  canManage: boolean
  onSave: SaveSystemHandler
  onSelectDatasource: (id: number) => void
  onAddDatasource: (systemId: number) => void
  onDelete: (systemId: number) => void
  onDirtyChange?: (dirty: boolean) => void
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
    canManage && (
      name !== node.name ||
      summary !== (node.summary ?? "") ||
      description !== (node.description ?? "") ||
      owner !== (node.owner ?? "") ||
      status !== node.status ||
      orgId !== node.org_id
    )

  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])
  // 다른 노드를 고르거나 화면을 떠나면 이 폼은 사라진다 — 변경 없음으로 되돌려 둔다
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange])

  const reset = () => {
    setName(node.name)
    setSummary(node.summary ?? "")
    setDescription(node.description ?? "")
    setOwner(node.owner ?? "")
    setStatus(node.status)
    setOrgId(node.org_id)
  }

  const save = async () => {
    if (!dirty || !name.trim()) return
    setSaving(true)
    try {
      await onSave(node.id, {
        name: name.trim(),
        summary,
        description,
        owner: owner.trim(),
        status,
        org_id: orgId,
      })
    } finally {
      setSaving(false)
    }
  }

  const orgPath = (id: number | null) =>
    id == null ? "조직 미지정" : flatOrgs.find((o) => o.id === id)?.path ?? String(id)
  const datasetTotal = node.datasources.reduce((n, d) => n + d.dataset_count, 0)

  return (
    <div className="grid gap-8">
      {/* 머리 */}
      <div className="grid gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Boxes className="h-5 w-5 text-amber-600" />
          <h2 className="text-lg font-semibold">{node.name}</h2>
          <SystemStatusBadge status={node.status} />
          {canManage && (
            <DropdownMenu>
              <Tip label={`${node.name} 메뉴`}><DropdownMenuTrigger asChild>
                <Button size="icon" variant="ghost" className="ml-auto h-8 w-8" aria-label={`${node.name} 메뉴`}>
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger></Tip>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => onAddDatasource(node.id)}>
                  <Database className="mr-2 h-4 w-4" />
                  데이터 소스 추가
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onSelect={() => onDelete(node.id)}>
                  <Trash2 className="mr-2 h-4 w-4" />
                  삭제
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
        {node.summary && <p className="text-sm">{node.summary}</p>}
        <dl className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">소속</dt>
            <dd>{orgPath(node.org_id)}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">담당자</dt>
            <dd>{node.owner || "-"}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">데이터 소스</dt>
            <dd className="tabular-nums">{node.datasources.length.toLocaleString()}개</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">데이터셋</dt>
            <dd className="tabular-nums">{datasetTotal.toLocaleString()}개</dd>
          </div>
        </dl>
      </div>

      {/* 기본 정보 */}
      {canManage ? (
        <DetailSection title="기본 정보" className="max-w-2xl">
          <div className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor={`${idPrefix}-name`} className="text-sm">이름 <span className="text-destructive">*</span></Label>
              <Input id={`${idPrefix}-name`} value={name} onChange={(e) => setName(e.target.value)} maxLength={200} className="h-9 text-sm" />
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
                  <SelectItem value="none" className="text-sm">조직 미지정</SelectItem>
                  {flatOrgs.map((o) => (
                    <SelectItem key={o.id} value={String(o.id)} className="text-sm">{o.path}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-1.5">
                <Label htmlFor={`${idPrefix}-owner`} className="text-sm">담당자</Label>
                <Input
                  id={`${idPrefix}-owner`}
                  value={owner}
                  onChange={(e) => setOwner(e.target.value)}
                  placeholder="예: 홍길동 (data-team@example.go.kr)"
                  maxLength={200}
                  className="h-9 text-sm"
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor={`${idPrefix}-status`} className="text-sm">상태</Label>
                <Select value={status} onValueChange={setStatus}>
                  <SelectTrigger id={`${idPrefix}-status`} className="h-9 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SYSTEM_STATUSES.map((v) => (
                      <SelectItem key={v} value={v} className="text-sm">{systemStatusLabel(v)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <p className="text-xs">담당자는 이름과 연락처(이메일 · 부서)를 함께 적어 두면 문의할 곳을 바로 찾을 수 있습니다.</p>
          </div>

          {/* 저장 줄 — 고친 내용이 있을 때 화면 아래에 붙는다 */}
          {dirty && (
            <div className="sticky bottom-0 z-10 -mx-1 flex items-center gap-2 border-t bg-background/95 px-1 py-2 backdrop-blur">
              <span className="text-sm text-amber-700 dark:text-amber-400">저장 안 된 변경이 있습니다</span>
              <span className="ml-auto flex gap-2">
                <Button size="sm" variant="ghost" onClick={reset} disabled={saving}>취소</Button>
                <Button size="sm" onClick={() => void save()} disabled={saving || !name.trim()}>
                  {saving ? "저장 중…" : "저장"}
                </Button>
              </span>
            </div>
          )}
        </DetailSection>
      ) : (
        <DetailSection title="기본 정보" className="max-w-2xl">
          <InfoTable rows={[
            { label: "이름", value: node.name },
            { label: "요약", value: node.summary || "-" },
            { label: "설명", value: node.description ? <span className="whitespace-pre-wrap">{node.description}</span> : "-" },
            { label: "소속 조직", value: orgPath(node.org_id) },
            { label: "담당자", value: node.owner || "-" },
            { label: "상태", value: <SystemStatusBadge status={node.status} /> },
          ]} />
        </DetailSection>
      )}

      {/* 데이터 소스 */}
      <DetailSection
        title={`데이터 소스 (${node.datasources.length})`}
        actions={canManage ? (
          <Button size="sm" variant="outline" onClick={() => onAddDatasource(node.id)}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            데이터 소스 추가
          </Button>
        ) : undefined}
      >
        {node.datasources.length > 0 ? (
          <DatasourcesTable rows={node.datasources} onSelect={onSelectDatasource} />
        ) : (
          <p className="text-sm text-muted-foreground">
            데이터 소스가 없습니다.
            {canManage ? " 새로 추가하거나, 미분류 데이터 소스의 기본 정보에서 소속 시스템을 이 시스템으로 바꾸세요." : ""}
          </p>
        )}
      </DetailSection>
    </div>
  )
}
