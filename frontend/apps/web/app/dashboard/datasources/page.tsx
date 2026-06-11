"use client"

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { Eye, EyeOff, Server } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { Switch } from "@workspace/ui/components/switch"
import { Textarea } from "@workspace/ui/components/textarea"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { DashboardHeader } from "@/components/dashboard-header"
import type { Datasource } from "@/features/datasets/data/schema"
import { ErdTab } from "@/features/datasets/components/erd-tab"
import { usePermissions } from "@/features/permissions/use-permissions"
import {
  DATASOURCE_CONFIGS,
  DATASOURCE_TYPES,
  getDefaultConfig,
  type DatasourceFieldDef,
} from "@/features/datasources/datasource-configs"
import {
  assignDatasourceSystem,
  createOrganization,
  createSystem,
  deleteOrganization,
  deleteSystem,
  fetchTopology,
  updateOrganization,
  updateSystem,
  type Topology,
  type TopologyOrganization,
  type TopologyDatasource,
  type TopologySystem,
} from "@/features/topology/api"
import { TopologyTree, type SelectedNode } from "@/features/topology/components/topology-tree"

const BASE = "/api/v1/catalog"

// 데이터 소스 응답에 소속 시스템 정보가 추가됨
type DatasourceRow = Datasource & { system_id?: number | null; system_name?: string | null }

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function fetchDatasources(): Promise<DatasourceRow[]> {
  const res = await fetch(`${BASE}/datasources`)
  if (!res.ok) throw new Error(`Failed: ${res.status}`)
  return res.json()
}

async function createDatasource(payload: {
  name: string
  type: string
  origin: string
}): Promise<Datasource> {
  const res = await fetch(`${BASE}/datasources`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Failed: ${res.status}`)
  }
  return res.json()
}

async function updateDatasource(id: number, payload: { name?: string; origin?: string }): Promise<Datasource> {
  const res = await fetch(`${BASE}/datasources/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Failed: ${res.status}`)
  }
  return res.json()
}

async function deleteDatasource(id: number, force = false): Promise<void> {
  const res = await fetch(`${BASE}/datasources/${id}?force=${force}`, { method: "DELETE" })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Failed: ${res.status}`)
  }
}

async function fetchDeleteImpact(
  id: number,
): Promise<{ dataset_count: number; external_lineage_count: number }> {
  const res = await fetch(`${BASE}/datasources/${id}/delete-impact`)
  if (!res.ok) return { dataset_count: 0, external_lineage_count: 0 }
  return res.json()
}

async function testDatasourceConnection(
  type: string,
  config: Record<string, unknown>,
): Promise<{ ok: boolean; message: string; latency_ms?: number }> {
  const res = await fetch(`${BASE}/datasources/test-connection`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type, config }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    return { ok: false, message: err.detail || `Failed: ${res.status}` }
  }
  return res.json()
}

async function fetchDatasourceConfig(id: number): Promise<Record<string, unknown> | null> {
  const res = await fetch(`${BASE}/datasources/${id}/configuration`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`Failed: ${res.status}`)
  const data = await res.json()
  return data.config
}

async function saveDatasourceConfig(id: number, config: Record<string, unknown>): Promise<void> {
  const res = await fetch(`${BASE}/datasources/${id}/configuration`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config }),
  })
  if (!res.ok) throw new Error(`Failed: ${res.status}`)
}

// ---------------------------------------------------------------------------
// Validation helper
// ---------------------------------------------------------------------------

function hasRequiredFields(
  fields: DatasourceFieldDef[],
  values: Record<string, unknown>,
): boolean {
  const seen = new Set<string>()
  for (const field of fields) {
    if (seen.has(field.key)) continue
    seen.add(field.key)
    if (!field.required) continue

    if (field.showWhen) {
      const allConditions = fields
        .filter((f) => f.key === field.key && f.showWhen)
        .map((f) => f.showWhen!)
      const visible = allConditions.some(
        (cond) => String(values[cond.field] ?? "") === cond.value,
      )
      if (!visible) continue
    }

    const val = values[field.key]
    if (val === undefined || val === null || val === "") return false
  }
  return true
}

// ---------------------------------------------------------------------------
// Topology flatten helpers
// ---------------------------------------------------------------------------

interface FlatOrg {
  id: number
  name: string
  depth: number
}
interface FlatSystem {
  id: number
  name: string
  orgName: string
}

function flattenOrgs(orgs: TopologyOrganization[], depth = 0, acc: FlatOrg[] = []): FlatOrg[] {
  for (const o of orgs) {
    acc.push({ id: o.id, name: o.name, depth })
    flattenOrgs(o.children, depth + 1, acc)
  }
  return acc
}

function flattenSystems(orgs: TopologyOrganization[], acc: FlatSystem[] = []): FlatSystem[] {
  for (const o of orgs) {
    for (const s of o.systems) acc.push({ id: s.id, name: s.name, orgName: o.name })
    flattenSystems(o.children, acc)
  }
  return acc
}

// ---------------------------------------------------------------------------
// Dynamic config form
// ---------------------------------------------------------------------------

