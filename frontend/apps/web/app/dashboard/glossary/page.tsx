"use client"

import { useCallback, useContext, useEffect, useMemo, useRef, useState, createContext } from "react"
import { useSearchParams } from "next/navigation"
import { errorMessage, toast } from "@/lib/notify"
import {
  BookOpen, ChevronRight, FolderOpen, FileText, Plus, Pencil, Trash2, Check, X, MoveRight,
} from "lucide-react"
import { AgGridReact } from "ag-grid-react"
import { AllCommunityModule, ModuleRegistry, type ColDef, type CellValueChangedEvent } from "ag-grid-community"

import { Checkbox } from "@workspace/ui/components/checkbox"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Textarea } from "@workspace/ui/components/textarea"
import {
  Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Popover, PopoverContent, PopoverTrigger } from "@workspace/ui/components/popover"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { DashboardHeader } from "@/components/dashboard-header"
import {
  createGlossaryTerm, deleteGlossaryTerm, fetchGlossaryTerms, updateGlossaryTerm,
} from "@/features/glossary/api"
import type { GlossaryTerm } from "@/features/datasets/data/schema"
import { useAuth } from "@/features/auth"
import { usePermissions } from "@/features/permissions/use-permissions"

ModuleRegistry.registerModules([AllCommunityModule])

const GLOSSARY_EDIT_FEATURE = "glossary.edit"
const isCategory = (t: { term_type?: string | null }) => (t.term_type ?? "TERM") === "CATEGORY"
const errMsg = (e: unknown, fallback: string) => (errorMessage(e) ?? fallback)

// ---------------------------------------------------------------------------
// Tree node type
// ---------------------------------------------------------------------------
type TreeNode = GlossaryTerm & { children: TreeNode[]; depth: number }

function buildTree(terms: GlossaryTerm[]): TreeNode[] {
  const map = new Map<number, TreeNode>()
  const roots: TreeNode[] = []
  for (const t of terms) map.set(t.id, { ...t, children: [], depth: 0 })
  for (const node of map.values()) {
    if (node.parent_id && map.has(node.parent_id)) {
      map.get(node.parent_id)!.children.push(node)
    } else {
      roots.push(node)
    }
  }
  // Compute depth recursively after tree structure is built
  const setDepth = (nodes: TreeNode[], d: number) => {
    for (const n of nodes) {
      n.depth = d
      setDepth(n.children, d + 1)
    }
  }
  setDepth(roots, 0)
  // Sort alphabetically
  const sortNodes = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => a.name.localeCompare(b.name))
    for (const n of nodes) sortNodes(n.children)
  }
  sortNodes(roots)
  return roots
}

// Collect all descendant IDs (including self)
function collectIds(node: TreeNode): number[] {
  const ids = [node.id]
  for (const c of node.children) ids.push(...collectIds(c))
  return ids
}

// ---------------------------------------------------------------------------
// Folder Tree Picker (used in Move popovers)
// ---------------------------------------------------------------------------

