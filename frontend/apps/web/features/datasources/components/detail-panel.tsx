"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { MoreHorizontal, Server, Trash2 } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import { StatusBadge } from "@workspace/ui/components/status-badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { LINE_TABS_LIST, LINE_TABS_TRIGGER } from "@/components/detail-section"
import { Tip } from "@/components/tip"
import { ErdTab } from "@/features/datasets/components/erd-tab"
import { fetchDatasourceSyncRuns, type DatasourceRow, type SyncRun } from "@/features/datasources/api"
import { findOrg, findSystem, type FlatOrg, type FlatSystem } from "@/features/datasources/utils"
import {
  DatasourceDetail,
  type SaveDatasourceHandler,
} from "@/features/datasources/components/datasource-detail"
import { OriginText } from "@/features/datasets/components/origin-text"
import { CatalogBindings } from "@/features/catalogs/components/catalog-bindings"
import { fetchDatasourceCatalogs, type Catalog } from "@/features/catalogs/api"
import { SyncPanel } from "@/features/datasources/components/sync-panel"
import { SystemDetail, type SaveSystemHandler } from "@/features/datasources/components/system-detail"
import { OrgDetail, type SaveOrgHandler } from "@/features/datasources/components/org-detail"
import { UnassignedDetail, type BulkAssignHandler } from "@/features/datasources/components/unassigned-detail"
import { datasourceDatasetsHref } from "@/features/datasources/links"
import { fmtTime, STATUS_LABEL, STATUS_TONE } from "@/features/datasources/sync-format"
import type { Topology, TopologyDatasource, TopologyOrganization } from "@/features/topology/api"
import type { SelectedNode } from "@/features/topology/components/topology-tree"

/** 데이터 소스 상세 탭 — 주소의 ``tab`` 값과 같다(기본 ``info`` 는 주소에 넣지 않는다). */
export const DATASOURCE_TABS = ["info", "catalogs", "sync", "erd"] as const
export type DatasourceTab = (typeof DATASOURCE_TABS)[number]

interface DetailProps {
  selected: SelectedNode | null
  datasources: DatasourceRow[]
  topology: Topology
  flatOrgs: FlatOrg[]
  flatSystems: FlatSystem[]
  onSelect: (n: SelectedNode) => void
  onSaveSystem: SaveSystemHandler
  onSaveDatasource: SaveDatasourceHandler
  onSaveOrg: SaveOrgHandler
  onDeleteOrg: (id: number) => void
  onAddSubOrg: (parentId: number) => void
  onAddSystem: (orgId: number | null) => void
  onAddDatasourceToSystem: (systemId: number | null) => void
  onAssignDatasources: BulkAssignHandler
  onAssignSystems: BulkAssignHandler
  onDeleteSystem: (id: number) => void
  /** 데이터 소스 삭제 확인 대화상자를 연다(트리 행 메뉴와 같은 동작) */
  onDeleteDatasource: (id: number) => void
  onDirtyChange: (dirty: boolean) => void
  tab: DatasourceTab
  onTabChange: (tab: DatasourceTab) => void
  canManage: boolean
  canSync: boolean
}

// 데이터 소스 전체 ERD — DDL FK 파싱 기반이므로 RDBMS 타입에서만 의미가 있다
// (데이터셋 상세의 더보기 > ER 다이어그램과 동일 게이팅 목록).
const RDBMS_TYPES = ["mysql", "mariadb", "postgresql", "greenplum", "oracle", "mssql", "sqlserver", "starrocks", "tibero"]

/** 토폴로지에서 데이터 소스(데이터셋 수)와 소속 시스템 · 조직 경로를 찾는다 */
function locate(topology: Topology, id: number): { ds: TopologyDatasource | null; path: string[] } {
  const walk = (orgs: TopologyOrganization[], trail: string[]): { ds: TopologyDatasource; path: string[] } | null => {
    for (const o of orgs) {
      for (const s of o.systems) {
        const ds = s.datasources.find((d) => d.id === id)
        if (ds) return { ds, path: [...trail, o.name, s.name] }
      }
      const hit = walk(o.children, [...trail, o.name])
      if (hit) return hit
    }
    return null
  }
  const hit = walk(topology.organizations, [])
  if (hit) return hit
  for (const s of topology.unassigned.systems ?? []) {
    const ds = s.datasources.find((d) => d.id === id)
    if (ds) return { ds, path: ["조직 미지정", s.name] }
  }
  return { ds: topology.unassigned.datasources.find((d) => d.id === id) ?? null, path: [] }
}

