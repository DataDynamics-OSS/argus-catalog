"use client"

import { useEffect, useMemo, useState } from "react"
import { Link2, Plus, SlidersHorizontal, Trash2 } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Checkbox } from "@workspace/ui/components/checkbox"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { Tabs, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"

import { fetchMcpDeployments, fetchMcpEffective } from "@/features/mcp-servers/api"
import type { McpDeploymentSummary, McpEffectiveState } from "@/features/mcp-servers/data/schema"

import {
  addAIAgentMcpServer,
  deleteAIAgentMcpServer,
  updateAIAgentMcpServer,
  type AIAgentMcpServer,
} from "../api"

const AUTH_OPTIONS = ["none", "bearer", "api_key", "basic", "oauth2"]

function deploymentHref(name: string) {
  return `/dashboard/mcp-servers?deployment=${encodeURIComponent(name)}`
}

function RegisteredBadge({ m }: { m: AIAgentMcpServer }) {
  if (m.registered && m.mcp_deployment_name) {
    return (
      <a href={deploymentHref(m.mcp_deployment_name)} className="inline-flex flex-col">
        <span className="inline-flex items-center gap-1">
          <Badge variant="outline" className="border-emerald-300 text-[10px] text-emerald-700 dark:text-emerald-300">
            카탈로그
          </Badge>
          <span className="text-xs text-primary hover:underline">{m.mcp_deployment_name}</span>
        </span>
        <span className="text-[11px] text-muted-foreground">
          {[m.mcp_server_name, m.deployment_environment, m.deployment_status].filter(Boolean).join(" · ")}
        </span>
      </a>
    )
  }
  return (
    <Badge
      variant="outline"
      className="border-amber-300 text-[10px] text-amber-700 dark:text-amber-300"
      title="MCP Server 카탈로그에 등록되지 않은 직접 입력 연결입니다. 도구·리소스 범위를 관리하려면 카탈로그 배포와 연결하세요."
    >
      미등록
    </Badge>
  )
}

export function AgentMcpCard({
  agentName,
  items,
  onChanged,
  canEdit = true,
}: {
  agentName: string
  items: AIAgentMcpServer[]
  onChanged: () => Promise<void>
  /** 등록자·소유자·관리자만 연결 추가·범위 수정·해제 버튼을 본다 */
  canEdit?: boolean
}) {
  const [deployments, setDeployments] = useState<McpDeploymentSummary[]>([])
  const [scopeTarget, setScopeTarget] = useState<AIAgentMcpServer | null>(null)

  useEffect(() => {
    fetchMcpDeployments({ pageSize: 100 })
      .then((d) => setDeployments(d.items.filter((x) => x.status !== "retired")))
      .catch(() => setDeployments([]))
  }, [])

  const unregistered = items.filter((m) => !m.registered).length

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          MCP 서버 ({items.length})
          {unregistered > 0 && (
            <Badge variant="outline" className="border-amber-300 text-[10px] text-amber-700 dark:text-amber-300">
              미등록 {unregistered}
            </Badge>
          )}
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          MCP Server 카탈로그의 배포를 선택하면 노출 도구·리소스 범위가 연동되고, 에이전트별 허용 그룹·도구로 범위를 좁힐 수 있습니다.
          직접 입력한 연결은 “미등록”으로 표시됩니다.
        </p>
      </CardHeader>
      <CardContent className="space-y-2">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>이름</TableHead>
              <TableHead>카탈로그</TableHead>
              <TableHead>URL</TableHead>
              <TableHead>인증</TableHead>
              <TableHead>허용 범위</TableHead>
              <TableHead className="w-20" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((m) => (
              <TableRow key={m.id}>
                <TableCell className="font-medium">{m.name}</TableCell>
                <TableCell>
                  <div className="flex flex-col gap-1">
                    <RegisteredBadge m={m} />
                    {canEdit && !m.registered && deployments.length > 0 && (
                      <LinkToDeployment agentName={agentName} m={m} deployments={deployments} onChanged={onChanged} />
                    )}
                  </div>
                </TableCell>
                <TableCell className="break-all font-mono text-xs text-muted-foreground">{m.url || "-"}</TableCell>
                <TableCell>{m.auth_method ? <Badge variant="outline">{m.auth_method}</Badge> : "-"}</TableCell>
                <TableCell className="text-xs">
                  {m.registered ? (
                    <div className="flex flex-col">
                      <span>
                        그룹: {m.allowed_groups?.length ? m.allowed_groups.join(", ") : <span className="text-muted-foreground">전체</span>}
                      </span>
                      <span>
                        도구: {m.allowed_tools?.length ? `${m.allowed_tools.length}개 지정` : <span className="text-muted-foreground">전체</span>}
                      </span>
                      {m.effective_tool_count != null && (
                        <span className="text-muted-foreground">실효 도구 {m.effective_tool_count}개</span>
                      )}
                    </div>
                  ) : (
                    <span className="text-muted-foreground">관리 불가</span>
                  )}
                </TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    {canEdit && m.registered && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        aria-label="허용 범위"
                        title="허용 그룹·도구"
                        onClick={() => setScopeTarget(m)}
                      >
                        <SlidersHorizontal className="h-4 w-4" />
                      </Button>
                    )}
                    {canEdit && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label="삭제"
                      onClick={async () => {
                        try {
                          await deleteAIAgentMcpServer(agentName, m.id)
                          await onChanged()
                        } catch (e) {
                          notifyError(e, "MCP 서버를 삭제하지 못했습니다.")
                        }
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {canEdit && <AddMcpForm agentName={agentName} deployments={deployments} onAdded={onChanged} />}
      </CardContent>
      <AllowedScopeDialog
        agentName={agentName}
        target={scopeTarget}
        onClose={() => setScopeTarget(null)}
        onSaved={onChanged}
      />
    </Card>
  )
}

function LinkToDeployment({
  agentName, m, deployments, onChanged,
}: {
  agentName: string
  m: AIAgentMcpServer
  deployments: McpDeploymentSummary[]
  onChanged: () => Promise<void>
}) {
  return (
    <Select
      value=""
      onValueChange={async (v) => {
        try {
          await updateAIAgentMcpServer(agentName, m.id, { mcp_deployment_name: v })
          toast.success("카탈로그 배포와 연결했습니다.")
          await onChanged()
        } catch (e) {
          notifyError(e, "카탈로그 배포와 연결하지 못했습니다.")
        }
      }}
    >
      <SelectTrigger size="sm" className="h-7 w-[180px] text-xs">
        <Link2 className="h-3.5 w-3.5" />
        <SelectValue placeholder="카탈로그 배포와 연결" />
      </SelectTrigger>
      <SelectContent>
        {deployments.map((d) => (
          <SelectItem key={d.name} value={d.name}>
            {d.display_name || d.name} ({d.environment})
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function AddMcpForm({
  agentName, deployments, onAdded,
}: {
  agentName: string
  deployments: McpDeploymentSummary[]
  onAdded: () => Promise<void>
}) {
  const [mode, setMode] = useState<"catalog" | "manual">("catalog")
  const [deployment, setDeployment] = useState("")
  const [name, setName] = useState("")
  const [url, setUrl] = useState("")
  const [auth, setAuth] = useState("none")
  const [busy, setBusy] = useState(false)

  const canSubmit = mode === "catalog" ? !!deployment : !!name.trim()

  const submit = async () => {
    setBusy(true)
    try {
      if (mode === "catalog") {
        await addAIAgentMcpServer(agentName, { mcp_deployment_name: deployment })
        setDeployment("")
      } else {
        await addAIAgentMcpServer(agentName, {
          name: name.trim(),
          url: url.trim() || undefined,
          auth_method: auth === "none" ? undefined : auth,
        })
        setName(""); setUrl("")
      }
      await onAdded()
    } catch (e) {
      notifyError(e, "MCP 연결을 추가하지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border p-2">
      <Tabs value={mode} onValueChange={(v) => setMode(v as typeof mode)}>
        <TabsList className="h-8">
          <TabsTrigger value="catalog" className="text-xs">카탈로그 배포 선택</TabsTrigger>
          <TabsTrigger value="manual" className="text-xs">직접 입력 (미등록)</TabsTrigger>
        </TabsList>
      </Tabs>
      {mode === "catalog" ? (
        deployments.length === 0 ? (
          <span className="text-xs text-muted-foreground">
            등록된 MCP 배포가 없습니다. <a href="/dashboard/mcp-servers" className="text-primary hover:underline">MCP Server 카탈로그</a>에서 먼저 등록하세요.
          </span>
        ) : (
          <Select value={deployment} onValueChange={setDeployment}>
            <SelectTrigger size="sm" className="h-8 w-[320px]">
              <SelectValue placeholder="MCP 배포 선택" />
            </SelectTrigger>
            <SelectContent>
              {deployments.map((d) => (
                <SelectItem key={d.name} value={d.name}>
                  {d.display_name || d.name} · {d.server_name} ({d.environment})
                  {d.enabled_mutating_groups.length > 0 ? " · 변경 도구 활성" : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )
      ) : (
        <>
          <Input placeholder="MCP 이름" value={name} onChange={(e) => setName(e.target.value)} className="h-8 w-[180px]" />
          <Input placeholder="URL" value={url} onChange={(e) => setUrl(e.target.value)} className="h-8 w-[260px]" />
          <Select value={auth} onValueChange={setAuth}>
            <SelectTrigger size="sm" className="h-8 w-[110px]"><SelectValue /></SelectTrigger>
            <SelectContent>{AUTH_OPTIONS.map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}</SelectContent>
          </Select>
        </>
      )}
      <Button size="sm" className="h-8" disabled={busy || !canSubmit} onClick={submit}>
        <Plus className="mr-1 h-4 w-4" /> 추가
      </Button>
    </div>
  )
}

function AllowedScopeDialog({
  agentName, target, onClose, onSaved,
}: {
  agentName: string
  target: AIAgentMcpServer | null
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const [eff, setEff] = useState<McpEffectiveState | null>(null)
  const [groups, setGroups] = useState<Set<string>>(new Set())
  const [tools, setTools] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!target?.mcp_deployment_name) return
    setEff(null)
    setGroups(new Set(target.allowed_groups ?? []))
    setTools(new Set(target.allowed_tools ?? []))
    fetchMcpEffective(target.mcp_deployment_name).then(setEff).catch(() => setEff(null))
  }, [target])

  const exposedTools = useMemo(() => (eff?.tools ?? []).filter((t) => t.exposed !== false && !t.blocked), [eff])

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, key: string) => {
    const n = new Set(set)
    if (n.has(key)) n.delete(key)
    else n.add(key)
    setter(n)
  }

  const save = async () => {
    if (!target) return
    setSaving(true)
    try {
      await updateAIAgentMcpServer(agentName, target.id, {
        allowed_groups: groups.size ? Array.from(groups) : null,
        allowed_tools: tools.size ? Array.from(tools) : null,
      })
      toast.success("허용 범위를 저장했습니다.")
      onClose()
      await onSaved()
    } catch (e) {
      notifyError(e, "허용 범위를 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={!!target} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>허용 그룹·도구 — {target?.mcp_deployment_name}</DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">
          아무것도 선택하지 않으면 배포에서 노출된 도구 전체를 사용합니다. 그룹과 도구를 함께 고르면 둘 다 만족하는 도구만 허용되며,
          정책 번들에는 배포 노출 도구(차단 제외) ∩ 허용 범위만 포함됩니다.
        </p>
        {!eff ? (
          <p className="text-sm text-muted-foreground">불러오는 중...</p>
        ) : (
          <div className="grid max-h-[55vh] gap-4 overflow-y-auto sm:grid-cols-[1fr_2fr]">
            <div className="space-y-1.5">
              <div className="text-xs font-medium">그룹</div>
              {eff.groups.map((g) => {
                return (
                  <label key={g.key} className={`flex items-center gap-2 text-sm ${g.observed_enabled === false ? "opacity-50" : ""}`}>
                    <Checkbox
                      checked={groups.has(g.key)}
                      onCheckedChange={() => toggle(groups, setGroups, g.key)}
                    />
                    <span>{g.label || g.key}</span>
                    {g.mutating && <Badge variant="outline" className="h-4 px-1 text-[10px] text-orange-700">변경</Badge>}
                    <span className="text-[11px] text-muted-foreground">{g.exposed_count}/{g.tool_count}</span>
                  </label>
                )
              })}
            </div>
            <div className="space-y-1.5">
              <div className="text-xs font-medium">도구 (사용 가능 {exposedTools.length}개)</div>
              {exposedTools.map((t) => (
                <label key={t.name} className="flex items-center gap-2 text-sm">
                  <Checkbox checked={tools.has(t.name)} onCheckedChange={() => toggle(tools, setTools, t.name)} />
                  <span className="font-mono text-xs">{t.name}</span>
                  {t.group_key && <span className="text-[11px] text-muted-foreground">{t.group_key}</span>}
                  {t.mutating && <Badge variant="outline" className="h-4 px-1 text-[10px] text-orange-700">변경</Badge>}
                </label>
              ))}
            </div>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>취소</Button>
          <Button onClick={save} disabled={saving || !eff}>{saving ? "저장 중..." : "저장"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
