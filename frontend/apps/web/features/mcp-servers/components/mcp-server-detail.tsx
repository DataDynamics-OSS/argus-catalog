"use client"

import { Fragment, useCallback, useEffect, useState } from "react"
import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  FileJson,
  Loader2,
  Pencil,
  Plus,
  Server,
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

import { useAuth } from "@/features/auth"
import {
  addMcpPrompt,
  addMcpResource,
  addMcpTool,
  deleteMcpPrompt,
  deleteMcpResource,
  deleteMcpTool,
  fetchMcpServer,
  fetchMcpServerJson,
  fetchMcpServerStatusHistory,
  retireMcpServer,
  updateMcpServer,
  updateMcpTool,
  type McpServerPayload,
} from "../api"
import {
  ARGUS_META_NAMESPACE,
  FRAMEWORK_LABEL,
  PROVIDER_LABEL,
  PROVIDER_TYPES,
  RISK_LABEL,
  RISK_OPTIONS,
  STATUS_LABEL,
  STATUS_OPTIONS,
  type McpServerDetail as Detail,
  type McpStatusHistory,
  type McpTool,
} from "../data/schema"
import {
  FieldRow,
  OriginBadge,
  OriginLegend,
  RiskBadge,
  RowSourceBadge,
  StatusBadge,
  fmtDate,
} from "./mcp-badges"
import { DeploymentHeader, DeploymentRows } from "./mcp-list"
import { useMcpServers } from "./mcp-provider"
import { ManifestImportDialog } from "./tabs/manifest-dialog"
import { VersionsPanel } from "./tabs/versions-panel"
import { usePermissions } from "@/features/permissions/use-permissions"

