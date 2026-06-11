"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { ChevronDown, ChevronRight, FolderOpen, FolderTree, Minus, Plus } from "lucide-react"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@workspace/ui/components/alert-dialog"
import { Button } from "@workspace/ui/components/button"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@workspace/ui/components/context-menu"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { DashboardHeader } from "@/components/dashboard-header"
import {
  addDatasetCategory,
  createCategory,
  createTaxonomy,
  deleteCategory,
  deleteTaxonomy,
  fetchTaxonomies,
  fetchTaxonomyTree,
  removeDatasetCategory,
  updateCategory,
  type Taxonomy,
  type TaxonomyTree,
  type TreeCategory,
} from "@/features/taxonomy/api"
import { fetchDatasets, type PaginatedDatasets } from "@/features/datasets/api"
import { DatasetsGrid } from "@/features/datasets/components/datasets-grid"

type Sel = { kind: "category"; id: number } | { kind: "uncategorized" } | null

export default function TaxonomiesPage() {
  const [taxonomies, setTaxonomies] = useState<Taxonomy[]>([])
  const [taxId, setTaxId] = useState<number | null>(null)
  const [tree, setTree] = useState<TaxonomyTree | null>(null)
  const [selected, setSelected] = useState<Sel>(null)
  const [datasets, setDatasets] = useState<PaginatedDatasets>({ items: [], total: 0, page: 1, page_size: 50 })
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  const [rootOpen, setRootOpen] = useState(true)
  const [asideWidth, setAsideWidth] = useState(288) // 트리 폭(px), w-72 = 18rem 기준
  const dragRef = useRef<{ startX: number; startW: number } | null>(null)
  // 분류 트리 드래그 정렬 상태
  const [dragInfo, setDragInfo] = useState<{ id: number; parentId: number | null } | null>(null)
  const [dropTarget, setDropTarget] = useState<{ id: number; pos: "before" | "after" } | null>(null)
  // 매핑 제거 확인 대상 데이터셋 id
  const [removeTarget, setRemoveTarget] = useState<number | null>(null)

  // 카테고리/데이터셋 사이 separator 드래그로 트리 폭 조절
  const startResize = (e: React.MouseEvent) => {
    e.preventDefault()
    dragRef.current = { startX: e.clientX, startW: asideWidth }
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return
      const next = dragRef.current.startW + (ev.clientX - dragRef.current.startX)
      setAsideWidth(Math.min(560, Math.max(200, next)))
    }
    const onUp = () => {
      dragRef.current = null
      window.removeEventListener("mousemove", onMove)
      window.removeEventListener("mouseup", onUp)
      document.body.style.userSelect = ""
      document.body.style.cursor = ""
    }
    document.body.style.userSelect = "none"
    document.body.style.cursor = "col-resize"
    window.addEventListener("mousemove", onMove)
    window.addEventListener("mouseup", onUp)
  }

  // dialogs
  const [taxDialog, setTaxDialog] = useState<{ open: boolean; name: string }>({ open: false, name: "" })
  const [catDialog, setCatDialog] = useState<{ open: boolean; mode: "create" | "edit"; id?: number; name: string; parentId: number | null }>(
    { open: false, mode: "create", name: "", parentId: null },
  )
  const [mapOpen, setMapOpen] = useState(false)
  const [mapSearch, setMapSearch] = useState("")
  const [mapResults, setMapResults] = useState<PaginatedDatasets["items"]>([])
  const [mapSelected, setMapSelected] = useState<Set<number>>(new Set())

  const loadTaxonomies = useCallback(async () => {
    const list = await fetchTaxonomies().catch(() => [])
    setTaxonomies(list)
    setTaxId((prev) => prev ?? (list[0]?.id ?? null))
  }, [])

  const loadTree = useCallback(async (id: number) => {
    setTree(await fetchTaxonomyTree(id).catch(() => null))
  }, [])

  useEffect(() => { loadTaxonomies() }, [loadTaxonomies])
  useEffect(() => { if (taxId != null) loadTree(taxId) }, [taxId, loadTree])

  const loadDatasets = useCallback(async (sel: Sel) => {
    // 선택이 없으면 전체 데이터셋, 분류 선택 시 해당 분류, 미분류 선택 시 미분류만 표시.
    const empty = { items: [], total: 0, page: 1, page_size: 50 }
    let params: Parameters<typeof fetchDatasets>[0]
    if (!sel) {
      params = { pageSize: 50 }
    } else if (sel.kind === "category") {
      params = { categoryId: sel.id, pageSize: 50 }
    } else {
      if (taxId == null) { setDatasets(empty); return }
      params = { taxonomyId: taxId, uncategorized: true, pageSize: 50 }
    }
    setDatasets(await fetchDatasets(params).catch(() => empty))
  }, [taxId])

  useEffect(() => { loadDatasets(selected) }, [selected, loadDatasets, tree])

  const refresh = async () => { if (taxId != null) await loadTree(taxId); await loadDatasets(selected) }

  // ---- taxonomy ----
  const handleTaxCreate = async () => {
    if (!taxDialog.name.trim()) return
    try {
      const t = await createTaxonomy({ name: taxDialog.name.trim() })
      setTaxDialog({ open: false, name: "" })
      await loadTaxonomies(); setTaxId(t.id)
      toast.success("분류체계를 생성했습니다.")
    } catch (e) { toast.error(e instanceof Error ? e.message : "생성 실패") }
  }
  const handleTaxDelete = async () => {
    if (taxId == null) return
    try {
      await deleteTaxonomy(taxId)
      toast.success("분류체계를 삭제했습니다.")
      setTaxId(null); setTree(null); setSelected(null)
      await loadTaxonomies()
    } catch (e) { toast.error(e instanceof Error ? e.message : "삭제 실패(하위 분류 존재?)") }
  }

  // ---- category ----
  const openCreateCat = (parentId: number | null) => setCatDialog({ open: true, mode: "create", name: "", parentId })
  const openEditCat = (c: TreeCategory) => setCatDialog({ open: true, mode: "edit", id: c.id, name: c.name, parentId: c.parent_id })
  const handleCatSave = async () => {
    if (!catDialog.name.trim() || taxId == null) return
    try {
      if (catDialog.mode === "create") {
        await createCategory({ taxonomy_id: taxId, name: catDialog.name.trim(), parent_id: catDialog.parentId })
      } else if (catDialog.id) {
        await updateCategory(catDialog.id, { name: catDialog.name.trim() })
      }
      setCatDialog((d) => ({ ...d, open: false }))
      await refresh()
    } catch (e) { toast.error(e instanceof Error ? e.message : "저장 실패") }
  }
  const handleCatDelete = async (id: number) => {
    try { await deleteCategory(id); if (selected?.kind === "category" && selected.id === id) setSelected(null); await refresh() }
    catch (e) { toast.error(e instanceof Error ? e.message : "삭제 실패(하위 분류 존재?)") }
  }

  // ---- mapping ----
  const openMap = async () => {
    setMapSearch(""); setMapSelected(new Set()); setMapOpen(true)
    await runMapSearch("") // 초기 목록 로드
  }
  const runMapSearch = async (q: string) => {
    setMapSearch(q)
    const r = await fetchDatasets({ search: q || undefined, pageSize: 50 }).catch(() => null)
    setMapResults(r?.items ?? [])
  }
  const toggleMapSelect = (id: number) =>
    setMapSelected((s) => { const n = new Set(s); if (n.has(id)) { n.delete(id) } else { n.add(id) }; return n })
  const toggleMapSelectAll = (checked: boolean) =>
    setMapSelected(checked ? new Set(mapResults.map((d) => d.id)) : new Set())
  const mapSelectedDatasets = async () => {
    if (selected?.kind !== "category" || mapSelected.size === 0) return
    try {
      await Promise.all([...mapSelected].map((id) => addDatasetCategory(id, selected.id)))
      toast.success(`${mapSelected.size}개 매핑 추가`)
      setMapOpen(false)
      await refresh()
    } catch (e) { toast.error(e instanceof Error ? e.message : "매핑 실패") }
  }
  const unmapDataset = async (datasetId: number) => {
    if (selected?.kind !== "category") return
    try { await removeDatasetCategory(datasetId, selected.id); toast.success("매핑 제거"); await refresh() }
    catch (e) { toast.error(e instanceof Error ? e.message : "제거 실패") }
  }

  // ---- tree render ----
  const toggle = (id: number) => setCollapsed((p) => { const n = new Set(p); if (n.has(id)) { n.delete(id) } else { n.add(id) }; return n })
  const rowCls = (active: boolean) =>
    "group flex h-[30px] items-center gap-1 overflow-hidden rounded px-1.5 text-sm cursor-pointer " +
    (active ? "bg-accent text-accent-foreground" : "hover:bg-muted")

  // ---- 드래그 정렬 (같은 부모 내 형제 순서 변경) ----
  const findNode = (list: TreeCategory[], id: number): TreeCategory | null => {
    for (const n of list) {
      if (n.id === id) return n
      const f = findNode(n.children, id)
      if (f) return f
    }
    return null
  }
  const siblingsOf = (parentId: number | null): TreeCategory[] =>
    parentId == null ? (tree?.categories ?? []) : (findNode(tree?.categories ?? [], parentId)?.children ?? [])

  const handleReorder = async (dragId: number, targetId: number, pos: "before" | "after") => {
    if (!dragInfo || dragId === targetId) return
    const ids = siblingsOf(dragInfo.parentId).map((s) => s.id)
    const from = ids.indexOf(dragId)
    if (from < 0) return
    ids.splice(from, 1)
    let to = ids.indexOf(targetId)
    if (to < 0) return // 형제가 아니면 무시
    if (pos === "after") to += 1
    ids.splice(to, 0, dragId)
    try {
      // 변경된 순서를 sort_order(0,1,2...) 로 영속화
      await Promise.all(ids.map((id, idx) => updateCategory(id, { sort_order: idx })))
      await refresh()
    } catch (e) { toast.error(e instanceof Error ? e.message : "순서 변경 실패") }
  }

  const renderCat = (c: TreeCategory, depth: number) => {
    const open = !collapsed.has(c.id)
    const active = selected?.kind === "category" && selected.id === c.id
    const isDragging = dragInfo?.id === c.id
    const indicating = dropTarget?.id === c.id ? dropTarget.pos : null
    return (
      <div key={c.id}>
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <div
              className={rowCls(active) + (isDragging ? " opacity-40" : "")}
              style={{ paddingLeft: depth * 16 + 6, boxShadow: indicating === "before" ? "inset 0 2px 0 0 var(--primary)" : indicating === "after" ? "inset 0 -2px 0 0 var(--primary)" : undefined }}
              onClick={() => setSelected({ kind: "category", id: c.id })}
              draggable
              onDragStart={(e) => { e.stopPropagation(); setDragInfo({ id: c.id, parentId: c.parent_id }); e.dataTransfer.effectAllowed = "move" }}
              onDragEnd={() => { setDragInfo(null); setDropTarget(null) }}
              onDragOver={(e) => {
                if (!dragInfo || dragInfo.id === c.id || dragInfo.parentId !== c.parent_id) return // 같은 부모(형제)만
                e.preventDefault()
                const rect = e.currentTarget.getBoundingClientRect()
                setDropTarget({ id: c.id, pos: e.clientY - rect.top < rect.height / 2 ? "before" : "after" })
              }}
              onDragLeave={(e) => { if (e.currentTarget === e.target) setDropTarget((t) => (t?.id === c.id ? null : t)) }}
              onDrop={(e) => {
                if (!dropTarget || dropTarget.id !== c.id || !dragInfo) return
                e.preventDefault(); e.stopPropagation()
                handleReorder(dragInfo.id, c.id, dropTarget.pos)
                setDragInfo(null); setDropTarget(null)
              }}
            >
              <button type="button" className="shrink-0 text-muted-foreground" onClick={(e) => { e.stopPropagation(); toggle(c.id) }}>
                {c.children.length > 0 ? (open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />) : <span className="inline-block h-3.5 w-3.5" />}
              </button>
              <FolderTree className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate">{c.name}</span>
              <span className="ml-auto shrink-0 rounded bg-muted px-1.5 text-xs text-muted-foreground tabular-nums">{c.dataset_count}</span>
            </div>
          </ContextMenuTrigger>
          <ContextMenuContent className="w-44">
            <ContextMenuItem onClick={() => openCreateCat(c.id)}>하위 분류 추가</ContextMenuItem>
            <ContextMenuItem onClick={() => openEditCat(c)}>이름 변경</ContextMenuItem>
            <ContextMenuSeparator />
            <ContextMenuItem variant="destructive" onClick={() => handleCatDelete(c.id)}>삭제</ContextMenuItem>
          </ContextMenuContent>
        </ContextMenu>
        {open && c.children.map((ch) => renderCat(ch, depth + 1))}
      </div>
    )
  }

  return (
    <>
      <DashboardHeader title="분류 체계" />
      <div className="flex flex-1 flex-col gap-3 p-4 min-h-0">
        <div className="flex items-center gap-2">
          <Select value={taxId != null ? String(taxId) : ""} onValueChange={(v) => { setTaxId(Number(v)); setSelected(null) }}>
            <SelectTrigger className="h-9 w-60 text-sm"><SelectValue placeholder="분류체계 선택" /></SelectTrigger>
            <SelectContent>
              {taxonomies.map((t) => <SelectItem key={t.id} value={String(t.id)} className="text-sm">{t.name}</SelectItem>)}
            </SelectContent>
          </Select>
          <Button size="sm" variant="outline" onClick={() => setTaxDialog({ open: true, name: "" })}><Plus className="mr-1 h-4 w-4" /> 분류체계 추가</Button>
          {taxId != null && <Button size="sm" variant="outline" className="text-destructive" onClick={handleTaxDelete}><Minus className="mr-1 h-4 w-4" /> 분류체계 삭제</Button>}
        </div>

        <div className="flex flex-1 gap-1 min-h-0">
          {/* 좌: 분류 트리 */}
          <aside className="shrink-0 overflow-auto rounded-md border p-2" style={{ width: asideWidth }}>
            {!tree ? (
              <p className="py-8 text-center text-sm text-muted-foreground">분류체계를 선택/생성하세요.</p>
            ) : (
              <div className="flex flex-col gap-0.5">
                {/* 루트 "카테고리" — 우클릭으로 최상위 분류 추가 */}
                <ContextMenu>
                  <ContextMenuTrigger asChild>
                    <div className={rowCls(false)} style={{ paddingLeft: 6 }} onClick={() => setRootOpen((o) => !o)}>
                      <button type="button" className="shrink-0 text-muted-foreground" onClick={(e) => { e.stopPropagation(); setRootOpen((o) => !o) }}>
                        {rootOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                      </button>
                      <FolderTree className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate font-semibold">카테고리</span>
                    </div>
                  </ContextMenuTrigger>
                  <ContextMenuContent className="w-44">
                    <ContextMenuItem onClick={() => openCreateCat(null)}>분류 추가</ContextMenuItem>
                  </ContextMenuContent>
                </ContextMenu>
                {rootOpen && (
                  <>
                    {tree.categories.length === 0 && (
                      <p className="py-2 text-center text-xs text-muted-foreground" style={{ paddingLeft: 22 }}>
                        분류가 없습니다. &quot;카테고리&quot; 우클릭 → 분류 추가
                      </p>
                    )}
                    {tree.categories.map((c) => renderCat(c, 1))}
                    <div className={rowCls(selected?.kind === "uncategorized")} style={{ paddingLeft: 1 * 16 + 6 }} onClick={() => setSelected({ kind: "uncategorized" })}>
                      <span className="w-4 shrink-0" />
                      <FolderOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate">미분류</span>
                      <span className="ml-auto shrink-0 rounded bg-muted px-1.5 text-xs text-muted-foreground tabular-nums">{tree.uncategorized_count}</span>
                    </div>
                  </>
                )}
              </div>
            )}
          </aside>

          {/* 드래그 separator — 트리 폭 조절 (클릭 영역은 넓게, 보이는 선은 얇고 옅게) */}
          <div
            role="separator"
            aria-orientation="vertical"
            onMouseDown={startResize}
            className="group flex w-1.5 shrink-0 cursor-col-resize items-stretch justify-center"
          >
            <span className="w-px bg-border/40 transition-colors group-hover:bg-border/80" />
          </div>

          {/* 우: 데이터셋. 선택 전엔 전체, 분류 선택 시 해당 분류, 미분류 선택 시 미분류만. */}
          <main className="flex-1 overflow-auto rounded-md border p-4">
            <div className="grid gap-3">
              <div className="flex items-center justify-between">
                <h2 className="text-base font-semibold">
                  {selected == null
                    ? "전체 데이터셋"
                    : selected.kind === "uncategorized"
                      ? "미분류"
                      : "데이터셋"}{" "}
                  ({datasets.total})
                </h2>
                {selected?.kind === "category" && (
                  <Button size="sm" onClick={openMap}><Plus className="mr-1 h-4 w-4" /> 데이터셋 매핑</Button>
                )}
              </div>
              {/* 데이터셋 메뉴와 동일한 그리드를 그대로 재사용. 분류 선택 시 행 끝에 "제거"(매핑 해제, 확인 후). */}
              <DatasetsGrid
                data={datasets.items}
                onRemove={selected?.kind === "category" ? (id) => setRemoveTarget(id) : undefined}
              />
            </div>
          </main>
        </div>
      </div>

      {/* 분류체계 생성 */}
      <Dialog open={taxDialog.open} onOpenChange={(o) => setTaxDialog((d) => ({ ...d, open: o }))}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle>분류체계 추가</DialogTitle></DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-1.5">
              <Label className="text-sm">이름 <span className="text-destructive">*</span></Label>
              <Input value={taxDialog.name} onChange={(e) => setTaxDialog((d) => ({ ...d, name: e.target.value }))} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleTaxCreate() } }} autoFocus placeholder="예: 업무 도메인" className="h-9 text-sm" />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setTaxDialog((d) => ({ ...d, open: false }))}>취소</Button>
              <Button onClick={handleTaxCreate} disabled={!taxDialog.name.trim()}>생성</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* 분류 생성/수정 */}
      <Dialog open={catDialog.open} onOpenChange={(o) => setCatDialog((d) => ({ ...d, open: o }))}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle>{catDialog.mode === "create" ? "분류 추가" : "분류 이름 변경"}</DialogTitle></DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-1.5">
              <Label className="text-sm">이름 <span className="text-destructive">*</span></Label>
              <Input value={catDialog.name} onChange={(e) => setCatDialog((d) => ({ ...d, name: e.target.value }))} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleCatSave() } }} autoFocus placeholder="예: 마케팅" className="h-9 text-sm" />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setCatDialog((d) => ({ ...d, open: false }))}>취소</Button>
              <Button onClick={handleCatSave} disabled={!catDialog.name.trim()}>저장</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* 데이터셋 매핑 picker — 그리드 + 체크박스 다중 선택 */}
      <Dialog open={mapOpen} onOpenChange={setMapOpen}>
        <DialogContent className="max-w-4xl max-h-[85vh] overflow-y-auto">
          <DialogHeader><DialogTitle>데이터셋 매핑</DialogTitle></DialogHeader>
          <div className="grid gap-3 py-2">
            <Input value={mapSearch} onChange={(e) => runMapSearch(e.target.value)} placeholder="데이터셋 검색..." className="h-9 text-sm" />
            <div className="max-h-[55vh] overflow-y-auto">
              <DatasetsGrid
                data={mapResults}
                emptyText={mapSearch ? "검색 결과 없음" : "데이터셋이 없습니다."}
                selectedIds={mapSelected}
                onToggleSelect={toggleMapSelect}
                onToggleSelectAll={toggleMapSelectAll}
              />
            </div>
            <div className="flex items-center justify-end gap-2 pt-1">
              <span className="mr-auto text-sm text-muted-foreground">{mapSelected.size}개 선택됨</span>
              <Button variant="outline" onClick={() => setMapOpen(false)}>취소</Button>
              <Button onClick={mapSelectedDatasets} disabled={mapSelected.size === 0}>
                <Plus className="mr-1 h-4 w-4" /> 선택 추가
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* 매핑 제거 확인 */}
      <AlertDialog open={removeTarget != null} onOpenChange={(o) => { if (!o) setRemoveTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>분류에서 데이터셋을 제거할까요?</AlertDialogTitle>
            <AlertDialogDescription>
              {(() => {
                const d = datasets.items.find((x) => x.id === removeTarget)
                const name = d ? (d.display_name || d.name) : "이 데이터셋"
                return `"${name}" 을(를) 현재 분류에서 제거합니다. 데이터셋 자체는 삭제되지 않으며, 매핑만 해제됩니다.`
              })()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                if (removeTarget != null) await unmapDataset(removeTarget)
                setRemoveTarget(null)
              }}
            >
              제거
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
