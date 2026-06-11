"use client"

import { useState } from "react"
import {
  Boxes,
  Building2,
  ChevronDown,
  ChevronRight,
  Database,
  FolderOpen,
} from "lucide-react"

import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@workspace/ui/components/context-menu"
import type {
  Topology,
  TopologyOrganization,
  TopologySystem,
} from "@/features/topology/api"

export type SelectedNode =
  | { kind: "org"; id: number }
  | { kind: "system"; id: number }
  | { kind: "datasource"; id: number }
  | { kind: "unassigned" }

export function nodeKey(n: SelectedNode): string {
  return n.kind === "unassigned" ? "unassigned" : `${n.kind}:${n.id}`
}

const ORIGIN_DOT: Record<string, string> = {
  PROD: "bg-emerald-600",
  STAGING: "bg-orange-500",
  DEV: "bg-sky-500",
}

interface TreeProps {
  topology: Topology
  selected: SelectedNode | null
  onSelect: (node: SelectedNode) => void
  // 조직
  onAddRootOrg: () => void
  onAddDatasource: () => void
  onAddSubOrg: (parentId: number) => void
  onAddSystem: (orgId: number) => void
  onEditOrg: (orgId: number) => void
  onDeleteOrg: (orgId: number) => void
  // 시스템
  onAddDatasourceToSystem: (systemId: number) => void
  onDeleteSystem: (systemId: number) => void
  // 데이터 소스
  onDeleteDatasource: (datasourceId: number) => void
}

