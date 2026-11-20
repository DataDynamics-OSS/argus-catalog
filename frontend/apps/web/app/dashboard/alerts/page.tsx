"use client"

import { datasourceTypeLabel } from "@/features/datasources/datasource-configs"
import { Tip } from "@/components/tip"
import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { DashboardHeader } from "@/components/dashboard-header"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { Card, CardContent } from "@workspace/ui/components/card"
import { Badge } from "@workspace/ui/components/badge"
import { StatusBadge as ToneBadge } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { Switch } from "@workspace/ui/components/switch"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@workspace/ui/components/table"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import {
  Bell, Check, ChevronDown, ChevronRight, Eye, HelpCircle, X, Plus, Pencil, Trash2,
  Database, Tag, GitBranch, Server, Globe, RotateCcw, Eraser,
} from "lucide-react"
import { toast, notifyError } from "@/lib/notify"
import { SeverityBadge } from "@/components/severity-badge"
import { KeyValueTable } from "@/components/key-value-table"
import { authFetch, throwOnError } from "@/features/auth/auth-fetch"
import { usePermissions } from "@/features/permissions/use-permissions"
import { AdminAuditButton } from "@/features/audit/admin-audit-sheet"
import { RuleCreateDialog } from "@/features/alerts/components/rule-create-dialog"
import { notifyAlertsChanged } from "@/features/alerts/events"
import {
  ALERT_TYPE_LABEL, CHANNEL_LABEL, SCOPE_LABEL, SEVERITY_LABEL, STATUS_LABEL, STATUS_TONE, TRIGGER_LABEL, labelOf,
} from "@/features/alerts/labels"
import { type DatasetFocus, type DatasetTab, datasetHref } from "@/features/datasets/links"
import { fetchDataset } from "@/features/datasets/api"
import { datasetOwnerLabels } from "@/features/datasets/permissions"

const BASE = "/api/v1/alerts"
const RULES_FEATURE = "alerts.rules"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Alert = {
  id: number; alert_type: string; severity: string
  source_dataset_id: number | null; source_dataset_name: string | null; source_datasource_type: string | null
  // 데이터셋이 아닌 엔터티 알림 (예: entity_type=mcp_deployment, entity_ref=배포 이름)
  entity_type: string | null; entity_ref: string | null
  affected_dataset_id: number | null; affected_dataset_name: string | null; affected_datasource_type: string | null
  lineage_id: number | null; rule_id: number | null; rule_name: string | null
  change_summary: string; change_detail: string | null
  status: string; resolved_by: string | null; resolved_at: string | null; created_at: string
  can_update: boolean
}

const ENTITY_LABEL: Record<string, string> = { mcp_deployment: "MCP 배포", datasource: "데이터 소스" }
function entityLabel(t: string): string {
  return ENTITY_LABEL[t] ?? t
}

// 데이터셋이 아닌 엔터티 알림의 '출처' 이동 경로 — 종류별로 다르다(DEF-054, 예전엔 모두 MCP 서버 화면).
// 수집 알림(datasource)의 entity_ref 는 데이터 소스 이름. 모르는 종류는 링크 없이 보여 준다.
function entityHref(type: string, ref: string | null): string | null {
  if (type === "datasource") {
    return ref ? `/dashboard/datasources?datasource=${encodeURIComponent(ref)}` : "/dashboard/datasources"
  }
  if (type === "mcp_deployment") return "/dashboard/mcp-servers"
  return null
}

type PaginatedAlerts = { items: Alert[]; total: number; page: number; page_size: number }
type BulkResult = { updated: number[]; skipped: { id: number; reason: string }[] }

type AlertRule = {
  id: number; rule_name: string; description: string | null
  scope_type: string; scope_id: number | null; scope_name: string | null; scope_missing: boolean
  trigger_type: string; trigger_config: string
  severity_override: string | null; channels: string
  notify_owners: string; webhook_url: string | null; webhook_configured: boolean; subscribers: string | null
  is_active: string; created_by: string | null
  created_at: string; updated_at: string; alert_count: number
}

// 상태 → 가능한 다음 상태 (백엔드 _TRANSITIONS 와 동일)
const NEXT_STATUS: Record<string, string[]> = {
  OPEN: ["ACKNOWLEDGED", "RESOLVED", "DISMISSED"],
  ACKNOWLEDGED: ["RESOLVED", "DISMISSED", "OPEN"],
  RESOLVED: ["OPEN"],
  DISMISSED: ["OPEN"],
}
const ACTION_META: Record<string, { label: string; icon: typeof Eye }> = {
  ACKNOWLEDGED: { label: "확인", icon: Eye },
  RESOLVED: { label: "해결", icon: Check },
  DISMISSED: { label: "무시", icon: X },
  OPEN: { label: "다시 열기", icon: RotateCcw },
}

async function api<T>(url: string, init: RequestInit & { json?: unknown } = {}, fallback = "요청을 처리하지 못했습니다."): Promise<T> {
  const { json, ...rest } = init
  const res = await authFetch(url, json === undefined ? rest : {
    ...rest, headers: { "Content-Type": "application/json" }, body: JSON.stringify(json),
  })
  if (!res.ok) await throwOnError(res, fallback)
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}


