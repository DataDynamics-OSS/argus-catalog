"use client"

import { useState } from "react"
import { FolderOpen, Plus } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { DetailSection } from "@/components/detail-section"
import { OriginText } from "@/features/datasets/components/origin-text"
import type { Topology } from "@/features/topology/api"
import { SystemStatusBadge } from "@/features/topology/components/system-status-badge"
import type { FlatOrg, FlatSystem } from "@/features/datasources/utils"

/** 여러 개를 한 번에 배정 — 알림 · 다시 읽기는 호출 측이 한다 */
export type BulkAssignHandler = (ids: number[], targetId: number) => Promise<void>

const n = (v: number) => v.toLocaleString()

/** 고른 것을 한 번에 배정하는 줄 — 대상 선택 + 배정 단추. 고른 게 없으면 숨긴다 */
function AssignBar({
  count, label, placeholder, options, onAssign, onClear,
}: {
  count: number
  label: string
  placeholder: string
  options: { id: number; label: string }[]
  onAssign: (targetId: number) => Promise<void>
  onClear: () => void
}) {
  const [target, setTarget] = useState("")
  const [busy, setBusy] = useState(false)
  if (count === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md bg-muted/60 px-3 py-2 text-sm">
      <span className="font-medium">{n(count)}개 선택</span>
      <Select value={target} onValueChange={setTarget}>
        <SelectTrigger className="h-8 min-w-56 bg-background text-sm" aria-label={placeholder}>
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.id} value={String(o.id)} className="text-sm">{o.label}</SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        size="sm"
        className="h-8"
        disabled={!target || busy}
        onClick={async () => {
          setBusy(true)
          try {
            await onAssign(Number(target))
            setTarget("")
          } finally {
            setBusy(false)
          }
        }}
      >
        {busy ? "배정 중â¦" : label}
      </Button>
      <Button size="sm" variant="ghost" className="ml-auto h-8" onClick={onClear} disabled={busy}>선택 해제</Button>
    </div>
  )
}

/** 표 머리의 전체 선택 체크박스 상태 */
function headState(selected: Set<number>, ids: number[]): boolean | "indeterminate" {
  const hit = ids.filter((id) => selected.has(id)).length
  return hit === 0 ? false : hit === ids.length ? true : "indeterminate"
}

/**
 * 미분류 상세(오른쪽) — 조직 미지정 시스템과 미분류(시스템 없는) 데이터 소스.
 * 관리 권한이 있으면 표에서 여러 개를 골라 한 번에 조직 · 시스템에 배정하고, 구역 옆 단추로 바로 추가한다.
 */
