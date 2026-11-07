"use client"

import { useRef, useState, type KeyboardEvent, type ReactNode } from "react"
import {
  Boxes,
  Building2,
  ChevronDown,
  ChevronRight,
  Database,
  FolderOpen,
  MoreHorizontal,
} from "lucide-react"

import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@workspace/ui/components/context-menu"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
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

// 펼침/포커스 추적용 트리 키 (조직 루트·미분류 버킷은 선택 키와 별개)
const ROOT_KEY = "__root__"
const UNASSIGNED_KEY = "__unassigned__"

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
  /** 관리 권한이 없으면 우클릭 메뉴(추가/편집/삭제)를 막는다 */
  readOnly?: boolean
}

/** 행 작업 — 우클릭 메뉴와 키보드/"…" 버튼 메뉴가 같은 목록을 쓴다 */
interface RowAction {
  label: string
  onSelect: () => void
  destructive?: boolean
  separatorBefore?: boolean
}

interface RowSpec {
  treeKey: string
  parentKey: string | null
  level: number
  /** 화면 들여쓰기(px) */
  paddingLeft: number
  active: boolean
  /** 선택 가능한 노드인지 (조직 루트는 펼침/접기만) */
  selectable: boolean
  expandable: boolean
  expanded: boolean
  /** 클릭·Enter·Space 동작 */
  onActivate: () => void
  actions: RowAction[]
  /** 행 내용(아이콘·이름·배지) */
  content: ReactNode
  /** 내용에 우측 정렬 배지가 없으면 "…" 버튼을 우측 끝으로 민다 */
  pushActionsRight: boolean
  menuLabel: string
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
  readOnly = false,
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

  // 키보드 탐색 — roving tabindex (트리 안에서 Tab 정지점은 한 행뿐)
  const treeRef = useRef<HTMLDivElement>(null)
  const [focusedKey, setFocusedKey] = useState<string | null>(null)
  // 키보드로 연 행 작업 메뉴(DropdownMenu)의 행 키
  const [menuKey, setMenuKey] = useState<string | null>(null)
  // 키보드(Shift+F10 / ContextMenu 키)로 메뉴를 연 직후 뒤따르는 브라우저 contextmenu 이벤트는 무시
  const keyboardMenuAt = useRef(0)

  const selKey = selected ? nodeKey(selected) : ""
  const selTreeKey = selKey === "unassigned" ? UNASSIGNED_KEY : selKey

  const unassignedActive = selKey === "unassigned"
  const unassignedSystems = topology.unassigned.systems ?? []
  const unassignedDatasources = topology.unassigned.datasources
  const unassignedCount = unassignedSystems.length + unassignedDatasources.length

  const rootOpen = isOpen(ROOT_KEY)
  const unassignedOpen = isOpen(UNASSIGNED_KEY)

  // 현재 화면에 보이는 행 키 — 포커스 행이 접혀 사라지면 선택 행/루트로 Tab 정지점을 옮긴다
  const visibleKeys = new Set<string>([ROOT_KEY])
  const collectSystem = (s: TopologySystem) => {
    const key = `system:${s.id}`
    visibleKeys.add(key)
    if (isOpen(key)) for (const p of s.datasources) visibleKeys.add(`datasource:${p.id}`)
  }
  const collectOrg = (o: TopologyOrganization) => {
    const key = `org:${o.id}`
    visibleKeys.add(key)
    if (!isOpen(key)) return
    o.children.forEach(collectOrg)
    o.systems.forEach(collectSystem)
  }
  if (rootOpen) {
    topology.organizations.forEach(collectOrg)
    visibleKeys.add(UNASSIGNED_KEY)
    if (unassignedOpen) {
      unassignedSystems.forEach(collectSystem)
      for (const p of unassignedDatasources) visibleKeys.add(`datasource:${p.id}`)
    }
  }
  const tabbableKey =
    focusedKey && visibleKeys.has(focusedKey)
      ? focusedKey
      : visibleKeys.has(selTreeKey)
        ? selTreeKey
        : ROOT_KEY

  const treeItems = () =>
    Array.from(treeRef.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? [])
  const focusRow = (key: string) =>
    treeRef.current?.querySelector<HTMLElement>(`[role="treeitem"][data-tree-key="${key}"]`)?.focus()