export function TopologyTree({
  topology,
  selected,
  onSelect,
  onAddRootOrg,
  onAddDatasource,
  onAddDatasourceToSystem,
  onAddSubOrg,
  onAddSystem,
  onEditOrg,
  onDeleteOrg,
  onDeleteSystem,
  onDeleteDatasource,
}: TreeProps) {
  // 펼침 상태 (기본 전체 펼침)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const isOpen = (key: string) => !collapsed.has(key)
  const toggle = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const selKey = selected ? nodeKey(selected) : ""

  const rowClass = (active: boolean) =>
    // 높이 30px 고정 — hover 액션 아이콘 등장/소멸 시 행 높이가 변하지 않도록.
    "group flex h-[30px] items-center gap-1 overflow-hidden rounded px-1.5 text-sm cursor-pointer " +
    (active ? "bg-accent text-accent-foreground" : "hover:bg-muted")

  function renderDatasource(p: TopologySystem["datasources"][number], depth: number) {
    const active = selKey === `datasource:${p.id}`
    return (
      <ContextMenu key={`datasource:${p.id}`}>
        <ContextMenuTrigger asChild>
          <div
            className={rowClass(active)}
            style={{ paddingLeft: depth * 16 + 6 }}
            onClick={() => onSelect({ kind: "datasource", id: p.id })}
          >
            <span className="w-4 shrink-0" />
            <span className={`h-2 w-2 shrink-0 rounded-full ${ORIGIN_DOT[p.origin] ?? "bg-muted-foreground"}`} />
            <Database className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate">{p.name}</span>
            <span className="ml-auto shrink-0 rounded bg-muted px-1.5 text-xs text-muted-foreground tabular-nums">
              {p.dataset_count}
            </span>
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent className="w-44">
          <ContextMenuItem variant="destructive" onClick={() => onDeleteDatasource(p.id)}>
            삭제
          </ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>
    )
  }

  function renderSystem(s: TopologySystem, depth: number) {
    const key = `system:${s.id}`
    const open = isOpen(key)
    const active = selKey === key
    const hasChildren = s.datasources.length > 0
    return (
      <div key={key}>
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <div
              className={rowClass(active)}
              style={{ paddingLeft: depth * 16 + 6 }}
              onClick={() => onSelect({ kind: "system", id: s.id })}
            >
              <button
                type="button"
                className="shrink-0 text-muted-foreground"
                onClick={(e) => {
                  e.stopPropagation()
                  toggle(key)
                }}
              >
                {hasChildren ? (
                  open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />
                ) : (
                  <span className="inline-block h-3.5 w-3.5" />
                )}
              </button>
              <Boxes className="h-3.5 w-3.5 shrink-0 text-amber-600" />
              <span className="truncate">{s.name}</span>
              {s.status !== "ACTIVE" && (
                <span className="shrink-0 rounded border px-1 text-[10px] text-muted-foreground">
                  {s.status}
                </span>
              )}
              <span className="ml-auto shrink-0 rounded bg-muted px-1.5 text-xs text-muted-foreground tabular-nums">
                {s.datasources.length}
              </span>
            </div>
          </ContextMenuTrigger>
          <ContextMenuContent className="w-44">
            <ContextMenuItem onClick={() => onAddDatasourceToSystem(s.id)}>데이터 소스 추가</ContextMenuItem>
            <ContextMenuSeparator />
            <ContextMenuItem variant="destructive" onClick={() => onDeleteSystem(s.id)}>
              삭제
            </ContextMenuItem>
          </ContextMenuContent>
        </ContextMenu>
        {open && s.datasources.map((p) => renderDatasource(p, depth + 1))}
      </div>
    )
  }

  function renderOrg(o: TopologyOrganization, depth: number) {
    const key = `org:${o.id}`
    const open = isOpen(key)
    const active = selKey === key
    const hasChildren = o.children.length > 0 || o.systems.length > 0
    return (
      <div key={key}>
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <div
              className={rowClass(active)}
              style={{ paddingLeft: depth * 16 + 6 }}
              onClick={() => onSelect({ kind: "org", id: o.id })}
            >
              <button
                type="button"
                className="shrink-0 text-muted-foreground"
                onClick={(e) => {
                  e.stopPropagation()
                  toggle(key)
                }}
              >
                {hasChildren ? (
                  open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />
                ) : (
                  <span className="inline-block h-3.5 w-3.5" />
                )}
              </button>
              <Building2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate font-medium">{o.name}</span>
            </div>
          </ContextMenuTrigger>
          <ContextMenuContent className="w-44">
            <ContextMenuItem onClick={() => onAddSubOrg(o.id)}>하위 조직 추가</ContextMenuItem>
            <ContextMenuItem onClick={() => onAddSystem(o.id)}>시스템 추가</ContextMenuItem>
            <ContextMenuSeparator />
            <ContextMenuItem onClick={() => onEditOrg(o.id)}>이름 변경</ContextMenuItem>
            <ContextMenuItem variant="destructive" onClick={() => onDeleteOrg(o.id)}>
              삭제
            </ContextMenuItem>
          </ContextMenuContent>
        </ContextMenu>
        {open && (
          <>
            {o.children.map((c) => renderOrg(c, depth + 1))}
            {o.systems.map((s) => renderSystem(s, depth + 1))}
          </>
        )}
      </div>
    )
  }

  const unassignedActive = selKey === "unassigned"
  const unassignedSystems = topology.unassigned.systems ?? []
  const unassignedDatasources = topology.unassigned.datasources
  const unassignedCount = unassignedSystems.length + unassignedDatasources.length

  const rootOpen = isOpen("__root__")
  const unassignedOpen = isOpen("__unassigned__")

  return (
    <div className="flex flex-col gap-0.5">
      {/* 최상위 "조직" 루트 — 삭제 불가, 조직 추가만 제공 */}
      <ContextMenu>
        <ContextMenuTrigger asChild>
          <div
            className={rowClass(false)}
            style={{ paddingLeft: 6 }}
            onClick={() => toggle("__root__")}
          >
            <button
              type="button"
              className="shrink-0 text-muted-foreground"
              onClick={(e) => {
                e.stopPropagation()
                toggle("__root__")
              }}
            >
              {rootOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            </button>
            <Building2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate font-semibold">조직</span>
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent className="w-44">
          <ContextMenuItem onClick={() => onAddRootOrg()}>조직 추가</ContextMenuItem>
          <ContextMenuItem onClick={() => onAddDatasource()}>데이터 소스 추가</ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>

      {rootOpen && (
        <>
          {topology.organizations.length === 0 && (
            <p
              className="py-2 text-center text-xs text-muted-foreground"
              style={{ paddingLeft: 22 }}
            >
              조직이 없습니다. &quot;조직&quot; 우클릭 → 조직 추가
            </p>
          )}
          {topology.organizations.map((o) => renderOrg(o, 1))}

          {/* 미분류 — 항상 표시, 삭제 불가. 우클릭 → 데이터 소스 추가만 제공 */}
          <div>
            <ContextMenu>
              <ContextMenuTrigger asChild>
                <div
                  className={rowClass(unassignedActive)}
                  style={{ paddingLeft: 1 * 16 + 6 }}
                  onClick={() => onSelect({ kind: "unassigned" })}
                >
                  <button
                    type="button"
                    className="shrink-0 text-muted-foreground"
                    onClick={(e) => {
                      e.stopPropagation()
                      toggle("__unassigned__")
                    }}
                  >
                    {unassignedCount > 0 ? (
                      unassignedOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />
                    ) : (
                      <span className="inline-block h-3.5 w-3.5" />
                    )}
                  </button>
                  <FolderOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="truncate">미분류</span>
                  <span className="ml-auto shrink-0 rounded bg-muted px-1.5 text-xs text-muted-foreground tabular-nums">
                    {unassignedCount}
                  </span>
                </div>
              </ContextMenuTrigger>
              <ContextMenuContent className="w-44">
                <ContextMenuItem onClick={() => onAddDatasource()}>데이터 소스 추가</ContextMenuItem>
              </ContextMenuContent>
            </ContextMenu>
            {unassignedOpen && (
              <>
                {unassignedSystems.map((s) => renderSystem(s, 2))}
                {unassignedDatasources.map((p) => renderDatasource(p, 2))}
              </>
            )}
          </div>
        </>
      )}
    </div>
  )
}
