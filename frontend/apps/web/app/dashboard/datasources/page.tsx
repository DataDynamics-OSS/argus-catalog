"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { errorMessage, notifyError, toast } from "@/lib/notify"

import { Plus } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { useConfirm } from "@/components/confirm-provider"
import { DashboardHeader } from "@/components/dashboard-header"
import { ORIGINS } from "@/features/datasets/origin"
import { OriginText } from "@/features/datasets/components/origin-text"
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
import { DATASOURCE_TABS, DetailPanel, type DatasourceTab } from "@/features/datasources/components/detail-panel"
import { useResizableWidth } from "@/hooks/use-resizable-width"
import type { SystemSavePayload } from "@/features/datasources/components/system-detail"
import type { OrgSavePayload } from "@/features/datasources/components/org-detail"
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
  updateOrganization,
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
  const [tab, setTab] = useState<DatasourceTab>("info")
  const askConfirm = useConfirm()
  // 데이터 소스 기본 정보에 저장 안 된 변경이 있는지 — 다른 노드로 옮기거나 화면을 떠날 때 확인한다
  const dirtyRef = useRef(false)
  const onDirtyChange = useCallback((d: boolean) => { dirtyRef.current = d }, [])

  // 데이터 소스·조직·시스템 관리 — 명시 허용형 권한(미설정이면 관리자 전용)
  const { canUseFeature } = usePermissions()
  const canManage = canUseFeature("datasources.manage")
  const canSync = canUseFeature("datasources.sync")

  // 좌측 토폴로지 트리 스크롤 위치 — 추가/편집/삭제 후 재조회 시 위치 복원.
  const treeRef = useRef<HTMLElement>(null)
  const treeScrollRef = useRef(0)

  // 트리/상세 사이 separator 드래그로 트리 폭 조절 — 마지막 폭을 브라우저에 기억한다.
  const { width: asideWidth, startResize } = useResizableWidth("argus.datasources.asideWidth", {
    defaultWidth: 288, // w-72 = 18rem
    min: 200,
    max: 560,
  })
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
      list.push({ id: s.id, name: s.name, orgName: "조직 미지정" })
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

  // 선택 노드 · 탭을 주소에 남긴다 — 새로 고침 · 링크 공유 · 뒤로 가기 뒤에도 같은 곳이 열린다.
  // ?datasource=<datasource_id> · ?system=<id> · ?org=<id> · ?unassigned=1, 탭은 &tab=<catalogs|sync|erd>
  const writeUrl = useCallback((n: SelectedNode | null, t: DatasourceTab) => {
    const q = new URLSearchParams()
    if (n?.kind === "datasource") {
      const ds = datasources.find((d) => d.id === n.id)
      q.set("datasource", ds?.datasource_id || String(n.id))
      if (t !== "info") q.set("tab", t)
    } else if (n?.kind === "system") q.set("system", String(n.id))
    else if (n?.kind === "org") q.set("org", String(n.id))
    else if (n?.kind === "unassigned") q.set("unassigned", "1")
    const qs = q.toString()
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname)
  }, [datasources])

  // 노드를 고른다 — 저장 안 된 변경이 있으면 먼저 확인
  const selectNode = useCallback(async (n: SelectedNode | null) => {
    if (dirtyRef.current) {
      const ok = await askConfirm({
        title: "저장 안 된 변경",
        description: "데이터 소스 기본 정보에 저장하지 않은 변경이 있습니다. 버리고 이동할까요?",
        confirmText: "버리고 이동",
        destructive: true,
      })
      if (!ok) return
      dirtyRef.current = false
    }
    setSelected(n)
  }, [askConfirm])

  // 주소 반영 — 주소에서 처음 선택을 읽은 뒤부터(아래 효과보다 먼저 선언해, 읽기 전에 주소를 지우지 않는다)
  const urlSelectApplied = useRef(false)
  useEffect(() => {
    if (urlSelectApplied.current) writeUrl(selected, tab)
  }, [selected, tab, writeUrl])

  // 저장 안 된 변경이 있으면 창을 닫거나 다른 주소로 갈 때 브라우저가 확인한다
  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (!dirtyRef.current) return
      e.preventDefault()
      e.returnValue = ""
    }
    window.addEventListener("beforeunload", onBeforeUnload)
    return () => window.removeEventListener("beforeunload", onBeforeUnload)
  }, [])

  // 주소로 들어오면 그 노드를 선택한다. ?datasource=<이름|datasource_id|id> 는 알림 상세 '출처' 링크(DEF-054) —
  // 수집 알림의 entity_ref 는 데이터 소스 이름이다. 최초 로드 뒤 한 번만 적용한다.
  useEffect(() => {
    if (urlSelectApplied.current || !hasData) return
    urlSelectApplied.current = true
    const params = new URLSearchParams(window.location.search)
    const wanted = params.get("datasource")
    const tabParam = params.get("tab")
    if (tabParam && (DATASOURCE_TABS as readonly string[]).includes(tabParam)) setTab(tabParam as DatasourceTab)
    if (wanted) {
      const match = datasources.find((d) => d.name === wanted)
        ?? datasources.find((d) => d.datasource_id === wanted || String(d.id) === wanted)
      if (match) setSelected({ kind: "datasource", id: match.id })
      else toast.error(`데이터 소스 '${wanted}' 을(를) 찾을 수 없습니다. 삭제되었거나 이름이 바뀌었을 수 있습니다.`)
      return
    }
    const sys = Number(params.get("system"))
    const org = Number(params.get("org"))
    if (sys && findSystem(topology, sys)) setSelected({ kind: "system", id: sys })
    else if (org && findOrg(topology.organizations, org)) setSelected({ kind: "org", id: org })
    else if (params.get("unassigned")) setSelected({ kind: "unassigned" })
  }, [hasData, datasources, topology])

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
  const handleSaveSystem = async (id: number, payload: SystemSavePayload): Promise<boolean> => {
    try {
      await updateSystem(id, payload)
      toast.success("시스템을 저장했습니다.")
      await loadAll()
      return true
    } catch (err) {
      notifyError(err, "시스템을 저장하지 못했습니다.")
      return false
    }
  }

  // 조직 저장(오른쪽 상세 인라인 폼) — 이름 · 상위 조직(이동) · 설명
  const handleSaveOrg = async (id: number, payload: OrgSavePayload): Promise<boolean> => {
    try {
      await updateOrganization(id, payload)
      toast.success("조직을 저장했습니다.")
      await loadAll()
      return true
    } catch (err) {
      notifyError(err, "조직을 저장하지 못했습니다.")
      return false
    }
  }

  // 미분류 상세에서 여러 개를 한 번에 배정 — 하나씩 보내고 성공 · 실패 수를 알린다
  const bulkAssign = async (
    ids: number[], run: (id: number) => Promise<unknown>, what: string, target: string,
  ) => {
    const results = await Promise.allSettled(ids.map(run))
    const failed = results.filter((r): r is PromiseRejectedResult => r.status === "rejected")
    const ok = ids.length - failed.length
    if (failed.length === 0) toast.success(`${what} ${ok}개를 ${target}에 배정했습니다.`)
    else notifyError(failed[0]?.reason, `${what} ${failed.length}개를 배정하지 못했습니다.${ok ? ` (${ok}개는 배정함)` : ""}`)
    await loadAll()
  }
  const assignDatasources = (ids: number[], systemId: number) =>
    bulkAssign(ids, (id) => assignDatasourceSystem(id, systemId), "데이터 소스",
      `'${findSystem(topology, systemId)?.name ?? systemId}' 시스템`)
  const assignSystems = (ids: number[], orgId: number) =>
    bulkAssign(ids, (id) => updateSystem(id, { org_id: orgId }), "시스템",
      `'${findOrg(topology.organizations, orgId)?.name ?? orgId}' 조직`)

  // 트리 메뉴와 상세의 작업 메뉴가 같이 쓴다
  const editOrg = (id: number) => {
    const o = findOrg(topology.organizations, id)
    if (o) openEditOrg(o.id, o.name, o.parent_id)
  }
  const deleteOrg = (id: number) => {
    const o = findOrg(topology.organizations, id)
    setTopoDelete({ kind: "org", id, name: o?.name ?? String(id) })
  }
  const deleteSystem = (id: number) => {
    const s = findSystem(topology, id)
    setTopoDelete({ kind: "system", id, name: s?.name ?? String(id), datasourceCount: s?.datasources.length ?? 0 })
  }

  // ---- Render -------------------------------------------------------------

  return (
    <>
      <DashboardHeader title="데이터 소스" />
      {/* 높이를 화면(머리줄 4rem 제외)에 묶는다 — 트리와 상세가 각자 안에서 스크롤해야
          상세의 저장 줄(sticky, main 의 아래 여백만큼 -bottom-4)이 보이는 화면 아래에 붙는다 */}
      <div className="flex h-[calc(100svh-4rem)] shrink-0 flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm">
            조직 · 시스템 · 데이터 소스 구조
            {canManage ? " — 행의 ⋯ 메뉴나 우클릭으로 하위 항목을 추가 · 편집 · 삭제합니다." : " (조회 전용)"}
          </p>
          {canManage && (
            <span className="ml-auto flex gap-2">
              <Button size="sm" variant="outline" onClick={() => openCreateOrg(null)}>
                <Plus className="mr-1 h-3.5 w-3.5" />
                조직 추가
              </Button>
              <Button size="sm" onClick={() => openAddDialog(null)}>
                <Plus className="mr-1 h-3.5 w-3.5" />
                데이터 소스 추가
              </Button>
            </span>
          )}
        </div>
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
              <p className="py-8 text-center text-sm text-muted-foreground">불러오는 중…</p>
            ) : !hasData && loadError ? (
              <p className="py-8 text-center text-sm text-muted-foreground">목록을 불러오지 못했습니다.</p>
            ) : (
              <TopologyTree
                readOnly={!canManage}
                topology={topology}
                selected={selected}
                onSelect={(n) => void selectNode(n)}
                onAddRootOrg={() => openCreateOrg(null)}
                onAddDatasource={() => openAddDialog(null)}
                onAddDatasourceToSystem={(sid) => openAddDialog(sid)}
                onAddSubOrg={(parentId) => openCreateOrg(parentId)}
                onAddSystem={(orgId) => openCreateSystem(orgId)}
                onEditOrg={editOrg}
                onDeleteOrg={deleteOrg}
                onDeleteSystem={deleteSystem}
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
          {/* 트리만 테두리를 가진다 — 상세는 화면 바탕에 바로(박스는 한 겹) */}
          <main className="flex-1 overflow-auto px-4 pb-4 pt-1">
            <DetailPanel
              selected={selected}
              datasources={datasources}
              topology={topology}
              flatOrgs={flatOrgs}
              flatSystems={flatSystems}
              onSelect={(n) => void selectNode(n)}
              onSaveOrg={handleSaveOrg}
              onDeleteOrg={deleteOrg}
              onAddSubOrg={(parentId) => openCreateOrg(parentId)}
              onAddSystem={(orgId) => openCreateSystem(orgId)}
              onAddDatasourceToSystem={(sid) => openAddDialog(sid)}
              onDeleteSystem={deleteSystem}
              onDeleteDatasource={(id) => {
                const p = datasources.find((x) => x.id === id)
                if (p) openDeleteDialog(p)
              }}
              onAssignDatasources={assignDatasources}
              onAssignSystems={assignSystems}
              onDirtyChange={onDirtyChange}
              tab={tab}
              onTabChange={setTab}
              onSaveSystem={handleSaveSystem}
              onSaveDatasource={handleSaveDatasource}
              canManage={canManage}
              canSync={canSync}
            />
          </main>
        </div>

        {/* 범례 */}
        <div className="flex h-[28px] items-center gap-4 text-xs">
          <span className="font-medium text-foreground">범례 :</span>
          {ORIGINS.map((o) => <OriginText key={o} origin={o} />)}
        </div>
      </div>

      <AddDatasourceDialog
        key={`add-${addDialog.session}`}
        open={addDialog.open}
        onOpenChange={(o) => setAddDialog((d) => ({ ...d, open: o }))}
        targetSystemId={addDialog.targetSystemId}
        onReload={loadAll}
        onCreated={(id) => { setSelected({ kind: "datasource", id }); setTab("info") }}
        canSync={canSync}
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