  const handleRowKeyDown = (e: KeyboardEvent<HTMLDivElement>, row: RowSpec) => {
    // 행 안의 버튼·포털 메뉴에서 버블링된 이벤트는 무시
    if (e.target !== e.currentTarget) return
    const openMenu = () => {
      if (readOnly || row.actions.length === 0) return
      e.preventDefault()
      keyboardMenuAt.current = Date.now()
      setMenuKey(row.treeKey)
    }
    switch (e.key) {
      case "ArrowDown":
      case "ArrowUp": {
        e.preventDefault()
        const items = treeItems()
        const i = items.indexOf(e.currentTarget)
        items[e.key === "ArrowDown" ? i + 1 : i - 1]?.focus()
        break
      }
      case "Home":
      case "End": {
        e.preventDefault()
        const items = treeItems()
        items[e.key === "Home" ? 0 : items.length - 1]?.focus()
        break
      }
      case "ArrowRight": {
        e.preventDefault()
        if (!row.expandable) break
        if (!row.expanded) {
          toggle(row.treeKey)
        } else {
          // 이미 펼쳐져 있으면 첫 자식으로 이동
          const items = treeItems()
          const next = items[items.indexOf(e.currentTarget) + 1]
          if (next?.dataset.parentKey === row.treeKey) next.focus()
        }
        break
      }
      case "ArrowLeft": {
        e.preventDefault()
        if (row.expandable && row.expanded) toggle(row.treeKey)
        else if (row.parentKey) focusRow(row.parentKey)
        break
      }
      case "Enter":
      case " ":
        e.preventDefault()
        row.onActivate()
        break
      case "F10":
        if (e.shiftKey) openMenu()
        break
      case "ContextMenu":
        openMenu()
        break
    }
  }

  const rowClass = (active: boolean) =>
    // 높이 30px 고정 — hover 액션 아이콘 등장/소멸 시 행 높이가 변하지 않도록.
    "group flex h-[30px] items-center gap-1 overflow-hidden rounded px-1.5 text-sm cursor-pointer " +
    "outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring " +
    (active ? "bg-accent text-accent-foreground" : "hover:bg-muted")

  function renderContextItems(actions: RowAction[]) {
    return actions.flatMap((a) => [
      ...(a.separatorBefore ? [<ContextMenuSeparator key={`${a.label}-sep`} />] : []),
      <ContextMenuItem
        key={a.label}
        variant={a.destructive ? "destructive" : undefined}
        onClick={() => a.onSelect()}
      >
        {a.label}
      </ContextMenuItem>,
    ])
  }

  function renderActionMenu(row: RowSpec) {
    return (
      // 메뉴(포털 포함) 안의 클릭/우클릭이 행 선택·행 우클릭 메뉴로 번지지 않도록 막는다
      <span
        className={`flex shrink-0 items-center ${row.pushActionsRight ? "ml-auto" : ""}`}
        onClick={(e) => e.stopPropagation()}
        onContextMenu={(e) => {
          e.preventDefault()
          e.stopPropagation()
        }}
      >
        <DropdownMenu
          open={menuKey === row.treeKey}
          onOpenChange={(o) => setMenuKey(o ? row.treeKey : null)}
        >
          <DropdownMenuTrigger asChild>
            {/* 마우스용 보조 버튼 — 키보드는 행에서 Shift+F10 / 메뉴 키로 같은 메뉴를 연다 */}
            <button
              type="button"
              tabIndex={-1}
              aria-hidden="true"
              className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground opacity-0 hover:bg-background hover:text-foreground group-hover:opacity-100 group-focus-visible:opacity-100 data-[state=open]:opacity-100"
            >
              <MoreHorizontal className="h-3.5 w-3.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            className="min-w-44"
            aria-label={row.menuLabel}
            onCloseAutoFocus={(e) => {
              // 메뉴를 닫으면 "…" 버튼 대신 행으로 포커스를 돌려준다.
              // (메뉴 항목이 다이얼로그를 열어 포커스가 이미 옮겨졌으면 건드리지 않는다)
              e.preventDefault()
              const activeEl = document.activeElement
              if (!activeEl || activeEl === document.body || treeRef.current?.contains(activeEl)) {
                focusRow(row.treeKey)
              }
            }}
          >
            {row.actions.flatMap((a) => [
              ...(a.separatorBefore ? [<DropdownMenuSeparator key={`${a.label}-sep`} />] : []),
              <DropdownMenuItem
                key={a.label}
                variant={a.destructive ? "destructive" : undefined}
                onSelect={() => a.onSelect()}
              >
                {a.label}
              </DropdownMenuItem>,
            ])}
          </DropdownMenuContent>
        </DropdownMenu>
      </span>
    )
  }