function ConfigForm({
  fields,
  values,
  onChange,
}: {
  fields: DatasourceFieldDef[]
  values: Record<string, unknown>
  onChange: (key: string, value: unknown) => void
}) {
  const [showPasswords, setShowPasswords] = useState<Record<string, boolean>>({})

  const seen = new Set<string>()
  const uniqueFields = fields.filter((f) => {
    if (seen.has(f.key)) return false
    seen.add(f.key)
    return true
  })

  const isVisible = (field: DatasourceFieldDef): boolean => {
    const conditions = fields
      .filter((f) => f.key === field.key && f.showWhen)
      .map((f) => f.showWhen!)
    if (conditions.length === 0) return true
    return conditions.some((cond) => String(values[cond.field] ?? "") === cond.value)
  }

  return (
    <div className="grid gap-3">
      {uniqueFields.map((field) => {
        if (!isVisible(field)) return null

        if (field.type === "toggle") {
          return (
            <div key={field.key} className="flex items-center justify-between">
              <Label className="text-sm">{field.label}</Label>
              <Switch
                checked={Boolean(values[field.key] ?? field.defaultValue ?? false)}
                onCheckedChange={(v) => onChange(field.key, v)}
              />
            </div>
          )
        }

        if (field.type === "select") {
          return (
            <div key={field.key} className="grid gap-1.5">
              <Label className="text-sm">
                {field.label}
                {field.required && <span className="text-destructive ml-0.5">*</span>}
              </Label>
              <Select
                value={String(values[field.key] ?? field.defaultValue ?? "")}
                onValueChange={(v) => onChange(field.key, v)}
              >
                <SelectTrigger className="h-9 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {field.options?.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value} className="text-sm">
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )
        }

        const isPassword = field.type === "password"
        const showPw = showPasswords[field.key] ?? false

        return (
          <div key={field.key} className="grid gap-1.5">
            <Label className="text-sm">
              {field.label}
              {field.required && <span className="text-destructive ml-0.5">*</span>}
            </Label>
            <div className="relative">
              <Input
                type={isPassword && !showPw ? "password" : field.type === "number" ? "number" : "text"}
                placeholder={field.placeholder}
                value={String(values[field.key] ?? "")}
                onChange={(e) =>
                  onChange(
                    field.key,
                    field.type === "number" ? Number(e.target.value) || "" : e.target.value,
                  )
                }
                className={`h-9 text-sm ${isPassword ? "pr-9" : ""}`}
              />
              {isPassword && (
                <button
                  type="button"
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  onClick={() => setShowPasswords((p) => ({ ...p, [field.key]: !showPw }))}
                  tabIndex={-1}
                >
                  {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function DatasourcesPage() {
  const [datasources, setDatasources] = useState<DatasourceRow[]>([])
  const [topology, setTopology] = useState<Topology>({
    organizations: [],
    unassigned: { datasources: [], systems: [] },
  })
  const [isLoading, setIsLoading] = useState(true)
  const [selected, setSelected] = useState<SelectedNode | null>(null)

  // 좌측 토폴로지 트리 스크롤 위치 — 추가/편집/삭제 후 재조회 시 위치 복원.
  const treeRef = useRef<HTMLElement>(null)
  const treeScrollRef = useRef(0)

  // 트리/상세 사이 separator 드래그로 트리 폭 조절 (분류 체계 페이지와 동일 패턴)
  const [asideWidth, setAsideWidth] = useState(288) // w-72 = 18rem 기준
  const resizeRef = useRef<{ startX: number; startW: number } | null>(null)
  const startResize = (e: React.MouseEvent) => {
    e.preventDefault()
    resizeRef.current = { startX: e.clientX, startW: asideWidth }
    const onMove = (ev: MouseEvent) => {
      if (!resizeRef.current) return
      const next = resizeRef.current.startW + (ev.clientX - resizeRef.current.startX)
      setAsideWidth(Math.min(560, Math.max(200, next)))
    }
    const onUp = () => {
      resizeRef.current = null
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
  useEffect(() => {
    if (isLoading) return
    const el = treeRef.current
    if (!el) return
    const id = requestAnimationFrame(() => {
      el.scrollTop = treeScrollRef.current
    })
    return () => cancelAnimationFrame(id)
  }, [isLoading])

  // Add datasource dialog
  const [addOpen, setAddOpen] = useState(false)
  const [addStep, setAddStep] = useState<"type" | "config">("type")
  const [selectedType, setSelectedType] = useState<string>("")
  const [addDisplayName, setAddDisplayName] = useState("")
  const [addOrigin, setAddOrigin] = useState<string>("DEV")
  const [addConfig, setAddConfig] = useState<Record<string, unknown>>({})
  const [addSaving, setAddSaving] = useState(false)
  const [addTesting, setAddTesting] = useState(false)
  const [addTargetSystemId, setAddTargetSystemId] = useState<number | null>(null)

  // Delete datasource dialog
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleteDatasourceTarget, setDeleteDatasourceTarget] = useState<Datasource | null>(null)
  const [deleteConfirmInput, setDeleteConfirmInput] = useState("")
  const [deleting, setDeleting] = useState(false)
  const [deleteImpact, setDeleteImpact] = useState<{ dataset_count: number; external_lineage_count: number }>({ dataset_count: 0, external_lineage_count: 0 })

  // Organization dialog (create/edit)
  const [orgDialog, setOrgDialog] = useState<{
    open: boolean
    mode: "create" | "edit"
    id?: number
    name: string
    parentId: number | null
  }>({ open: false, mode: "create", name: "", parentId: null })
  const [orgSaving, setOrgSaving] = useState(false)

  // System dialog (create/edit)
  // 시스템 생성용 다이얼로그 (편집은 우측 상세 인라인 폼에서 처리)
  const [sysDialog, setSysDialog] = useState<{
    open: boolean
    name: string
    orgId: number | null
    summary: string
    description: string
    owner: string
    status: string
  }>({ open: false, name: "", orgId: null, summary: "", description: "", owner: "", status: "ACTIVE" })
  const [sysSaving, setSysSaving] = useState(false)

  const flatOrgs = useMemo(() => flattenOrgs(topology.organizations), [topology])
  const flatSystems = useMemo(() => {
    const list = flattenSystems(topology.organizations)
    for (const s of topology.unassigned.systems ?? []) {
      list.push({ id: s.id, name: s.name, orgName: "미분류" })
    }
    return list
  }, [topology])

  const loadAll = useCallback(async () => {
    try {
      setIsLoading(true)
      const [pf, topo] = await Promise.all([fetchDatasources(), fetchTopology()])
      setDatasources(pf)
      setTopology(topo)
    } catch {
      // ignore
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadAll()
  }, [loadAll])

  // ---- Add Datasource -------------------------------------------------------

  const openAddDialog = (targetSystemId: number | null = null) => {
    setAddTargetSystemId(targetSystemId)
    setSelectedType("")
    setAddDisplayName("")
    setAddOrigin("DEV")
    setAddConfig({})
    setAddStep("type")
    setAddOpen(true)
  }

  const handleSelectType = (typeName: string) => {
    setSelectedType(typeName)
    const pt = DATASOURCE_TYPES.find((t) => t.name === typeName)
    setAddDisplayName(pt?.display ?? typeName)
    setAddConfig(getDefaultConfig(typeName))
    setAddStep("config")
  }

  const handleAddSave = async () => {
    if (!selectedType || !addDisplayName.trim()) return
    setAddSaving(true)
    try {
      const created = await createDatasource({
        name: addDisplayName.trim(),
        type: selectedType,
        origin: addOrigin,
      })
      await saveDatasourceConfig(created.id, addConfig)
      // 컨텍스트 메뉴로 지정한 시스템(우선) 또는 현재 선택된 시스템에 즉시 배정
      const targetSys = addTargetSystemId ?? (selected?.kind === "system" ? selected.id : null)
      if (targetSys != null) {
        await assignDatasourceSystem(created.id, targetSys).catch(() => {})
      }
      toast.success(`데이터 소스 "${addDisplayName}" 을 생성했습니다.`)
      setAddOpen(false)
      await loadAll()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "데이터 소스 생성에 실패했습니다.")
    } finally {
      setAddSaving(false)
    }
  }

  const handleAddTest = async () => {
    if (!selectedType) return
    setAddTesting(true)
    const r = await testDatasourceConnection(selectedType, addConfig)
    setAddTesting(false)
    if (r.ok) toast.success(`연결 성공${r.latency_ms != null ? ` (${r.latency_ms}ms)` : ""}`)
    else toast.error(`연결 실패: ${r.message}`)
  }

  // ---- Save Datasource (우측 상세 인라인 폼) --------------------------------

  const handleSaveDatasource = async (
    id: number,
    name: string,
    origin: string,
    systemId: number | null,
    config: Record<string, unknown>,
  ) => {
    const cur = datasources.find((x) => x.id === id)
    try {
      const patch: { name?: string; origin?: string } = {}
      if (cur && name.trim() && name.trim() !== cur.name) patch.name = name.trim()
      if (cur && origin !== cur.origin) patch.origin = origin
      if (Object.keys(patch).length > 0) await updateDatasource(id, patch)
      if (cur && systemId !== (cur.system_id ?? null)) {
        await assignDatasourceSystem(id, systemId)
      }
      await saveDatasourceConfig(id, config)
      toast.success("데이터 소스을 저장했습니다.")
      await loadAll()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "저장에 실패했습니다.")
    }
  }

  // ---- Delete Datasource ----------------------------------------------------

  const openDeleteDialog = async (datasource: Datasource) => {
    setDeleteDatasourceTarget(datasource)
    setDeleteConfirmInput("")
    setDeleteImpact({ dataset_count: 0, external_lineage_count: 0 })
    setDeleteOpen(true)
    try {
      setDeleteImpact(await fetchDeleteImpact(datasource.id))
    } catch {
      // 영향 조회 실패해도 다이얼로그는 연다
    }
  }

  const handleDeleteConfirm = async () => {
    if (!deleteDatasourceTarget) return
    setDeleting(true)
    try {
      // 데이터셋이 있으면 force=true 로 cascade 삭제 (다이얼로그에 영향 표시됨)
      await deleteDatasource(deleteDatasourceTarget.id, deleteImpact.dataset_count > 0)
      toast.success(`데이터 소스 "${deleteDatasourceTarget.name}" 을 삭제했습니다.`)
      setDeleteOpen(false)
      setSelected(null)
      await loadAll()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "데이터 소스 삭제에 실패했습니다.")
    } finally {
      setDeleting(false)
    }
  }

  const deleteExpectedInput = deleteDatasourceTarget ? `DELETE ${deleteDatasourceTarget.name}` : ""

  // ---- Organization -------------------------------------------------------

  const openCreateOrg = (parentId: number | null) =>
    setOrgDialog({ open: true, mode: "create", name: "", parentId })
  const openEditOrg = (id: number, name: string, parentId: number | null) =>
    setOrgDialog({ open: true, mode: "edit", id, name, parentId })

  const handleOrgSave = async () => {
    if (!orgDialog.name.trim()) return
    setOrgSaving(true)
    try {
      if (orgDialog.mode === "create") {
        await createOrganization({ name: orgDialog.name.trim(), parent_id: orgDialog.parentId })
        toast.success("조직을 생성했습니다.")
      } else if (orgDialog.id) {
        await updateOrganization(orgDialog.id, { name: orgDialog.name.trim() })
        toast.success("조직을 수정했습니다.")
      }
      setOrgDialog((d) => ({ ...d, open: false }))
      await loadAll()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "저장에 실패했습니다.")
    } finally {
      setOrgSaving(false)
    }
  }

  const handleDeleteOrg = async (id: number) => {
    try {
      await deleteOrganization(id)
      toast.success("조직을 삭제했습니다.")
      setSelected(null)
      await loadAll()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "삭제에 실패했습니다.")
    }
  }

  // ---- System -------------------------------------------------------------

  const openCreateSystem = (orgId: number | null) =>
    setSysDialog({
      open: true,
      name: "",
      orgId: orgId ?? null,
      summary: "",
      description: "",
      owner: "",
      status: "ACTIVE",
    })
  // 시스템 편집은 우측 상세 패널의 인라인 폼에서 저장한다 (팝업 대신).
  const handleSaveSystem = async (
    id: number,
    payload: {
      name: string
      summary: string
      description: string
      owner: string
      status: string
      org_id: number | null
    },
  ) => {
    try {
      await updateSystem(id, payload)
      toast.success("시스템을 저장했습니다.")
      await loadAll()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "저장에 실패했습니다.")
    }
  }

  const handleSysSave = async () => {
    if (!sysDialog.name.trim()) return
    setSysSaving(true)
    try {
      await createSystem({
        name: sysDialog.name.trim(),
        org_id: sysDialog.orgId,
        summary: sysDialog.summary || undefined,
        description: sysDialog.description || undefined,
        owner: sysDialog.owner || undefined,
        status: sysDialog.status,
      })
      toast.success("시스템을 생성했습니다.")
      setSysDialog((d) => ({ ...d, open: false }))
      await loadAll()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "저장에 실패했습니다.")
    } finally {
      setSysSaving(false)
    }
  }

  const handleDeleteSystem = async (id: number, force = false) => {
    try {
      await deleteSystem(id, force)
      toast.success("시스템을 삭제했습니다.")
      setSelected(null)
      await loadAll()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "삭제에 실패했습니다.")
    }
  }

  // ---- Render -------------------------------------------------------------

  const addFields = DATASOURCE_CONFIGS[selectedType] ?? []
  const addConfigValid = addFields.length === 0 || hasRequiredFields(addFields, addConfig)

  return (
    <>
      <DashboardHeader title="데이터 소스" />
      <div className="flex flex-1 flex-col gap-3 p-4 min-h-0">
        <p className="text-sm text-muted-foreground">조직 · 시스템 · 데이터 소스 구조 (행 우클릭으로 추가/편집)</p>

        <div className="flex flex-1 gap-1 min-h-0">
          {/* 좌측: 토폴로지 트리 */}
          <aside
            ref={treeRef}
            onScroll={(e) => { treeScrollRef.current = e.currentTarget.scrollTop }}
            className="shrink-0 overflow-auto rounded-md border p-2"
            style={{ width: asideWidth }}
          >
            {isLoading ? (
              <p className="py-8 text-center text-sm text-muted-foreground">불러오는 중...</p>
            ) : (
              <TopologyTree
                topology={topology}
                selected={selected}
                onSelect={setSelected}
                onAddRootOrg={() => openCreateOrg(null)}
                onAddDatasource={() => openAddDialog(null)}
                onAddDatasourceToSystem={(sid) => openAddDialog(sid)}
                onAddSubOrg={(parentId) => openCreateOrg(parentId)}
                onAddSystem={(orgId) => openCreateSystem(orgId)}
                onEditOrg={(id) => {
                  const o = findOrg(topology.organizations, id)
                  if (o) openEditOrg(o.id, o.name, o.parent_id)
                }}
                onDeleteOrg={handleDeleteOrg}
                onDeleteSystem={(id) => {
                  const s = findSystem(topology, id)
                  handleDeleteSystem(id, (s?.datasources.length ?? 0) > 0)
                }}
                onDeleteDatasource={(id) => {
                  const p = datasources.find((x) => x.id === id)
                  if (p) openDeleteDialog(p)
                }}
              />
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

          {/* 우측: 선택 노드 상세 */}
          <main className="flex-1 overflow-auto rounded-md border p-4">
            <DetailPanel
              selected={selected}
              datasources={datasources}
              topology={topology}
              flatOrgs={flatOrgs}
              flatSystems={flatSystems}
              onSelect={setSelected}
              onSaveSystem={handleSaveSystem}
              onSaveDatasource={handleSaveDatasource}
            />
          </main>
        </div>

        {/* 범례 */}
        <div className="flex h-[28px] items-center gap-4 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">범례 :</span>
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded-full bg-sky-500" /> DEV
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded-full bg-orange-500" /> STAGING
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded-full bg-emerald-600" /> PROD
          </span>
        </div>
      </div>

      {/* ---- Add Datasource Dialog ---- */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{addStep === "type" ? "데이터 소스 유형 선택" : `${addDisplayName} 추가`}</DialogTitle>
            {addStep === "type" && (
              <DialogDescription>추가할 데이터 데이터 소스 유형을 선택하세요.</DialogDescription>
            )}
          </DialogHeader>

          {addStep === "type" ? (
            <div className="grid grid-cols-2 gap-2 py-2">
              {DATASOURCE_TYPES.filter((pt) => !pt.hiddenFromAddDialog).map((pt) => (
                <Button
                  key={pt.name}
                  variant="outline"
                  className="justify-start h-auto py-2.5 px-3 disabled:opacity-50"
                  disabled={!pt.implemented}
                  title={pt.implemented ? undefined : "준비 중입니다"}
                  onClick={() => handleSelectType(pt.name)}
                >
                  <Server className="h-4 w-4 mr-2 shrink-0 text-muted-foreground" />
                  <div className="text-left flex-1">
                    <div className="text-sm font-medium flex items-center gap-1.5">
                      {pt.display}
                      {!pt.implemented && (
                        <span className="inline-flex items-center rounded border px-1 py-0 text-[10px] font-normal text-muted-foreground">
                          준비 중
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground font-mono">{pt.name}</div>
                  </div>
                </Button>
              ))}
            </div>
          ) : (
            <div className="grid gap-4 py-2">
              <div className="grid gap-1.5">
                <Label className="text-sm">
                  표시 이름 <span className="text-destructive">*</span>
                </Label>
                <Input
                  value={addDisplayName}
                  onChange={(e) => setAddDisplayName(e.target.value)}
                  placeholder="예: 운영 PostgreSQL"
                  className="h-9 text-sm"
                />
              </div>
              <div className="grid gap-1.5">
                <Label className="text-sm">
                  환경 <span className="text-destructive">*</span>
                </Label>
                <Select value={addOrigin} onValueChange={setAddOrigin}>
                  <SelectTrigger className="h-9 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="DEV" className="text-sm">DEV</SelectItem>
                    <SelectItem value="STAGING" className="text-sm">STAGING</SelectItem>
                    <SelectItem value="PROD" className="text-sm">PROD</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {addFields.length > 0 && (
                <div className="border-t pt-3">
                  <p className="text-sm font-medium mb-3">연결 설정</p>
                  <ConfigForm
                    fields={addFields}
                    values={addConfig}
                    onChange={(k, v) => setAddConfig((prev) => ({ ...prev, [k]: v }))}
                  />
                </div>
              )}

              <div className="flex justify-end gap-2 pt-2">
                <Button variant="outline" onClick={() => setAddOpen(false)} disabled={addSaving}>
                  취소
                </Button>
                <Button variant="outline" onClick={handleAddTest} disabled={addTesting || addSaving}>
                  {addTesting ? "테스트 중..." : "연결 테스트"}
                </Button>
                <Button onClick={handleAddSave} disabled={addSaving || !addDisplayName.trim() || !addConfigValid}>
                  {addSaving ? "생성 중..." : "생성"}
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ---- Delete Datasource Dialog ---- */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>데이터 소스 삭제</DialogTitle>
            <DialogDescription>
              이 작업은 되돌릴 수 없습니다. 삭제를 확정하려면 아래에{" "}
              <span className="font-mono font-semibold text-foreground">{deleteExpectedInput}</span> 을(를) 입력하세요.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            {deleteImpact.dataset_count > 0 && (
              <div className="rounded-md border border-destructive/40 bg-destructive/5 p-2.5 text-xs text-destructive">
                이 데이터 소스의 <span className="font-semibold">데이터셋 {deleteImpact.dataset_count}개</span>
                {deleteImpact.external_lineage_count > 0 && (
                  <> 와 이를 참조하는 <span className="font-semibold">타 데이터 소스 리니지 {deleteImpact.external_lineage_count}개</span></>
                )}
                가 함께 삭제됩니다.
              </div>
            )}
            <Input
              placeholder={deleteExpectedInput}
              value={deleteConfirmInput}
              onChange={(e) => setDeleteConfirmInput(e.target.value)}
              className="font-mono text-sm"
            />
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setDeleteOpen(false)} disabled={deleting}>
                취소
              </Button>
              <Button
                variant="destructive"
                onClick={handleDeleteConfirm}
                disabled={deleting || deleteConfirmInput !== deleteExpectedInput}
              >
                {deleting ? "삭제 중..." : "삭제"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ---- Organization Dialog ---- */}
      <Dialog open={orgDialog.open} onOpenChange={(o) => setOrgDialog((d) => ({ ...d, open: o }))}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{orgDialog.mode === "create" ? "조직 추가" : "조직 수정"}</DialogTitle>
            {orgDialog.mode === "create" && orgDialog.parentId && (
              <DialogDescription>
                상위 조직: {flatOrgs.find((o) => o.id === orgDialog.parentId)?.name ?? orgDialog.parentId}
              </DialogDescription>
            )}
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-1.5">
              <Label className="text-sm">조직 이름 <span className="text-destructive">*</span></Label>
              <Input
                value={orgDialog.name}
                onChange={(e) => setOrgDialog((d) => ({ ...d, name: e.target.value }))}
                placeholder="예: 데이터데이터 소스본부"
                className="h-9 text-sm"
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setOrgDialog((d) => ({ ...d, open: false }))} disabled={orgSaving}>
                취소
              </Button>
              <Button onClick={handleOrgSave} disabled={orgSaving || !orgDialog.name.trim()}>
                {orgSaving ? "저장 중..." : "저장"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ---- System Dialog ---- */}
      <Dialog open={sysDialog.open} onOpenChange={(o) => setSysDialog((d) => ({ ...d, open: o }))}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>시스템 추가</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-1.5">
              <Label className="text-sm">시스템 이름 <span className="text-destructive">*</span></Label>
              <Input
                value={sysDialog.name}
                onChange={(e) => setSysDialog((d) => ({ ...d, name: e.target.value }))}
                placeholder="예: 고객데이터데이터 소스"
                className="h-9 text-sm"
              />
            </div>
            <div className="grid gap-1.5">
              <Label className="text-sm">요약</Label>
              <Input
                value={sysDialog.summary}
                onChange={(e) => setSysDialog((d) => ({ ...d, summary: e.target.value }))}
                placeholder="한 줄 요약"
                maxLength={200}
                className="h-9 text-sm"
              />
            </div>
            <div className="grid gap-1.5">
              <Label className="text-sm">설명</Label>
              <Textarea
                value={sysDialog.description}
                onChange={(e) => setSysDialog((d) => ({ ...d, description: e.target.value }))}
                placeholder="상세 설명"
                rows={3}
                className="text-sm"
              />
            </div>
            <div className="grid gap-1.5">
              <Label className="text-sm">소속 조직</Label>
              <Select
                value={sysDialog.orgId ? String(sysDialog.orgId) : "none"}
                onValueChange={(v) =>
                  setSysDialog((d) => ({ ...d, orgId: v === "none" ? null : Number(v) }))
                }
              >
                <SelectTrigger className="h-9 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none" className="text-sm">미분류 (조직 미배정)</SelectItem>
                  {flatOrgs.map((o) => (
                    <SelectItem key={o.id} value={String(o.id)} className="text-sm">
                      {" ".repeat(o.depth * 2)}{o.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label className="text-sm">담당자</Label>
              <Input
                value={sysDialog.owner}
                onChange={(e) => setSysDialog((d) => ({ ...d, owner: e.target.value }))}
                placeholder="예: 홍길동"
                className="h-9 text-sm"
              />
            </div>
            <div className="grid gap-1.5">
              <Label className="text-sm">상태</Label>
              <Select value={sysDialog.status} onValueChange={(v) => setSysDialog((d) => ({ ...d, status: v }))}>
                <SelectTrigger className="h-9 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ACTIVE" className="text-sm">ACTIVE</SelectItem>
                  <SelectItem value="INACTIVE" className="text-sm">INACTIVE</SelectItem>
                  <SelectItem value="DEPRECATED" className="text-sm">DEPRECATED</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setSysDialog((d) => ({ ...d, open: false }))} disabled={sysSaving}>
                취소
              </Button>
              <Button onClick={handleSysSave} disabled={sysSaving || !sysDialog.name.trim()}>
                {sysSaving ? "저장 중..." : "저장"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

// ---------------------------------------------------------------------------
// Detail panel (우측)
// ---------------------------------------------------------------------------

interface SystemSavePayload {
  name: string
  summary: string
  description: string
  owner: string
  status: string
  org_id: number | null
}

interface DetailProps {
  selected: SelectedNode | null
  datasources: DatasourceRow[]
  topology: Topology
  flatOrgs: FlatOrg[]
  flatSystems: FlatSystem[]
  onSelect: (n: SelectedNode) => void
  onSaveSystem: (id: number, payload: SystemSavePayload) => void
  onSaveDatasource: (
    id: number,
    name: string,
    origin: string,
    systemId: number | null,
    config: Record<string, unknown>,
  ) => void
}

function findOrg(orgs: TopologyOrganization[], id: number): TopologyOrganization | null {
  for (const o of orgs) {
    if (o.id === id) return o
    const f = findOrg(o.children, id)
    if (f) return f
  }
  return null
}

function findSystem(topology: Topology, id: number): TopologySystem | null {
  const walk = (orgs: TopologyOrganization[]): TopologySystem | null => {
    for (const o of orgs) {
      for (const s of o.systems) if (s.id === id) return s
      const f = walk(o.children)
      if (f) return f
    }
    return null
  }
  return walk(topology.organizations) ?? (topology.unassigned.systems ?? []).find((s) => s.id === id) ?? null
}

// 키-값 정보 테이블
function InfoTable({ rows }: { rows: { label: string; value: ReactNode }[] }) {
  return (
    <div className="overflow-hidden rounded-md border">
      <Table>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.label}>
              <TableCell className="w-32 bg-muted/40 font-medium text-muted-foreground">
                {r.label}
              </TableCell>
              <TableCell>{r.value}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

// 데이터 소스 목록 테이블 (행 클릭 시 해당 데이터 소스 선택)
function DatasourcesTable({
  rows,
  onSelect,
}: {
  rows: TopologyDatasource[]
  onSelect: (id: number) => void
}) {
  return (
    <div className="overflow-hidden rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>이름</TableHead>
            <TableHead>유형</TableHead>
            <TableHead>환경</TableHead>
            <TableHead className="text-right">데이터셋</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((p) => (
            <TableRow
              key={p.id}
              className="cursor-pointer"
              onClick={() => onSelect(p.id)}
            >
              <TableCell className="font-medium">{p.name}</TableCell>
              <TableCell className="text-muted-foreground">{p.type}</TableCell>
              <TableCell className="text-muted-foreground">{p.origin}</TableCell>
              <TableCell className="text-right tabular-nums">{p.dataset_count}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

// 데이터 소스 인라인 편집 폼 (우측 상세). key={datasource.id} 로 선택 변경 시 폼/설정이 리셋된다.
function DatasourceDetail({
  datasource,
  flatSystems,
  onSave,
}: {
  datasource: DatasourceRow
  flatSystems: FlatSystem[]
  onSave: (id: number, name: string, origin: string, systemId: number | null, config: Record<string, unknown>) => void
}) {
  const { isFeatureAllowed } = usePermissions()
  const [name, setName] = useState(datasource.name)
  const [origin, setOrigin] = useState(datasource.origin ?? "DEV")
  const [systemId, setSystemId] = useState<number | null>(datasource.system_id ?? null)
  const [config, setConfig] = useState<Record<string, unknown>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    fetchDatasourceConfig(datasource.id)
      .then((c) => { if (active) setConfig(c ?? getDefaultConfig(datasource.type)) })
      .catch(() => { if (active) setConfig(getDefaultConfig(datasource.type)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [datasource.id, datasource.type])

  const fields = DATASOURCE_CONFIGS[datasource.type] ?? []
  const configValid = fields.length === 0 || hasRequiredFields(fields, config)

  const save = async () => {
    if (!name.trim() || !configValid) return
    setSaving(true)
    await onSave(datasource.id, name.trim(), origin, systemId, config)
    setSaving(false)
  }

  const runTest = async () => {
    setTesting(true)
    const r = await testDatasourceConnection(datasource.type, config)
    setTesting(false)
    if (r.ok) toast.success(`연결 성공${r.latency_ms != null ? ` (${r.latency_ms}ms)` : ""}`)
    else toast.error(`연결 실패: ${r.message}`)
  }

  return (
    <div className="grid gap-4 max-w-xl">
      <div className="flex items-center gap-2">
        <Server className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-base font-semibold">{datasource.name}</h2>
      </div>
      <InfoTable
        rows={[
          { label: "유형", value: datasource.type },
          { label: "데이터 소스 ID", value: <span className="font-mono text-xs">{datasource.datasource_id}</span> },
        ]}
      />
      <div className="grid gap-3">
        <div className="grid gap-1.5">
          <Label className="text-sm">표시 이름 <span className="text-destructive">*</span></Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} className="h-9 text-sm" />
        </div>
        <div className="grid gap-1.5">
          <Label className="text-sm">환경</Label>
          <Select value={origin} onValueChange={setOrigin}>
            <SelectTrigger className="h-9 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="DEV" className="text-sm">DEV</SelectItem>
              <SelectItem value="STAGING" className="text-sm">STAGING</SelectItem>
              <SelectItem value="PROD" className="text-sm">PROD</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">변경 시 이 데이터 소스의 모든 데이터셋 환경도 함께 변경됩니다. (URN은 불변)</p>
        </div>
        <div className="grid gap-1.5">
          <Label className="text-sm">소속 시스템</Label>
          <Select value={systemId ? String(systemId) : "none"} onValueChange={(v) => setSystemId(v === "none" ? null : Number(v))}>
            <SelectTrigger className="h-9 text-sm">
              <SelectValue placeholder="미분류" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none" className="text-sm">미분류</SelectItem>
              {flatSystems.map((s) => (
                <SelectItem key={s.id} value={String(s.id)} className="text-sm">
                  {s.name} <span className="text-muted-foreground">· {s.orgName}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {/* 연결 설정 — 접속 정보(비밀번호 포함)라 기능 권한(datasources.connection)으로 통제 */}
        {!isFeatureAllowed("datasources.connection") ? (
          <p className="border-t pt-3 text-sm text-muted-foreground">
            연결 설정을 볼 수 있는 권한이 없습니다.
          </p>
        ) : loading ? (
          <p className="py-2 text-sm text-muted-foreground">연결 설정 불러오는 중...</p>
        ) : fields.length > 0 ? (
          <div className="border-t pt-3">
            <p className="mb-3 text-sm font-medium">연결 설정</p>
            <ConfigForm fields={fields} values={config} onChange={(k, v) => setConfig((prev) => ({ ...prev, [k]: v }))} />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">이 데이터 소스 유형에 대한 설정 템플릿이 없습니다.</p>
        )}
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="outline" onClick={runTest} disabled={testing || loading}>
            {testing ? "테스트 중..." : "연결 테스트"}
          </Button>
          <Button size="sm" onClick={save} disabled={saving || loading || !name.trim() || !configValid}>
            {saving ? "저장 중..." : "저장"}
          </Button>
        </div>
      </div>
    </div>
  )
}

// 시스템 인라인 편집 폼 (우측 상세). key={node.id} 로 선택 변경 시 폼이 리셋된다.
function SystemDetail({
  node,
  flatOrgs,
  onSave,
  onSelectDatasource,
}: {
  node: TopologySystem
  flatOrgs: FlatOrg[]
  onSave: (id: number, payload: SystemSavePayload) => void
  onSelectDatasource: (id: number) => void
}) {
  const [name, setName] = useState(node.name)
  const [summary, setSummary] = useState(node.summary ?? "")
  const [description, setDescription] = useState(node.description ?? "")
  const [owner, setOwner] = useState(node.owner ?? "")
  const [status, setStatus] = useState(node.status)
  const [orgId, setOrgId] = useState<number | null>(node.org_id)
  const [saving, setSaving] = useState(false)

  const dirty =
    name !== node.name ||
    summary !== (node.summary ?? "") ||
    description !== (node.description ?? "") ||
    owner !== (node.owner ?? "") ||
    status !== node.status ||
    orgId !== node.org_id

  const save = async () => {
    if (!name.trim()) return
    setSaving(true)
    await onSave(node.id, {
      name: name.trim(),
      summary,
      description,
      owner,
      status,
      org_id: orgId,
    })
    setSaving(false)
  }

  return (
    <div className="grid gap-4 max-w-xl">
      <h2 className="text-base font-semibold">{node.name}</h2>
      <div className="grid gap-3">
        <div className="grid gap-1.5">
          <Label className="text-sm">이름 <span className="text-destructive">*</span></Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} className="h-9 text-sm" />
        </div>
        <div className="grid gap-1.5">
          <Label className="text-sm">요약</Label>
          <Input
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder="한 줄 요약"
            maxLength={200}
            className="h-9 text-sm"
          />
        </div>
        <div className="grid gap-1.5">
          <Label className="text-sm">설명</Label>
          <Textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="상세 설명"
            rows={4}
            className="text-sm"
          />
        </div>
        <div className="grid gap-1.5">
          <Label className="text-sm">소속 조직</Label>
          <Select value={orgId ? String(orgId) : "none"} onValueChange={(v) => setOrgId(v === "none" ? null : Number(v))}>
            <SelectTrigger className="h-9 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none" className="text-sm">미분류</SelectItem>
              {flatOrgs.map((o) => (
                <SelectItem key={o.id} value={String(o.id)} className="text-sm">
                  {" ".repeat(o.depth * 2)}{o.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-1.5">
            <Label className="text-sm">담당자</Label>
            <Input value={owner} onChange={(e) => setOwner(e.target.value)} className="h-9 text-sm" />
          </div>
          <div className="grid gap-1.5">
            <Label className="text-sm">상태</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="h-9 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ACTIVE" className="text-sm">ACTIVE</SelectItem>
                <SelectItem value="INACTIVE" className="text-sm">INACTIVE</SelectItem>
                <SelectItem value="DEPRECATED" className="text-sm">DEPRECATED</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="flex justify-end">
          <Button size="sm" onClick={save} disabled={saving || !name.trim() || !dirty}>
            {saving ? "저장 중..." : "저장"}
          </Button>
        </div>
      </div>

      <div className="grid gap-1.5">
        <Label className="text-sm text-muted-foreground">데이터 소스 ({node.datasources.length})</Label>
        {node.datasources.length > 0 ? (
          <DatasourcesTable rows={node.datasources} onSelect={onSelectDatasource} />
        ) : (
          <p className="text-sm text-muted-foreground">
            데이터 소스이 없습니다. 미분류 데이터 소스을 선택해 이 시스템에 배정하세요.
          </p>
        )}
      </div>
    </div>
  )
}

function DetailPanel(props: DetailProps) {
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
    if (!p) return <p className="text-sm text-muted-foreground">데이터 소스을 찾을 수 없습니다.</p>
    // 데이터 소스 전체 ERD — DDL FK 파싱 기반이므로 RDBMS 타입에서만 의미가 있다
    // (데이터셋 상세의 더보기 > ER 다이어그램과 동일 게이팅 목록).
    const RDBMS_TYPES = ["mysql", "mariadb", "postgresql", "greenplum", "oracle", "mssql", "sqlserver", "starrocks", "tibero"]
    return (
      <div className="grid gap-4">
        <DatasourceDetail key={p.id} datasource={p} flatSystems={flatSystems} onSave={props.onSaveDatasource} />
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
        <p className="text-sm text-muted-foreground">시스템에 배정되지 않은 데이터 소스입니다. 각 데이터 소스을 선택해 시스템에 배정하세요.</p>
        {items.length > 0 ? (
          <DatasourcesTable rows={items} onSelect={(id) => props.onSelect({ kind: "datasource", id })} />
        ) : (
          <p className="text-sm text-muted-foreground">미분류 데이터 소스이 없습니다.</p>
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
