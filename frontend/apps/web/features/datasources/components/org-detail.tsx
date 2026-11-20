"use client"

import { Tip } from "@/components/tip"
import { useEffect, useId, useMemo, useState } from "react"
import { Boxes, Building2, MoreHorizontal, Plus, Trash2 } from "lucide-react"

import { MenuItemContent } from "@/components/menu-item-content"

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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { Textarea } from "@workspace/ui/components/textarea"
import { DetailSection } from "@/components/detail-section"
import type { TopologyOrganization } from "@/features/topology/api"
import { SystemStatusBadge } from "@/features/topology/components/system-status-badge"
import { InfoTable } from "@/features/datasources/components/detail-tables"
import { orgTotals, type FlatOrg } from "@/features/datasources/utils"

export interface OrgSavePayload {
  name: string
  parent_id: number | null
  description: string
}

/** 저장 결과 — 실패하면 false(폼은 고친 내용을 그대로 둔다) */
export type SaveOrgHandler = (id: number, payload: OrgSavePayload) => Promise<boolean>

/** 자기 자신과 모든 하위 조직 id — 상위 조직 선택지에서 뺀다(순환 이동 방지, 서버도 거부) */
function selfAndDescendants(o: TopologyOrganization, acc = new Set<number>()): Set<number> {
  acc.add(o.id)
  for (const c of o.children) selfAndDescendants(c, acc)
  return acc
}

const n = (v: number) => v.toLocaleString()

/**
 * 조직 상세(오른쪽). key={org.id} 로 선택이 바뀌면 다시 만든다.
 * - 머리: 경로 · 하위 조직 · 시스템 · 데이터 소스 · 데이터셋(하위 포함), 작업 메뉴(하위 조직 · 시스템 추가, 삭제)
 * - 기본 정보: 이름 · 상위 조직(이동) · 설명. 고친 내용이 있으면 아래 "저장 안 된 변경" 줄. 관리 권한이 없으면 표
 * - 하위 조직 · 시스템 표
 */