  function renderRow(row: RowSpec) {
    const hasActions = !readOnly && row.actions.length > 0
    return (
      <ContextMenu>
        <ContextMenuTrigger asChild disabled={readOnly}>
          <div
            role="treeitem"
            data-tree-key={row.treeKey}
            data-parent-key={row.parentKey ?? undefined}
            aria-level={row.level}
            aria-expanded={row.expandable ? row.expanded : undefined}
            aria-selected={row.selectable ? row.active : false}
            aria-keyshortcuts={hasActions ? "Shift+F10" : undefined}
            tabIndex={tabbableKey === row.treeKey ? 0 : -1}
            className={rowClass(row.active)}
            style={{ paddingLeft: row.paddingLeft }}
            onClick={row.onActivate}
            onKeyDown={(e) => handleRowKeyDown(e, row)}
            onFocus={(e) => {
              if (e.target === e.currentTarget) setFocusedKey(row.treeKey)
            }}
            onContextMenu={(e) => {
              // 키보드로 이미 작업 메뉴를 열었으면 뒤따르는 contextmenu 로 우클릭 메뉴를 또 열지 않는다
              if (Date.now() - keyboardMenuAt.current < 1000) e.preventDefault()
            }}
          >
            {row.content}
            {hasActions && renderActionMenu(row)}
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent className="w-44">{renderContextItems(row.actions)}</ContextMenuContent>
      </ContextMenu>
    )
  }

  // 마우스용 펼침/접기 토글 — 키보드는 행에서 ←/→ 로 조작
  function renderToggle(key: string, hasChildren: boolean, open: boolean) {
    return (
      <button
        type="button"
        tabIndex={-1}
        aria-hidden="true"
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
    )
  }

  function renderDatasource(p: TopologySystem["datasources"][number], depth: number, parentKey: string) {
    const key = `datasource:${p.id}`
    return (
      <div key={key} role="none">
        {renderRow({
          treeKey: key,
          parentKey,
          level: depth + 1,
          paddingLeft: depth * 16 + 6,
          active: selKey === key,
          selectable: true,
          expandable: false,
          expanded: false,
          onActivate: () => onSelect({ kind: "datasource", id: p.id }),
          actions: [{ label: "삭제", destructive: true, onSelect: () => onDeleteDatasource(p.id) }],
          pushActionsRight: false,
          menuLabel: `${p.name} 작업`,
          content: (
            <>
              <span className="w-4 shrink-0" />
              <span className={`h-2 w-2 shrink-0 rounded-full ${ORIGIN_DOT[p.origin] ?? "bg-muted-foreground"}`} />
              <Database className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate">{p.name}</span>
              <span className="ml-auto shrink-0 rounded bg-muted px-1.5 text-xs text-muted-foreground tabular-nums">
                {p.dataset_count}
              </span>
            </>
          ),
        })}
      </div>
    )
  }

  function renderSystem(s: TopologySystem, depth: number, parentKey: string) {
    const key = `system:${s.id}`
    const open = isOpen(key)
    const hasChildren = s.datasources.length > 0
    return (
      <div key={key} role="none">
        {renderRow({
          treeKey: key,
          parentKey,
          level: depth + 1,
          paddingLeft: depth * 16 + 6,
          active: selKey === key,
          selectable: true,
          expandable: hasChildren,
          expanded: open,
          onActivate: () => onSelect({ kind: "system", id: s.id }),
          actions: [
            { label: "데이터 소스 추가", onSelect: () => onAddDatasourceToSystem(s.id) },
            { label: "삭제", destructive: true, separatorBefore: true, onSelect: () => onDeleteSystem(s.id) },
          ],
          pushActionsRight: false,
          menuLabel: `${s.name} 작업`,
          content: (
            <>
              {renderToggle(key, hasChildren, open)}
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
            </>
          ),
        })}
        {open && hasChildren && (
          <div role="group">{s.datasources.map((p) => renderDatasource(p, depth + 1, key))}</div>
        )}
      </div>
    )
  }

