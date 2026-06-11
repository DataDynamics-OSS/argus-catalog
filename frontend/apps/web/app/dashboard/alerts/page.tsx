"use client"

import { useCallback, useEffect, useState } from "react"
import { DashboardHeader } from "@/components/dashboard-header"
import { Card, CardContent } from "@workspace/ui/components/card"
import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { Switch } from "@workspace/ui/components/switch"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@workspace/ui/components/table"
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@workspace/ui/components/alert-dialog"
import { Bell, Check, ChevronDown, ChevronRight, Eye, HelpCircle, X, Plus, Pencil, Trash2, Database, Tag, GitBranch, Server, Globe } from "lucide-react"
import { toast } from "sonner"
import { authFetch } from "@/features/auth/auth-fetch"
import { RuleCreateDialog } from "@/features/alerts/components/rule-create-dialog"

const BASE = "/api/v1/alerts"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Alert = {
  id: number; alert_type: string; severity: string
  source_dataset_id: number; source_dataset_name: string | null; source_datasource_type: string | null
  affected_dataset_id: number | null; affected_dataset_name: string | null; affected_datasource_type: string | null
  lineage_id: number | null; rule_id: number | null; rule_name: string | null
  change_summary: string; change_detail: string | null
  status: string; resolved_by: string | null; resolved_at: string | null; created_at: string
}

type PaginatedAlerts = { items: Alert[]; total: number; page: number; page_size: number }

type AlertRule = {
  id: number; rule_name: string; description: string | null
  scope_type: string; scope_id: number | null; scope_name: string | null
  trigger_type: string; trigger_config: string
  severity_override: string | null; channels: string
  notify_owners: string; webhook_url: string | null; subscribers: string | null
  is_active: string; created_by: string | null
  created_at: string; updated_at: string; alert_count: number
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AlertsPage() {
  const [tab, setTab] = useState("alerts")
  const [ruleDialogOpen, setRuleDialogOpen] = useState(false)
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null)

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
            {tab === "rules" && (
              <Button size="sm" onClick={() => { setEditingRule(null); setRuleDialogOpen(true) }} className="gap-1.5">
                <Plus className="h-3.5 w-3.5" />
                규칙 추가
              </Button>
            )}
          </div>

          <TabsContent value="alerts" className="mt-4">
            <AlertsTab />
          </TabsContent>
          <TabsContent value="rules" className="mt-4">
            <RulesTab onAddRule={() => setRuleDialogOpen(true)} onEditRule={(rule) => { setEditingRule(rule); setRuleDialogOpen(true) }} />
          </TabsContent>
        </Tabs>
      </div>

      <RuleCreateDialog
        open={ruleDialogOpen}
        onOpenChange={(o) => { setRuleDialogOpen(o); if (!o) setEditingRule(null) }}
        onCreated={() => setTab("rules")}
        editRule={editingRule}
      />
    </>
  )
}

// ---------------------------------------------------------------------------
// Alerts Tab
// ---------------------------------------------------------------------------

