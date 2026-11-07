"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { errorMessage, notifyError, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import { DashboardHeader } from "@/components/dashboard-header"
import { useAuth } from "@/features/auth"
import type { Datasource } from "@/features/datasets/data/schema"
import { usePermissions } from "@/features/permissions/use-permissions"
import {
  fetchDatasources,
  saveDatasourceConfig,
  updateDatasource,
  type DatasourceRow,
} from "@/features/datasources/api"
import { findOrg, findSystem, flattenOrgs, flattenSystems } from "@/features/datasources/utils"
import { AddDatasourceDialog } from "@/features/datasources/components/add-datasource-dialog"
import { DeleteDatasourceDialog } from "@/features/datasources/components/delete-datasource-dialog"
import { DetailPanel } from "@/features/datasources/components/detail-panel"
import type { SystemSavePayload } from "@/features/datasources/components/system-detail"
import {
  INITIAL_ORG_DIALOG,
  INITIAL_SYSTEM_DIALOG,
  OrgDialog,
  SystemCreateDialog,
  TopologyDeleteConfirm,
  type OrgDialogState,
  type SystemDialogState,
  type TopologyDeleteTarget,
} from "@/features/datasources/components/topology-dialogs"
import {
  assignDatasourceSystem,
  fetchTopology,
  updateSystem,
  type Topology,
} from "@/features/topology/api"
import { TopologyTree, type SelectedNode } from "@/features/topology/components/topology-tree"