export function JsonBlock({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="text-muted-foreground">-</span>
  return (
    <pre className="max-h-80 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-xs leading-relaxed">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function Tags({ items }: { items: string[] | null | undefined }) {
  if (!items || items.length === 0) return <span className="text-muted-foreground">-</span>
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((t) => <Badge key={t} variant="secondary" className="text-[11px]">{t}</Badge>)}
    </div>
  )
}

function Link({ href }: { href: string | null }) {
  if (!href) return <span className="text-muted-foreground">-</span>
  return (
    <a href={href} target="_blank" rel="noreferrer" className="break-all text-primary hover:underline">
      {href}
    </a>
  )
}

export function McpServerDetail({ name }: { name: string }) {
  const canRegisterDeployment = usePermissions().canUseFeature("mcp.register")
  const { setSelectedServer, setSelectedDeployment, setOpen, setAddDeploymentServer, reload, reloadKey } =
    useMcpServers()
  const askConfirm = useConfirm()
  const { user } = useAuth()
  const [server, setServer] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editOpen, setEditOpen] = useState(false)
  const [manifestOpen, setManifestOpen] = useState(false)

  const load = useCallback(async () => {
    try {
      setServer(await fetchMcpServer(name))
      setError(null)
    } catch (e) {
      setError(errorMessage(e) ?? "MCP 서버 정보를 불러오지 못했습니다.")
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
        <Button variant="ghost" size="sm" onClick={() => setSelectedServer(null)}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 목록
        </Button>
        <p className="text-sm text-destructive">{error}</p>
      </div>
    )
  }
  if (!server) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const activeTools = server.tools.filter((t) => !t.removed_at)
  // 서버 정의 수정·폐기·도구/리소스/프롬프트 변경은 등록자·관리자만 — 서버(_guard_server)와 같은 기준
  const canEdit = !!user && (user.is_admin || (!!server.created_by && server.created_by === user.username))

  return (
    <div className="flex flex-col gap-4">
      {/* 헤더 */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-2">
          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setSelectedServer(null)} aria-label="목록">
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <Server className="h-5 w-5 text-muted-foreground" />
              <h2 className="text-lg font-semibold">{server.display_name || server.title || server.name}</h2>
              <StatusBadge status={server.status} />
              <Badge variant="outline">{FRAMEWORK_LABEL[server.framework] ?? server.framework}</Badge>
              <Badge variant="outline">v{server.version}</Badge>
            </div>
            <div className="mt-0.5 font-mono text-xs text-muted-foreground">{server.urn}</div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {canEdit && (
            <>
              <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
                <Pencil className="mr-1 h-4 w-4" /> 편집
              </Button>
              <Button size="sm" variant="outline" onClick={() => setManifestOpen(true)}>
                <FileJson className="mr-1 h-4 w-4" /> manifest
              </Button>
            </>
          )}
          {canRegisterDeployment && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setAddDeploymentServer(server.name)
              setOpen("add-deployment")
            }}
          >
            <Plus className="mr-1 h-4 w-4" /> 배포 등록
          </Button>
          )}
          {canEdit && (
          <Button
            size="sm"
            variant="outline"
            className="text-destructive"
            disabled={server.status === "retired"}
            onClick={async () => {
              if (!(await askConfirm({
                title: "서버 정의 폐기",
                description: `'${server.name}' 서버 정의를 폐기할까요? (배포가 남아 있으면 거부됩니다)`,
                confirmText: "폐기",
                destructive: true,
              }))) return
              try {
                await retireMcpServer(server.name)
                toast.success("서버 정의를 폐기했습니다.")
                reload()
              } catch (e) {
                notifyError(e, "서버 정의를 폐기하지 못했습니다.")
              }
            }}
          >
            <Trash2 className="mr-1 h-4 w-4" /> 폐기
          </Button>
          )}
        </div>
      </div>
      <OriginLegend />

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">개요</TabsTrigger>
          <TabsTrigger value="tools">도구 ({activeTools.length})</TabsTrigger>
          <TabsTrigger value="resources">리소스·프롬프트</TabsTrigger>
          <TabsTrigger value="deployments">배포 ({server.deployments.length})</TabsTrigger>
          <TabsTrigger value="versions">버전 이력</TabsTrigger>
          <TabsTrigger value="export">server.json</TabsTrigger>
          <TabsTrigger value="history">이력</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                카탈로그 관리 정보 <OriginBadge origin="argus" />
              </CardTitle>
            </CardHeader>
            <CardContent className="divide-y">
              <FieldRow label="이름" origin="argus"><span className="font-mono">{server.name}</span></FieldRow>
              <FieldRow label="표시명" origin="argus">{server.display_name}</FieldRow>
              <FieldRow label="상태" origin="argus"><StatusBadge status={server.status} /></FieldRow>
              <FieldRow label="버전" origin="argus">{server.version}</FieldRow>
              <FieldRow label="제공자 유형" origin="argus">{PROVIDER_LABEL[server.provider_type] ?? server.provider_type}</FieldRow>
              <FieldRow label="벤더" origin="argus">{server.vendor}</FieldRow>
              <FieldRow label="프레임워크" origin="argus">{FRAMEWORK_LABEL[server.framework] ?? server.framework}</FieldRow>
              <FieldRow label="대상 플랫폼" origin="argus">{server.target_platform}</FieldRow>
              <FieldRow label="소유자" origin="argus">{server.owner_email}</FieldRow>
              <FieldRow label="부서" origin="argus">{server.department}</FieldRow>
              <FieldRow label="카테고리" origin="argus">{server.category}</FieldRow>
              <FieldRow label="태그" origin="argus"><Tags items={server.tags} /></FieldRow>
              <FieldRow label="지원 전송" origin="argus"><Tags items={server.supported_transports} /></FieldRow>
              <FieldRow label="소스 저장소" origin="argus">
                {server.source_repo ? <span className="font-mono text-xs">{server.source_repo}{server.source_path ? ` · ${server.source_path}` : ""}</span> : null}
              </FieldRow>
              <FieldRow label="패키지 체크섬" origin="argus">
                {server.package_checksum ? <span className="font-mono text-xs">{server.package_checksum}</span> : null}
              </FieldRow>
              <FieldRow label="OCI 아티팩트" origin="argus">{server.oci_artifact_ref}</FieldRow>
              <FieldRow label="최근 수집" origin="argus">{fmtDate(server.last_synced_at)}</FieldRow>
              <FieldRow label="등록/수정" origin="argus">
                <span className="text-xs text-muted-foreground">
                  {server.created_by || "-"} · {fmtDate(server.created_at)} / {server.updated_by || "-"} · {fmtDate(server.updated_at)}
                </span>
              </FieldRow>
            </CardContent>
          </Card>
          <div className="flex flex-col gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">
                  MCP Registry 메타데이터 <OriginBadge origin="registry" />
                </CardTitle>
              </CardHeader>
              <CardContent className="divide-y">
                <FieldRow label="Registry 이름" origin="registry">
                  {server.registry_name ? <span className="font-mono text-xs">{server.registry_name}</span> : null}
                </FieldRow>
                <FieldRow label="제목" origin="registry">{server.title}</FieldRow>
                <FieldRow label="설명" origin="registry">
                  {server.description ? <span className="whitespace-pre-wrap">{server.description}</span> : null}
                </FieldRow>
                <FieldRow label="웹사이트" origin="registry"><Link href={server.website_url} /></FieldRow>
                <FieldRow label="저장소" origin="registry"><Link href={server.repository_url} /></FieldRow>
                <FieldRow label="라이선스" origin="registry">{server.license}</FieldRow>
                <FieldRow label="packages" origin="registry">
                  {server.packages?.length ? <JsonBlock value={server.packages} /> : null}
                </FieldRow>
                <FieldRow label="remotes" origin="registry">
                  {server.remotes?.length ? <JsonBlock value={server.remotes} /> : null}
                </FieldRow>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">
                  MCP 서버 보고값 (initialize) <OriginBadge origin="mcp" />
                </CardTitle>
              </CardHeader>
              <CardContent className="divide-y">
                <FieldRow label="serverInfo" origin="mcp">
                  {server.server_info ? <JsonBlock value={server.server_info} /> : null}
                </FieldRow>
                <FieldRow label="프로토콜 버전" origin="mcp">{server.protocol_version}</FieldRow>
                <FieldRow label="capabilities" origin="mcp">
                  {server.capabilities ? <JsonBlock value={server.capabilities} /> : null}
                </FieldRow>
                <FieldRow label="instructions" origin="mcp">
                  {server.instructions ? <span className="whitespace-pre-wrap text-xs">{server.instructions}</span> : null}
                </FieldRow>
                <FieldRow label="capability 해시" origin="argus">
                  {server.capability_hash ? <span className="font-mono text-[11px]">{server.capability_hash.slice(0, 16)}…</span> : null}
                </FieldRow>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="tools" className="mt-4">
          <ToolsPanel server={server} onChanged={load} canEdit={canEdit} />
        </TabsContent>

        <TabsContent value="resources" className="mt-4">
          <ResourcesPanel server={server} onChanged={load} canEdit={canEdit} />
        </TabsContent>

        <TabsContent value="deployments" className="mt-4">
          <div className="rounded-md border">
            <Table>
              <DeploymentHeader showServer={false} />
              <TableBody>
                {server.deployments.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={11} className="h-20 text-center text-muted-foreground">
                      배포가 없습니다. “배포 등록”으로 실제 운영 인스턴스(엔드포인트)를 등록하세요.
                    </TableCell>
                  </TableRow>
                ) : (
                  <DeploymentRows items={server.deployments} onOpen={setSelectedDeployment} showServer={false} />
                )}
              </TableBody>
            </Table>
          </div>
        </TabsContent>

        <TabsContent value="versions" className="mt-4">
          <VersionsPanel name={server.name} refreshKey={server.capability_hash} />
        </TabsContent>

        <TabsContent value="export" className="mt-4">
          <ServerJsonPanel name={server.name} />
        </TabsContent>

        <TabsContent value="history" className="mt-4">
          <StatusHistoryPanel load={() => fetchMcpServerStatusHistory(server.name)} />
        </TabsContent>
      </Tabs>

      <ServerEditDialog open={editOpen} onOpenChange={setEditOpen} server={server} onSaved={load} />
      <ManifestImportDialog open={manifestOpen} onOpenChange={setManifestOpen} target="server" name={server.name} onApplied={load} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// 도구