export function OrgDetail({
  org,
  flatOrgs,
  canManage,
  onSave,
  onSelect,
  onAddSubOrg,
  onAddSystem,
  onDelete,
  onDirtyChange,
}: {
  org: TopologyOrganization
  flatOrgs: FlatOrg[]
  canManage: boolean
  onSave: SaveOrgHandler
  onSelect: (n: { kind: "org"; id: number } | { kind: "system"; id: number }) => void
  onAddSubOrg: (parentId: number) => void
  onAddSystem: (orgId: number) => void
  onDelete: (id: number) => void
  onDirtyChange?: (dirty: boolean) => void
}) {
  const [name, setName] = useState(org.name)
  const [parentId, setParentId] = useState<number | null>(org.parent_id)
  const [description, setDescription] = useState(org.description ?? "")
  const [saving, setSaving] = useState(false)
  const idPrefix = useId()

  const dirty =
    canManage && (name !== org.name || parentId !== org.parent_id || description !== (org.description ?? ""))

  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])
  // 다른 노드를 고르거나 화면을 떠나면 이 폼은 사라진다 — 변경 없음으로 되돌려 둔다
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange])

  const excluded = useMemo(() => selfAndDescendants(org), [org])
  const parentChoices = flatOrgs.filter((o) => !excluded.has(o.id))
  const path = flatOrgs.find((o) => o.id === org.id)?.path ?? org.name
  const parentPath = (id: number | null) => (id == null ? null : flatOrgs.find((o) => o.id === id)?.path ?? String(id))
  const totals = orgTotals(org)
  // 하위 조직 · 시스템이 남아 있으면 서버가 삭제를 거부한다 — 미리 막고 이유를 보인다
  const deleteBlocked = org.children.length > 0 || org.systems.length > 0

  const reset = () => {
    setName(org.name)
    setParentId(org.parent_id)
    setDescription(org.description ?? "")
  }

  const save = async () => {
    if (!dirty || !name.trim()) return
    setSaving(true)
    try {
      await onSave(org.id, { name: name.trim(), parent_id: parentId, description })
    } finally {
      setSaving(false)
    }
  }

  const nodeButton = (target: { kind: "org"; id: number } | { kind: "system"; id: number }, label: string) => (
    <button type="button" title={label} className="max-w-full truncate text-left font-medium text-primary underline-offset-2 hover:underline"
      onClick={() => onSelect(target)}>
      {label}
    </button>
  )

  return (
    <div className="grid gap-8">
      {/* 머리 */}
      <div className="grid gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Building2 className="h-5 w-5 text-muted-foreground" />
          <h2 className="text-lg font-semibold">{org.name}</h2>
          {canManage && (
            <DropdownMenu>
              <Tip label={`${org.name} 메뉴`}><DropdownMenuTrigger asChild>
                <Button size="icon" variant="ghost" className="ml-auto h-8 w-8" aria-label={`${org.name} 메뉴`}>
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger></Tip>
              <DropdownMenuContent align="end" className="max-w-72">
                <DropdownMenuItem onSelect={() => onAddSubOrg(org.id)}>
                  <Building2 className="mr-2 h-4 w-4" />
                  하위 조직 추가
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => onAddSystem(org.id)}>
                  <Boxes className="mr-2 h-4 w-4" />
                  시스템 추가
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" disabled={deleteBlocked} onSelect={() => onDelete(org.id)}>
                  <MenuItemContent
                    icon={Trash2}
                    label="삭제"
                    hint={deleteBlocked
                      ? `하위 조직 ${org.children.length}개 · 시스템 ${org.systems.length}개를 먼저 옮기거나 지워야 삭제할 수 있습니다.`
                      : undefined}
                  />
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
        {org.description && <p className="line-clamp-2 text-sm">{org.description}</p>}
        <dl className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">경로</dt>
            <dd>{path}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">하위 조직</dt>
            <dd className="tabular-nums">{n(org.children.length)}개</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">시스템</dt>
            <dd className="tabular-nums">{n(totals.systems)}개</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">데이터 소스</dt>
            <dd className="tabular-nums">{n(totals.datasources)}개</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">데이터셋</dt>
            <dd className="tabular-nums">{n(totals.datasets)}개</dd>
          </div>
          <span className="text-xs leading-5">(하위 조직 포함)</span>
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
              <Label htmlFor={`${idPrefix}-parent`} className="text-sm">상위 조직</Label>
              <Select value={parentId ? String(parentId) : "none"} onValueChange={(v) => setParentId(v === "none" ? null : Number(v))}>
                <SelectTrigger id={`${idPrefix}-parent`} className="h-9 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none" className="text-sm">없음 (최상위)</SelectItem>
                  {parentChoices.map((o) => (
                    <SelectItem key={o.id} value={String(o.id)} className="text-sm">{o.path}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                바꾸면 하위 조직 · 시스템 · 데이터 소스가 함께 옮겨 갑니다. 자기 자신과 하위 조직 아래로는 옮길 수 없습니다.
              </p>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={`${idPrefix}-description`} className="text-sm">설명</Label>
              <Textarea
                id={`${idPrefix}-description`}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="하는 일, 담당 업무, 연락처 등"
                rows={3}
                className="text-sm"
              />
            </div>
          </div>

          {/* 저장 줄 — 고친 내용이 있을 때 화면 아래에 붙는다 */}
          {dirty && (
            <div className="sticky -bottom-4 z-10 -mx-1 flex items-center gap-2 border-t bg-background/95 px-1 py-2 backdrop-blur">
              <span className="text-sm text-amber-700 dark:text-amber-400">저장 안 된 변경이 있습니다</span>
              <span className="ml-auto flex gap-2">
                <Button size="sm" variant="ghost" onClick={reset} disabled={saving}>취소</Button>
                <Button size="sm" onClick={() => void save()} disabled={saving || !name.trim()}>
                  {saving ? "저장 중â¦" : "저장"}
                </Button>
              </span>
            </div>
          )}
        </DetailSection>
      ) : (
        <DetailSection title="기본 정보" className="max-w-2xl">
          <InfoTable rows={[
            { label: "이름", value: org.name },
            {
              label: "상위 조직",
              value: org.parent_id != null
                ? nodeButton({ kind: "org", id: org.parent_id }, parentPath(org.parent_id) ?? "")
                : "없음 (최상위)",
            },
            { label: "설명", value: org.description ? <span className="whitespace-pre-wrap">{org.description}</span> : "-" },
          ]} />
        </DetailSection>
      )}

      {/* 하위 조직 */}
      <DetailSection
        title={`하위 조직 (${org.children.length})`}
        actions={canManage ? (
          <Button size="sm" variant="outline" onClick={() => onAddSubOrg(org.id)}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            하위 조직 추가
          </Button>
        ) : undefined}
      >
        {org.children.length ? (
          <div className="overflow-hidden rounded-md border">
            <Table className="table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead>이름</TableHead>
                  <TableHead className="w-24 text-center">하위 조직</TableHead>
                  <TableHead className="w-24 text-center">시스템</TableHead>
                  <TableHead className="w-28 text-center">데이터 소스</TableHead>
                  <TableHead className="w-24 text-center">데이터셋</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {org.children.map((c) => {
                  const t = orgTotals(c)
                  return (
                    <TableRow key={c.id}>
                      <TableCell className="truncate">{nodeButton({ kind: "org", id: c.id }, c.name)}</TableCell>
                      <TableCell className="text-center tabular-nums">{n(c.children.length)}</TableCell>
                      <TableCell className="text-center tabular-nums">{n(t.systems)}</TableCell>
                      <TableCell className="text-center tabular-nums">{n(t.datasources)}</TableCell>
                      <TableCell className="text-center tabular-nums">{n(t.datasets)}</TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>
        ) : <p className="text-sm text-muted-foreground">하위 조직이 없습니다.</p>}
        {org.children.length > 0 && <p className="text-xs">수는 그 조직의 하위 조직까지 합한 값입니다.</p>}
      </DetailSection>

      {/* 시스템 */}
      <DetailSection
        title={`시스템 (${org.systems.length})`}
        actions={canManage ? (
          <Button size="sm" variant="outline" onClick={() => onAddSystem(org.id)}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            시스템 추가
          </Button>
        ) : undefined}
      >
        {org.systems.length ? (
          <div className="overflow-hidden rounded-md border">
            <Table className="table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead>이름</TableHead>
                  <TableHead className="w-28 text-center">상태</TableHead>
                  <TableHead className="w-48">담당자</TableHead>
                  <TableHead className="w-28 text-center">데이터 소스</TableHead>
                  <TableHead className="w-24 text-center">데이터셋</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {org.systems.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="truncate">{nodeButton({ kind: "system", id: s.id }, s.name)}</TableCell>
                    <TableCell className="text-center"><SystemStatusBadge status={s.status} /></TableCell>
                    <TableCell className="truncate" title={s.owner ?? undefined}>{s.owner || "-"}</TableCell>
                    <TableCell className="text-center tabular-nums">{n(s.datasources.length)}</TableCell>
                    <TableCell className="text-center tabular-nums">
                      {n(s.datasources.reduce((acc, d) => acc + d.dataset_count, 0))}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : <p className="text-sm text-muted-foreground">이 조직에 바로 속한 시스템이 없습니다.</p>}
      </DetailSection>
    </div>
  )
}
