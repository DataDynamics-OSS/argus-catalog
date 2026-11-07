"use client"

import { Fragment, useCallback, useEffect, useState } from "react"
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  FileCode,
  FileJson,
  KeyRound,
  Loader2,
  Pencil,
  Plug,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from "lucide-react"
import { errorMessage, notifyError, toast } from "@/lib/notify"
import { useConfirm } from "@/components/confirm-provider"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import {
  Dialog,
  DialogContent,
  DialogFooter,
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { Textarea } from "@workspace/ui/components/textarea"
import { cn } from "@workspace/ui/lib/utils"

import { useAuth } from "@/features/auth"
import { usePermissions } from "@/features/permissions/use-permissions"
import {
  addMcpScope,
  collectMcpDeployment,
  deleteMcpCredential,
  deleteMcpScope,
  fetchMcpCredential,
  fetchMcpDeployment,
  fetchMcpDeploymentAgents,
  fetchMcpDeploymentStatusHistory,
  fetchMcpEffective,
  fetchMcpSyncLog,
  fetchUnregisteredMcp,
  healthCheckMcpDeployment,
  importMcpConfig,
  linkUnregisteredMcp,
  putMcpCredential,
  retireMcpDeployment,
  simulateMcpAccess,
  updateMcpDeployment,
  type McpDeploymentPayload,
} from "../api"
import {
  AUTH_METHODS,
  ENVIRONMENTS,
  FRAMEWORK_LABEL,
  STATUS_LABEL,
  STATUS_OPTIONS,
  TRANSPORTS,
  type McpCollectResult,
  type McpCredential,
  type McpDeploymentAgent,
  type McpDeploymentDetail as Detail,
  type McpEffectiveState,
  type McpSimulateLayer,
  type McpSimulateResult,
  type McpSyncLog,
  type McpUnregistered,
} from "../data/schema"
import {
  EffectBadge,
  FieldRow,
  HealthDot,
  OriginBadge,
  OriginLegend,
  RiskBadge,
  RowSourceBadge,
  StatusBadge,
  fmtDate,
} from "./mcp-badges"
import { useMcpServers } from "./mcp-provider"
import { JsonBlock, StatusHistoryPanel } from "./mcp-server-detail"
import { AuditPanel } from "./tabs/audit-panel"
import { FindingsPanel } from "./tabs/findings-panel"
import { ManifestImportDialog } from "./tabs/manifest-dialog"
import { AgentScopeDialog } from "./tabs/agent-scope-dialog"
import { PolicyPanel } from "./tabs/policy-panel"

const BASIS_LABEL: Record<string, string> = {
  inferred: "수집 결과로 추정",
  config: "설정 파일 기준",
  manifest: "manifest 보고",
}

export function McpDeploymentDetail({ name }: { name: string }) {
  const { setSelectedDeployment, setSelectedServer, reload, reloadKey } = useMcpServers()
  const askConfirm = useConfirm()
  const { user } = useAuth()
  const { isFeatureAllowed, canUseFeature } = usePermissions()
  const [dep, setDep] = useState<Detail | null>(null)
  const [eff, setEff] = useState<McpEffectiveState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [editOpen, setEditOpen] = useState(false)
  const [collectOpen, setCollectOpen] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [manifestOpen, setManifestOpen] = useState(false)
  const [tab, setTab] = useState("effective")

  const load = useCallback(async () => {
    try {
      const [d, e] = await Promise.all([fetchMcpDeployment(name), fetchMcpEffective(name)])
      setDep(d)
      setEff(e)
      setError(null)
    } catch (e) {
      setError(errorMessage(e) ?? "MCP 배포 정보를 불러오지 못했습니다.")
    }
  }, [name])

  useEffect(() => {
    load()
  }, [load, reloadKey])

  useEffect(() => {
    window.scrollTo(0, 0)
  }, [name])

  if (error) {
    return (
      <div className="space-y-3">
        <Button variant="ghost" size="sm" onClick={() => setSelectedDeployment(null)}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 목록
        </Button>
        <p className="text-sm text-destructive">{error}</p>
      </div>
    )
  }
  if (!dep || !eff) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const isForge = dep.server_framework === "argus-forge"
  const warnings = dep.security_warnings ?? []
  // 헬스 체크·수집·편집·폐기·정책·범위·자격증명·토큰은 배포 등록자·관리자만 — 서버(_guard_deployment)와 같은 기준
  const canEdit = !!user && (user.is_admin || (!!dep.created_by && dep.created_by === user.username))
  // 승인 정책·에이전트별 범위는 여기에 기능 권한 mcp.policy 까지 필요(관리자는 항상) — 서버 _guard_policy 와 같은 기준
  const canEditPolicy = canEdit && (!!user?.is_admin || isFeatureAllowed("mcp.policy"))

  const runHealth = async () => {
    setBusy("health")
    try {
      const r = await healthCheckMcpDeployment(dep.name)
      toast.success(`헬스 체크: ${r.health_status}`)
      await load()
    } catch (e) {
      notifyError(e, "헬스 체크를 실행하지 못했습니다.")
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* 헤더 */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-2">
          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setSelectedDeployment(null)} aria-label="목록">
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <Plug className="h-5 w-5 text-muted-foreground" />
              <h2 className="text-lg font-semibold">{dep.display_name || dep.name}</h2>
              <Badge variant="outline">{dep.environment}</Badge>
              <StatusBadge status={dep.status} />
              <HealthDot status={dep.health_status} />
              {dep.policy_mode === "enforce" ? (
                <Badge className="bg-violet-600 text-white hover:bg-violet-600">정책 적용(enforce)</Badge>
              ) : (
                <Badge variant="outline">관측 모드(observe)</Badge>
              )}
              {!dep.policy_version && (
                <button type="button" onClick={() => setTab("policy")}>
                  <Badge variant="outline" className="border-dashed text-muted-foreground">승인 정책 없음</Badge>
                </button>
              )}
              {dep.open_finding_count > 0 && (
                <button type="button" onClick={() => setTab("findings")} className="inline-flex items-center gap-1">
                  <AlertTriangle className="h-3.5 w-3.5 text-red-600" />
                  <span className="text-xs font-medium text-red-700 dark:text-red-400">finding {dep.open_finding_count}</span>
                  <RiskBadge risk={dep.max_finding_severity} />
                </button>
              )}
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span className="font-mono">{dep.urn}</span>
              {dep.server_name && (
                <>
                  <span>·</span>
                  <button type="button" className="text-primary hover:underline" onClick={() => setSelectedServer(dep.server_name)}>
                    서버 정의: {dep.server_name}
                  </button>
                  <span>({FRAMEWORK_LABEL[dep.server_framework ?? ""] ?? dep.server_framework})</span>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {canEdit && (
          <>
          <Button size="sm" variant="outline" disabled={busy !== null} onClick={runHealth}>
            {busy === "health" ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Activity className="mr-1 h-4 w-4" />}
            헬스 체크
          </Button>
          <Button size="sm" variant="outline" onClick={() => setCollectOpen(true)}>
            <RefreshCw className="mr-1 h-4 w-4" /> 수집
          </Button>
          {isForge && (
            <Button size="sm" variant="outline" onClick={() => setConfigOpen(true)}>
              <FileCode className="mr-1 h-4 w-4" /> 설정 가져오기
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={() => setManifestOpen(true)}>
            <FileJson className="mr-1 h-4 w-4" /> manifest
          </Button>
          <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
            <Pencil className="mr-1 h-4 w-4" /> 편집
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="text-destructive"
            disabled={dep.status === "retired"}
            onClick={async () => {
              if (!(await askConfirm({
                title: "배포 폐기",
                description: `'${dep.name}' 배포를 폐기할까요? (사용 중인 에이전트가 있으면 거부됩니다)`,
                confirmText: "폐기",
                destructive: true,
              }))) return
              try {
                await retireMcpDeployment(dep.name)
                toast.success("배포를 폐기했습니다.")
                reload()
              } catch (e) {
                notifyError(e, "배포를 폐기하지 못했습니다.")
              }
            }}
          >
            <Trash2 className="mr-1 h-4 w-4" /> 폐기
          </Button>
          </>
          )}
        </div>
      </div>
      <OriginLegend />

      {warnings.length > 0 && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-700 dark:bg-amber-950/30">
          <div className="mb-1 flex items-center gap-1.5 font-medium text-amber-800 dark:text-amber-300">
            <AlertTriangle className="h-4 w-4" /> 보안 경고 ({warnings.length}) <OriginBadge origin="server_config" />
          </div>
          <ul className="list-disc space-y-0.5 pl-5 text-amber-900 dark:text-amber-200">
            {warnings.map((w) => <li key={w}>{w}</li>)}
          </ul>
        </div>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="flex-wrap">
          <TabsTrigger value="effective">실효 상태</TabsTrigger>
          <TabsTrigger value="overview">개요</TabsTrigger>
          <TabsTrigger value="policy">승인 정책</TabsTrigger>
          <TabsTrigger value="findings">finding ({dep.open_finding_count})</TabsTrigger>
          <TabsTrigger value="audit">감사 이벤트</TabsTrigger>
          <TabsTrigger value="scopes">리소스 범위 ({eff.scopes.filter((s) => s.layer !== "approved").length})</TabsTrigger>
          <TabsTrigger value="credential">자격증명</TabsTrigger>
          <TabsTrigger value="agents">사용 에이전트 ({dep.agent_count})</TabsTrigger>
          <TabsTrigger value="sync">수집 이력</TabsTrigger>
          <TabsTrigger value="history">상태 이력</TabsTrigger>
        </TabsList>

        <TabsContent value="effective" className="mt-4">
          <EffectivePanel eff={eff} />
        </TabsContent>

        <TabsContent value="overview" className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">배포 관리 정보 <OriginBadge origin="argus" /></CardTitle>
            </CardHeader>
            <CardContent className="divide-y">
              <FieldRow label="이름" origin="argus"><span className="font-mono">{dep.name}</span></FieldRow>
              <FieldRow label="표시명" origin="argus">{dep.display_name}</FieldRow>
              <FieldRow label="설명" origin="argus">{dep.description}</FieldRow>
              <FieldRow label="환경" origin="argus">{dep.environment}</FieldRow>
              <FieldRow label="상태" origin="argus"><StatusBadge status={dep.status} /></FieldRow>
              <FieldRow label="전송" origin="argus">{dep.transport}</FieldRow>
              <FieldRow label="MCP 엔드포인트" origin="argus">
                {dep.endpoint ? <span className="break-all font-mono text-xs">{dep.endpoint}</span> : null}
              </FieldRow>
              {isForge && (
                <FieldRow label="도구 REST API" origin="argus">
                  {dep.rest_api_base ? <span className="break-all font-mono text-xs">{dep.rest_api_base}</span> : null}
                </FieldRow>
              )}
              <FieldRow label="인증 방식" origin="argus">{dep.auth_method}</FieldRow>
              <FieldRow label="배포 범위" origin="argus">{dep.deployment_scope}</FieldRow>
              <FieldRow label="호스트" origin="argus">{dep.host}</FieldRow>
              <FieldRow label="연결 데이터 소스" origin="argus">{dep.datasource_name}</FieldRow>
              <FieldRow label="정책 모드" origin="argus">{dep.policy_mode}</FieldRow>
              <FieldRow label="정책 버전" origin="argus">
                {dep.policy_version ? (
                  <span className="font-mono text-xs">{dep.policy_version} · {dep.policy_updated_by ?? "-"}</span>
                ) : null}
              </FieldRow>
              <FieldRow label="승인 버전" origin="argus">{dep.approved_version}</FieldRow>
              <FieldRow label="감사 이벤트 보존" origin="argus">{dep.audit_retention_days}일</FieldRow>
              <FieldRow label="자동 수집 주기" origin="argus">
                {dep.collect_interval_minutes ? `${dep.collect_interval_minutes}분` : "수동"}
              </FieldRow>
              <FieldRow label="헬스" origin="argus">
                <span className="inline-flex items-center gap-2">
                  <HealthDot status={dep.health_status} />
                  {dep.latency_ms != null && <span className="text-xs text-muted-foreground">{dep.latency_ms}ms</span>}
                  {dep.health_fail_count > 0 && <span className="text-xs text-red-600">연속 실패 {dep.health_fail_count}회</span>}
                  <span className="text-xs text-muted-foreground">{fmtDate(dep.last_health_check_at)}</span>
                </span>
              </FieldRow>
              <FieldRow label="최근 관측" origin="argus">
                {fmtDate(dep.last_observed_at)}{dep.observed_source ? ` (${dep.observed_source})` : ""}
              </FieldRow>
              <FieldRow label="소유자 / 부서" origin="argus">{[dep.owner_email, dep.department].filter(Boolean).join(" / ") || null}</FieldRow>
            </CardContent>
          </Card>
          <div className="flex flex-col gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">서버 설정 수집값 <OriginBadge origin="server_config" /></CardTitle>
                <p className="text-xs text-muted-foreground">
                  MCP 표준에는 없는 정보입니다. Argus Forge 서버는 config.yaml 가져오기 또는 /readyz 로 수집합니다.
                </p>
              </CardHeader>
              <CardContent className="divide-y">
                <FieldRow label="대상 시스템" origin="server_config">
                  {dep.target_endpoint ? <span className="break-all font-mono text-xs">{dep.target_endpoint}</span> : null}
                </FieldRow>
                <FieldRow label="실행 계정" origin="server_config">{dep.service_principal}</FieldRow>
                <FieldRow label="대상 인증" origin="server_config">{dep.principal_auth}</FieldRow>
                <FieldRow label="인스턴스 ID" origin="server_config">
                  {dep.instance_id ? <span className="font-mono text-xs">{dep.instance_id}</span> : null}
                </FieldRow>
                <FieldRow label="제한(limits)" origin="server_config">
                  {dep.limits && Object.keys(dep.limits).length ? <JsonBlock value={dep.limits} /> : null}
                </FieldRow>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">MCP 서버 보고값 <OriginBadge origin="mcp" /></CardTitle>
              </CardHeader>
              <CardContent className="divide-y">
                <FieldRow label="배포 버전" origin="mcp">{dep.deployed_version}</FieldRow>
                <FieldRow label="프로토콜 버전" origin="mcp">{dep.protocol_version}</FieldRow>
                <FieldRow label="serverInfo" origin="mcp">{dep.server_info ? <JsonBlock value={dep.server_info} /> : null}</FieldRow>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="policy" className="mt-4">
          <PolicyPanel name={dep.name} onChanged={load} canEdit={canEditPolicy} />
        </TabsContent>

        <TabsContent value="findings" className="mt-4">
          <FindingsPanel name={dep.name} onChanged={load} canEdit={canEdit} key={`${dep.open_finding_count}-${dep.policy_version}`} />
        </TabsContent>

        <TabsContent value="audit" className="mt-4">
          <AuditPanel dep={dep} onChanged={load} canEdit={canEdit} />
        </TabsContent>

        <TabsContent value="scopes" className="mt-4">
          <ScopesPanel name={dep.name} eff={eff} onChanged={load} canEdit={canEdit} />
        </TabsContent>

        <TabsContent value="credential" className="mt-4">
          <CredentialPanel name={dep.name} onChanged={load} canEdit={canEdit && canUseFeature("mcp.credential")} />
        </TabsContent>

        <TabsContent value="agents" className="mt-4">
          <AgentsPanel name={dep.name} onChanged={load} canEdit={canEditPolicy} isAdmin={!!user?.is_admin} />
        </TabsContent>

        <TabsContent value="sync" className="mt-4">
          <SyncLogPanel name={dep.name} key={`${dep.last_observed_at}`} />
        </TabsContent>

        <TabsContent value="history" className="mt-4">
          <StatusHistoryPanel load={() => fetchMcpDeploymentStatusHistory(dep.name)} />
        </TabsContent>
      </Tabs>

      <DeploymentEditDialog open={editOpen} onOpenChange={setEditOpen} dep={dep} onSaved={load} />
      <CollectDialog open={collectOpen} onOpenChange={setCollectOpen} dep={dep} onApplied={load} />
      <ConfigImportDialog open={configOpen} onOpenChange={setConfigOpen} dep={dep} onApplied={load} />
      <ManifestImportDialog open={manifestOpen} onOpenChange={setManifestOpen} target="deployment" name={dep.name} onApplied={load} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// 실효 상태
// ---------------------------------------------------------------------------

function OnOff({ value }: { value: boolean | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-xs text-muted-foreground">미확인</span>
  return value ? (
    <span className="text-xs font-medium text-emerald-700 dark:text-emerald-400">켜짐</span>
  ) : (
    <span className="text-xs text-muted-foreground">꺼짐</span>
  )
}

function EffectivePanel({ eff }: { eff: McpEffectiveState }) {
  const [onlyExposed, setOnlyExposed] = useState(true)
  const tools = eff.tools.filter((t) => !onlyExposed || t.exposed)
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">도구 그룹 on/off</CardTitle>
          <p className="text-xs text-muted-foreground">
            관측값 <OriginBadge origin="server_config" className="align-middle" /> 은 서버 설정 또는 수집 결과로 확인한 실제 상태,
            승인값 <OriginBadge origin="argus" className="align-middle" /> 은 Argus 에서 승인한 목표 상태(Phase 2 정책 연동)입니다.
          </p>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>그룹</TableHead>
                <TableHead>설정 키</TableHead>
                <TableHead>위험도</TableHead>
                <TableHead>관측</TableHead>
                <TableHead>근거</TableHead>
                <TableHead>승인</TableHead>
                <TableHead className="text-right">노출 / 전체</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {eff.groups.length === 0 ? (
                <TableRow><TableCell colSpan={7} className="h-14 text-center text-muted-foreground">그룹 정보가 없습니다.</TableCell></TableRow>
              ) : eff.groups.map((g) => (
                <TableRow key={g.id} className={g.mutating && g.observed_enabled ? "bg-orange-50/50 dark:bg-orange-950/20" : undefined}>
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      <span className="font-medium">{g.label || g.key}</span>
                      {g.mutating && <Badge variant="outline" className="h-4 border-orange-300 px-1 text-[10px] text-orange-700 dark:text-orange-300">변경</Badge>}
                    </div>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{g.config_key || "-"}</TableCell>
                  <TableCell><RiskBadge risk={g.risk} /></TableCell>
                  <TableCell><OnOff value={g.observed_enabled} /></TableCell>
                  <TableCell className="text-xs text-muted-foreground">{g.observed_basis ? BASIS_LABEL[g.observed_basis] ?? g.observed_basis : "-"}</TableCell>
                  <TableCell><OnOff value={g.approved_enabled} /></TableCell>
                  <TableCell className="text-right text-sm">{g.exposed_count} / {g.tool_count}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="text-sm">
              도구 노출 상태 ({eff.tools.filter((t) => t.exposed).length} 노출 / {eff.tools.length} 전체)
            </CardTitle>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <Switch checked={onlyExposed} onCheckedChange={setOnlyExposed} />
              노출된 도구만
            </label>
          </div>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>도구</TableHead>
                <TableHead>그룹</TableHead>
                <TableHead>위험도</TableHead>
                <TableHead>노출</TableHead>
                <TableHead>차단 <OriginBadge origin="argus" className="ml-0.5 align-middle" /></TableHead>
                <TableHead>승인 필요</TableHead>
                <TableHead>출처</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tools.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="h-14 text-center text-muted-foreground">
                    {eff.tools.length === 0 ? "아직 수집되지 않았습니다. “수집”을 실행하세요." : "노출된 도구가 없습니다."}
                  </TableCell>
                </TableRow>
              ) : tools.map((t) => (
                <TableRow key={t.id} className={cn(!t.exposed && "opacity-60")}>
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-sm">{t.name}</span>
                      {t.mutating && <Badge variant="outline" className="h-4 border-orange-300 px-1 text-[10px] text-orange-700 dark:text-orange-300">변경</Badge>}
                    </div>
                  </TableCell>
                  <TableCell className="text-xs">{t.group_key || "-"}</TableCell>
                  <TableCell><RiskBadge risk={t.risk} /></TableCell>
                  <TableCell><OnOff value={t.exposed} /></TableCell>
                  <TableCell>{t.blocked ? <Badge variant="destructive" className="text-[10px]">차단</Badge> : "-"}</TableCell>
                  <TableCell>{t.requires_approval ? <ShieldCheck className="h-4 w-4 text-violet-600" /> : "-"}</TableCell>
                  <TableCell><RowSourceBadge source={t.source} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 리소스 범위 + 시뮬레이터
// ---------------------------------------------------------------------------

function ScopesPanel({ name, eff, onChanged, canEdit }: {
  name: string; eff: McpEffectiveState; onChanged: () => Promise<void>; canEdit: boolean
}) {
  const [rt, setRt] = useState("")
  const [action, setAction] = useState("any")
  const [effect, setEffect] = useState("deny")
  const [patterns, setPatterns] = useState("")
  const [desc, setDesc] = useState("")

  const [simType, setSimType] = useState("")
  const [simRes, setSimRes] = useState("")
  const [simAction, setSimAction] = useState("read")
  const [simLayer, setSimLayer] = useState<McpSimulateLayer>("combined")
  const [simAgent, setSimAgent] = useState("none")
  const [agents, setAgents] = useState<McpDeploymentAgent[]>([])
  const [sim, setSim] = useState<McpSimulateResult | null>(null)
  useEffect(() => {
    fetchMcpDeploymentAgents(name).then(setAgents).catch(() => setAgents([]))
  }, [name])

  // 승인 계층 규칙은 "승인 정책" 탭에서 관리한다
  const scopes = eff.scopes.filter((s) => s.layer !== "approved")
  const resourceTypes = Array.from(new Set(eff.scopes.map((s) => s.resource_type)))

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">리소스 범위 규칙</CardTitle>
          <p className="text-xs text-muted-foreground">
            도구가 접근할 수 있는 리소스(DB·토픽·버킷·경로 등)를 리소스 유형 × 동작 × 효과 × 패턴(fnmatch)으로 정규화한 규칙입니다.
            평가 순서는 protect &gt; deny &gt; allow 목록이며, delete 는 write 규칙도 함께 적용됩니다.
            설정 가져오기로 수집된 규칙 <OriginBadge origin="server_config" className="align-middle" /> 은 재가져오기 시 갱신되고,
            수동 규칙 <OriginBadge origin="argus" className="align-middle" /> 은 Argus 에서만 관리됩니다.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>리소스 유형</TableHead>
                <TableHead>동작</TableHead>
                <TableHead>효과</TableHead>
                <TableHead>패턴</TableHead>
                <TableHead>설정 키</TableHead>
                <TableHead>출처</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {scopes.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="h-14 text-center text-muted-foreground">
                    규칙이 없습니다. 범위 미지정 리소스 접근은 “알 수 없음(unknown)”으로 평가됩니다.
                  </TableCell>
                </TableRow>
              ) : scopes.map((s) => (
                <TableRow key={s.id}>
                  <TableCell className="font-mono text-xs">{s.resource_type}</TableCell>
                  <TableCell className="text-xs">{s.action}</TableCell>
                  <TableCell><EffectBadge effect={s.effect} /></TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {s.patterns.map((p) => <code key={p} className="rounded bg-muted px-1 text-[11px]">{p}</code>)}
                    </div>
                    {s.description && <div className="mt-0.5 text-[11px] text-muted-foreground">{s.description}</div>}
                  </TableCell>
                  <TableCell className="font-mono text-[11px] text-muted-foreground">{s.config_key || "-"}</TableCell>
                  <TableCell><RowSourceBadge source={s.source} /></TableCell>
                  <TableCell>
                    {canEdit && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label="삭제"
                      onClick={async () => {
                        try {
                          await deleteMcpScope(name, s.id)
                          await onChanged()
                        } catch (e) {
                          notifyError(e, "리소스 범위를 삭제하지 못했습니다.")
                        }
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {canEdit && (
          <div className="grid gap-2 rounded-md border p-3 sm:grid-cols-[1fr_110px_110px_2fr_1fr_auto] sm:items-end">
            <div className="grid gap-1">
              <Label className="text-xs">리소스 유형</Label>
              <Input value={rt} onChange={(e) => setRt(e.target.value)} placeholder="예: hive.database" className="h-8" list="mcp-scope-types" />
              <datalist id="mcp-scope-types">
                {resourceTypes.map((t) => <option key={t} value={t} />)}
              </datalist>
            </div>
            <div className="grid gap-1">
              <Label className="text-xs">동작</Label>
              <Select value={action} onValueChange={setAction}>
                <SelectTrigger size="sm" className="h-8"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {["read", "write", "delete", "any"].map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1">
              <Label className="text-xs">효과</Label>
              <Select value={effect} onValueChange={setEffect}>
                <SelectTrigger size="sm" className="h-8"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {["allow", "deny", "protect", "mask"].map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1">
              <Label className="text-xs">패턴 (쉼표 구분, fnmatch)</Label>
              <Input value={patterns} onChange={(e) => setPatterns(e.target.value)} placeholder="예: prod_*, finance" className="h-8" />
            </div>
            <div className="grid gap-1">
              <Label className="text-xs">설명</Label>
              <Input value={desc} onChange={(e) => setDesc(e.target.value)} className="h-8" />
            </div>
            <Button
              size="sm"
              disabled={!rt.trim() || !patterns.trim()}
              onClick={async () => {
                try {
                  await addMcpScope(name, {
                    resource_type: rt.trim(),
                    action,
                    effect,
                    patterns: patterns.split(",").map((p) => p.trim()).filter(Boolean),
                    description: desc.trim() || undefined,
                  })
                  setPatterns("")
                  setDesc("")
                  await onChanged()
                } catch (e) {
                  notifyError(e, "리소스 범위를 추가하지 못했습니다.")
                }
              }}
            >
              <Plus className="mr-1 h-4 w-4" /> 규칙 추가
            </Button>
          </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">접근 시뮬레이터</CardTitle>
          <p className="text-xs text-muted-foreground">
            이 배포의 도구가 특정 리소스에 접근할 수 있는지 평가합니다. 실제 서버 호출은 하지 않습니다.
            “서버 설정” 은 위 규칙, “승인 정책” 은 승인 범위, “둘 다” 는 두 계층을 모두 통과해야 허용입니다.
            에이전트를 고르면 “둘 다” 에 그 에이전트의 리소스 범위까지 적용합니다.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-2 sm:grid-cols-[1fr_2fr_110px_130px_150px_auto] sm:items-end">
            <div className="grid gap-1">
              <Label className="text-xs">리소스 유형</Label>
              <Input value={simType} onChange={(e) => setSimType(e.target.value)} className="h-8" list="mcp-scope-types" />
            </div>
            <div className="grid gap-1">
              <Label className="text-xs">리소스</Label>
              <Input value={simRes} onChange={(e) => setSimRes(e.target.value)} placeholder="예: prod_sales" className="h-8" />
            </div>
            <div className="grid gap-1">
              <Label className="text-xs">동작</Label>
              <Select value={simAction} onValueChange={setSimAction}>
                <SelectTrigger size="sm" className="h-8"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {["read", "write", "delete"].map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1">
              <Label className="text-xs">기준</Label>
              <Select value={simLayer} onValueChange={(v) => setSimLayer(v as McpSimulateLayer)}>
                <SelectTrigger size="sm" className="h-8"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="combined">둘 다</SelectItem>
                  <SelectItem value="observed">서버 설정</SelectItem>
                  <SelectItem value="approved">승인 정책</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1">
              <Label className="text-xs">에이전트</Label>
              <Select value={simAgent} onValueChange={setSimAgent} disabled={simLayer !== "combined" || agents.length === 0}>
                <SelectTrigger size="sm" className="h-8"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">지정 안 함</SelectItem>
                  {agents.map((a) => (
                    <SelectItem key={a.link_id} value={String(a.link_id)}>{a.agent_display_name || a.agent_name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={!simType.trim() || !simRes.trim()}
              onClick={async () => {
                try {
                  setSim(await simulateMcpAccess(name, {
                    resource_type: simType.trim(), resource: simRes.trim(), action: simAction, layer: simLayer,
                    agent_link_id: simLayer === "combined" && simAgent !== "none" ? Number(simAgent) : null,
                  }))
                } catch (e) {
                  notifyError(e, "접근 시뮬레이션을 실행하지 못했습니다.")
                }
              }}
            >
              평가
            </Button>
          </div>
          {sim && (
            <div
              className={cn(
                "rounded-md border p-3 text-sm",
                sim.decision === "allow" && "border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/30",
                sim.decision === "deny" && "border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950/30",
                sim.decision === "unknown" && "border-zinc-300 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900/40",
              )}
            >
              <div className="font-medium">
                결과: {sim.decision === "allow" ? "허용" : sim.decision === "deny" ? "거부" : "알 수 없음"}
              </div>
              <div className="text-xs text-muted-foreground">{sim.reason}</div>
              {sim.by_layer && (
                <div className="mt-1 grid gap-0.5 text-xs">
                  <div>서버 설정: <b>{sim.by_layer.observed.decision}</b> — {sim.by_layer.observed.reason}</div>
                  <div>승인 정책: <b>{sim.by_layer.approved.decision}</b> — {sim.by_layer.approved.reason}</div>
                  {sim.by_layer.agent && (
                    <div>에이전트 범위: <b>{sim.by_layer.agent.decision}</b> — {sim.by_layer.agent.reason}</div>
                  )}
                </div>
              )}
              {sim.matched.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {sim.matched.map((m) => (
                    <span key={m.id} className="inline-flex items-center gap-1 text-xs">
                      <EffectBadge effect={m.effect} />
                      <code className="text-[11px]">{m.patterns.join(", ")}</code>
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 자격증명
// ---------------------------------------------------------------------------

function CredentialPanel({ name, onChanged, canEdit }: { name: string; onChanged: () => Promise<void>; canEdit: boolean }) {
  const askConfirm = useConfirm()
  const [cred, setCred] = useState<McpCredential | null | undefined>(undefined)
  const [err, setErr] = useState<string | null>(null)
  const [kind, setKind] = useState("bearer")
  const [secret, setSecret] = useState("")
  const [header, setHeader] = useState("")
  const [username, setUsername] = useState("")

  const load = useCallback(() => {
    fetchMcpCredential(name)
      .then((c) => { setCred(c); setErr(null) })
      .catch((e) => { setCred(null); setErr(errorMessage(e) ?? "자격증명을 불러오지 못했습니다.") })
  }, [name])
  useEffect(load, [load])

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <KeyRound className="h-4 w-4" /> 수집용 자격증명 <OriginBadge origin="argus" />
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          Argus 가 이 배포에서 도구 목록을 수집하거나 헬스 체크할 때 사용하는 자격증명입니다. 암호화 저장되며 값은 다시 표시되지 않습니다.
          mcp.json 가져오기 시 환경변수·헤더의 값은 저장하지 않고 키 이름만 기록합니다.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {err ? (
          <p className="text-sm text-muted-foreground">{err}</p>
        ) : cred === undefined ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : cred ? (
          <div className="flex flex-wrap items-center gap-3 rounded-md border p-3 text-sm">
            <Badge variant="outline">{cred.kind}</Badge>
            {cred.header_name && <span className="font-mono text-xs">{cred.header_name}</span>}
            {cred.username && <span className="text-xs">{cred.username}</span>}
            <span className="font-mono text-xs">{cred.masked}</span>
            <span className="text-xs text-muted-foreground">{cred.updated_by || "-"} · {fmtDate(cred.updated_at)}</span>
            {canEdit && (
            <Button
              size="sm"
              variant="outline"
              className="ml-auto text-destructive"
              onClick={async () => {
                if (!(await askConfirm({
                  title: "자격증명 삭제",
                  description: "저장된 자격증명을 삭제할까요? 헬스 체크·수집이 인증 없이 호출됩니다.",
                  confirmText: "삭제",
                  destructive: true,
                }))) return
                try {
                  await deleteMcpCredential(name)
                  toast.success("자격증명을 삭제했습니다.")
                  load()
                  await onChanged()
                } catch (e) {
                  notifyError(e, "자격증명을 삭제하지 못했습니다.")
                }
              }}
            >
              삭제
            </Button>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">등록된 자격증명이 없습니다.</p>
        )}
        {canEdit && (
        <div className="grid gap-2 sm:grid-cols-[120px_1fr_1fr_2fr_auto] sm:items-end">
          <div className="grid gap-1">
            <Label className="text-xs">유형</Label>
            <Select value={kind} onValueChange={setKind}>
              <SelectTrigger size="sm" className="h-8"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="bearer">bearer</SelectItem>
                <SelectItem value="api_key">api_key</SelectItem>
                <SelectItem value="basic">basic</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1">
            <Label className="text-xs">헤더 이름</Label>
            <Input value={header} onChange={(e) => setHeader(e.target.value)} disabled={kind !== "api_key"} placeholder="X-API-Key" className="h-8" />
          </div>
          <div className="grid gap-1">
            <Label className="text-xs">사용자</Label>
            <Input value={username} onChange={(e) => setUsername(e.target.value)} disabled={kind !== "basic"} className="h-8" />
          </div>
          <div className="grid gap-1">
            <Label className="text-xs">비밀값</Label>
            <Input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="new-password" className="h-8" />
          </div>
          <Button
            size="sm"
            disabled={!secret}
            onClick={async () => {
              try {
                await putMcpCredential(name, {
                  kind,
                  secret,
                  header_name: kind === "api_key" ? header.trim() || undefined : undefined,
                  username: kind === "basic" ? username.trim() || undefined : undefined,
                })
                setSecret("")
                toast.success("저장했습니다.")
                load()
                await onChanged()
              } catch (e) {
                notifyError(e, "자격증명을 저장하지 못했습니다.")
              }
            }}
          >
            {cred ? "교체" : "저장"}
          </Button>
        </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// 사용 에이전트
// ---------------------------------------------------------------------------

function AgentsPanel({ name, onChanged, canEdit, isAdmin }: {
  name: string; onChanged: () => Promise<void>; canEdit: boolean; isAdmin: boolean
}) {
  const [agents, setAgents] = useState<McpDeploymentAgent[] | null>(null)
  const [unregistered, setUnregistered] = useState<McpUnregistered[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [scopeAgent, setScopeAgent] = useState<McpDeploymentAgent | null>(null)

  const load = useCallback(() => {
    fetchMcpDeploymentAgents(name).then(setAgents).catch(() => setAgents([]))
    fetchUnregisteredMcp().then(setUnregistered).catch(() => setUnregistered([]))
  }, [name])
  useEffect(load, [load])

  const keyOf = (u: McpUnregistered) => `${u.name}|${u.url ?? ""}`

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">이 배포를 사용하는 AI Agent</CardTitle>
          <p className="text-xs text-muted-foreground">
            에이전트별로 허용 그룹·도구(AI Agent 상세 → MCP 서버)와 리소스 범위(아래 “범위 편집”)를 좁힐 수 있습니다.
            정책 번들에 에이전트별로 실려 강제(enforce) 모드에서 서버가 인스턴스 정책 위에 추가로 집행합니다.
          </p>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>에이전트</TableHead>
                <TableHead>상태</TableHead>
                <TableHead>허용 그룹</TableHead>
                <TableHead>허용 도구</TableHead>
                <TableHead className="text-right">실효 도구</TableHead>
                <TableHead>리소스 범위</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {agents === null ? (
                <TableRow><TableCell colSpan={6} className="h-14 text-center text-muted-foreground">불러오는 중...</TableCell></TableRow>
              ) : agents.length === 0 ? (
                <TableRow><TableCell colSpan={6} className="h-14 text-center text-muted-foreground">연결된 에이전트가 없습니다.</TableCell></TableRow>
              ) : agents.map((a) => (
                <TableRow key={a.link_id}>
                  <TableCell>
                    <a href={`/dashboard/ai-agents?agent=${encodeURIComponent(a.agent_name)}`} className="font-medium text-primary hover:underline">
                      {a.agent_display_name || a.agent_name}
                    </a>
                    <div className="text-xs text-muted-foreground">{a.agent_name}</div>
                  </TableCell>
                  <TableCell>{a.agent_status ? <StatusBadge status={a.agent_status} /> : "-"}</TableCell>
                  <TableCell className="text-xs">{a.allowed_groups?.length ? a.allowed_groups.join(", ") : <span className="text-muted-foreground">전체</span>}</TableCell>
                  <TableCell className="text-xs">{a.allowed_tools?.length ? a.allowed_tools.join(", ") : <span className="text-muted-foreground">전체</span>}</TableCell>
                  <TableCell className="text-right">{a.effective_tool_count}</TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      {a.resource_scopes.length ? (
                        <span className="flex flex-wrap gap-1">
                          {a.resource_scopes.slice(0, 3).map((sc) => (
                            <span key={sc.id} className="inline-flex items-center gap-1 text-xs">
                              <EffectBadge effect={sc.effect} />
                              <code className="text-[11px]">{sc.resource_type}:{sc.patterns.join(",")}</code>
                            </span>
                          ))}
                          {a.resource_scopes.length > 3 && (
                            <span className="text-xs text-muted-foreground">+{a.resource_scopes.length - 3}</span>
                          )}
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">인스턴스 범위만</span>
                      )}
                      {canEdit && (
                        <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => setScopeAgent(a)}>
                          범위 편집
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <AgentScopeDialog
        name={name}
        agent={scopeAgent}
        onOpenChange={(v) => !v && setScopeAgent(null)}
        onSaved={load}
      />

      {isAdmin && unregistered.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">미등록 MCP 연결을 이 배포로 연결 (관리자)</CardTitle>
            <p className="text-xs text-muted-foreground">
              AI Agent 에 이름·URL 로 직접 입력된 연결을 이 배포와 연결하면 카탈로그 등록 상태가 되어 실효 도구·정책 번들에 반영됩니다.
            </p>
          </CardHeader>
          <CardContent className="space-y-2">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  <TableHead>이름</TableHead>
                  <TableHead>URL</TableHead>
                  <TableHead>에이전트</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {unregistered.map((u) => {
                  const k = keyOf(u)
                  return (
                    <TableRow key={k}>
                      <TableCell>
                        <input
                          type="checkbox"
                          checked={selected.has(k)}
                          onChange={(e) => setSelected((prev) => {
                            const n = new Set(prev)
                            if (e.target.checked) n.add(k)
                            else n.delete(k)
                            return n
                          })}
                        />
                      </TableCell>
                      <TableCell className="font-medium">{u.name}</TableCell>
                      <TableCell className="break-all font-mono text-xs text-muted-foreground">{u.url || "-"}</TableCell>
                      <TableCell className="text-xs">{u.agents.join(", ")}</TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
            <Button
              size="sm"
              disabled={selected.size === 0}
              onClick={async () => {
                const ids = unregistered.filter((u) => selected.has(keyOf(u))).flatMap((u) => u.link_ids)
                try {
                  const r = await linkUnregisteredMcp(name, ids)
                  toast.success(`${r.linked}개 연결을 이 배포로 연결했습니다.`)
                  setSelected(new Set())
                  load()
                  await onChanged()
                } catch (e) {
                  notifyError(e, "미등록 MCP를 연결하지 못했습니다.")
                }
              }}
            >
              선택 항목 연결
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 수집 이력
// ---------------------------------------------------------------------------

function SyncLogPanel({ name }: { name: string }) {
  const [rows, setRows] = useState<McpSyncLog[] | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  useEffect(() => {
    fetchMcpSyncLog(name).then(setRows).catch(() => setRows([]))
  }, [name])
  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>시각</TableHead>
            <TableHead>종류</TableHead>
            <TableHead>경로</TableHead>
            <TableHead>결과</TableHead>
            <TableHead>메시지</TableHead>
            <TableHead className="text-right">소요</TableHead>
            <TableHead>실행자</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows === null ? (
            <TableRow><TableCell colSpan={7} className="h-14 text-center text-muted-foreground">불러오는 중...</TableCell></TableRow>
          ) : rows.length === 0 ? (
            <TableRow><TableCell colSpan={7} className="h-14 text-center text-muted-foreground">수집 이력이 없습니다.</TableCell></TableRow>
          ) : rows.map((r) => (
            <Fragment key={r.id}>
              <TableRow className={r.diff ? "cursor-pointer" : undefined} onClick={() => r.diff && setOpen(open === r.id ? null : r.id)}>
                <TableCell className="text-xs">{fmtDate(r.created_at)}</TableCell>
                <TableCell className="text-xs">{r.kind}</TableCell>
                <TableCell className="text-xs">{r.source || "-"}</TableCell>
                <TableCell>{r.ok ? <Badge variant="outline" className="text-emerald-700">성공</Badge> : <Badge variant="destructive">실패</Badge>}</TableCell>
                <TableCell className="max-w-md truncate text-xs text-muted-foreground" title={r.message ?? ""}>{r.message || (r.diff ? "변경 내역 보기" : "-")}</TableCell>
                <TableCell className="text-right text-xs">{r.duration_ms != null ? `${r.duration_ms}ms` : "-"}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{r.triggered_by || "-"}</TableCell>
              </TableRow>
              {open === r.id && r.diff && (
                <TableRow className="hover:bg-transparent">
                  <TableCell colSpan={7} className="bg-muted/20"><DiffView diff={r.diff} /></TableCell>
                </TableRow>
              )}
            </Fragment>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 수집 / 설정 가져오기 diff
// ---------------------------------------------------------------------------

function NameList({ label, names, tone }: { label: string; names: unknown; tone: string }) {
  const list = Array.isArray(names) ? (names as string[]) : []
  if (list.length === 0) return null
  return (
    <div className="text-xs">
      <span className={cn("font-medium", tone)}>{label} ({list.length})</span>
      <div className="mt-0.5 flex flex-wrap gap-1">
        {list.map((n) => <code key={n} className="rounded bg-muted px-1 text-[11px]">{n}</code>)}
      </div>
    </div>
  )
}

export function DiffView({ diff }: { diff: Record<string, unknown> }) {
  const tools = (diff.tools ?? {}) as Record<string, unknown>
  const groups = (diff.groups ?? {}) as Record<string, [boolean | null, boolean]>
  const resources = (diff.resources ?? null) as Record<string, unknown> | null
  const prompts = (diff.prompts ?? null) as Record<string, unknown> | null
  const warnings = Array.isArray(diff.security_warnings) ? (diff.security_warnings as string[]) : []
  const groupKeys = Object.keys(groups)
  const newGroups = Array.isArray(diff.new_groups) ? (diff.new_groups as string[]) : []
  const nothing =
    !["added", "changed", "not_exposed", "removed"].some((k) => Array.isArray(tools[k]) && (tools[k] as unknown[]).length) &&
    groupKeys.length === 0 && newGroups.length === 0 &&
    !resources && !prompts && warnings.length === 0 &&
    diff.scopes === undefined
  return (
    <div className="space-y-2">
      {nothing && <p className="text-xs text-muted-foreground">변경 사항이 없습니다.</p>}
      <NameList label="새 도구" names={tools.added} tone="text-emerald-700 dark:text-emerald-400" />
      <NameList label="변경된 도구" names={tools.changed} tone="text-amber-700 dark:text-amber-400" />
      <NameList label="정의에서 제거된 도구" names={tools.removed} tone="text-red-700 dark:text-red-400" />
      <NameList label="노출되지 않는 도구" names={tools.not_exposed} tone="text-zinc-600 dark:text-zinc-400" />
      <NameList label="새 도구 그룹" names={newGroups} tone="text-emerald-700 dark:text-emerald-400" />
      {groupKeys.length > 0 && (
        <div className="text-xs">
          <span className="font-medium">그룹 on/off 변화</span>
          <div className="mt-0.5 flex flex-wrap gap-2">
            {Object.entries(groups).map(([g, [before, after]]) => (
              <span key={g} className="rounded border px-1.5 py-0.5">
                {g}: {before === null ? "미확인" : before ? "켜짐" : "꺼짐"} → {after ? "켜짐" : "꺼짐"}
              </span>
            ))}
          </div>
        </div>
      )}
      {resources && (
        <>
          <NameList label="새 리소스" names={resources.added} tone="text-emerald-700 dark:text-emerald-400" />
          <NameList label="사라진 리소스" names={resources.removed} tone="text-zinc-600 dark:text-zinc-400" />
        </>
      )}
      {prompts && (
        <>
          <NameList label="새 프롬프트" names={prompts.added} tone="text-emerald-700 dark:text-emerald-400" />
          <NameList label="사라진 프롬프트" names={prompts.removed} tone="text-zinc-600 dark:text-zinc-400" />
        </>
      )}
      {diff.scopes !== undefined && (
        <div className="text-xs">리소스 범위 규칙 {String(diff.scopes)}개 · limits {String(diff.limits ?? 0)}개</div>
      )}
      {warnings.length > 0 && (
        <div className="text-xs text-amber-700 dark:text-amber-400">
          <span className="font-medium">보안 경고</span>
          <ul className="list-disc pl-5">{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}
    </div>
  )
}

function CollectDialog({
  open, onOpenChange, dep, onApplied,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  dep: Detail
  onApplied: () => Promise<void>
}) {
  const isForge = dep.server_framework === "argus-forge"
  const [source, setSource] = useState<"auto" | "mcp" | "forge_rest" | "manifest">("auto")
  const [preview, setPreview] = useState<McpCollectResult | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) setPreview(null)
  }, [open])

  const run = async (dryRun: boolean) => {
    setBusy(true)
    try {
      const r = await collectMcpDeployment(dep.name, { source, dry_run: dryRun })
      if (!r.ok) {
        toast.error(r.message || "도구를 수집하지 못했습니다.")
        setPreview(r)
        return
      }
      if (dryRun) {
        setPreview(r)
      } else {
        toast.success(`도구를 수집했습니다: ${r.tool_count}개 (노출 ${r.exposed_count})`)
        onOpenChange(false)
        await onApplied()
      }
    } catch (e) {
      notifyError(e, "도구를 수집하지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>도구·리소스 수집</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <p className="text-xs text-muted-foreground">
            표준 MCP(Streamable HTTP: initialize → tools/list · resources/list · prompts/list)로 수집합니다.
            Argus Forge 서버는 manifest(<code>/manifest</code>)를 먼저 시도하고, 없으면 도구 REST(<code>/tools</code>)로 수집합니다.
            stdio 서버는 실행하지 않습니다.
            먼저 미리보기로 변경 내역을 확인한 뒤 반영하세요.
          </p>
          <div className="flex items-center gap-2">
            <Label className="text-xs">수집 경로</Label>
            <Select value={source} onValueChange={(v) => { setSource(v as typeof source); setPreview(null) }}>
              <SelectTrigger size="sm" className="w-[220px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">자동 ({isForge ? "manifest → Forge REST" : "MCP 프로토콜"})</SelectItem>
                <SelectItem value="mcp">MCP 프로토콜</SelectItem>
                {isForge && <SelectItem value="forge_rest">Forge 도구 REST</SelectItem>}
                <SelectItem value="manifest">manifest (GET /api/v1/manifest)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {!dep.endpoint && !dep.rest_api_base && (
            <p className="text-xs text-destructive">엔드포인트가 없습니다. 먼저 “편집”에서 MCP 엔드포인트를 입력하세요.</p>
          )}
          {preview && (
            <div className="rounded-md border p-3">
              {preview.ok ? (
                <>
                  <div className="mb-2 text-xs text-muted-foreground">
                    경로: {preview.source} · 도구 {preview.tool_count}개 · 노출 {preview.exposed_count}개
                  </div>
                  {preview.message && <div className="mb-2 text-xs text-muted-foreground">{preview.message}</div>}
                  {preview.diff && <DiffView diff={preview.diff} />}
                </>
              ) : (
                <p className="text-sm text-destructive">{preview.message}</p>
              )}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>닫기</Button>
          <Button variant="outline" onClick={() => run(true)} disabled={busy}>
            {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}미리보기
          </Button>
          <Button onClick={() => run(false)} disabled={busy || !preview?.ok}>반영</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ConfigImportDialog({
  open, onOpenChange, dep, onApplied,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  dep: Detail
  onApplied: () => Promise<void>
}) {
  const [content, setContent] = useState("")
  const [fillEndpoint, setFillEndpoint] = useState(!dep.endpoint)
  const [preview, setPreview] = useState<McpCollectResult | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) setPreview(null)
  }, [open])

  const run = async (dryRun: boolean) => {
    setBusy(true)
    try {
      const r = await importMcpConfig(dep.name, { content, dry_run: dryRun, fill_endpoint: fillEndpoint })
      if (dryRun) setPreview(r)
      else {
        toast.success("설정을 반영했습니다.")
        onOpenChange(false)
        setContent("")
        await onApplied()
      }
    } catch (e) {
      notifyError(e, "설정을 가져오지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Argus Forge 설정(config.yaml) 가져오기</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            서버의 config.yaml 을 붙여넣으면 도구 그룹 on/off(<code>allow.*</code>), 리소스 범위(허용·차단·보호 목록), limits,
            대상 시스템·실행 계정을 추출합니다. 비밀번호·토큰 등 비밀값은 저장하지 않습니다.
          </p>
          <Textarea
            value={content}
            onChange={(e) => { setContent(e.target.value); setPreview(null) }}
            rows={14}
            className="font-mono text-xs"
            placeholder={"mcp:\n  host: 0.0.0.0\n  port: 8080\nhive:\n  ...\nallow:\n  write: false"}
          />
          <label className="flex items-center gap-2 text-xs">
            <Switch checked={fillEndpoint} onCheckedChange={setFillEndpoint} />
            엔드포인트가 비어 있으면 설정의 host/port 로 채우기
          </label>
          {preview && (
            <div className="rounded-md border p-3">
              {preview.diff ? <DiffView diff={preview.diff} /> : <p className="text-sm">{preview.message}</p>}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>닫기</Button>
          <Button variant="outline" onClick={() => run(true)} disabled={busy || !content.trim()}>미리보기</Button>
          <Button onClick={() => run(false)} disabled={busy || !preview}>반영</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// 편집 다이얼로그
// ---------------------------------------------------------------------------

type DepEdit = {
  display_name: string
  description: string
  environment: string
  status: string
  status_note: string
  transport: string
  endpoint: string
  rest_api_base: string
  auth_method: string
  deployment_scope: string
  host: string
  collect_interval_minutes: string
  audit_retention_days: string
  owner_email: string
  department: string
}

function toDepEdit(d: Detail): DepEdit {
  return {
    display_name: d.display_name ?? "",
    description: d.description ?? "",
    environment: d.environment,
    status: d.status,
    status_note: "",
    transport: d.transport,
    endpoint: d.endpoint ?? "",
    rest_api_base: d.rest_api_base ?? "",
    auth_method: d.auth_method,
    deployment_scope: d.deployment_scope,
    host: d.host ?? "",
    collect_interval_minutes: d.collect_interval_minutes != null ? String(d.collect_interval_minutes) : "",
    audit_retention_days: String(d.audit_retention_days ?? 180),
    owner_email: d.owner_email ?? "",
    department: d.department ?? "",
  }
}

function SelectField({ label, value, options, onChange }: {
  label: string
  value: string
  options: readonly string[]
  onChange: (v: string) => void
}) {
  return (
    <div className="grid gap-1.5">
      <Label className="flex items-center gap-1.5">{label}<OriginBadge origin="argus" /></Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger><SelectValue /></SelectTrigger>
        <SelectContent>
          {options.map((o) => <SelectItem key={o} value={o}>{STATUS_LABEL[o] ?? o}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  )
}

function A({ children }: { children: React.ReactNode }) {
  return <Label className="flex items-center gap-1.5">{children}<OriginBadge origin="argus" /></Label>
}

function DeploymentEditDialog({
  open, onOpenChange, dep, onSaved,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  dep: Detail
  onSaved: () => Promise<void>
}) {
  const [f, setF] = useState<DepEdit>(() => toDepEdit(dep))
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (open) setF(toDepEdit(dep))
  }, [open, dep])
  const set = (k: keyof DepEdit) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setF((p) => ({ ...p, [k]: e.target.value }))
  const sel = (k: keyof DepEdit) => (v: string) => setF((p) => ({ ...p, [k]: v }))
  const n = (v: string) => v.trim() || null

  const save = async () => {
    setSaving(true)
    try {
      const payload: McpDeploymentPayload = {
        display_name: n(f.display_name),
        description: n(f.description),
        environment: f.environment,
        status: f.status,
        transport: f.transport,
        endpoint: n(f.endpoint),
        rest_api_base: n(f.rest_api_base),
        auth_method: f.auth_method,
        deployment_scope: f.deployment_scope,
        host: n(f.host),
        collect_interval_minutes: f.collect_interval_minutes.trim() ? Number(f.collect_interval_minutes) : null,
        owner_email: n(f.owner_email),
        department: n(f.department),
      }
      const retention = Number(f.audit_retention_days)
      if (f.audit_retention_days.trim() && retention !== dep.audit_retention_days) payload.audit_retention_days = retention
      if (f.status !== dep.status && f.status_note.trim()) payload.status_note = f.status_note.trim()
      await updateMcpDeployment(dep.name, payload)
      toast.success("저장했습니다.")
      onOpenChange(false)
      await onSaved()
    } catch (e) {
      notifyError(e, "MCP 배포를 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>MCP 배포 편집</DialogTitle>
        </DialogHeader>
        <div className="grid max-h-[65vh] gap-3 overflow-y-auto py-1 pr-1 sm:grid-cols-2">
          <div className="grid gap-1.5"><A>표시명</A><Input value={f.display_name} onChange={set("display_name")} /></div>
          <SelectField label="환경" value={f.environment} options={ENVIRONMENTS} onChange={sel("environment")} />
          <SelectField label="상태" value={f.status} options={STATUS_OPTIONS} onChange={sel("status")} />
          <div className="grid gap-1.5">
            <A>상태 변경 메모</A>
            <Input value={f.status_note} onChange={set("status_note")} disabled={f.status === dep.status} />
          </div>
          <SelectField label="전송" value={f.transport} options={TRANSPORTS} onChange={sel("transport")} />
          <SelectField label="인증 방식" value={f.auth_method} options={AUTH_METHODS} onChange={sel("auth_method")} />
          <div className="grid gap-1.5 sm:col-span-2"><A>MCP 엔드포인트</A><Input value={f.endpoint} onChange={set("endpoint")} placeholder="https://host:port/mcp" /></div>
          {dep.server_framework === "argus-forge" && (
            <div className="grid gap-1.5 sm:col-span-2"><A>도구 REST API (Forge)</A><Input value={f.rest_api_base} onChange={set("rest_api_base")} placeholder="https://host:port/api/v1" /></div>
          )}
          <SelectField label="배포 범위" value={f.deployment_scope} options={["shared", "external", "local"]} onChange={sel("deployment_scope")} />
          <div className="grid gap-1.5"><A>호스트</A><Input value={f.host} onChange={set("host")} /></div>
          <div className="grid gap-1.5"><A>자동 수집 주기(분, 비우면 수동)</A><Input type="number" min={0} value={f.collect_interval_minutes} onChange={set("collect_interval_minutes")} /></div>
          <div className="grid gap-1.5"><A>감사 이벤트 보존(일)</A><Input type="number" min={1} max={3650} value={f.audit_retention_days} onChange={set("audit_retention_days")} /></div>
          <p className="text-xs text-muted-foreground sm:col-span-2">정책 모드(observe/enforce)는 “승인 정책” 탭에서 변경합니다 (이력이 남음).</p>
          <div className="grid gap-1.5"><A>소유자 이메일</A><Input value={f.owner_email} onChange={set("owner_email")} /></div>
          <div className="grid gap-1.5"><A>부서</A><Input value={f.department} onChange={set("department")} /></div>
          <div className="grid gap-1.5 sm:col-span-2"><A>설명</A><Textarea value={f.description} onChange={set("description")} rows={2} /></div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>취소</Button>
          <Button onClick={save} disabled={saving}>{saving ? "저장 중..." : "저장"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