// ---------------------------------------------------------------------------

const UNSET = "__unset__"

function ToolsPanel({ server, onChanged, canEdit }: { server: Detail; onChanged: () => Promise<void>; canEdit: boolean }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [showRemoved, setShowRemoved] = useState(false)
  const tools = server.tools.filter((t) => showRemoved || !t.removed_at)
  const groups = [...server.groups].sort((a, b) => a.sort_order - b.sort_order)

  const patch = async (t: McpTool, payload: Parameters<typeof updateMcpTool>[2]) => {
    try {
      await updateMcpTool(server.name, t.id, payload)
      await onChanged()
    } catch (e) {
      notifyError(e, "도구를 수정하지 못했습니다.")
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            도구 그룹 ({groups.length}) <OriginBadge origin="argus" />
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            그룹은 서버가 도구를 켜고 끄는 단위입니다. Argus Forge 서버는 설정 키(<code>allow.*</code>)로 그룹 단위 등록 여부가 결정되며,
            일반 MCP 서버는 tool annotations(readOnlyHint / destructiveHint) 기준으로 자동 분류합니다.
          </p>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>그룹</TableHead>
                <TableHead>설정 키 <OriginBadge origin="server_config" className="ml-0.5 align-middle" /></TableHead>
                <TableHead>변경</TableHead>
                <TableHead>기본 활성 <OriginBadge origin="server_config" className="ml-0.5 align-middle" /></TableHead>
                <TableHead>기본 위험도 <OriginBadge origin="argus" className="ml-0.5 align-middle" /></TableHead>
                <TableHead className="text-right">도구 수</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.length === 0 ? (
                <TableRow><TableCell colSpan={6} className="h-14 text-center text-muted-foreground">그룹이 없습니다.</TableCell></TableRow>
              ) : groups.map((g) => (
                <TableRow key={g.id}>
                  <TableCell>
                    <div className="font-medium">{g.label || g.key}</div>
                    <div className="font-mono text-[11px] text-muted-foreground">{g.key}</div>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{g.config_key || "-"}</TableCell>
                  <TableCell>{g.mutating ? <Badge variant="outline" className="border-orange-300 text-orange-700 dark:text-orange-300">변경</Badge> : <span className="text-xs text-muted-foreground">읽기</span>}</TableCell>
                  <TableCell className="text-sm">{g.default_enabled ? "켜짐" : "꺼짐"}</TableCell>
                  <TableCell><RiskBadge risk={g.default_risk} /></TableCell>
                  <TableCell className="text-right">{g.tool_count}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="text-sm">도구</CardTitle>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <Switch checked={showRemoved} onCheckedChange={setShowRemoved} />
              서버에서 사라진 도구 포함
            </label>
          </div>
          <p className="text-xs text-muted-foreground">
            이름·설명·입력 스키마·annotations 는 MCP 서버가 보고한 값 <OriginBadge origin="mcp" className="align-middle" />,
            그룹·위험도·승인 필요·리소스 유형은 Argus 에서 관리하는 값 <OriginBadge origin="argus" className="align-middle" /> 입니다.
          </p>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-6" />
                <TableHead>도구 <OriginBadge origin="mcp" className="ml-0.5 align-middle" /></TableHead>
                <TableHead>그룹 <OriginBadge origin="argus" className="ml-0.5 align-middle" /></TableHead>
                <TableHead>위험도 <OriginBadge origin="argus" className="ml-0.5 align-middle" /></TableHead>
                <TableHead>승인 필요 <OriginBadge origin="argus" className="ml-0.5 align-middle" /></TableHead>
                <TableHead>출처</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {tools.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="h-16 text-center text-muted-foreground">
                    도구가 없습니다. 배포를 등록하고 “수집”을 실행하면 서버가 보고한 도구가 채워집니다.
                  </TableCell>
                </TableRow>
              ) : tools.map((t) => {
                const open = expanded.has(t.id)
                return (
                  <Fragment key={t.id}>
                    <TableRow className={t.removed_at ? "opacity-50" : undefined}>
                      <TableCell>
                        <button
                          type="button"
                          aria-label="펼치기"
                          onClick={() => setExpanded((prev) => {
                            const n = new Set(prev)
                            if (n.has(t.id)) n.delete(t.id)
                            else n.add(t.id)
                            return n
                          })}
                        >
                          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </button>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono text-sm">{t.name}</span>
                          {t.mutating && <Badge variant="outline" className="h-4 border-orange-300 px-1 text-[10px] text-orange-700 dark:text-orange-300">변경</Badge>}
                          {t.removed_at && <Badge variant="outline" className="h-4 px-1 text-[10px]">사라짐</Badge>}
                        </div>
                        {t.description && (
                          <div className="line-clamp-1 max-w-xl text-xs text-muted-foreground">{t.description}</div>
                        )}
                      </TableCell>
                      <TableCell>
                        <Select
                          disabled={!canEdit}
                          value={t.group_key ?? UNSET}
                          onValueChange={(v) => patch(t, { group_key: v === UNSET ? null : v })}
                        >
                          <SelectTrigger size="sm" className="h-7 w-[130px] text-xs"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value={UNSET}>미지정</SelectItem>
                            {groups.map((g) => <SelectItem key={g.key} value={g.key}>{g.label || g.key}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <Select
                            disabled={!canEdit}
                            value={t.risk ?? UNSET}
                            onValueChange={(v) => patch(t, { risk: v === UNSET ? null : v, risk_confirmed: v !== UNSET })}
                          >
                            <SelectTrigger size="sm" className="h-7 w-[90px] text-xs"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value={UNSET}>미분류</SelectItem>
                              {RISK_OPTIONS.map((r) => <SelectItem key={r} value={r}>{RISK_LABEL[r]}</SelectItem>)}
                            </SelectContent>
                          </Select>
                          {!t.risk_confirmed && t.risk && (
                            <span className="text-[10px] text-muted-foreground" title="그룹 기본값/annotations 로 추정된 위험도입니다. 선택하면 확정됩니다.">추정</span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Switch
                          disabled={!canEdit}
                          checked={t.requires_approval}
                          onCheckedChange={(v) => patch(t, { requires_approval: v })}
                        />
                      </TableCell>
                      <TableCell><RowSourceBadge source={t.source} /></TableCell>
                      <TableCell>
                        {canEdit && t.source === "manual" && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            aria-label="삭제"
                            onClick={async () => {
                              try {
                                await deleteMcpTool(server.name, t.id)
                                await onChanged()
                              } catch (e) {
                                notifyError(e, "도구를 삭제하지 못했습니다.")
                              }
                            }}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                    {open && (
                      <TableRow className="hover:bg-transparent">
                        <TableCell />
                        <TableCell colSpan={6} className="space-y-1 bg-muted/20">
                          <FieldRow label="title" origin="mcp">{t.title}</FieldRow>
                          <FieldRow label="description" origin="mcp">
                            {t.description ? <span className="whitespace-pre-wrap text-xs">{t.description}</span> : null}
                          </FieldRow>
                          <FieldRow label="annotations" origin="mcp">{t.annotations ? <JsonBlock value={t.annotations} /> : null}</FieldRow>
                          <FieldRow label="inputSchema" origin="mcp">{t.input_schema ? <JsonBlock value={t.input_schema} /> : null}</FieldRow>
                          <FieldRow label="outputSchema" origin="mcp">{t.output_schema ? <JsonBlock value={t.output_schema} /> : null}</FieldRow>
                          <FieldRow label="리소스 유형" origin="argus">
                            <Tags items={t.resource_types} />
                          </FieldRow>
                          <FieldRow label="메모" origin="argus">
                            {canEdit
                              ? <ToolNotes tool={t} onSave={(notes) => patch(t, { notes })} />
                              : t.notes ? <span className="whitespace-pre-wrap text-xs">{t.notes}</span> : null}
                          </FieldRow>
                          {t.removed_at && <FieldRow label="사라진 시각" origin="argus">{fmtDate(t.removed_at)}</FieldRow>}
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                )
              })}
              {canEdit && <AddToolRow serverName={server.name} groups={groups.map((g) => g.key)} onAdded={onChanged} />}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

function ToolNotes({ tool, onSave }: { tool: McpTool; onSave: (v: string | null) => void }) {
  const [v, setV] = useState(tool.notes ?? "")
  return (
    <div className="flex items-start gap-2">
      <Textarea value={v} onChange={(e) => setV(e.target.value)} rows={2} className="text-xs" placeholder="운영 메모 (Argus 전용)" />
      <Button size="sm" variant="outline" disabled={v === (tool.notes ?? "")} onClick={() => onSave(v.trim() || null)}>
        저장
      </Button>
    </div>
  )
}

function AddToolRow({ serverName, groups, onAdded }: { serverName: string; groups: string[]; onAdded: () => Promise<void> }) {
  const [name, setName] = useState("")
  const [group, setGroup] = useState(UNSET)
  const [risk, setRisk] = useState(UNSET)
  const [busy, setBusy] = useState(false)
  return (
    <TableRow className="hover:bg-transparent">
      <TableCell />
      <TableCell>
        <Input placeholder="도구 이름 (수동 등록)" value={name} onChange={(e) => setName(e.target.value)} className="h-8" />
      </TableCell>
      <TableCell>
        <Select value={group} onValueChange={setGroup}>
          <SelectTrigger size="sm" className="h-7 w-[130px] text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={UNSET}>미지정</SelectItem>
            {groups.map((g) => <SelectItem key={g} value={g}>{g}</SelectItem>)}
          </SelectContent>
        </Select>
      </TableCell>
      <TableCell>
        <Select value={risk} onValueChange={setRisk}>
          <SelectTrigger size="sm" className="h-7 w-[90px] text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={UNSET}>미분류</SelectItem>
            {RISK_OPTIONS.map((r) => <SelectItem key={r} value={r}>{RISK_LABEL[r]}</SelectItem>)}
          </SelectContent>
        </Select>
      </TableCell>
      <TableCell colSpan={2} className="text-xs text-muted-foreground">수집 전 사전 등록용</TableCell>
      <TableCell>
        <Button
          size="icon"
          className="h-7 w-7"
          aria-label="도구 추가"
          disabled={busy || !name.trim()}
          onClick={async () => {
            setBusy(true)
            try {
              await addMcpTool(serverName, {
                name: name.trim(),
                group_key: group === UNSET ? undefined : group,
                risk: risk === UNSET ? undefined : risk,
              })
              setName("")
              await onAdded()
            } catch (e) {
              notifyError(e, "도구를 등록하지 못했습니다.")
            } finally {
              setBusy(false)
            }
          }}
        >
          <Plus className="h-4 w-4" />
        </Button>
      </TableCell>
    </TableRow>
  )
}

// ---------------------------------------------------------------------------
// 리소스 · 프롬프트
// ---------------------------------------------------------------------------

function ResourcesPanel({ server, onChanged, canEdit }: { server: Detail; onChanged: () => Promise<void>; canEdit: boolean }) {
  const [uri, setUri] = useState("")
  const [promptName, setPromptName] = useState("")
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            리소스 ({server.resources.length}) <OriginBadge origin="mcp" />
          </CardTitle>
          <p className="text-xs text-muted-foreground">resources/list · resources/templates/list 로 수집됩니다.</p>
        </CardHeader>
        <CardContent className="space-y-2">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>URI</TableHead>
                <TableHead>이름</TableHead>
                <TableHead>MIME</TableHead>
                <TableHead>출처</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {server.resources.length === 0 ? (
                <TableRow><TableCell colSpan={5} className="h-14 text-center text-muted-foreground">리소스가 없습니다.</TableCell></TableRow>
              ) : server.resources.map((r) => (
                <TableRow key={r.id} className={r.removed_at ? "opacity-50" : undefined}>
                  <TableCell className="break-all font-mono text-xs">
                    {r.uri}
                    {r.is_template && <Badge variant="outline" className="ml-1 h-4 px-1 text-[10px]">template</Badge>}
                  </TableCell>
                  <TableCell className="text-sm">{r.title || r.name || "-"}</TableCell>
                  <TableCell className="text-xs">{r.mime_type || "-"}</TableCell>
                  <TableCell><RowSourceBadge source={r.source} /></TableCell>
                  <TableCell>
                    {/* 수동 등록 행만 삭제 — 수집된 리소스는 다음 수집에서 다시 들어온다(도구와 같은 기준) */}
                    {canEdit && r.source === "manual" && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        aria-label="삭제"
                        onClick={async () => {
                          try {
                            await deleteMcpResource(server.name, r.id)
                            await onChanged()
                          } catch (e) {
                            notifyError(e, "리소스를 삭제하지 못했습니다.")
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
          <div className="flex gap-2">
            <Input placeholder="리소스 URI 수동 등록 (예: file:///logs/{date})" value={uri} onChange={(e) => setUri(e.target.value)} className="h-8" />
            <Button
              size="sm"
              disabled={!uri.trim()}
              onClick={async () => {
                try {
                  await addMcpResource(server.name, { uri: uri.trim(), is_template: uri.includes("{") })
                  setUri("")
                  await onChanged()
                } catch (e) {
                  notifyError(e, "리소스를 등록하지 못했습니다.")
                }
              }}
            >
              추가
            </Button>
          </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            프롬프트 ({server.prompts.length}) <OriginBadge origin="mcp" />
          </CardTitle>
          <p className="text-xs text-muted-foreground">prompts/list 로 수집됩니다.</p>
        </CardHeader>
        <CardContent className="space-y-2">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>이름</TableHead>
                <TableHead>설명</TableHead>
                <TableHead>인자</TableHead>
                <TableHead>출처</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {server.prompts.length === 0 ? (
                <TableRow><TableCell colSpan={5} className="h-14 text-center text-muted-foreground">프롬프트가 없습니다.</TableCell></TableRow>
              ) : server.prompts.map((p) => (
                <TableRow key={p.id} className={p.removed_at ? "opacity-50" : undefined}>
                  <TableCell className="font-mono text-sm">{p.name}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{p.description || "-"}</TableCell>
                  <TableCell className="text-xs">
                    {(p.arguments ?? []).map((a) => String((a as { name?: string }).name ?? "")).filter(Boolean).join(", ") || "-"}
                  </TableCell>
                  <TableCell><RowSourceBadge source={p.source} /></TableCell>
                  <TableCell>
                    {/* 수동 등록 행만 삭제 — 수집된 프롬프트는 다음 수집에서 다시 들어온다(도구와 같은 기준) */}
                    {canEdit && p.source === "manual" && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        aria-label="삭제"
                        onClick={async () => {
                          try {
                            await deleteMcpPrompt(server.name, p.id)
                            await onChanged()
                          } catch (e) {
                            notifyError(e, "프롬프트를 삭제하지 못했습니다.")
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
          <div className="flex gap-2">
            <Input placeholder="프롬프트 이름 수동 등록" value={promptName} onChange={(e) => setPromptName(e.target.value)} className="h-8" />
            <Button
              size="sm"
              disabled={!promptName.trim()}
              onClick={async () => {
                try {
                  await addMcpPrompt(server.name, { name: promptName.trim() })
                  setPromptName("")
                  await onChanged()
                } catch (e) {
                  notifyError(e, "프롬프트를 등록하지 못했습니다.")
                }
              }}
            >
              추가
            </Button>
          </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// server.json 내보내기
// ---------------------------------------------------------------------------

function ServerJsonPanel({ name }: { name: string }) {
  const [doc, setDoc] = useState<Record<string, unknown> | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    fetchMcpServerJson(name).then(setDoc).catch((e) => setErr(errorMessage(e) ?? "server.json을 불러오지 못했습니다."))
  }, [name])
  const text = doc ? JSON.stringify(doc, null, 2) : ""
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-sm">MCP Registry server.json</CardTitle>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={!doc}
              onClick={() => {
                navigator.clipboard.writeText(text)
                toast.success("복사했습니다.")
              }}
            >
              <Copy className="mr-1 h-4 w-4" /> 복사
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={!doc}
              onClick={() => {
                const blob = new Blob([text], { type: "application/json" })
                const a = document.createElement("a")
                a.href = URL.createObjectURL(blob)
                a.download = `${name}.server.json`
                a.click()
                URL.revokeObjectURL(a.href)
              }}
            >
              <Download className="mr-1 h-4 w-4" /> 다운로드
            </Button>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          표준 필드는 다른 MCP Registry·클라이언트와 그대로 호환됩니다. Argus 전용 관리 정보 <OriginBadge origin="argus" className="align-middle" />
          는 <code className="rounded bg-muted px-1">_meta[&quot;{ARGUS_META_NAMESPACE}&quot;]</code> 아래에만 포함되며,
          다시 가져오면 복원됩니다.
        </p>
      </CardHeader>
      <CardContent>
        {err ? <p className="text-sm text-destructive">{err}</p> : <JsonBlock value={doc} />}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// 상태 이력
// ---------------------------------------------------------------------------

export function StatusHistoryPanel({ load }: { load: () => Promise<McpStatusHistory[]> }) {
  const [rows, setRows] = useState<McpStatusHistory[] | null>(null)
  useEffect(() => {
    load().then(setRows).catch(() => setRows([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>시각</TableHead>
            <TableHead>변경</TableHead>
            <TableHead>메모</TableHead>
            <TableHead>변경자</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows === null ? (
            <TableRow><TableCell colSpan={4} className="h-14 text-center text-muted-foreground">불러오는 중...</TableCell></TableRow>
          ) : rows.length === 0 ? (
            <TableRow><TableCell colSpan={4} className="h-14 text-center text-muted-foreground">상태 변경 이력이 없습니다.</TableCell></TableRow>
          ) : rows.map((h) => (
            <TableRow key={h.id}>
              <TableCell className="text-xs">{fmtDate(h.changed_at)}</TableCell>
              <TableCell className="text-sm">
                {h.from_status ? <StatusBadge status={h.from_status} /> : "-"} → <StatusBadge status={h.to_status} />
              </TableCell>
              <TableCell className="text-sm">{h.note || "-"}</TableCell>
              <TableCell className="text-xs text-muted-foreground">{h.changed_by || "-"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 편집 다이얼로그
// ---------------------------------------------------------------------------

type EditState = {
  display_name: string
  status: string
  status_note: string
  version: string
  owner_email: string
  department: string
  category: string
  tags: string
  provider_type: string
  vendor: string
  target_platform: string
  registry_name: string
  title: string
  description: string
  website_url: string
  repository_url: string
  license: string
}

function toEdit(s: Detail): EditState {
  return {
    display_name: s.display_name ?? "",
    status: s.status,
    status_note: "",
    version: s.version,
    owner_email: s.owner_email ?? "",
    department: s.department ?? "",
    category: s.category ?? "",
    tags: (s.tags ?? []).join(", "),
    provider_type: s.provider_type,
    vendor: s.vendor ?? "",
    target_platform: s.target_platform ?? "",
    registry_name: s.registry_name ?? "",
    title: s.title ?? "",
    description: s.description ?? "",
    website_url: s.website_url ?? "",
    repository_url: s.repository_url ?? "",
    license: s.license ?? "",
  }
}

function L({ children, origin }: { children: React.ReactNode; origin: "argus" | "registry" }) {
  return <Label className="flex items-center gap-1.5">{children}<OriginBadge origin={origin} /></Label>
}

function ServerEditDialog({
  open, onOpenChange, server, onSaved,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  server: Detail
  onSaved: () => Promise<void>
}) {
  const [f, setF] = useState<EditState>(() => toEdit(server))
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (open) setF(toEdit(server))
  }, [open, server])

  const set = (k: keyof EditState) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setF((p) => ({ ...p, [k]: e.target.value }))
  const n = (v: string) => v.trim() || null

  const save = async () => {
    setSaving(true)
    try {
      const payload: McpServerPayload = {
        display_name: n(f.display_name),
        status: f.status,
        version: f.version.trim() || server.version,
        owner_email: n(f.owner_email),
        department: n(f.department),
        category: n(f.category),
        tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean),
        provider_type: f.provider_type,
        vendor: n(f.vendor),
        target_platform: n(f.target_platform),
        registry_name: n(f.registry_name),
        title: n(f.title),
        description: n(f.description),
        website_url: n(f.website_url),
        repository_url: n(f.repository_url),
        license: n(f.license),
      }
      if (f.status !== server.status && f.status_note.trim()) payload.status_note = f.status_note.trim()
      await updateMcpServer(server.name, payload)
      toast.success("저장했습니다.")
      onOpenChange(false)
      await onSaved()
    } catch (e) {
      notifyError(e, "MCP 서버 정보를 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>MCP 서버 정의 편집</DialogTitle>
        </DialogHeader>
        <div className="grid max-h-[65vh] gap-3 overflow-y-auto py-1 pr-1 sm:grid-cols-2">
          <div className="grid gap-1.5"><L origin="argus">표시명</L><Input value={f.display_name} onChange={set("display_name")} /></div>
          <div className="grid gap-1.5"><L origin="argus">버전</L><Input value={f.version} onChange={set("version")} /></div>
          <div className="grid gap-1.5">
            <L origin="argus">상태</L>
            <Select value={f.status} onValueChange={(v) => setF((p) => ({ ...p, status: v }))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {STATUS_OPTIONS.map((s) => <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <L origin="argus">상태 변경 메모</L>
            <Input value={f.status_note} onChange={set("status_note")} disabled={f.status === server.status} placeholder="상태 변경 시 이력에 기록" />
          </div>
          <div className="grid gap-1.5">
            <L origin="argus">제공자 유형</L>
            <Select value={f.provider_type} onValueChange={(v) => setF((p) => ({ ...p, provider_type: v }))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {PROVIDER_TYPES.map((s) => <SelectItem key={s} value={s}>{PROVIDER_LABEL[s]}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5"><L origin="argus">벤더</L><Input value={f.vendor} onChange={set("vendor")} /></div>
          <div className="grid gap-1.5"><L origin="argus">대상 플랫폼</L><Input value={f.target_platform} onChange={set("target_platform")} placeholder="예: hive, kafka, github" /></div>
          <div className="grid gap-1.5"><L origin="argus">카테고리</L><Input value={f.category} onChange={set("category")} /></div>
          <div className="grid gap-1.5"><L origin="argus">소유자 이메일</L><Input value={f.owner_email} onChange={set("owner_email")} /></div>
          <div className="grid gap-1.5"><L origin="argus">부서</L><Input value={f.department} onChange={set("department")} /></div>
          <div className="grid gap-1.5 sm:col-span-2"><L origin="argus">태그 (쉼표 구분)</L><Input value={f.tags} onChange={set("tags")} /></div>
          <div className="sm:col-span-2 border-t pt-2 text-xs font-medium text-muted-foreground">MCP Registry 표준 메타데이터</div>
          <div className="grid gap-1.5"><L origin="registry">Registry 이름</L><Input value={f.registry_name} onChange={set("registry_name")} placeholder="io.github.org/server" /></div>
          <div className="grid gap-1.5"><L origin="registry">제목</L><Input value={f.title} onChange={set("title")} /></div>
          <div className="grid gap-1.5 sm:col-span-2"><L origin="registry">설명</L><Textarea value={f.description} onChange={set("description")} rows={3} /></div>
          <div className="grid gap-1.5"><L origin="registry">웹사이트</L><Input value={f.website_url} onChange={set("website_url")} /></div>
          <div className="grid gap-1.5"><L origin="registry">저장소 URL</L><Input value={f.repository_url} onChange={set("repository_url")} /></div>
          <div className="grid gap-1.5"><L origin="registry">라이선스</L><Input value={f.license} onChange={set("license")} /></div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>취소</Button>
          <Button onClick={save} disabled={saving}>{saving ? "저장 중..." : "저장"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