function FolderTreePicker({
  terms,
  excludeIds,
  onSelect,
}: {
  terms: GlossaryTerm[]
  excludeIds?: Set<number>
  onSelect: (targetId: number | null) => void
}) {
  const categories = terms.filter(t => (t.term_type ?? "TERM") === "CATEGORY")
  const catTree = buildTree(categories)
  const [expanded, setExpanded] = useState<Set<number>>(new Set(categories.map(c => c.id)))

  const toggleExp = (id: number) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  const renderNodes = (nodes: TreeNode[]): React.ReactNode => {
    return nodes.map(node => {
      if (excludeIds?.has(node.id)) return null
      const hasChildren = node.children.filter(c => (c.term_type ?? "TERM") === "CATEGORY").length > 0
      const isExp = expanded.has(node.id)
      return (
        <div key={node.id}>
          <button
            type="button"
            onClick={() => onSelect(node.id)}
            className="w-full text-left flex items-center gap-1.5 px-2 py-1.5 text-sm hover:bg-muted/50 rounded transition-colors"
            style={{ paddingLeft: `${node.depth * 16 + 8}px` }}
          >
            {hasChildren ? (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); toggleExp(node.id) }}
                className="shrink-0 p-0.5"
              >
                <ChevronRight className={`h-3 w-3 transition-transform ${isExp ? "rotate-90" : ""}`} />
              </button>
            ) : (
              <span className="w-4" />
            )}
            <FolderOpen className="h-3.5 w-3.5 text-amber-500 shrink-0" />
            <span className="truncate">{node.name}</span>
          </button>
          {hasChildren && isExp && renderNodes(node.children.filter(c => (c.term_type ?? "TERM") === "CATEGORY"))}
        </div>
      )
    })
  }

  return (
    <div className="py-1 max-h-[300px] overflow-y-auto">
      <button
        type="button"
        onClick={() => onSelect(null)}
        className="w-full text-left flex items-center gap-1.5 px-3 py-1.5 text-sm hover:bg-muted/50 rounded transition-colors"
      >
        <BookOpen className="h-3.5 w-3.5 text-muted-foreground" />
        Root (최상위)
      </button>
      {renderNodes(catTree)}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Delete renderer for AG Grid
// ---------------------------------------------------------------------------
const TermDeleteCtx = createContext<(id: number) => void>(() => {})
function TermDeleteRenderer(props: { value: number }) {
  const onDelete = useContext(TermDeleteCtx)
  return (
    <button type="button" onClick={() => onDelete(props.value)} aria-label="용어 삭제"
      className="flex items-center justify-center w-full h-full text-muted-foreground hover:text-destructive transition-colors cursor-pointer">
      <Trash2 className="h-3.5 w-3.5" />
    </button>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function GlossaryPage() {
  const { user } = useAuth()
  const { isFeatureGranted } = usePermissions()
  // 편집 권한 — admin 이거나 권한 관리에서 glossary.edit 를 명시 허용받은 역할 (미설정이면 admin 전용)
  const canEdit = !!user?.is_admin || isFeatureGranted(GLOSSARY_EDIT_FEATURE)
  const searchParams = useSearchParams()
  const [terms, setTerms] = useState<GlossaryTerm[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  // 검색 결과 등 외부에서 ?term=<id> 로 진입하면 해당 용어를 초기 선택한다.
  const initialTermId = Number(searchParams.get("term")) || null
  const [selectedNodeId, setSelectedNodeId] = useState<number | null>(initialTermId)
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())

  // Multi-select for Move to
  const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set())
  const [movePopoverOpen, setMovePopoverOpen] = useState(false)

  // Category editing
  const [editingNodeId, setEditingNodeId] = useState<number | null>(null)
  const [editingName, setEditingName] = useState("")

  // Add dialog
  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [addMode, setAddMode] = useState<"category" | "term">("term")
  const [newName, setNewName] = useState("")
  const [newDesc, setNewDesc] = useState("")
  const [creating, setCreating] = useState(false)

  // 삭제 확인
  const [pendingDelete, setPendingDelete] = useState<GlossaryTerm | null>(null)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    try {
      setIsLoading(true)
      setTerms(await fetchGlossaryTerms())
      setLoadError(null)
    } catch (e) {
      setLoadError(errMsg(e, "용어집을 불러오지 못했습니다."))
    } finally {
      setIsLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])

  // Tree
  const tree = useMemo(() => buildTree(terms), [terms])
  const nodeMap = useMemo(() => {
    const m = new Map<number, TreeNode>()
    const walk = (nodes: TreeNode[]) => { for (const n of nodes) { m.set(n.id, n); walk(n.children) } }
    walk(tree)
    return m
  }, [tree])
  // 분류별 하위(모든 깊이) 용어 수 — 트리를 한 번만 훑어 계산 (줄마다 다시 세지 않는다)
  const leafCounts = useMemo(() => {
    const m = new Map<number, number>()
    const count = (n: TreeNode): number => {
      let c = isCategory(n) ? 0 : 1
      for (const ch of n.children) c += count(ch)
      if (isCategory(n)) m.set(n.id, c)
      return c
    }
    for (const r of tree) count(r)
    return m
  }, [tree])

  // ?term=<id> 로 들어오면 상위 분류들을 펼쳐 트리에서 위치가 보이게 한다 (최초 1회)
  const revealedInitial = useRef(false)
  useEffect(() => {
    if (revealedInitial.current || !initialTermId || nodeMap.size === 0) return
    revealedInitial.current = true
    const ancestors: number[] = []
    const seen = new Set<number>()
    let cur = nodeMap.get(initialTermId)?.parent_id ?? null
    while (cur && !seen.has(cur)) {
      seen.add(cur)
      ancestors.push(cur)
      cur = nodeMap.get(cur)?.parent_id ?? null
    }
    if (ancestors.length) setExpandedIds((prev) => new Set([...prev, ...ancestors]))
  }, [initialTermId, nodeMap])

  // Selected node
  const selectedNode = selectedNodeId ? nodeMap.get(selectedNodeId) ?? null : null
  // 새 항목의 상위 — 선택한 것이 용어면 그 용어가 속한 분류 (용어 아래에는 항목을 둘 수 없음)
  const createParent = selectedNode
    ? (isCategory(selectedNode) ? selectedNode : (selectedNode.parent_id ? nodeMap.get(selectedNode.parent_id) ?? null : null))
    : null

  // Terms to show in right panel: TERM type under selected node (or all TERMs if none selected)
  const rightTerms = useMemo(() => {
    if (!selectedNode) {
      return terms.filter(t => (t.term_type ?? "TERM") === "TERM")
    }
    const allIds = new Set(collectIds(selectedNode))
    return terms.filter(t => allIds.has(t.id) && (t.term_type ?? "TERM") === "TERM")
  }, [selectedNode, terms])


  // Tree toggle
  const toggleExpand = (id: number) => {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  // Checkbox toggle
  const toggleCheck = (id: number) => {
    setCheckedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  /** 여러 항목의 상위를 순서대로 바꾸고, 실패한 항목은 사유와 함께 알린다. */
  const applyMoves = async (ids: number[], targetId: number | null) => {
    const failures: string[] = []
    let moved = 0
    for (const id of ids) {
      try {
        await updateGlossaryTerm(id, { parent_id: targetId })
        moved++
      } catch (e) {
        failures.push(`${nodeMap.get(id)?.name ?? id}: ${errMsg(e, "이동하지 못했습니다.")}`)
      }
    }
    if (moved) toast.success(`${moved}개 항목을 이동했습니다.`)
    if (failures.length) toast.error(`이동하지 못한 항목이 있습니다 — ${failures.join(" / ")}`)
    await load()
  }

  // Move to: update parent_id of all checked nodes
  const moveTo = async (targetId: number | null) => {
    // 자기 자신이나 자기 하위로는 옮길 수 없다 — 대상이 옮길 항목의 하위 트리 안에 있으면 건너뛴다.
    // (하위 항목을 상위 분류로 끌어올리는 이동은 허용)
    const ids: number[] = []
    let skipped = 0
    for (const id of checkedIds) {
      const node = nodeMap.get(id)
      if (!node || node.parent_id === targetId) continue // 이미 그 위치
      if (targetId !== null && collectIds(node).includes(targetId)) { skipped++; continue }
      ids.push(id)
    }
    setCheckedIds(new Set())
    setMovePopoverOpen(false)
    if (skipped) toast.error(`자기 자신이나 하위 분류로는 옮길 수 없어 ${skipped}개 항목을 건너뛰었습니다.`)
    if (ids.length) await applyMoves(ids, targetId)
  }

  // Category CRUD
  const openAdd = (mode: "category" | "term") => {
    setAddMode(mode)
    setNewName("")
    setNewDesc("")
    setAddDialogOpen(true)
  }

  const handleCreate = async () => {
    if (!newName.trim() || creating) return
    setCreating(true)
    try {
      await createGlossaryTerm({
        name: newName.trim(),
        description: newDesc.trim() || undefined,
        parent_id: createParent?.id,
        term_type: addMode === "category" ? "CATEGORY" : "TERM",
      })
      setAddDialogOpen(false)
      toast.success(addMode === "category" ? "분류를 추가했습니다." : "용어를 추가했습니다.")
      // 하위 분류를 추가했으면 바로 보이도록 상위 분류를 펼친다
      if (addMode === "category" && createParent) {
        const parentId = createParent.id
        setExpandedIds(prev => new Set([...prev, parentId]))
      }
      await load()
    } catch (e) {
      // 대화상자는 열어 둔 채 사유(이름 중복 등)를 알린다
      toast.error(errMsg(e, "추가하지 못했습니다."))
    } finally {
      setCreating(false)
    }
  }

  const startRename = (id: number, name: string) => {
    setEditingNodeId(id)
    setEditingName(name)
  }

  const saveRename = async () => {
    if (!editingNodeId || !editingName.trim()) return
    if (editingName.trim() === nodeMap.get(editingNodeId)?.name) { setEditingNodeId(null); return }
    try {
      await updateGlossaryTerm(editingNodeId, { name: editingName.trim() })
      setEditingNodeId(null)
      await load()
    } catch (e) {
      // 편집 상태를 유지해 다시 고칠 수 있게 한다
      toast.error(errMsg(e, "이름을 바꾸지 못했습니다."))
    }
  }

  const cancelRename = () => setEditingNodeId(null)

  // 분류 설명 편집 (분류는 그리드에 나오지 않으므로 별도 대화상자)
  const [descTarget, setDescTarget] = useState<GlossaryTerm | null>(null)
  const [descDraft, setDescDraft] = useState("")
  const [savingDesc, setSavingDesc] = useState(false)
  const openDescEdit = (t: GlossaryTerm) => { setDescTarget(t); setDescDraft(t.description ?? "") }
  const saveDesc = async () => {
    if (!descTarget) return
    setSavingDesc(true)
    try {
      await updateGlossaryTerm(descTarget.id, { description: descDraft.trim() || null })
      toast.success("분류 설명을 저장했습니다.")
      setDescTarget(null)
      await load()
    } catch (e) {
      toast.error(errMsg(e, "설명을 저장하지 못했습니다."))
    } finally {
      setSavingDesc(false)
    }
  }

  const requestDelete = useCallback((id: number) => {
    const t = terms.find((x) => x.id === id)
    if (t) setPendingDelete(t)
  }, [terms])

  const confirmDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await deleteGlossaryTerm(pendingDelete.id)
      if (selectedNodeId === pendingDelete.id) setSelectedNodeId(null)
      toast.success(`'${pendingDelete.name}'을(를) 삭제했습니다.`)
      setPendingDelete(null)
      await load()
    } catch (e) {
      // 차단형(하위 용어 존재 등) 삭제 실패 — 백엔드 안내 메시지를 토스트로.
      toast.error(errMsg(e, "삭제하지 못했습니다."))
      setPendingDelete(null)
    } finally {
      setDeleting(false)
    }
  }

  // AG Grid — term update (실패하면 서버 값으로 되돌린다)
  const onCellValueChanged = useCallback(async (event: CellValueChangedEvent) => {
    const { data, colDef, newValue, oldValue } = event
    if (newValue === oldValue) return
    const field = colDef.field
    if (!field || !data.id) return
    try {
      const value = field === "description" ? ((newValue ?? "") as string).trim() || null : newValue
      await updateGlossaryTerm(data.id, { [field]: value })
    } catch (e) {
      toast.error(errMsg(e, "저장하지 못했습니다."))
      await load()
    }
  }, [load])

  // Grid selection for Move Term
  const gridRef = useRef<AgGridReact>(null)
  const [gridSelectedIds, setGridSelectedIds] = useState<Set<number>>(new Set())
  const [moveTermPopoverOpen, setMoveTermPopoverOpen] = useState(false)

  const onGridSelectionChanged = useCallback(() => {
    const api = gridRef.current?.api
    if (!api) return
    const ids = new Set<number>()
    api.getSelectedRows().forEach((row: { id: number }) => ids.add(row.id))
    setGridSelectedIds(ids)
  }, [])

  const moveTermsTo = async (targetId: number | null) => {
    const ids = [...gridSelectedIds].filter((id) => nodeMap.get(id)?.parent_id !== targetId)
    setGridSelectedIds(new Set())
    gridRef.current?.api?.deselectAll()
    setMoveTermPopoverOpen(false)
    if (ids.length) await applyMoves(ids, targetId)
  }

  // 편집 권한이 없으면 선택·편집·삭제 열을 두지 않는다 (서버도 403 으로 막음)
  const columnDefs = useMemo<ColDef[]>(() => [
    ...(canEdit ? [{ headerCheckboxSelection: true, checkboxSelection: true, width: 45, maxWidth: 45, editable: false, sortable: false, headerName: "" }] : []),
    { headerName: "이름", field: "name", minWidth: 160, editable: canEdit, cellStyle: { fontWeight: 500, display: "flex", alignItems: "center" } },
    { headerName: "설명", field: "description", flex: 1, minWidth: 250, editable: canEdit, wrapText: true, autoHeight: true, cellStyle: { lineHeight: "1.4", whiteSpace: "normal", display: "flex", alignItems: "center" } },
    ...(canEdit ? [{ headerName: "", field: "id", width: 45, maxWidth: 45, editable: false, sortable: false, cellRenderer: "deleteRenderer" }] : []),
  ] as ColDef[], [canEdit])

  const components = useMemo(() => ({ deleteRenderer: TermDeleteRenderer }), [])

  // Visible tree nodes (CATEGORY only — TERMs are shown in right grid)
  const visibleNodes = useMemo(() => {
    const result: TreeNode[] = []
    const walk = (nodes: TreeNode[]) => {
      for (const n of nodes) {
        if ((n.term_type ?? "TERM") !== "CATEGORY") continue
        result.push(n)
        if (expandedIds.has(n.id)) walk(n.children)
      }
    }
    walk(tree)
    return result
  }, [tree, expandedIds])

  // Breadcrumb
  const breadcrumb = useMemo(() => {
    if (!selectedNode) return "전체 용어"
    const parts: string[] = []
    let current: TreeNode | undefined = selectedNode
    while (current) {
      parts.unshift(current.name)
      current = current.parent_id ? nodeMap.get(current.parent_id) : undefined
    }
    return parts.join(" / ")
  }, [selectedNode, nodeMap])

  return (
    <TermDeleteCtx.Provider value={requestDelete}>
    <>
      <DashboardHeader title="용어집" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <div className="flex flex-1 gap-4 min-h-0">

          {/* ========== Left: Tree ========== */}
          <div className="flex flex-col w-72 min-w-[260px] min-h-0 border rounded-lg" style={{ fontFamily: "var(--font-sans)" }}>
            <div className="flex items-center justify-between px-3 py-2 border-b flex-shrink-0">
              <span className="text-sm font-medium">분류</span>
              <div className="flex items-center gap-1">
                {canEdit && (
                  <Button variant="outline" size="sm" onClick={() => openAdd("category")}>
                    <Plus className="h-3.5 w-3.5 mr-1" />추가
                  </Button>
                )}
                {canEdit && checkedIds.size > 0 && (
                  <Popover open={movePopoverOpen} onOpenChange={setMovePopoverOpen}>
                    <PopoverTrigger asChild>
                      <Button variant="outline" size="sm">
                        <MoveRight className="h-3.5 w-3.5 mr-1" />이동
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-[260px] p-0" align="end">
                      <FolderTreePicker
                        terms={terms}
                        excludeIds={checkedIds}
                        onSelect={(id) => { moveTo(id); setMovePopoverOpen(false) }}
                      />
                    </PopoverContent>
                  </Popover>
                )}
              </div>
            </div>

            <div className="flex-1 overflow-y-auto py-1">
              {/* 최상위 "전체 용어" 항목 */}
              <button
                type="button"
                onClick={() => setSelectedNodeId(null)}
                className={`w-full text-left flex items-center gap-2 px-3 py-1.5 text-sm transition-colors ${
                  selectedNodeId === null ? "bg-primary/10 text-primary font-medium" : "hover:bg-muted/50"
                }`}
              >
                <BookOpen className="h-3.5 w-3.5 text-muted-foreground" />
                전체 용어
                <span className="ml-auto text-sm text-muted-foreground">
                  {terms.filter(t => (t.term_type ?? "TERM") === "TERM").length}
                </span>
              </button>

              {/* Tree nodes */}
              {visibleNodes.map(node => {
                const isNodeCategory = (node.term_type ?? "TERM") === "CATEGORY"
                const hasChildren = node.children.length > 0
                const isExpanded = expandedIds.has(node.id)
                const isSelected = selectedNodeId === node.id
                const isEditing = editingNodeId === node.id
                const leafCount = leafCounts.get(node.id) ?? 0

                return (
                  <div
                    key={node.id}
                    className={`flex items-center gap-1 pr-1 transition-colors group ${
                      isSelected ? "bg-primary/10" : "hover:bg-muted/50"
                    }`}
                    style={{ paddingLeft: `${node.depth * 16 + 8}px` }}
                  >
                    {/* Expand toggle */}
                    <button
                      type="button"
                      className={`shrink-0 p-0.5 rounded hover:bg-muted ${isNodeCategory && hasChildren ? "cursor-pointer" : "invisible"}`}
                      onClick={(e) => { e.stopPropagation(); if (hasChildren) toggleExpand(node.id) }}
                    >
                      <ChevronRight className={`h-3.5 w-3.5 transition-transform ${isExpanded ? "rotate-90" : ""}`} />
                    </button>

                    {/* Checkbox */}
                    {canEdit && (
                      <Checkbox
                        checked={checkedIds.has(node.id)}
                        onCheckedChange={() => toggleCheck(node.id)}
                        onClick={(e) => e.stopPropagation()}
                        className="shrink-0"
                      />
                    )}

                    {/* Icon */}
                    {isNodeCategory
                      ? <FolderOpen className="h-3.5 w-3.5 text-amber-500 shrink-0" />
                      : <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                    }

                    {/* Name (editable) */}
                    {isEditing ? (
                      <div className="flex items-center gap-1 flex-1 min-w-0">
                        <Input
                          value={editingName}
                          onChange={e => setEditingName(e.target.value)}
                          onKeyDown={e => { if (e.key === "Enter") saveRename(); if (e.key === "Escape") cancelRename() }}
                          className="h-7 text-sm px-1 flex-1"
                          autoFocus
                        />
                        <button type="button" onClick={saveRename} className="text-green-600 hover:text-green-800">
                          <Check className="h-3.5 w-3.5" />
                        </button>
                        <button type="button" onClick={cancelRename} className="text-muted-foreground hover:text-foreground">
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setSelectedNodeId(node.id)}
                        className={`flex-1 min-w-0 text-left truncate text-sm py-1 ${isSelected ? "text-primary font-medium" : ""}`}
                      >
                        {node.name}
                      </button>
                    )}

                    {/* Count */}
                    {!isEditing && isNodeCategory && (
                      <span className="text-sm text-muted-foreground shrink-0">{leafCount}</span>
                    )}

                    {/* Actions (visible on hover) */}
                    {!isEditing && canEdit && (
                      <div className="shrink-0 flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
                        <button type="button" onClick={(e) => { e.stopPropagation(); startRename(node.id, node.name) }}
                          aria-label="이름 바꾸기" className="p-0.5 text-muted-foreground hover:text-foreground">
                          <Pencil className="h-3 w-3" />
                        </button>
                        <button type="button" onClick={(e) => { e.stopPropagation(); requestDelete(node.id) }}
                          aria-label="삭제" className="p-0.5 text-muted-foreground hover:text-destructive">
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          {/* ========== Right: Term List (AG Grid) ========== */}
          <div className="flex flex-col flex-1 min-h-0">
            <div className="flex items-center justify-between mb-2 flex-shrink-0">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{breadcrumb}</span>
                  <span className="text-sm text-muted-foreground">({rightTerms.length})</span>
                </div>
                {selectedNode && isCategory(selectedNode) && (
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <p className="text-sm text-muted-foreground truncate" title={selectedNode.description ?? ""}>
                      {selectedNode.description || "분류 설명 없음"}
                    </p>
                    {canEdit && (
                      <button type="button" onClick={() => openDescEdit(selectedNode)}
                        aria-label="분류 설명 편집" className="p-0.5 text-muted-foreground hover:text-foreground shrink-0">
                        <Pencil className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                )}
              </div>
              {canEdit && (
                <div className="flex items-center gap-1">
                  <Button variant="outline" size="sm" onClick={() => openAdd("term")}>
                    <Plus className="h-3.5 w-3.5 mr-1" />용어 추가
                  </Button>
                  {gridSelectedIds.size > 0 && (
                    <Popover open={moveTermPopoverOpen} onOpenChange={setMoveTermPopoverOpen}>
                      <PopoverTrigger asChild>
                        <Button variant="outline" size="sm">
                          <MoveRight className="h-3.5 w-3.5 mr-1" />이동 ({gridSelectedIds.size})
                        </Button>
                      </PopoverTrigger>
                      <PopoverContent className="w-[260px] p-0" align="end">
                        <FolderTreePicker
                          terms={terms}
                          onSelect={(id) => { moveTermsTo(id); setMoveTermPopoverOpen(false) }}
                        />
                      </PopoverContent>
                    </Popover>
                  )}
                </div>
              )}
            </div>
            {loadError && (
              <div className="mb-2 flex items-center justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm">
                <span className="text-destructive">{loadError}</span>
                <Button size="sm" variant="outline" onClick={load} disabled={isLoading}>다시 시도</Button>
              </div>
            )}
            <div className="ag-theme-alpine flex-1 min-h-0" style={{
              "--ag-font-family": "var(--font-sans)",
              "--ag-font-size": "14px",
            } as React.CSSProperties}>
              <AgGridReact
                ref={gridRef}
                columnDefs={columnDefs}
                rowData={rightTerms}
                defaultColDef={{ resizable: true, sortable: true, filter: false, minWidth: 50 }}
                headerHeight={32}
                rowHeight={30}
                rowSelection="multiple"
                onSelectionChanged={onGridSelectionChanged}
                stopEditingWhenCellsLoseFocus
                onCellValueChanged={onCellValueChanged}
                animateRows={false}
                getRowId={(params) => String(params.data.id)}
                components={components}
                loading={isLoading && terms.length === 0}
                overlayNoRowsTemplate={loadError ? "용어집을 불러오지 못했습니다." : "용어가 없습니다."}
              />
            </div>
          </div>
        </div>
      </div>

      {/* ========== Delete Confirm ========== */}
      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(o) => { if (!o && !deleting) setPendingDelete(null) }}
        title={pendingDelete && isCategory(pendingDelete) ? "분류 삭제" : "용어 삭제"}
        desc={pendingDelete
          ? isCategory(pendingDelete)
            ? `'${pendingDelete.name}' 분류를 삭제합니다. 하위 분류나 용어가 있으면 삭제되지 않습니다.`
            : `'${pendingDelete.name}' 용어를 삭제합니다. 이 용어가 연결된 데이터셋에서도 연결이 해제되며, 되돌릴 수 없습니다.`
          : ""}
        confirmText="삭제"
        destructive
        isLoading={deleting}
        handleConfirm={confirmDelete}
      />

      {/* ========== Category Description Dialog ========== */}
      <Dialog open={descTarget !== null} onOpenChange={(o) => { if (!o && !savingDesc) setDescTarget(null) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>분류 설명 편집</DialogTitle>
            <DialogDescription>{descTarget ? `'${descTarget.name}' 분류의 설명을 수정합니다.` : ""}</DialogDescription>
          </DialogHeader>
          <Textarea value={descDraft} onChange={e => setDescDraft(e.target.value)} rows={4}
            placeholder="이 분류에 속하는 용어의 범위·용도를 설명하세요..." />
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={savingDesc}>취소</Button>
            </DialogClose>
            <Button onClick={saveDesc} disabled={savingDesc}>저장</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ========== Add Dialog ========== */}
      <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {addMode === "category" ? "분류 추가" : "용어 추가"}
            </DialogTitle>
            <DialogDescription>
              {addMode === "category"
                ? `${createParent ? `"${createParent.name}" 아래에 ` : "최상위에 "}새 분류를 추가합니다.`
                : `${createParent ? `"${createParent.name}" 아래에 ` : "최상위에 "}용어를 추가합니다.`
              }
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-2">
              <Label>이름</Label>
              <Input value={newName} onChange={e => setNewName(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && !e.nativeEvent.isComposing) handleCreate() }} placeholder={addMode === "category" ? "예: 고객 관리" : "예: 고객 생애 가치"} />
              <p className="text-sm text-muted-foreground">
                {addMode === "category"
                  ? "분류 이름은 용어집 전체에서 하나만 쓸 수 있습니다(같은 이름의 용어와는 공존 가능)."
                  : "용어 이름은 용어집 전체에서 하나만 쓸 수 있습니다(같은 이름의 분류와는 공존 가능)."}
              </p>
            </div>
            <div className="grid gap-2">
              <Label>설명</Label>
              <Textarea value={newDesc} onChange={e => setNewDesc(e.target.value)} rows={3} placeholder="용어 설명을 입력하세요..." />
            </div>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">취소</Button>
            </DialogClose>
            <Button onClick={handleCreate} disabled={!newName.trim() || creating}>생성</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
    </TermDeleteCtx.Provider>
  )
}
