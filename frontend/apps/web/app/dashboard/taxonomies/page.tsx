"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { ChevronDown, ChevronRight, FolderOpen, FolderTree, Minus, Pencil, Plus } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"

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
import { useAuth } from "@/features/auth"
import { usePermissions } from "@/features/permissions/use-permissions"
import {
  addDatasetCategory,
  createCategory,
  createTaxonomy,
  deleteCategory,
  deleteTaxonomy,
  fetchTaxonomies,
  fetchTaxonomyTree,
  removeDatasetCategory,
  fetchDirectDatasetIds,
  updateCategory,
  updateTaxonomy,
  type Taxonomy,
  type TaxonomyTree,
  type TreeCategory,
} from "@/features/taxonomy/api"
import { countCategories } from "@/features/taxonomy/tree"
import { fetchDatasets, type PaginatedDatasets } from "@/features/datasets/api"
import { DatasetsGrid } from "@/features/datasets/components/datasets-grid"
import { GridPagination } from "@/components/grid-pagination"

type Sel = { kind: "category"; id: number } | { kind: "uncategorized" } | null

export default function TaxonomiesPage() {
  // 편집(분류체계·분류·데이터셋 매핑) — admin 이거나 권한 관리에서 taxonomies.edit 를 명시 허용받은 역할
  // (미설정이면 admin 전용, 서버도 같은 키로 403). 조회는 누구나.
  const { user } = useAuth()
  const { isFeatureGranted } = usePermissions()
  const canEdit = !!user?.is_admin || isFeatureGranted("taxonomies.edit")
  const [taxonomies, setTaxonomies] = useState<Taxonomy[]>([])
  const [taxId, setTaxId] = useState<number | null>(null)
  const [tree, setTree] = useState<TaxonomyTree | null>(null)
  const [selected, setSelected] = useState<Sel>(null)
  const [datasets, setDatasets] = useState<PaginatedDatasets>({ items: [], total: 0, page: 1, page_size: 50 })
  // 선택한 분류에 *직접* 매핑된 데이터셋 id — 목록은 하위 분류까지 포함하지만 해제는 직접 매핑만 가능.
  const [directIds, setDirectIds] = useState<Set<number>>(new Set())
  // 우측 그리드 페이지네이션 — page 는 1-base(서버), pageSize 는 페이지당 행 수.
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  const [rootOpen, setRootOpen] = useState(true)
  const [asideWidth, setAsideWidth] = useState(288) // 트리 폭(px), w-72 = 18rem 기준
  const dragRef = useRef<{ startX: number; startW: number } | null>(null)
  // 분류 트리 드래그 정렬 상태
  const [dragInfo, setDragInfo] = useState<{ id: number; parentId: number | null } | null>(null)
  const [dropTarget, setDropTarget] = useState<{ id: number; pos: "before" | "after" } | null>(null)
  // 매핑 제거 확인 대상 데이터셋 id
  const [removeTarget, setRemoveTarget] = useState<number | null>(null)
  // 삭제 확인 대상 — 분류(우클릭 삭제)·분류체계(삭제 버튼)
  const [catDeleteTarget, setCatDeleteTarget] = useState<TreeCategory | null>(null)
  const [taxDeleteOpen, setTaxDeleteOpen] = useState(false)

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
  // 분류체계 생성/수정(이름·설명) 공용 대화상자
  const [taxDialog, setTaxDialog] = useState<{ open: boolean; mode: "create" | "edit"; name: string; description: string }>(
    { open: false, mode: "create", name: "", description: "" },
  )
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

  const loadDatasets = useCallback(async (sel: Sel, pg: number, ps: number) => {
    // 선택이 없으면 전체 데이터셋, 분류 선택 시 해당 분류, 미분류 선택 시 미분류만 표시.
    const empty = { items: [], total: 0, page: 1, page_size: ps }
    let params: Parameters<typeof fetchDatasets>[0]
    if (!sel) {
      params = { page: pg, pageSize: ps }
    } else if (sel.kind === "category") {
      params = { categoryId: sel.id, page: pg, pageSize: ps }
      const [ds, direct] = await Promise.all([
        fetchDatasets(params).catch(() => empty),
        fetchDirectDatasetIds(sel.id).catch(() => [] as number[]),
      ])
      setDirectIds(new Set(direct))
      setDatasets(ds)
      return
    } else {
      if (taxId == null) { setDatasets(empty); return }
      params = { taxonomyId: taxId, uncategorized: true, page: pg, pageSize: ps }
    }
    setDatasets(await fetchDatasets(params).catch(() => empty))
  }, [taxId])

  useEffect(() => { loadDatasets(selected, page, pageSize) }, [selected, page, pageSize, loadDatasets, tree])

  // 선택 변경 시 1페이지로 복귀(트리/미분류/분류체계 전환 공통).
  const selectNode = useCallback((sel: Sel) => { setSelected(sel); setPage(1) }, [])

  const refresh = async () => { if (taxId != null) await loadTree(taxId); await loadDatasets(selected, page, pageSize) }

  // ---- taxonomy ----
  const currentTax = taxonomies.find((t) => t.id === taxId) ?? null
  const openCreateTax = () => setTaxDialog({ open: true, mode: "create", name: "", description: "" })
  const openEditTax = () => {
    if (!currentTax) return
    setTaxDialog({ open: true, mode: "edit", name: currentTax.name, description: currentTax.description ?? "" })
  }
  const handleTaxSave = async () => {
    if (!taxDialog.name.trim()) return
    const description = taxDialog.description.trim()
    try {
      if (taxDialog.mode === "create") {
        const t = await createTaxonomy({ name: taxDialog.name.trim(), description: description || undefined })
        setTaxDialog((d) => ({ ...d, open: false }))
        await loadTaxonomies(); setTaxId(t.id)
        toast.success("분류체계를 생성했습니다.")
      } else if (taxId != null) {
        // 설명을 비우면 null 로 저장(설명 삭제)
        await updateTaxonomy(taxId, { name: taxDialog.name.trim(), description: description || null })
        setTaxDialog((d) => ({ ...d, open: false }))
        await loadTaxonomies(); await loadTree(taxId)
        toast.success("분류체계를 수정했습니다.")
      }
    } catch (e) { notifyError(e, "분류체계를 저장하지 못했습니다.") }
  }
  const handleTaxDelete = async () => {
    if (taxId == null) return
    try {
      await deleteTaxonomy(taxId)
      toast.success("분류체계를 삭제했습니다.")
      setTaxId(null); setTree(null); selectNode(null)
      await loadTaxonomies()
    } catch (e) { notifyError(e, "분류체계를 삭제하지 못했습니다. 하위 분류가 남아 있는지 확인하세요.") }
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
    } catch (e) { notifyError(e, "분류를 저장하지 못했습니다.") }
  }
  const handleCatDelete = async (id: number) => {
    try { await deleteCategory(id); if (selected?.kind === "category" && selected.id === id) selectNode(null); await refresh() }
    catch (e) { notifyError(e, "분류를 삭제하지 못했습니다. 하위 분류가 남아 있는지 확인하세요.") }
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
      toast.success(`데이터셋 ${mapSelected.size}개를 매핑했습니다.`)
      setMapOpen(false)
      await refresh()
    } catch (e) { notifyError(e, "데이터셋을 분류에 매핑하지 못했습니다.") }
  }
  const unmapDataset = async (datasetId: number) => {
    if (selected?.kind !== "category") return
    try { await removeDatasetCategory(datasetId, selected.id); toast.success("매핑을 제거했습니다."); await refresh() }
    catch (e) { notifyError(e, "매핑을 제거하지 못했습니다.") }
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
    } catch (e) { notifyError(e, "분류 순서를 바꾸지 못했습니다.") }
  }

  const renderCat = (c: TreeCategory, depth: number) => {
    const open = !collapsed.has(c.id)
    const active = selected?.kind === "category" && selected.id === c.id
    const isDragging = dragInfo?.id === c.id
    const indicating = dropTarget?.id === c.id ? dropTarget.pos : null
    return (
      <div key={c.id}>
        <ContextMenu>
          <ContextMenuTrigger asChild disabled={!canEdit}>
            <div
              className={rowCls(active) + (isDragging ? " opacity-40" : "")}
              style={{ paddingLeft: depth * 16 + 6, boxShadow: indicating === "before" ? "inset 0 2px 0 0 var(--primary)" : indicating === "after" ? "inset 0 -2px 0 0 var(--primary)" : undefined }}
              onClick={() => selectNode({ kind: "category", id: c.id })}
              draggable={canEdit}
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
            <ContextMenuItem variant="destructive" onClick={() => setCatDeleteTarget(c)}>삭제</ContextMenuItem>
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
          <Select value={taxId != null ? String(taxId) : ""} onValueChange={(v) => { setTaxId(Number(v)); selectNode(null) }}>
            <SelectTrigger className="h-9 w-60 text-sm"><SelectValue placeholder="분류체계 선택" /></SelectTrigger>
            <SelectContent>
              {taxonomies.map((t) => <SelectItem key={t.id} value={String(t.id)} className="text-sm">{t.name}</SelectItem>)}
            </SelectContent>
          </Select>
          {canEdit && <Button size="sm" variant="outline" onClick={openCreateTax}><Plus className="mr-1 h-4 w-4" /> 분류체계 추가</Button>}
          {canEdit && currentTax && <Button size="sm" variant="outline" onClick={openEditTax}><Pencil className="mr-1 h-4 w-4" /> 분류체계 수정</Button>}
          {canEdit && taxId != null && <Button size="sm" variant="outline" className="text-destructive" onClick={() => setTaxDeleteOpen(true)}><Minus className="mr-1 h-4 w-4" /> 분류체계 삭제</Button>}
          {currentTax?.description && <span className="truncate text-sm text-muted-foreground" title={currentTax.description}>{currentTax.description}</span>}
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
                  <ContextMenuTrigger asChild disabled={!canEdit}>
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
                        {canEdit ? <>분류가 없습니다. &quot;카테고리&quot; 우클릭 → 분류 추가</> : "분류가 없습니다."}
                      </p>
                    )}
                    {tree.categories.map((c) => renderCat(c, 1))}
                    <div className={rowCls(selected?.kind === "uncategorized")} style={{ paddingLeft: 1 * 16 + 6 }} onClick={() => selectNode({ kind: "uncategorized" })}>
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
                {canEdit && selected?.kind === "category" && (
                  <Button size="sm" onClick={openMap}><Plus className="mr-1 h-4 w-4" /> 데이터셋 매핑</Button>
                )}
              </div>
              {/* 데이터셋 메뉴와 동일한 그리드를 그대로 재사용. 분류 선택 시 행 끝에 "제거"(매핑 해제, 확인 후). */}
              <DatasetsGrid
                data={datasets.items}
                onRemove={canEdit && selected?.kind === "category" ? (id) => setRemoveTarget(id) : undefined}
                canRemove={(row) => directIds.has(row.id)}
              />
              {/* 페더레이션 탐색과 동일한 페이지네이션(서버 페이지). 데이터가 있으면 표시. */}
              {datasets.total > 0 && (() => {
                const pageCount = Math.max(1, Math.ceil(datasets.total / pageSize))
                const safePage = Math.min(page, pageCount)
                return (
                  <GridPagination
                    page={safePage - 1}
                    pageCount={pageCount}
                    pageSize={pageSize}
                    onPage={(p) => setPage(p + 1)}
                    onPageSize={(s) => { setPageSize(s); setPage(1) }}
                  />
                )
              })()}
            </div>
          </main>
        </div>
      </div>

      {/* 분류체계 생성/수정 */}
      <Dialog open={taxDialog.open} onOpenChange={(o) => setTaxDialog((d) => ({ ...d, open: o }))}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle>{taxDialog.mode === "create" ? "분류체계 추가" : "분류체계 수정"}</DialogTitle></DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-1.5">
              <Label className="text-sm">이름 <span className="text-destructive">*</span></Label>
              <Input value={taxDialog.name} onChange={(e) => setTaxDialog((d) => ({ ...d, name: e.target.value }))} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleTaxSave() } }} autoFocus placeholder="예: 업무 도메인" className="h-9 text-sm" />
            </div>
            <div className="grid gap-1.5">
              <Label className="text-sm">설명</Label>
              <Input value={taxDialog.description} onChange={(e) => setTaxDialog((d) => ({ ...d, description: e.target.value }))} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleTaxSave() } }} placeholder="분류체계의 용도·기준" className="h-9 text-sm" />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setTaxDialog((d) => ({ ...d, open: false }))}>취소</Button>
              <Button onClick={handleTaxSave} disabled={!taxDialog.name.trim()}>{taxDialog.mode === "create" ? "생성" : "저장"}</Button>
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

      {/* 분류 삭제 확인 — 매핑된 데이터셋이 있으면 매핑도 함께 해제됨을 알린다 */}
      <AlertDialog open={catDeleteTarget != null} onOpenChange={(o) => { if (!o) setCatDeleteTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>분류를 삭제할까요?</AlertDialogTitle>
            <AlertDialogDescription>
              {catDeleteTarget && (() => {
                const c = catDeleteTarget
                let msg = `"${c.name}" 분류를 삭제합니다.`
                if (c.children.length > 0) msg += ` 하위 분류 ${c.children.length}개가 있어 삭제되지 않습니다. 하위 분류를 먼저 정리하세요.`
                else if (c.dataset_count > 0) msg += ` 이 분류에 매핑된 데이터셋 ${c.dataset_count}개의 매핑도 함께 해제됩니다(데이터셋 자체는 삭제되지 않음).`
                return msg
              })()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                if (catDeleteTarget != null) await handleCatDelete(catDeleteTarget.id)
                setCatDeleteTarget(null)
              }}
            >
              삭제
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 분류체계 삭제 확인 */}
      <AlertDialog open={taxDeleteOpen} onOpenChange={setTaxDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>분류체계를 삭제할까요?</AlertDialogTitle>
            <AlertDialogDescription>
              {(() => {
                let msg = `"${currentTax?.name ?? "이 분류체계"}" 분류체계를 삭제합니다.`
                // 서버 409 메시지("하위 분류 N개")와 같은 기준 — 모든 단계의 분류 수
                const n = countCategories(tree?.categories ?? [])
                if (n > 0) msg += ` 분류 ${n}개가 있어 삭제되지 않습니다. 분류를 먼저 정리하세요.`
                return msg
              })()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                await handleTaxDelete()
                setTaxDeleteOpen(false)
              }}
            >
              삭제
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

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