/** 데이터 소스 머리 — 이름 · 유형 · 환경 · 소속 · ID · 데이터셋 수 · 마지막 수집 · 연결된 카탈로그 */
function DatasourceHeader({ datasource, topology, canManage, onDelete }: {
  datasource: DatasourceRow
  topology: Topology
  canManage: boolean
  onDelete: (id: number) => void
}) {
  const [lastRun, setLastRun] = useState<SyncRun | null | undefined>(undefined)
  const [catalogs, setCatalogs] = useState<Catalog[] | null>(null)
  useEffect(() => {
    let alive = true
    fetchDatasourceSyncRuns(datasource.id, 1)
      .then((r) => { if (alive) setLastRun(r[0] ?? null) })
      .catch(() => { if (alive) setLastRun(null) })
    fetchDatasourceCatalogs(datasource.id)
      .then((c) => { if (alive) setCatalogs(c) })
      .catch(() => { if (alive) setCatalogs(null) })
    return () => { alive = false }
  }, [datasource.id])

  const { ds, path } = locate(topology, datasource.id)
  const count = ds?.dataset_count ?? null

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Server className="h-5 w-5 text-muted-foreground" />
        <h2 className="text-lg font-semibold">{datasource.name}</h2>
        <Badge variant="outline" className="font-mono">{datasource.type}</Badge>
        <Badge variant="outline"><OriginText origin={datasource.origin} /></Badge>
        {/* 삭제는 ⋯ 메뉴로 — 조직 · 시스템 상세 머리와 같은 자리, 트리 행 메뉴와 같은 권한(TODO-136) */}
        {canManage && (
          <DropdownMenu>
            <Tip label={`${datasource.name} 메뉴`}><DropdownMenuTrigger asChild>
              <Button size="icon" variant="ghost" className="ml-auto h-8 w-8" aria-label={`${datasource.name} 메뉴`}>
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger></Tip>
            <DropdownMenuContent align="end">
              <DropdownMenuItem variant="destructive" onSelect={() => onDelete(datasource.id)}>
                <Trash2 className="mr-2 h-4 w-4" />
                삭제
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
      <dl className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
        <div className="flex gap-1.5">
          <dt className="text-muted-foreground">소속</dt>
          <dd>{path.length ? path.join(" › ") : "미분류"}</dd>
        </div>
        <div className="flex gap-1.5">
          <dt className="text-muted-foreground">ID</dt>
          <dd className="font-mono text-xs leading-5">{datasource.datasource_id}</dd>
        </div>
        <div className="flex gap-1.5">
          <dt className="text-muted-foreground">데이터셋</dt>
          <dd>
            {count == null ? "-" : count > 0 ? (
              <Link href={datasourceDatasetsHref(datasource.datasource_id)} className="text-primary underline-offset-2 hover:underline">
                {count.toLocaleString()}개
              </Link>
            ) : "0개"}
          </dd>
        </div>
        <div className="flex items-center gap-1.5">
          <dt className="text-muted-foreground">마지막 수집</dt>
          <dd className="flex items-center gap-1.5">
            {lastRun === undefined ? "…" : lastRun === null ? "없음" : (
              <>
                <StatusBadge tone={STATUS_TONE[lastRun.status]}>{STATUS_LABEL[lastRun.status]}</StatusBadge>
                <span className="tabular-nums">{fmtTime(lastRun.finished_at ?? lastRun.started_at ?? lastRun.queued_at)}</span>
              </>
            )}
          </dd>
        </div>
        <div className="flex gap-1.5">
          <dt className="text-muted-foreground">카탈로그</dt>
          <dd>{catalogs == null ? "…" : catalogs.length ? catalogs.map((c) => c.name).join(", ") : "연결 없음"}</dd>
        </div>
      </dl>
    </div>
  )
}

// 우측 상세 패널 — 선택된 노드 종류에 따라 상세/편집 폼을 보여 준다
export function DetailPanel(props: DetailProps) {
  const { selected, datasources, topology, flatSystems } = props

  if (!selected) {
    return (
      <p className="py-12 text-center text-sm text-muted-foreground">
        좌측에서 조직 · 시스템 · 데이터 소스를 선택하세요.
      </p>
    )
  }

  // 데이터 소스 상세 — 머리 + 밑줄형 탭(비활성 탭은 그리지 않으므로 ERD 는 탭을 열 때 불러온다)
  if (selected.kind === "datasource") {
    const p = datasources.find((x) => x.id === selected.id)
    if (!p) return <p className="text-sm text-muted-foreground">데이터 소스를 찾을 수 없습니다.</p>
    const hasErd = RDBMS_TYPES.includes(p.type)
    const tab = props.tab === "erd" && !hasErd ? "info" : props.tab
    return (
      <div className="grid gap-4">
        <DatasourceHeader key={p.id} datasource={p} topology={topology} canManage={props.canManage} onDelete={props.onDeleteDatasource} />
        <Tabs value={tab} onValueChange={(v) => props.onTabChange(v as DatasourceTab)}>
          <TabsList variant="line" className={LINE_TABS_LIST}>
            <TabsTrigger value="info" className={LINE_TABS_TRIGGER}>기본 정보</TabsTrigger>
            <TabsTrigger value="catalogs" className={LINE_TABS_TRIGGER}>카탈로그 연결</TabsTrigger>
            <TabsTrigger value="sync" className={LINE_TABS_TRIGGER}>수집</TabsTrigger>
            {hasErd && <TabsTrigger value="erd" className={LINE_TABS_TRIGGER}>ER 다이어그램</TabsTrigger>}
          </TabsList>
          {/* 기본 정보는 다른 탭을 볼 때도 남겨 둔다 — 고치던 내용이 탭을 옮겨도 사라지지 않게 */}
          <TabsContent value="info" forceMount className="pt-4 data-[state=inactive]:hidden">
            <DatasourceDetail
              key={p.id}
              datasource={p}
              flatSystems={flatSystems}
              onSave={props.onSaveDatasource}
              canManage={props.canManage}
              onDirtyChange={props.onDirtyChange}
            />
          </TabsContent>
          <TabsContent value="catalogs" className="max-w-3xl pt-4">
            <CatalogBindings key={p.id} datasource={p} canManage={props.canManage} />
          </TabsContent>
          <TabsContent value="sync" className="max-w-4xl pt-4">
            <SyncPanel key={p.id} datasource={p} canSync={props.canSync} />
          </TabsContent>
          {hasErd && (
            <TabsContent value="erd" className="pt-4">
              <ErdTab key={p.id} datasourceId={p.id} />
            </TabsContent>
          )}
        </Tabs>
      </div>
    )
  }

  // 미분류 — 조직 미지정 시스템 + 미분류 데이터 소스(트리의 미분류 행과 같은 범위, DEF-112)
  if (selected.kind === "unassigned") {
    return (
      <UnassignedDetail
        topology={topology}
        flatOrgs={props.flatOrgs}
        flatSystems={flatSystems}
        canManage={props.canManage}
        onSelect={props.onSelect}
        onAddDatasource={() => props.onAddDatasourceToSystem(null)}
        onAddSystem={() => props.onAddSystem(null)}
        onAssignDatasources={props.onAssignDatasources}
        onAssignSystems={props.onAssignSystems}
      />
    )
  }

  // 시스템 상세 — 인라인 편집 폼
  if (selected.kind === "system") {
    const node = findSystem(topology, selected.id)
    if (!node) return <p className="text-sm text-muted-foreground">시스템을 찾을 수 없습니다.</p>
    return (
      <SystemDetail
        key={node.id}
        node={node}
        flatOrgs={props.flatOrgs}
        onSave={props.onSaveSystem}
        onSelectDatasource={(id) => props.onSelect({ kind: "datasource", id })}
        onAddDatasource={props.onAddDatasourceToSystem}
        onDelete={props.onDeleteSystem}
        onDirtyChange={props.onDirtyChange}
        canManage={props.canManage}
      />
    )
  }

  // 조직 상세
  const org = findOrg(topology.organizations, selected.id)
  if (!org) return <p className="text-sm text-muted-foreground">조직을 찾을 수 없습니다.</p>
  return (
    <OrgDetail
      key={org.id}
      org={org}
      flatOrgs={props.flatOrgs}
      canManage={props.canManage}
      onSave={props.onSaveOrg}
      onSelect={props.onSelect}
      onAddSubOrg={props.onAddSubOrg}
      onAddSystem={props.onAddSystem}
      onDelete={props.onDeleteOrg}
      onDirtyChange={props.onDirtyChange}
    />
  )
}