function AlertsTab() {
  const [data, setData] = useState<PaginatedAlerts | null>(null)
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState("OPEN")
  const [severityFilter, setSeverityFilter] = useState("all")
  const [page, setPage] = useState(1)
  const [selectedAlert, setSelectedAlert] = useState<Alert | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)

  const fetchAlerts = useCallback(async () => {
    setLoading(true)
    const params = new URLSearchParams()
    if (statusFilter !== "all") params.set("status", statusFilter)
    if (severityFilter !== "all") params.set("severity", severityFilter)
    params.set("page", String(page))
    params.set("page_size", "20")
    try {
      const resp = await authFetch(`${BASE}?${params}`)
      if (resp.ok) setData(await resp.json())
    } catch { /* */ } finally { setLoading(false) }
  }, [statusFilter, severityFilter, page])

  useEffect(() => { fetchAlerts() }, [fetchAlerts])

  const updateStatus = async (alertId: number, status: string) => {
    await authFetch(`${BASE}/${alertId}/status`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    })
    fetchAlerts()
  }

  const totalPages = data ? Math.ceil(data.total / data.page_size) : 0

  return (
    <>
      {/* Filters */}
      <div className="flex items-center gap-3 mb-4">
        <Select value={statusFilter} onValueChange={v => { setStatusFilter(v); setPage(1) }}>
          <SelectTrigger className="w-40 h-9"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">전체 상태</SelectItem>
            <SelectItem value="OPEN">미확인</SelectItem>
            <SelectItem value="ACKNOWLEDGED">확인됨</SelectItem>
            <SelectItem value="RESOLVED">해결됨</SelectItem>
            <SelectItem value="DISMISSED">무시됨</SelectItem>
          </SelectContent>
        </Select>
        <Select value={severityFilter} onValueChange={v => { setSeverityFilter(v); setPage(1) }}>
          <SelectTrigger className="w-40 h-9"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">전체 심각도</SelectItem>
            <SelectItem value="BREAKING">심각</SelectItem>
            <SelectItem value="WARNING">경고</SelectItem>
            <SelectItem value="INFO">정보</SelectItem>
          </SelectContent>
        </Select>
        <span className="ml-auto text-sm text-muted-foreground">{data ? `${data.total}건` : ""}</span>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-20">심각도</TableHead>
                <TableHead>변경</TableHead>
                <TableHead className="w-44">출처</TableHead>
                <TableHead className="w-44">영향</TableHead>
                <TableHead className="w-24">상태</TableHead>
                <TableHead className="w-28">시간</TableHead>
                <TableHead className="w-24">작업</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow><TableCell colSpan={7} className="text-center py-8 text-muted-foreground">불러오는 중...</TableCell></TableRow>
              ) : !data || data.items.length === 0 ? (
                <TableRow><TableCell colSpan={7} className="text-center py-12">
                  <Bell className="h-8 w-8 text-muted-foreground/40 mx-auto mb-2" />
                  <p className="text-sm text-muted-foreground">알림이 없습니다</p>
                </TableCell></TableRow>
              ) : data.items.map(alert => (
                <TableRow key={alert.id} className="cursor-pointer hover:bg-muted/50"
                  onClick={() => { setSelectedAlert(alert); setDetailOpen(true) }}>
                  <TableCell><SeverityBadge severity={alert.severity} /></TableCell>
                  <TableCell>
                    <p className="text-sm truncate max-w-sm">{alert.change_summary}</p>
                    {alert.rule_name && (
                      <p className="text-sm text-muted-foreground mt-0.5">규칙: {alert.rule_name}</p>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="text-sm">
                      <span className="text-muted-foreground">{alert.source_datasource_type}</span>
                      <p className="font-medium truncate">{alert.source_dataset_name}</p>
                    </div>
                  </TableCell>
                  <TableCell>
                    {alert.affected_dataset_name ? (
                      <div className="text-sm">
                        <span className="text-muted-foreground">{alert.affected_datasource_type}</span>
                        <p className="font-medium truncate">{alert.affected_dataset_name}</p>
                      </div>
                    ) : <span className="text-sm text-muted-foreground">-</span>}
                  </TableCell>
                  <TableCell><StatusBadge status={alert.status} /></TableCell>
                  <TableCell><span className="text-sm text-muted-foreground">{formatTimeAgo(alert.created_at)}</span></TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                      {alert.status === "OPEN" && (
                        <>
                          <Button variant="ghost" size="icon" className="h-7 w-7" title="확인" onClick={() => updateStatus(alert.id, "ACKNOWLEDGED")}><Eye className="h-3.5 w-3.5" /></Button>
                          <Button variant="ghost" size="icon" className="h-7 w-7" title="해결" onClick={() => updateStatus(alert.id, "RESOLVED")}><Check className="h-3.5 w-3.5" /></Button>
                          <Button variant="ghost" size="icon" className="h-7 w-7" title="무시" onClick={() => updateStatus(alert.id, "DISMISSED")}><X className="h-3.5 w-3.5" /></Button>
                        </>
                      )}
                      {alert.status === "ACKNOWLEDGED" && (
                        <Button variant="ghost" size="icon" className="h-7 w-7" title="해결" onClick={() => updateStatus(alert.id, "RESOLVED")}><Check className="h-3.5 w-3.5" /></Button>
                      )}
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
          <span className="text-sm text-muted-foreground">{page} / {totalPages} 페이지</span>
          <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>다음</Button>
        </div>
      )}

      {/* Detail dialog */}
      <AlertDialog open={detailOpen} onOpenChange={setDetailOpen}>
        <AlertDialogContent className="max-w-lg">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              알림 상세 {selectedAlert && <SeverityBadge severity={selectedAlert.severity} />}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3 pt-2">
                {selectedAlert && (
                  <>
                    <div>
                      <span className="text-sm text-muted-foreground">변경</span>
                      <p className="text-sm font-medium">{selectedAlert.change_summary}</p>
                    </div>
                    {selectedAlert.rule_name && (
                      <div>
                        <span className="text-sm text-muted-foreground">규칙</span>
                        <p className="text-sm">{selectedAlert.rule_name}</p>
                      </div>
                    )}
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <span className="text-sm text-muted-foreground">출처</span>
                        <p className="text-sm">{selectedAlert.source_datasource_type}.{selectedAlert.source_dataset_name}</p>
                      </div>
                      <div>
                        <span className="text-sm text-muted-foreground">영향</span>
                        <p className="text-sm">{selectedAlert.affected_dataset_name ? `${selectedAlert.affected_datasource_type}.${selectedAlert.affected_dataset_name}` : "-"}</p>
                      </div>
                    </div>
                    {selectedAlert.change_detail && (
                      <div>
                        <span className="text-sm text-muted-foreground">상세</span>
                        <div className="mt-1 max-h-48 overflow-y-auto">
                          {(() => {
                            try {
                              const details = JSON.parse(selectedAlert.change_detail!)
                              return <div className="space-y-1.5">{details.map((d: Record<string, string>, i: number) => (
                                <div key={i} className="text-sm bg-muted/50 rounded px-3 py-2">
                                  <span className="font-medium">{d.changed_column || d.field}</span>
                                  <span className="text-muted-foreground ml-1">({d.change_type || d.type})</span>
                                  {d.mapped_to && <span className="text-muted-foreground ml-1">→ {d.mapped_to}</span>}
                                </div>
                              ))}</div>
                            } catch { return <pre className="text-sm whitespace-pre-wrap">{selectedAlert.change_detail}</pre> }
                          })()}
                        </div>
                      </div>
                    )}
                    <div className="text-sm text-muted-foreground">
                      생성: {new Date(selectedAlert.created_at).toLocaleString()}
                    </div>
                  </>
                )}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>닫기</AlertDialogCancel>
            {selectedAlert?.status === "OPEN" && (
              <AlertDialogAction onClick={() => { updateStatus(selectedAlert.id, "RESOLVED"); setDetailOpen(false) }}>해결</AlertDialogAction>
            )}
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

// ---------------------------------------------------------------------------
// Rules Tab
// ---------------------------------------------------------------------------

function RulesTab({ onAddRule, onEditRule }: { onAddRule: () => void; onEditRule: (rule: AlertRule) => void }) {
  const [rules, setRules] = useState<AlertRule[]>([])
  const [loading, setLoading] = useState(true)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [deleteTargetRule, setDeleteTargetRule] = useState<AlertRule | null>(null)

  const fetchRules = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await authFetch(`${BASE}/rules`)
      if (resp.ok) setRules(await resp.json())
    } catch { /* */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { fetchRules() }, [fetchRules])

  const toggleActive = async (rule: AlertRule) => {
    const newActive = rule.is_active === "true" ? "false" : "true"
    await authFetch(`${BASE}/rules/${rule.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_active: newActive }),
    })
    fetchRules()
  }

  const handleDeleteClick = (rule: AlertRule) => {
    if (rule.is_active === "true") {
      toast.error("활성 상태인 규칙은 삭제할 수 없습니다. 먼저 비활성화해주세요.")
      return
    }
    setDeleteTargetRule(rule)
    setDeleteConfirmOpen(true)
  }

  const confirmDelete = async () => {
    if (!deleteTargetRule) return
    await authFetch(`${BASE}/rules/${deleteTargetRule.id}`, { method: "DELETE" })
    setDeleteConfirmOpen(false)
    setDeleteTargetRule(null)
    fetchRules()
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground text-center py-8">규칙을 불러오는 중...</p>
  }

  if (rules.length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center justify-center py-12 gap-4">
          <Bell className="h-10 w-10 text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">아직 정의된 알림 규칙이 없습니다.</p>
          <Button size="sm" onClick={onAddRule} className="gap-1.5">
            <Plus className="h-3.5 w-3.5" /> 첫 규칙 만들기
          </Button>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-3">
      {rules.map(rule => (
        <Card key={rule.id} className={rule.is_active === "false" ? "opacity-60" : ""}>
          <CardContent className="p-4">
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0">
                {/* Header */}
                <div className="flex items-center gap-2 mb-2">
                  {rule.severity_override ? (
                    <SeverityBadge severity={rule.severity_override} />
                  ) : (
                    <Badge variant="outline" className="text-sm px-1.5 py-0">자동</Badge>
                  )}
                  <span className="font-medium text-sm">{rule.rule_name}</span>
                </div>

                {/* 적용 범위 */}
                <div className="flex items-center gap-2 mb-2">
                  <ScopeIcon type={rule.scope_type} />
                  <span className="text-sm text-muted-foreground">
                    {rule.scope_type}: {rule.scope_name || "전체"}
                  </span>
                </div>

                {/* 트리거 + 채널 */}
                <div className="flex items-center gap-4 text-smtext-muted-foreground">
                  <span>트리거: <span className="text-foreground">{rule.trigger_type}</span></span>
                  <span>채널: <span className="text-foreground">{rule.channels}</span></span>
                  {rule.subscribers && (
                    <span>구독자: <span className="text-foreground">{rule.subscribers.split(",").length}명</span></span>
                  )}
                  <span>알림 수: <span className="text-foreground">{rule.alert_count}</span></span>
                </div>

                {rule.description && (
                  <p className="text-sm text-muted-foreground mt-2">{rule.description}</p>
                )}
              </div>

              {/* Actions */}
              <div className="flex items-center gap-2 ml-4">
                <Switch
                  checked={rule.is_active === "true"}
                  onCheckedChange={() => toggleActive(rule)}
                />
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => onEditRule(rule)}>
                  <Pencil className="h-3.5 w-3.5 text-muted-foreground" />
                </Button>
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => handleDeleteClick(rule)}>
                  <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
                </Button>
              </div>
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
            <div className="border-t px-5 py-4 text-sm text-muted-foreground space-y-4">
              <div>
                <p className="font-semibold text-foreground mb-1">평가 시점 — 스키마 변경이 기록되는 순간</p>
                <p className="mb-1">
                  규칙은 주기적으로 검사하는 방식이 아니라, <b>스키마 변경 이력(스냅숏)이 생성되는 순간</b>
                  이벤트 방식으로 평가됩니다:
                </p>
                <ul className="list-disc pl-5 space-y-0.5">
                  <li><b>메타데이터 동기화</b> 실행 시 — 원본 DB 와 비교해 컬럼 추가/변경/삭제가 감지된 경우</li>
                  <li>데이터셋 상세 &gt; <b>스키마 탭에서 직접 수정·저장</b>한 경우</li>
                  <li><b>품질 검증 실패</b> 트리거: 품질 탭 "검증 실행" 또는 품질 배치(quality/*.py)
                    결과 반입 시 실패한 규칙이 있으면 즉시 평가됩니다</li>
                </ul>
                <p className="mt-1">
                  변경이 감지되지 않으면 알림은 생성되지 않습니다. 규칙을 새로 만들거나 수정해도
                  <b> 과거의 변경에 소급 적용되지는 않으며</b>, 저장 이후 발생하는 변경부터 평가됩니다.
                </p>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">매칭 순서</p>
                <ul className="list-disc pl-5 space-y-0.5">
                  <li><b>활성 규칙만</b> 평가합니다 (비활성 규칙은 건너뜀)</li>
                  <li>① <b>범위</b> 매칭 — 전체 / 데이터 소스 / 데이터셋 / 태그 / 리니지 중 변경된 데이터셋이 속하는지 확인</li>
                  <li>② <b>트리거</b> 평가 — 모든 변경(ANY) / 지정한 변경 유형(추가·변경·삭제) /
                    감시 컬럼 목록 / 컬럼 매핑 파손 여부</li>
                  <li>③ <b>심각도</b> 결정 — 자동 판정(삭제=치명, 변경=경고 등) 또는 규칙의 강제 지정 값</li>
                </ul>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">알림 전달</p>
                <ul className="list-disc pl-5 space-y-0.5">
                  <li><b>인앱</b>: 알림 탭 목록과 상단 헤더의 벨 아이콘에 표시</li>
                  <li><b>메일 / 웹훅</b>: 채널을 지정한 규칙만 — 구독자 목록과
                    (옵션을 켠 경우) 데이터셋 소유자에게 전달</li>
                </ul>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">시연 방법</p>
                <p>
                  감시 대상 데이터셋의 <b>스키마 탭에서 컬럼 하나를 수정·저장</b>해 보세요
                  (예: rental 의 감시 컬럼 타입 변경). 변경 이력이 생성되면서 매칭되는 규칙이
                  즉시 평가되고, 알림 탭에서 생성된 알림을 확인할 수 있습니다.
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Delete confirmation dialog */}
      <AlertDialog open={deleteConfirmOpen} onOpenChange={setDeleteConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>규칙 삭제</AlertDialogTitle>
            <AlertDialogDescription>
              &quot;{deleteTargetRule?.rule_name}&quot; 규칙을 정말 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction onClick={confirmDelete}>삭제</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
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

function SeverityBadge({ severity }: { severity: string }) {
  if (severity === "BREAKING") return <Badge className="bg-red-500 text-white text-sm px-1.5 py-0 border-0">심각</Badge>
  if (severity === "WARNING") return <Badge className="bg-amber-500 text-white text-sm px-1.5 py-0 border-0">경고</Badge>
  return <Badge variant="outline" className="text-sm px-1.5 py-0">정보</Badge>
}

function StatusBadge({ status }: { status: string }) {
  if (status === "OPEN") return <Badge variant="outline" className="text-sm px-1.5 py-0 border-red-300 text-red-600">미확인</Badge>
  if (status === "ACKNOWLEDGED") return <Badge variant="outline" className="text-sm px-1.5 py-0 border-amber-300 text-amber-600">확인</Badge>
  if (status === "RESOLVED") return <Badge variant="outline" className="text-sm px-1.5 py-0 border-green-300 text-green-600">해결</Badge>
  if (status === "DISMISSED") return <Badge variant="outline" className="text-sm px-1.5 py-0 border-gray-300 text-gray-500">무시</Badge>
  return <Badge variant="outline" className="text-sm px-1.5 py-0">{status}</Badge>
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