export function UnassignedDetail({
  topology,
  flatOrgs,
  flatSystems,
  canManage,
  onSelect,
  onAddDatasource,
  onAddSystem,
  onAssignDatasources,
  onAssignSystems,
}: {
  topology: Topology
  flatOrgs: FlatOrg[]
  flatSystems: FlatSystem[]
  canManage: boolean
  onSelect: (n: { kind: "system"; id: number } | { kind: "datasource"; id: number }) => void
  onAddDatasource: () => void
  onAddSystem: () => void
  onAssignDatasources: BulkAssignHandler
  onAssignSystems: BulkAssignHandler
}) {
  const systems = topology.unassigned.systems ?? []
  const datasources = topology.unassigned.datasources
  const [pickedSystems, setPickedSystems] = useState<Set<number>>(new Set())
  const [pickedDatasources, setPickedDatasources] = useState<Set<number>>(new Set())

  // 다시 읽은 뒤 사라진(배정된) 항목은 선택에서 뺀다
  const liveSystems = new Set([...pickedSystems].filter((id) => systems.some((s) => s.id === id)))
  const liveDatasources = new Set([...pickedDatasources].filter((id) => datasources.some((d) => d.id === id)))

  const datasetTotal =
    datasources.reduce((acc, d) => acc + d.dataset_count, 0) +
    systems.reduce((acc, s) => acc + s.datasources.reduce((a, d) => a + d.dataset_count, 0), 0)

  const toggle = (set: Set<number>, setter: (s: Set<number>) => void, id: number, on: boolean) => {
    const next = new Set(set)
    if (on) next.add(id)
    else next.delete(id)
    setter(next)
  }

  const nodeButton = (target: { kind: "system"; id: number } | { kind: "datasource"; id: number }, label: string) => (
    <button type="button" title={label} className="max-w-full truncate text-left font-medium text-primary underline-offset-2 hover:underline"
      onClick={() => onSelect(target)}>
      {label}
    </button>
  )

  const empty = systems.length === 0 && datasources.length === 0

  return (
    <div className="grid gap-8">
      {/* 머리 */}
      <div className="grid gap-2">
        <div className="flex items-center gap-2">
          <FolderOpen className="h-5 w-5 text-muted-foreground" />
          <h2 className="text-lg font-semibold">미분류</h2>
        </div>
        <p className="text-sm">
          조직에 속하지 않은 시스템(<span className="font-medium text-foreground">조직 미지정</span>)과 시스템에 속하지 않은
          데이터 소스(<span className="font-medium text-foreground">미분류</span>)가 모입니다.
          {canManage ? " 표에서 골라 한 번에 배정할 수 있습니다." : ""}
        </p>
        <dl className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">조직 미지정 시스템</dt>
            <dd className="tabular-nums">{n(systems.length)}개</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">미분류 데이터 소스</dt>
            <dd className="tabular-nums">{n(datasources.length)}개</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">데이터셋</dt>
            <dd className="tabular-nums">{n(datasetTotal)}개</dd>
          </div>
        </dl>
      </div>

      {empty && (
        <p className="text-sm">모든 데이터 소스와 시스템이 배정되어 있습니다.</p>
      )}

      {/* 조직 미지정 시스템 */}
      {(systems.length > 0 || canManage) && (
        <DetailSection
          title={`조직 미지정 시스템 (${systems.length})`}
          actions={canManage ? (
            <Button size="sm" variant="outline" onClick={onAddSystem}>
              <Plus className="mr-1 h-3.5 w-3.5" />
              시스템 추가
            </Button>
          ) : undefined}
        >
          {systems.length > 0 ? (
            <>
              {canManage && (
                <AssignBar
                  count={liveSystems.size}
                  label="조직에 배정"
                  placeholder="배정할 조직"
                  options={flatOrgs.map((o) => ({ id: o.id, label: o.path }))}
                  onAssign={async (orgId) => {
                    await onAssignSystems([...liveSystems], orgId)
                    setPickedSystems(new Set())
                  }}
                  onClear={() => setPickedSystems(new Set())}
                />
              )}
              <div className="overflow-hidden rounded-md border">
                <Table className="table-fixed">
                  <TableHeader>
                    <TableRow>
                      {canManage && (
                        <TableHead className="w-10">
                          <Checkbox
                            aria-label="시스템 모두 선택"
                            checked={headState(liveSystems, systems.map((s) => s.id))}
                            onCheckedChange={(v) => setPickedSystems(v === true ? new Set(systems.map((s) => s.id)) : new Set())}
                          />
                        </TableHead>
                      )}
                      <TableHead>이름</TableHead>
                      <TableHead className="w-28 text-center">상태</TableHead>
                      <TableHead className="w-48">담당자</TableHead>
                      <TableHead className="w-28 text-center">데이터 소스</TableHead>
                      <TableHead className="w-24 text-center">데이터셋</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {systems.map((s) => (
                      <TableRow key={s.id} data-state={liveSystems.has(s.id) ? "selected" : undefined}>
                        {canManage && (
                          <TableCell>
                            <Checkbox
                              aria-label={`${s.name} 선택`}
                              checked={liveSystems.has(s.id)}
                              onCheckedChange={(v) => toggle(liveSystems, setPickedSystems, s.id, v === true)}
                            />
                          </TableCell>
                        )}
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
            </>
          ) : <p className="text-sm text-muted-foreground">조직 미지정 시스템이 없습니다.</p>}
        </DetailSection>
      )}

      {/* 미분류 데이터 소스 */}
      {(datasources.length > 0 || canManage) && (
        <DetailSection
          title={`미분류 데이터 소스 (${datasources.length})`}
          actions={canManage ? (
            <Button size="sm" variant="outline" onClick={onAddDatasource}>
              <Plus className="mr-1 h-3.5 w-3.5" />
              데이터 소스 추가
            </Button>
          ) : undefined}
        >
          {datasources.length > 0 ? (
            <>
              {canManage && (
                <AssignBar
                  count={liveDatasources.size}
                  label="시스템에 배정"
                  placeholder="배정할 시스템"
                  options={flatSystems.map((s) => ({ id: s.id, label: `${s.name} · ${s.orgName}` }))}
                  onAssign={async (systemId) => {
                    await onAssignDatasources([...liveDatasources], systemId)
                    setPickedDatasources(new Set())
                  }}
                  onClear={() => setPickedDatasources(new Set())}
                />
              )}
              <div className="overflow-hidden rounded-md border">
                <Table className="table-fixed">
                  <TableHeader>
                    <TableRow>
                      {canManage && (
                        <TableHead className="w-10">
                          <Checkbox
                            aria-label="데이터 소스 모두 선택"
                            checked={headState(liveDatasources, datasources.map((d) => d.id))}
                            onCheckedChange={(v) => setPickedDatasources(v === true ? new Set(datasources.map((d) => d.id)) : new Set())}
                          />
                        </TableHead>
                      )}
                      <TableHead>이름</TableHead>
                      <TableHead className="w-32 text-center">유형</TableHead>
                      <TableHead className="w-36 text-center">환경</TableHead>
                      <TableHead className="w-24 text-center">데이터셋</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {datasources.map((d) => (
                      <TableRow key={d.id} data-state={liveDatasources.has(d.id) ? "selected" : undefined}>
                        {canManage && (
                          <TableCell>
                            <Checkbox
                              aria-label={`${d.name} 선택`}
                              checked={liveDatasources.has(d.id)}
                              onCheckedChange={(v) => toggle(liveDatasources, setPickedDatasources, d.id, v === true)}
                            />
                          </TableCell>
                        )}
                        <TableCell className="truncate">{nodeButton({ kind: "datasource", id: d.id }, d.name)}</TableCell>
                        <TableCell className="text-center font-mono text-xs">{d.type}</TableCell>
                        <TableCell className="text-center"><OriginText origin={d.origin} /></TableCell>
                        <TableCell className="text-center tabular-nums">{n(d.dataset_count)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              {canManage && flatSystems.length === 0 && (
                <p className="text-xs text-muted-foreground">배정할 시스템이 없습니다. 먼저 조직 아래에 시스템을 추가하세요.</p>
              )}
            </>
          ) : <p className="text-sm text-muted-foreground">미분류 데이터 소스가 없습니다.</p>}
        </DetailSection>
      )}
    </div>
  )
}
