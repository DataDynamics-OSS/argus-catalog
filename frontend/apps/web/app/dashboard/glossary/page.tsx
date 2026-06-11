"use client"

import { useCallback, useContext, useEffect, useMemo, useRef, useState, createContext } from "react"
import { useSearchParams } from "next/navigation"
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
import { DashboardHeader } from "@/components/dashboard-header"
import {
  createGlossaryTerm, deleteGlossaryTerm, fetchGlossaryTerms, updateGlossaryTerm,
} from "@/features/glossary/api"
import type { GlossaryTerm } from "@/features/datasets/data/schema"
import { useAuth } from "@/features/auth"

ModuleRegistry.registerModules([AllCommunityModule])

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
    <button type="button" onClick={() => onDelete(props.value)}
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
  const searchParams = useSearchParams()
  const [terms, setTerms] = useState<GlossaryTerm[]>([])
  const [, setIsLoading] = useState(true)
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

  const load = useCallback(async () => {
    try {
      setIsLoading(true)
      setTerms(await fetchGlossaryTerms())
    } catch { /* */ } finally {
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

  // Selected node
  const selectedNode = selectedNodeId ? nodeMap.get(selectedNodeId) ?? null : null

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

  // Move to: update parent_id of all checked nodes
  const moveTo = async (targetId: number | null) => {
    // Prevent moving into own descendants
    const targetDescendants = targetId ? new Set(collectIds(nodeMap.get(targetId)!)) : new Set<number>()
    for (const id of checkedIds) {
      if (id === targetId) continue
      if (targetDescendants.has(id)) continue
      await updateGlossaryTerm(id, { parent_id: targetId })
    }
    setCheckedIds(new Set())
    setMovePopoverOpen(false)
    await load()
  }

  // Category CRUD
  const addCategory = () => {
    setAddMode("category")
    setNewName("")
    setNewDesc("")
    setAddDialogOpen(true)
  }

  const addTerm = () => {
    setAddMode("term")
    setNewName("")
    setNewDesc("")
    setAddDialogOpen(true)
  }

  const handleCreate = async () => {
    if (!newName.trim()) return
    await createGlossaryTerm({
      name: newName.trim(),
      description: newDesc.trim() || undefined,
      parent_id: selectedNodeId ?? undefined,
      term_type: addMode === "category" ? "CATEGORY" : "TERM",
    })
    setAddDialogOpen(false)
    await load()
  }

  const startRename = (id: number, name: string) => {
    setEditingNodeId(id)
    setEditingName(name)
  }

  const saveRename = async () => {
    if (!editingNodeId || !editingName.trim()) return
    await updateGlossaryTerm(editingNodeId, { name: editingName.trim() })
    setEditingNodeId(null)
    await load()
  }

  const cancelRename = () => setEditingNodeId(null)

  const deleteNode = async (id: number) => {
    await deleteGlossaryTerm(id)
    if (selectedNodeId === id) setSelectedNodeId(null)
    await load()
  }

  // AG Grid — term update
  const onCellValueChanged = useCallback(async (event: CellValueChangedEvent) => {
    const { data, colDef, newValue, oldValue } = event
    if (newValue === oldValue) return
    const field = colDef.field
    if (!field || !data.id) return
    await updateGlossaryTerm(data.id, { [field]: newValue })
  }, [])

  const deleteTermFromGrid = useCallback(async (id: number) => {
    await deleteGlossaryTerm(id)
    load()
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
    for (const id of gridSelectedIds) {
      await updateGlossaryTerm(id, { parent_id: targetId })
    }
    setGridSelectedIds(new Set())
    setMoveTermPopoverOpen(false)
    await load()
  }

  const columnDefs = useMemo<ColDef[]>(() => [
    { headerCheckboxSelection: true, checkboxSelection: true, width: 45, maxWidth: 45, editable: false, sortable: false, headerName: "" },
    { headerName: "이름", field: "name", minWidth: 160, editable: true, cellStyle: { fontWeight: 500, display: "flex", alignItems: "center" } },
    { headerName: "설명", field: "description", flex: 1, minWidth: 250, editable: true, wrapText: true, autoHeight: true, cellStyle: { lineHeight: "1.4", whiteSpace: "normal", display: "flex", alignItems: "center" } },
    { headerName: "", field: "id", width: 45, maxWidth: 45, editable: false, sortable: false, cellRenderer: "deleteRenderer" },
  ] as ColDef[], [])

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
    <TermDeleteCtx.Provider value={deleteTermFromGrid}>
    <>
      <DashboardHeader title="용어집" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <div className="flex flex-1 gap-4 min-h-0">

          {/* ========== Left: Tree ========== */}
          <div className="flex flex-col w-72 min-w-[260px] min-h-0 border rounded-lg" style={{ fontFamily: "var(--font-sans)" }}>
            <div className="flex items-center justify-between px-3 py-2 border-b flex-shrink-0">
              <span className="text-sm font-medium">분류</span>
              <div className="flex items-center gap-1">
                {user?.is_admin && (
                  <Button variant="outline" size="sm" onClick={addCategory}>
                    <Plus className="h-3.5 w-3.5 mr-1" />추가
                  </Button>
                )}
                {user?.is_admin && checkedIds.size > 0 && (
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
                const leafCount = collectIds(node).filter(id => {
                  const n = nodeMap.get(id)
                  return n ? (n.term_type ?? "TERM") === "TERM" : false
                }).length

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
                    {user?.is_admin && (
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
                    {!isEditing && user?.is_admin && (
                      <div className="shrink-0 flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
                        <button type="button" onClick={(e) => { e.stopPropagation(); startRename(node.id, node.name) }}
                          className="p-0.5 text-muted-foreground hover:text-foreground">
                          <Pencil className="h-3 w-3" />
                        </button>
                        <button type="button" onClick={(e) => { e.stopPropagation(); deleteNode(node.id) }}
                          className="p-0.5 text-muted-foreground hover:text-destructive">
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
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{breadcrumb}</span>
                <span className="text-sm text-muted-foreground">({rightTerms.length})</span>
              </div>
              {user?.is_admin && (
                <div className="flex items-center gap-1">
                  <Button variant="outline" size="sm" onClick={addTerm}>
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
              />
            </div>
          </div>
        </div>
      </div>

      {/* ========== Add Dialog ========== */}
      <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {addMode === "category" ? "분류 추가" : "용어 추가"}
            </DialogTitle>
            <DialogDescription>
              {addMode === "category"
                ? `${selectedNode ? `"${selectedNode.name}" 아래에 ` : ""}새 분류를 추가합니다.`
                : `${selectedNode ? `"${selectedNode.name}" 아래에 ` : ""}용어를 추가합니다.`
              }
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-2">
              <Label>이름</Label>
              <Input value={newName} onChange={e => setNewName(e.target.value)} placeholder={addMode === "category" ? "예: 고객 관리" : "예: 고객 생애 가치"} />
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
            <Button onClick={handleCreate} disabled={!newName.trim()}>생성</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
    </TermDeleteCtx.Provider>
  )
}