  function renderOrg(o: TopologyOrganization, depth: number, parentKey: string) {
    const key = `org:${o.id}`
    const open = isOpen(key)
    const hasChildren = o.children.length > 0 || o.systems.length > 0
    return (
      <div key={key} role="none">
        {renderRow({
          treeKey: key,
          parentKey,
          level: depth + 1,
          paddingLeft: depth * 16 + 6,
          active: selKey === key,
          selectable: true,
          expandable: hasChildren,
          expanded: open,
          onActivate: () => onSelect({ kind: "org", id: o.id }),
          actions: [
            { label: "하위 조직 추가", onSelect: () => onAddSubOrg(o.id) },
            { label: "시스템 추가", onSelect: () => onAddSystem(o.id) },
            { label: "이름 변경", separatorBefore: true, onSelect: () => onEditOrg(o.id) },
            { label: "삭제", destructive: true, onSelect: () => onDeleteOrg(o.id) },
          ],
          pushActionsRight: true,
          menuLabel: `${o.name} 작업`,
          content: (
            <>
              {renderToggle(key, hasChildren, open)}
              <Building2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate font-medium">{o.name}</span>
            </>
          ),
        })}
        {open && hasChildren && (
          <div role="group">
            {o.children.map((c) => renderOrg(c, depth + 1, key))}
            {o.systems.map((s) => renderSystem(s, depth + 1, key))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div ref={treeRef} role="tree" aria-label="조직 · 시스템 · 데이터 소스" className="flex flex-col gap-0.5">
      {/* 최상위 "조직" 루트 — 삭제 불가, 조직 추가만 제공 */}
      {renderRow({
        treeKey: ROOT_KEY,
        parentKey: null,
        level: 1,
        paddingLeft: 6,
        active: false,
        selectable: false,
        expandable: true,
        expanded: rootOpen,
        onActivate: () => toggle(ROOT_KEY),
        actions: [
          { label: "조직 추가", onSelect: () => onAddRootOrg() },
          { label: "데이터 소스 추가", onSelect: () => onAddDatasource() },
        ],
        pushActionsRight: true,
        menuLabel: "조직 작업",
        content: (
          <>
            <button
              type="button"
              tabIndex={-1}
              aria-hidden="true"
              className="shrink-0 text-muted-foreground"
              onClick={(e) => {
                e.stopPropagation()
                toggle(ROOT_KEY)
              }}
            >
              {rootOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            </button>
            <Building2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate font-semibold">조직</span>
          </>
        ),
      })}

      {rootOpen && (
        <div role="group" className="flex flex-col gap-0.5">
          {topology.organizations.length === 0 && (
            <p
              className="py-2 text-center text-xs text-muted-foreground"
              style={{ paddingLeft: 22 }}
            >
              {readOnly ? "조직이 없습니다." : <>조직이 없습니다. &quot;조직&quot; 우클릭 → 조직 추가</>}
            </p>
          )}
          {topology.organizations.map((o) => renderOrg(o, 1, ROOT_KEY))}

          {/* 미분류 — 항상 표시, 삭제 불가. 우클릭 → 데이터 소스 추가만 제공 */}
          <div role="none">
            {renderRow({
              treeKey: UNASSIGNED_KEY,
              parentKey: ROOT_KEY,
              level: 2,
              paddingLeft: 1 * 16 + 6,
              active: unassignedActive,
              selectable: true,
              expandable: unassignedCount > 0,
              expanded: unassignedOpen,
              onActivate: () => onSelect({ kind: "unassigned" }),
              actions: [{ label: "데이터 소스 추가", onSelect: () => onAddDatasource() }],
              pushActionsRight: false,
              menuLabel: "미분류 작업",
              content: (
                <>
                  {renderToggle(UNASSIGNED_KEY, unassignedCount > 0, unassignedOpen)}
                  <FolderOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="truncate">미분류</span>
                  <span className="ml-auto shrink-0 rounded bg-muted px-1.5 text-xs text-muted-foreground tabular-nums">
                    {unassignedCount}
                  </span>
                </>
              ),
            })}
            {unassignedOpen && unassignedCount > 0 && (
              <div role="group">
                {unassignedSystems.map((s) => renderSystem(s, 2, UNASSIGNED_KEY))}
                {unassignedDatasources.map((p) => renderDatasource(p, 2, UNASSIGNED_KEY))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