/** 규칙 관리 — admin 이거나 alerts.rules 기능 권한이 명시적으로 허용된 역할. */
function useCanManageRules(): boolean {
  const { canUseFeature } = usePermissions()
  return canUseFeature(RULES_FEATURE)
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AlertsPage() {
  const canManage = useCanManageRules()
  const searchParams = useSearchParams()
  const focusId = searchParams.get("alert")
  const [tab, setTab] = useState("alerts")
  // 헤더 벨에서 ?alert=<id> 로 들어오면 알림 탭으로 전환 (비활성 탭은 언마운트되므로 페이지에서 처리)
  useEffect(() => { if (focusId) setTab("alerts") }, [focusId])
  const [ruleDialogOpen, setRuleDialogOpen] = useState(false)
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null)
  const [rulesVersion, setRulesVersion] = useState(0)

  return (
    <>
      <DashboardHeader title="알림" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <Tabs value={tab} onValueChange={setTab}>
          <div className="flex items-center justify-between">
            <TabsList>
              <TabsTrigger value="alerts">알림</TabsTrigger>
              <TabsTrigger value="rules">규칙</TabsTrigger>
            </TabsList>
            {tab === "rules" && canManage && (
              <Button size="sm" onClick={() => { setEditingRule(null); setRuleDialogOpen(true) }} className="gap-1.5">
                <Plus className="h-3.5 w-3.5" />
                규칙 추가
              </Button>
            )}
          </div>

          <TabsContent value="alerts" className="mt-4">
            <AlertsTab focusId={focusId} />
          </TabsContent>
          <TabsContent value="rules" className="mt-4">
            <RulesTab
              canManage={canManage}
              version={rulesVersion}
              onAddRule={() => { setEditingRule(null); setRuleDialogOpen(true) }}
              onEditRule={(rule) => { setEditingRule(rule); setRuleDialogOpen(true) }}
            />
          </TabsContent>
        </Tabs>
      </div>

      {canManage && (
        <RuleCreateDialog
          open={ruleDialogOpen}
          onOpenChange={(o) => { setRuleDialogOpen(o); if (!o) setEditingRule(null) }}
          onCreated={() => {
            toast.success(editingRule ? "규칙을 수정했습니다." : "규칙을 추가했습니다.")
            setTab("rules")
            setRulesVersion(v => v + 1)
          }}
          editRule={editingRule}
        />
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// Alerts Tab
// ---------------------------------------------------------------------------

function AlertsTab({ focusId }: { focusId: string | null }) {
  // 오래된 알림 정리는 명시 허용형 alerts.purge(미설정이면 관리자만, TODO-122)
  const { canUseFeature } = usePermissions()
  const canPurge = canUseFeature("alerts.purge")
  // 알림 처리(확인 · 해결 · 무시 · 다시 열기)는 기능 권한 alerts.ack(기본 허용형, TODO-124) — 없으면 처리 단추 · 선택을 숨긴다
  const canAck = canUseFeature("alerts.ack")
  const gate = useCallback((a: Alert): Alert => (canAck ? a : { ...a, can_update: false }), [canAck])
  const router = useRouter()
  const [data, setData] = useState<PaginatedAlerts | null>(null)
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState("OPEN")
  const [severityFilter, setSeverityFilter] = useState("all")
  const [typeFilter, setTypeFilter] = useState("all")
  const [page, setPage] = useState(1)
  const [selectedAlert, setSelectedAlert] = useState<Alert | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [purgeOpen, setPurgeOpen] = useState(false)

  const fetchAlerts = useCallback(async () => {
    setLoading(true)
    const params = new URLSearchParams()
    if (statusFilter !== "all") params.set("status", statusFilter)
    if (severityFilter !== "all") params.set("severity", severityFilter)
    if (typeFilter !== "all") params.set("alert_type", typeFilter)
    params.set("page", String(page))
    params.set("page_size", "20")
    try {
      const d = await api<PaginatedAlerts>(`${BASE}?${params}`, {}, "알림 목록을 불러오지 못했습니다.")
      setData({ ...d, items: d.items.map(gate) })
      setSelected(new Set())
    } catch (e) {
      notifyError(e, "알림 목록을 불러오지 못했습니다.")
    } finally { setLoading(false) }
  }, [statusFilter, severityFilter, typeFilter, page, gate])

  useEffect(() => { fetchAlerts() }, [fetchAlerts])

  // 헤더 벨에서 ?alert=<id> 로 들어오면 그 알림 상세를 연다
  useEffect(() => {
    if (!focusId) return
    api<Alert>(`${BASE}/${focusId}`, {}, "알림을 불러오지 못했습니다.")
      .then(a => setSelectedAlert(gate(a)))
      .catch(e => notifyError(e, "알림을 불러오지 못했습니다."))
      .finally(() => router.replace("/dashboard/alerts"))
  }, [focusId, router, gate])

  const updateStatus = async (alert: Alert, status: string) => {
    setBusy(true)
    try {
      const updated = await api<Alert>(`${BASE}/${alert.id}/status`, { method: "PUT", json: { status } }, "알림 상태를 바꾸지 못했습니다.")
      toast.success(`알림을 '${STATUS_LABEL[status]}' 상태로 바꿨습니다.`)
      if (selectedAlert?.id === alert.id) setSelectedAlert(gate(updated))
      fetchAlerts()
      notifyAlertsChanged()
    } catch (e) {
      notifyError(e, "상태를 바꾸지 못했습니다.")
    } finally { setBusy(false) }
  }

  const bulkUpdate = async (status: string) => {
    if (selected.size === 0) return
    setBusy(true)
    try {
      const res = await api<BulkResult>(`${BASE}/bulk-status`, {
        method: "POST", json: { ids: [...selected], status },
      }, "알림 상태를 일괄 변경하지 못했습니다.")
      if (res.updated.length) toast.success(`${res.updated.length}건을 '${STATUS_LABEL[status]}' 상태로 바꿨습니다.`)
      if (res.skipped.length) {
        const first = res.skipped[0]!
        toast.warning(`${res.skipped.length}건은 건너뛰었습니다 — #${first.id}: ${first.reason}`)
      }
      fetchAlerts()
      if (res.updated.length) notifyAlertsChanged()
    } catch (e) {
      notifyError(e, "알림 상태를 일괄 변경하지 못했습니다.")
    } finally { setBusy(false) }
  }

  const purge = async () => {
    setBusy(true)
    try {
      const res = await api<{ deleted: number }>(`${BASE}/closed?older_than_days=90`, { method: "DELETE" }, "오래된 알림을 정리하지 못했습니다.")
      toast.success(res.deleted ? `오래된 알림 ${res.deleted}건을 정리했습니다.` : "정리할 알림이 없습니다.")
      setPurgeOpen(false)
      fetchAlerts()
      if (res.deleted) notifyAlertsChanged()
    } catch (e) {
      notifyError(e, "오래된 알림을 정리하지 못했습니다.")
    } finally { setBusy(false) }
  }

  const items = data?.items ?? []
  const selectable = items.filter(a => a.can_update)
  const allSelected = selectable.length > 0 && selectable.every(a => selected.has(a.id))
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(selectable.map(a => a.id)))
  const toggleOne = (id: number) => setSelected(prev => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  })
  // 선택 항목 모두에 가능한 공통 전이만 일괄 버튼으로
  const selectedItems = items.filter(a => selected.has(a.id))
  const bulkActions = ["ACKNOWLEDGED", "RESOLVED", "DISMISSED", "OPEN"].filter(
    st => selectedItems.length > 0 && selectedItems.every(a => NEXT_STATUS[a.status]?.includes(st)),
  )

  const totalPages = data ? Math.ceil(data.total / data.page_size) : 0

  return (
    <>
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <Select value={statusFilter} onValueChange={v => { setStatusFilter(v); setPage(1) }}>
          <SelectTrigger className="w-36 h-9"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">전체 상태</SelectItem>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={severityFilter} onValueChange={v => { setSeverityFilter(v); setPage(1) }}>
          <SelectTrigger className="w-36 h-9"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">전체 심각도</SelectItem>
            {Object.entries(SEVERITY_LABEL).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={typeFilter} onValueChange={v => { setTypeFilter(v); setPage(1) }}>
          <SelectTrigger className="w-44 h-9"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">전체 유형</SelectItem>
            {Object.entries(ALERT_TYPE_LABEL).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}
          </SelectContent>
        </Select>
        {selected.size > 0 && (
          <div className="flex items-center gap-1.5">
            <span className="text-sm">{selected.size}건 선택</span>
            {bulkActions.map(st => (
              <Button key={st} variant="outline" size="sm" className="h-8" disabled={busy} onClick={() => bulkUpdate(st)}>
                {ACTION_META[st]!.label}
              </Button>
            ))}
            {bulkActions.length === 0 && (
              <span className="text-sm text-muted-foreground">(선택한 알림에 공통으로 가능한 작업이 없습니다)</span>
            )}
          </div>
        )}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-sm">{data ? `${data.total}건` : ""}</span>
          {canPurge && (
            <Button variant="ghost" size="sm" className="h-8 gap-1.5" onClick={() => setPurgeOpen(true)}
              title="해결·무시된 지 90일이 지난 알림을 삭제합니다">
              <Eraser className="h-3.5 w-3.5" /> 오래된 알림 정리
            </Button>
          )}
          {/* 정리는 되돌릴 수 없어 감사 로그(운영 작업)에 남는다 — 감사 로그 조회 권한(audit.view)이 있으면 이력 단추 */}
          {canPurge && <AdminAuditButton category="operations" />}
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <Checkbox checked={allSelected} disabled={selectable.length === 0} onCheckedChange={toggleAll}
                    aria-label="전체 선택" />
                </TableHead>
                <TableHead className="w-20">심각도</TableHead>
                <TableHead>변경</TableHead>
                <TableHead className="w-44">출처</TableHead>
                <TableHead className="w-44">영향</TableHead>
                <TableHead className="w-28">상태</TableHead>
                <TableHead className="w-28">시간</TableHead>
                <TableHead className="w-28">작업</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow><TableCell colSpan={8} className="text-center py-8 text-muted-foreground">불러오는 중…</TableCell></TableRow>
              ) : items.length === 0 ? (
                <TableRow><TableCell colSpan={8} className="text-center py-12">
                  <Bell className="h-8 w-8 text-muted-foreground/40 mx-auto mb-2" />
                  <p className="text-sm text-muted-foreground">알림이 없습니다</p>
                </TableCell></TableRow>
              ) : items.map(alert => (
                <TableRow key={alert.id} className="cursor-pointer hover:bg-muted/50"
                  onClick={() => setSelectedAlert(alert)}>
                  <TableCell onClick={e => e.stopPropagation()}>
                    <Checkbox checked={selected.has(alert.id)} disabled={!alert.can_update}
                      onCheckedChange={() => toggleOne(alert.id)} aria-label={`알림 ${alert.id} 선택`} />
                  </TableCell>
                  <TableCell><SeverityBadge severity={alert.severity} /></TableCell>
                  <TableCell>
                    <p className="text-sm truncate max-w-sm">{alert.change_summary}</p>
                    <p className="text-sm mt-0.5">
                      {labelOf(ALERT_TYPE_LABEL, alert.alert_type)}
                      {alert.rule_name && ` · 규칙: ${alert.rule_name}`}
                    </p>
                  </TableCell>
                  <TableCell>
                    <div className="text-sm">
                      <span >{alert.entity_type ? entityLabel(alert.entity_type) : alert.source_datasource_type}</span>
                      <p className="font-medium truncate">{alert.entity_ref ?? alert.source_dataset_name ?? "(삭제된 데이터셋)"}</p>
                    </div>
                  </TableCell>
                  <TableCell>
                    {alert.affected_dataset_name ? (
                      <div className="text-sm">
                        <span >{datasourceTypeLabel(alert.affected_datasource_type)}</span>
                        <p className="font-medium truncate">{alert.affected_dataset_name}</p>
                      </div>
                    ) : <span className="text-sm text-muted-foreground">-</span>}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={alert.status} />
                    {alert.resolved_by && (
                      <p className="text-sm mt-0.5 truncate" title={alert.resolved_at ? new Date(alert.resolved_at).toLocaleString() : ""}>
                        {alert.resolved_by}
                      </p>
                    )}
                  </TableCell>
                  <TableCell><span className="text-sm">{formatTimeAgo(alert.created_at)}</span></TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                      {alert.can_update && (NEXT_STATUS[alert.status] ?? []).map(st => {
                        const meta = ACTION_META[st]!
                        const Icon = meta.icon
                        return (
                          <Tip key={st} label={meta.label}><Button variant="ghost" size="icon" className="h-7 w-7" aria-label={meta.label}
                            disabled={busy} onClick={() => updateStatus(alert, st)}>
                            <Icon className="h-3.5 w-3.5" />
                          </Button></Tip>
                        )
                      })}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 mt-4">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>이전</Button>
          <span className="text-sm">{page} / {totalPages} 페이지</span>
          <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>다음</Button>
        </div>
      )}

      <AlertDetailDialog
        alert={selectedAlert}
        busy={busy}
        onClose={() => setSelectedAlert(null)}
        onChangeStatus={updateStatus}
      />

      <ConfirmDialog
        open={purgeOpen}
        onOpenChange={setPurgeOpen}
        title="오래된 알림 정리"
        desc="해결·무시 처리된 지 90일이 지난 알림과 전달 기록을 삭제합니다. 되돌릴 수 없습니다."
        confirmText="정리"
        destructive
        isLoading={busy}
        handleConfirm={purge}
      />
    </>
  )
}

// ---------------------------------------------------------------------------
// Alert detail
// ---------------------------------------------------------------------------

// 출처 화면의 메뉴 — 그 메뉴를 막은 역할에게는 링크 대신 글자만 보인다(DEF-158)
const ENTITY_MENU: Record<string, string> = { datasource: "datasources", mcp_deployment: "mcp-servers" }

function EntityLink({ type, entityRef }: { type: string; entityRef: string | null }) {
  const { isMenuAllowed } = usePermissions()
  const href = isMenuAllowed(ENTITY_MENU[type] ?? null) ? entityHref(type, entityRef) : null
  const label = `${entityLabel(type)} · ${entityRef ?? ""}`
  return href ? <Link href={href} className="text-primary hover:underline">{label}</Link> : <span>{label}</span>
}

function AlertDetailDialog({ alert, busy, onClose, onChangeStatus }: {
  alert: Alert | null
  busy: boolean
  onClose: () => void
  onChangeStatus: (alert: Alert, status: string) => void
}) {
  // 조치 대상 데이터셋 — 영향받은 데이터셋, 없으면 출처. 데이터셋 알림(entity_type 없음)이고 데이터셋이 남아 있을 때만
  const target = alert && !alert.entity_type
    ? (alert.affected_dataset_id && alert.affected_dataset_name
        ? { id: alert.affected_dataset_id, name: alert.affected_dataset_name }
        : alert.source_dataset_id && alert.source_dataset_name
          ? { id: alert.source_dataset_id, name: alert.source_dataset_name } : null)
    : null
  // '변경 요청 만들기' 는 변경 관리 메뉴와 changes.create 가 모두 있을 때만 — 데이터셋 상세의 '변경 요청 만들기' 와 같은 기준(DEF-064, DEF-079)
  const { canUseFeature, isMenuAllowed } = usePermissions()
  const canCreateChange = isMenuAllowed("changes") && canUseFeature("changes.create")
  const targetId = target?.id ?? null
  // 누구에게 연락할지 — 조치 대상 데이터셋의 소유자(TODO-077)
  const [owners, setOwners] = useState<string[] | null>(null)
  useEffect(() => {
    setOwners(null)
    if (!targetId) return
    let alive = true
    fetchDataset(targetId)
      // 소유자 목록 + 생성자 — 알림 수신 · 처리 권한과 같은 소유자 기준(DEF-138)
      .then((d) => { if (alive) setOwners(datasetOwnerLabels(d)) })
      .catch(() => { if (alive) setOwners([]) })
    return () => { alive = false }
  }, [targetId])

  return (
    <Dialog open={!!alert} onOpenChange={o => { if (!o) onClose() }}>
      <DialogContent className="max-w-xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            알림 상세 {alert && <SeverityBadge severity={alert.severity} />}
            {alert && <StatusBadge status={alert.status} />}
          </DialogTitle>
        </DialogHeader>
        {alert && (
          <div className="space-y-3">
            {/* 키-값 표 — 값이 없는 항목은 KeyValueTable 이 건너뛴다 */}
            <KeyValueTable items={[
              ["유형", labelOf(ALERT_TYPE_LABEL, alert.alert_type)],
              ["내용", <span key="s" className="font-medium">{alert.change_summary}</span>],
              ["규칙", alert.rule_name ?? (alert.rule_id ? `#${alert.rule_id}` : "(삭제된 규칙)")],
              ["출처", alert.entity_type
                ? <EntityLink key="src" type={alert.entity_type} entityRef={alert.entity_ref} />
                : <DatasetLink key="src" id={alert.source_dataset_id} name={alert.source_dataset_name} type={alert.source_datasource_type} tab={alertSourceTab(alert.alert_type)} focus={alertSourceFocus(alert.alert_type, alert.change_detail)} />],
              ["영향", alert.affected_dataset_id
                ? <DatasetLink key="aff" id={alert.affected_dataset_id} name={alert.affected_dataset_name} type={alert.affected_datasource_type} tab={alertAffectedTab(alert.alert_type)} />
                : <span key="aff" className="text-muted-foreground">-</span>],
              ["소유자", target
                ? (owners === null ? "불러오는 중…" : owners.length > 0 ? owners.join(", ") : "지정되지 않음")
                : null],
              ["생성", new Date(alert.created_at).toLocaleString()],
              [`${STATUS_LABEL[alert.status] ?? alert.status} 처리`, alert.resolved_by
                ? `${alert.resolved_by}${alert.resolved_at ? ` · ${new Date(alert.resolved_at).toLocaleString()}` : ""}`
                : null],
            ]} />
            {alert.change_detail && (
              <section className="space-y-1.5 border-t pt-3">
                <h3 className="text-sm font-medium">상세</h3>
                <div className="max-h-64 overflow-y-auto">
                  <AlertDetailBody alertType={alert.alert_type} raw={alert.change_detail} />
                </div>
              </section>
            )}
            {!alert.can_update && (
              <p className="text-xs text-muted-foreground">상태 변경은 규칙 관리자 또는 관련 데이터셋의 생성자·소유자만 할 수 있습니다.</p>
            )}
          </div>
        )}
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={onClose}>닫기</Button>
          {/* 알림을 보고 스키마를 맞춰야 하면 바로 변경 요청으로 — 대상 데이터셋이 잠긴 상태로 열린다 */}
          {target && canCreateChange && (
            <Button variant="outline" asChild>
              <Link href={`/dashboard/changes/new?dataset_id=${target.id}`}>변경 요청 만들기</Link>
            </Button>
          )}
          {alert?.can_update && (NEXT_STATUS[alert.status] ?? []).map(st => (
            <Button key={st} variant={st === "RESOLVED" ? "default" : "outline"} disabled={busy}
              onClick={() => onChangeStatus(alert, st)}>
              {ACTION_META[st]!.label}
            </Button>
          ))}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// 알림 유형에 맞는 데이터셋 상세 탭(TODO-072) — 스키마 변경은 출처의 변경 이력·영향 데이터셋의 리니지, 품질 실패는 품질 탭.
function alertSourceTab(alertType: string): DatasetTab | undefined {
  if (alertType === "SCHEMA_CHANGE") return "history"
  if (alertType === "QUALITY_FAILED") return "quality"
  if (alertType === "AI_READY_UNMET") return "ai-ready"
  if (REPORT_ALERTS.has(alertType)) return "comments"
  return undefined
}

const REPORT_ALERTS = new Set(["REPORT_RECEIVED", "REPORT_DUE", "REPORT_CLOSED"])

/** 신고 알림은 상세의 comment_id 로 그 댓글을 가리킨다(TODO-151). */
function alertSourceFocus(alertType: string, detail: string | null): DatasetFocus | undefined {
  if (!REPORT_ALERTS.has(alertType) || !detail) return undefined
  try {
    const id = Number((JSON.parse(detail) as { comment_id?: unknown })?.comment_id)
    return Number.isInteger(id) && id > 0 ? { comment: id } : undefined
  } catch {
    return undefined
  }
}

function alertAffectedTab(alertType: string): DatasetTab | undefined {
  if (alertType === "SCHEMA_CHANGE") return "lineage"
  if (alertType === "QUALITY_FAILED") return "quality"
  return undefined
}

function DatasetLink({ id, name, type, tab, focus }: {
  id: number | null; name: string | null; type: string | null; tab?: DatasetTab; focus?: DatasetFocus
}) {
  // 데이터셋 메뉴를 막은 역할에게는 링크 대신 글자만(DEF-158)
  const { isMenuAllowed } = usePermissions()
  if (!id) return <span>-</span>
  if (!name) return <span >(삭제된 데이터셋)</span>
  if (!isMenuAllowed("datasets")) return <span>{type ? `${datasourceTypeLabel(type)}.` : ""}{name}</span>
  return (
    <Link href={datasetHref(id, tab, focus)} className="text-primary hover:underline">
      {type ? `${datasourceTypeLabel(type)}.` : ""}{name}
    </Link>
  )
}

/** 알림 유형별 상세 — 스키마 변경(배열) / 품질 실패 / MCP finding. 모르는 형태는 원문. */
function AlertDetailBody({ alertType, raw }: { alertType: string; raw: string }) {
  let parsed: unknown
  try { parsed = JSON.parse(raw) } catch { parsed = undefined }
  const box = "text-sm bg-muted/50 rounded px-3 py-2"

  if (Array.isArray(parsed)) {
    return (
      <div className="space-y-1.5">
        {parsed.map((d: Record<string, unknown>, i: number) => {
          const col = String(d.changed_column ?? d.field ?? "")
          const type = String(d.change_type ?? d.type ?? "")
          return (
            <div key={i} className={box}>
              <span className="font-medium break-all">{col}</span>
              <span className="ml-1">({CHANGE_LABEL[type] ?? type})</span>
              {d.mapped_to ? <span className="ml-1">→ 매핑: {String(d.mapped_to)}</span> : null}
            </div>
          )
        })}
      </div>
    )
  }
  if (parsed && typeof parsed === "object") {
    const obj = parsed as Record<string, unknown>
    if (alertType === "QUALITY_FAILED" && Array.isArray(obj.failed_rules)) {
      return (
        <div className="space-y-1.5">
          {typeof obj.score === "number" && <KeyValueTable items={[["품질 점수", <b key="sc">{obj.score.toFixed(1)}%</b>]]} />}
          {(obj.failed_rules as Record<string, unknown>[]).map((f, i) => (
            <div key={i} className={box}>
              <span className="font-medium">{String(f.rule_name ?? "?")}</span>
              {f.severity ? <span className="ml-1.5"><SeverityBadge severity={String(f.severity)} /></span> : null}
              {f.check_type ? <span className="ml-1.5">{String(f.check_type)}</span> : null}
              {f.actual !== undefined && f.actual !== null && <span className="ml-1.5">실제값: {String(f.actual)}</span>}
              {f.detail ? <p className="mt-0.5 break-words">{String(f.detail)}</p> : null}
            </div>
          ))}
        </div>
      )
    }
    if (alertType.startsWith("MCP_")) {
      const rows: [string, unknown][] = [
        ["배포", obj.deployment], ["서버", obj.server], ["환경", obj.environment],
        ["종류", obj.kind], ["finding 심각도", obj.severity],
      ]
      return (
        <div className="space-y-2">
          <KeyValueTable items={rows.map(([k, v]) => [k, v === undefined || v === null || v === "" ? null : String(v)])} />
          {obj.detail !== undefined && obj.detail !== null && (
            <pre className={box + " whitespace-pre-wrap break-all"}>
              {typeof obj.detail === "string" ? obj.detail : JSON.stringify(obj.detail, null, 2)}
            </pre>
          )}
        </div>
      )
    }
  }
  return <pre className="text-sm whitespace-pre-wrap break-all">{raw}</pre>
}

const CHANGE_LABEL: Record<string, string> = { DROP: "삭제", MODIFY: "변경", ADD: "추가" }

// ---------------------------------------------------------------------------
// Rules Tab
// ---------------------------------------------------------------------------

function RulesTab({ canManage, version, onAddRule, onEditRule }: {
  canManage: boolean
  version: number
  onAddRule: () => void
  onEditRule: (rule: AlertRule) => void
}) {
  const [rules, setRules] = useState<AlertRule[]>([])
  const [loading, setLoading] = useState(true)
  const [helpOpen, setHelpOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<AlertRule | null>(null)
  const [busy, setBusy] = useState(false)

  const fetchRules = useCallback(async () => {
    setLoading(true)
    try {
      setRules(await api<AlertRule[]>(`${BASE}/rules`, {}, "규칙 목록을 불러오지 못했습니다."))
    } catch (e) {
      notifyError(e, "규칙 목록을 불러오지 못했습니다.")
    } finally { setLoading(false) }
  }, [])

  // version 이 바뀌면(규칙 저장 후) 다시 불러온다
  useEffect(() => { fetchRules() }, [fetchRules, version])

  const toggleActive = async (rule: AlertRule) => {
    const active = rule.is_active !== "true"
    try {
      await api(`${BASE}/rules/${rule.id}`, { method: "PUT", json: { is_active: active } }, "규칙 상태를 바꾸지 못했습니다.")
      toast.success(active ? `'${rule.rule_name}' 규칙을 활성화했습니다.` : `'${rule.rule_name}' 규칙을 비활성화했습니다.`)
      fetchRules()
    } catch (e) {
      notifyError(e, "규칙 상태를 바꾸지 못했습니다.")
    }
  }

  const handleDeleteClick = (rule: AlertRule) => {
    if (rule.is_active === "true") {
      toast.error("활성 상태인 규칙은 삭제할 수 없습니다. 먼저 비활성화해주세요.")
      return
    }
    setDeleteTarget(rule)
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    setBusy(true)
    try {
      await api(`${BASE}/rules/${deleteTarget.id}`, { method: "DELETE" }, "규칙을 삭제하지 못했습니다.")
      toast.success(`'${deleteTarget.rule_name}' 규칙을 삭제했습니다.`)
      setDeleteTarget(null)
      fetchRules()
    } catch (e) {
      notifyError(e, "규칙을 삭제하지 못했습니다.")
    } finally { setBusy(false) }
  }

  if (loading && rules.length === 0) {
    return <p className="text-sm text-muted-foreground text-center py-8">규칙을 불러오는 중…</p>
  }

  return (
    <div className="space-y-3">
      {!canManage && (
        <p className="text-sm">
          규칙 추가·수정·삭제는 관리자 또는 &lsquo;알림 규칙 관리&rsquo; 권한이 있는 역할만 할 수 있습니다.
        </p>
      )}

      {rules.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12 gap-4">
            <Bell className="h-10 w-10 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">아직 정의된 알림 규칙이 없습니다.</p>
            {canManage && (
              <Button size="sm" onClick={onAddRule} className="gap-1.5">
                <Plus className="h-3.5 w-3.5" /> 첫 규칙 만들기
              </Button>
            )}
          </CardContent>
        </Card>
      ) : rules.map(rule => (
        <Card key={rule.id} className={rule.is_active === "false" ? "opacity-60" : ""}>
          <CardContent className="p-4">
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0">
                {/* Header */}
                <div className="flex items-center gap-2 mb-2">
                  {rule.severity_override ? (
                    <SeverityBadge severity={rule.severity_override} />
                  ) : (
                    <Badge variant="outline">자동</Badge>
                  )}
                  <span className="font-medium text-sm">{rule.rule_name}</span>
                  {rule.is_active === "false" && <Badge variant="secondary">비활성</Badge>}
                </div>

                {/* 적용 범위 */}
                <div className="flex items-center gap-2 mb-2">
                  <ScopeIcon type={rule.scope_type} />
                  <span className="text-sm">
                    {labelOf(SCOPE_LABEL, rule.scope_type)}:{" "}
                    {rule.scope_type === "ALL" ? "전체" : rule.scope_missing
                      ? <span className="text-destructive">(삭제된 대상 — 이 규칙은 더 이상 발동하지 않습니다)</span>
                      : rule.scope_name}
                  </span>
                </div>

                {/* 트리거 + 채널 */}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                  <span>트리거: <span className="text-foreground">{labelOf(TRIGGER_LABEL, rule.trigger_type)}</span></span>
                  <span>채널: <span className="text-foreground">
                    {rule.channels.split(",").map(c => labelOf(CHANNEL_LABEL, c.trim())).join(", ")}
                  </span></span>
                  {rule.subscribers && (
                    <span>구독자: <span className="text-foreground">{rule.subscribers.split(",").length}명</span></span>
                  )}
                  <span>소유자 알림: <span className="text-foreground">{rule.notify_owners === "true" ? "켜짐" : "꺼짐"}</span></span>
                  <span>알림 수: <span className="text-foreground">{rule.alert_count}</span></span>
                  {rule.created_by && <span>생성: <span className="text-foreground">{rule.created_by}</span></span>}
                </div>

                {rule.description && (
                  <p className="text-sm mt-2">{rule.description}</p>
                )}
              </div>

              {/* Actions */}
              {canManage && (
                <div className="flex items-center gap-2 ml-4">
                  <Switch
                    checked={rule.is_active === "true"}
                    onCheckedChange={() => toggleActive(rule)}
                    aria-label="활성"
                  />
                  <Tip label="수정"><Button variant="ghost" size="icon" className="h-7 w-7" aria-label="수정" onClick={() => onEditRule(rule)}>
                    <Pencil className="h-3.5 w-3.5 text-muted-foreground" />
                  </Button></Tip>
                  <Tip label="삭제"><Button variant="ghost" size="icon" className="h-7 w-7" aria-label="삭제" onClick={() => handleDeleteClick(rule)}>
                    <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
                  </Button></Tip>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      ))}

      {/* 도움말 — 규칙이 언제 평가·적용되는지 (접이식) */}
      <Card>
        <CardContent className="p-0">
          <button
            type="button"
            onClick={() => setHelpOpen((o) => !o)}
            className="flex w-full items-center gap-2 px-4 py-3 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
          >
            <HelpCircle className="h-4 w-4" />
            도움말 — 규칙은 언제 적용되나요?
            {helpOpen ? <ChevronDown className="h-4 w-4 ml-auto" /> : <ChevronRight className="h-4 w-4 ml-auto" />}
          </button>
          {helpOpen && (
            <div className="border-t px-5 py-4 text-sm space-y-4">
              <div>
                <p className="font-semibold text-foreground mb-1">평가 시점 — 이벤트가 기록되는 순간</p>
                <p className="mb-1">규칙은 주기적으로 검사하지 않고, 아래 이벤트가 생길 때 즉시 평가됩니다:</p>
                <ul className="list-disc pl-5 space-y-0.5">
                  <li><b>스키마 변경</b> — 메타데이터 동기화에서 컬럼 추가/변경/삭제가 감지되거나, 데이터셋 상세 &gt; 스키마 탭에서 직접 수정·저장한 경우</li>
                  <li><b>품질 진단 실패</b> — 정밀 진단(품질 탭 · 품질 진단 작업) 결과에 실패한 규칙이 있는 경우</li>
                  <li><b>MCP 배포 finding</b> — 정책 위반·드리프트·보안 경고가 새로 열리거나 다시 열린 경우</li>
                </ul>
                <p className="mt-1">
                  규칙을 새로 만들거나 수정해도 <b>과거 이벤트에 소급 적용되지는 않으며</b>, 저장 이후 발생하는 이벤트부터 평가됩니다.
                </p>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">매칭 순서</p>
                <ul className="list-disc pl-5 space-y-0.5">
                  <li><b>활성 규칙만</b> 평가합니다 (비활성 규칙은 건너뜀)</li>
                  <li>① <b>범위</b> 매칭 — 전체 / 데이터 소스 / 데이터셋 / 태그 / 리니지 중 대상이 속하는지 확인
                    (MCP 트리거는 전체·데이터 소스만). 범위 대상이 삭제된 규칙은 발동하지 않습니다.</li>
                  <li>② <b>트리거</b> 평가 — 모든 변경 / 지정한 변경 유형(추가·변경·삭제) / 감시 컬럼 / 컬럼 매핑 변경 / 품질 실패 / MCP finding</li>
                  <li>③ <b>심각도</b> 결정 — 자동 판정(컬럼 삭제=치명, 타입 변경=경고 등) 또는 규칙의 강제 지정 값</li>
                </ul>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">알림 전달</p>
                <ul className="list-disc pl-5 space-y-0.5">
                  <li><b>앱 내</b>: 알림 탭 목록과 상단 헤더의 벨 아이콘에 표시되고, 구독자와 (옵션을 켠 경우) 데이터셋 소유자를 수신자로 기록합니다</li>
                  <li><b>Webhook</b>: 규칙에 지정한 URL 로 알림 1건당 한 번 POST 합니다. Slack Incoming Webhook 과 호환되는 <code>text</code> 필드를 포함하며, 전송 결과(성공·실패)가 기록됩니다</li>
                  <li>이메일 발송은 아직 지원하지 않습니다</li>
                </ul>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">알림 처리</p>
                <p>
                  확인·해결·무시·다시 열기는 규칙 관리자 또는 관련 데이터셋(출처·영향)의 생성자·소유자가 할 수 있으며,
                  처리자와 시각이 기록됩니다. 여러 건을 선택해 한 번에 처리할 수도 있습니다.
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={o => { if (!o) setDeleteTarget(null) }}
        title="규칙 삭제"
        desc={`"${deleteTarget?.rule_name ?? ""}" 규칙을 삭제합니다. 이미 발생한 알림은 남지만 규칙 연결은 끊어집니다. 되돌릴 수 없습니다.`}
        confirmText="삭제"
        destructive
        isLoading={busy}
        handleConfirm={confirmDelete}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function ScopeIcon({ type }: { type: string }) {
  const cls = "h-3.5 w-3.5 text-muted-foreground"
  switch (type) {
    case "DATASET": return <Database className={cls} />
    case "TAG": return <Tag className={cls} />
    case "LINEAGE": return <GitBranch className={cls} />
    case "DATASOURCE": return <Server className={cls} />
    default: return <Globe className={cls} />
  }
}

function StatusBadge({ status }: { status: string }) {
  return <ToneBadge tone={STATUS_TONE[status] ?? "neutral"} label={labelOf(STATUS_LABEL, status)} />
}

function formatTimeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "방금 전"
  if (mins < 60) return `${mins}분 전`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}시간 전`
  return `${Math.floor(hours / 24)}일 전`
}