export default function DatasourcesPage() {
  const [datasources, setDatasources] = useState<DatasourceRow[]>([])
  const [topology, setTopology] = useState<Topology>({
    organizations: [],
    unassigned: { datasources: [], systems: [] },
  })
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const loadedOnce = useRef(false)
  const [hasData, setHasData] = useState(false)
  const loadSeq = useRef(0)
  const [selected, setSelected] = useState<SelectedNode | null>(null)

  // 데이터 소스·조직·시스템 관리 — 명시 허용형 권한(미설정이면 관리자 전용)
  const { user } = useAuth()
  const { isFeatureGranted } = usePermissions()
  const canManage = !!user?.is_admin || isFeatureGranted("datasources.manage")

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

  // Add datasource dialog — session 이 바뀔 때마다 다이얼로그를 다시 마운트해 입력을 초기화한다
  const [addDialog, setAddDialog] = useState<{ open: boolean; session: number; targetSystemId: number | null }>({
    open: false,
    session: 0,
    targetSystemId: null,
  })

  // Delete datasource dialog
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; session: number; target: Datasource | null }>({
    open: false,
    session: 0,
    target: null,
  })

  // 조직/시스템 삭제 확인
  const [topoDelete, setTopoDelete] = useState<TopologyDeleteTarget | null>(null)

  // Organization dialog (create/edit)
  const [orgDialog, setOrgDialog] = useState<OrgDialogState>(INITIAL_ORG_DIALOG)

  // 시스템 생성용 다이얼로그 (편집은 우측 상세 인라인 폼에서 처리)
  const [sysDialog, setSysDialog] = useState<SystemDialogState>(INITIAL_SYSTEM_DIALOG)

  const flatOrgs = useMemo(() => flattenOrgs(topology.organizations), [topology])
  const flatSystems = useMemo(() => {
    const list = flattenSystems(topology.organizations)
    for (const s of topology.unassigned.systems ?? []) {
      list.push({ id: s.id, name: s.name, orgName: "미분류" })
    }
    return list
  }, [topology])

  // 최초 로드만 로딩 표시 — 재조회 때 트리를 언마운트하면 펼침 상태가 초기화된다.
  // 늦게 도착한 이전 응답은 버린다.
  const loadAll = useCallback(async () => {
    const seq = ++loadSeq.current
    if (!loadedOnce.current) setIsLoading(true)
    try {
      const [pf, topo] = await Promise.all([fetchDatasources(), fetchTopology()])
      if (seq !== loadSeq.current) return
      setDatasources(pf)
      setTopology(topo)
      setLoadError(null)
      loadedOnce.current = true
      setHasData(true)
    } catch (err) {
      if (seq !== loadSeq.current) return
      setLoadError(errorMessage(err) ?? "데이터 소스 목록을 불러오지 못했습니다.")
    } finally {
      if (seq === loadSeq.current) setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadAll()
  }, [loadAll])

  // ---- Add / Delete Datasource ----------------------------------------------

  const openAddDialog = (targetSystemId: number | null = null) =>
    setAddDialog((d) => ({ open: true, session: d.session + 1, targetSystemId }))

  const openDeleteDialog = (datasource: Datasource) =>
    setDeleteDialog((d) => ({ open: true, session: d.session + 1, target: datasource }))

  // ---- Save Datasource (우측 상세 인라인 폼) --------------------------------

  const handleSaveDatasource = async (
    id: number,
    name: string,
    origin: string,
    systemId: number | null,
    // null = 연결 설정은 변경 없음(또는 조회 권한 없음) → 저장하지 않는다
    config: Record<string, unknown> | null,
  ): Promise<boolean> => {
    const cur = datasources.find((x) => x.id === id)
    let ok = false
    try {
      const patch: { name?: string; origin?: string } = {}
      if (cur && name.trim() && name.trim() !== cur.name) patch.name = name.trim()
      if (cur && origin !== cur.origin) patch.origin = origin
      if (Object.keys(patch).length > 0) await updateDatasource(id, patch)
      if (cur && systemId !== (cur.system_id ?? null)) {
        await assignDatasourceSystem(id, systemId)
      }
      if (config) await saveDatasourceConfig(id, config)
      toast.success("데이터 소스를 저장했습니다.")
      ok = true
    } catch (err) {
      notifyError(err, "데이터 소스를 저장하지 못했습니다.")
    }
    // 일부 단계만 반영됐어도 화면이 서버 상태와 맞도록 항상 재조회
    await loadAll()
    return ok
  }

  // ---- Organization -------------------------------------------------------

  const openCreateOrg = (parentId: number | null) =>
    setOrgDialog({ open: true, mode: "create", name: "", parentId })
  const openEditOrg = (id: number, name: string, parentId: number | null) =>
    setOrgDialog({ open: true, mode: "edit", id, name, parentId })

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
  const handleSaveSystem = async (id: number, payload: SystemSavePayload) => {
    try {
      await updateSystem(id, payload)
      toast.success("시스템을 저장했습니다.")
      await loadAll()
    } catch (err) {
      notifyError(err, "시스템을 저장하지 못했습니다.")
    }
  }

  // ---- Render -------------------------------------------------------------

  return (
    <>
      <DashboardHeader title="데이터 소스" />
      <div className="flex flex-1 flex-col gap-3 p-4 min-h-0">
        <p className="text-sm text-muted-foreground">
          조직 · 시스템 · 데이터 소스 구조 {canManage ? "(행 우클릭으로 추가/편집)" : "(조회 전용)"}
        </p>
        {loadError && (
          <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <span className="flex-1">목록을 불러오지 못했습니다: {loadError}</span>
            <Button size="sm" variant="outline" className="h-7" onClick={() => void loadAll()}>
              다시 시도
            </Button>
          </div>
        )}

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
            ) : !hasData && loadError ? (
              <p className="py-8 text-center text-sm text-muted-foreground">목록을 불러오지 못했습니다.</p>
            ) : (
              <TopologyTree
                readOnly={!canManage}
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
                onDeleteOrg={(id) => {
                  const o = findOrg(topology.organizations, id)
                  setTopoDelete({ kind: "org", id, name: o?.name ?? String(id) })
                }}
                onDeleteSystem={(id) => {
                  const s = findSystem(topology, id)
                  setTopoDelete({
                    kind: "system",
                    id,
                    name: s?.name ?? String(id),
                    datasourceCount: s?.datasources.length ?? 0,
                  })
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
              canManage={canManage}
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

      <AddDatasourceDialog
        key={`add-${addDialog.session}`}
        open={addDialog.open}
        onOpenChange={(o) => setAddDialog((d) => ({ ...d, open: o }))}
        targetSystemId={addDialog.targetSystemId}
        onReload={loadAll}
      />

      <DeleteDatasourceDialog
        key={`delete-${deleteDialog.session}`}
        open={deleteDialog.open}
        onOpenChange={(o) => setDeleteDialog((d) => ({ ...d, open: o }))}
        target={deleteDialog.target}
        onDeleted={async () => {
          setSelected(null)
          await loadAll()
        }}
      />

      <TopologyDeleteConfirm
        target={topoDelete}
        onClose={() => setTopoDelete(null)}
        onDeleted={(t) => {
          if (selected?.kind === t.kind && selected.id === t.id) setSelected(null)
        }}
        onReload={loadAll}
      />

      <OrgDialog state={orgDialog} setState={setOrgDialog} flatOrgs={flatOrgs} onReload={loadAll} />

      <SystemCreateDialog state={sysDialog} setState={setSysDialog} flatOrgs={flatOrgs} onReload={loadAll} />
    </>
  )
}
