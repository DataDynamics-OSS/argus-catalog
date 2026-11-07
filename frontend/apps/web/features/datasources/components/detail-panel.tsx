"use client"

import { ErdTab } from "@/features/datasets/components/erd-tab"
import type { DatasourceRow } from "@/features/datasources/api"
import { findOrg, findSystem, type FlatOrg, type FlatSystem } from "@/features/datasources/utils"
import {
  DatasourceDetail,
  type SaveDatasourceHandler,
} from "@/features/datasources/components/datasource-detail"
import { InfoTable, DatasourcesTable } from "@/features/datasources/components/detail-tables"
import { SystemDetail, type SystemSavePayload } from "@/features/datasources/components/system-detail"
import type { Topology } from "@/features/topology/api"
import type { SelectedNode } from "@/features/topology/components/topology-tree"

interface DetailProps {
  selected: SelectedNode | null
  datasources: DatasourceRow[]
  topology: Topology
  flatOrgs: FlatOrg[]
  flatSystems: FlatSystem[]
  onSelect: (n: SelectedNode) => void
  onSaveSystem: (id: number, payload: SystemSavePayload) => void
  onSaveDatasource: SaveDatasourceHandler
  canManage: boolean
}

// 데이터 소스 전체 ERD — DDL FK 파싱 기반이므로 RDBMS 타입에서만 의미가 있다
// (데이터셋 상세의 더보기 > ER 다이어그램과 동일 게이팅 목록).
const RDBMS_TYPES = ["mysql", "mariadb", "postgresql", "greenplum", "oracle", "mssql", "sqlserver", "starrocks", "tibero"]

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

  // 데이터 소스 상세
  if (selected.kind === "datasource") {
    const p = datasources.find((x) => x.id === selected.id)
    if (!p) return <p className="text-sm text-muted-foreground">데이터 소스를 찾을 수 없습니다.</p>
    return (
      <div className="grid gap-4">
        <DatasourceDetail
          key={p.id}
          datasource={p}
          flatSystems={flatSystems}
          onSave={props.onSaveDatasource}
          canManage={props.canManage}
        />
        {RDBMS_TYPES.includes(p.type) && (
          <div className="grid gap-2">
            <h3 className="text-sm font-semibold">ER 다이어그램 (전체)</h3>
            <ErdTab datasourceId={p.id} />
          </div>
        )}
      </div>
    )
  }

  // 미분류 버킷
  if (selected.kind === "unassigned") {
    const items = topology.unassigned.datasources
    return (
      <div className="grid gap-3">
        <h2 className="text-base font-semibold">미분류 데이터 소스 ({items.length})</h2>
        <p className="text-sm text-muted-foreground">시스템에 배정되지 않은 데이터 소스입니다. 각 데이터 소스를 선택해 시스템에 배정하세요.</p>
        {items.length > 0 ? (
          <DatasourcesTable rows={items} onSelect={(id) => props.onSelect({ kind: "datasource", id })} />
        ) : (
          <p className="text-sm text-muted-foreground">미분류 데이터 소스가 없습니다.</p>
        )}
      </div>
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
        canManage={props.canManage}
      />
    )
  }

  // 조직 상세
  const org = findOrg(topology.organizations, selected.id)
  if (!org) return <p className="text-sm text-muted-foreground">조직을 찾을 수 없습니다.</p>
  return (
    <div className="grid gap-4 max-w-xl">
      <h2 className="text-base font-semibold">{org.name}</h2>
      <InfoTable
        rows={[
          { label: "하위 조직", value: `${org.children.length}개` },
          { label: "시스템", value: `${org.systems.length}개` },
        ]}
      />
    </div>
  )
}
