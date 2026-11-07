"use client"

/**
 * 배포 상세 — 승인 정책 탭 (Phase 2).
 *
 * 관측값(서버 설정·manifest·수집) 옆에 승인값(Argus 전용)을 편집한다. 저장하면 새 policy_version 과
 * 이력이 남고 finding 이 재계산된다. 관측 모드는 경보만, 강제(enforce) 모드는 서버가 정책 번들을 pull 해
 * 집행한다(Phase 3, EnforcementCard).
 */

import { Fragment, useCallback, useEffect, useMemo, useState } from "react"
import { History, Loader2, Plus, Save, ShieldCheck, Trash2, Wand2 } from "lucide-react"
import { errorMessage, notifyError, toast } from "@/lib/notify"

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
import { cn } from "@workspace/ui/lib/utils"

import { adoptMcpObserved, fetchMcpPolicy, fetchMcpPolicyHistory, saveMcpPolicy } from "../../api"
import {
  POLICY_MODE_LABEL,
  type McpDeploymentPolicy,
  type McpPolicyHistory,
  type McpPolicyUpdate,
  type McpScopeInput,
} from "../../data/schema"
import { EffectBadge, OriginBadge, RiskBadge, RowSourceBadge, fmtDate } from "../mcp-badges"
import { JsonBlock } from "../mcp-server-detail"
import { EnforcementCard } from "./enforcement-card"

type Tri = "on" | "off" | "unset"
type ToolEdit = { blocked: boolean; requires_approval: Tri }

type Draft = {
  groups: Record<string, Tri>
  tools: Record<string, ToolEdit>
  scopes: McpScopeInput[]
  limits: { key: string; value: string }[]
  approved_version: string
  policy_mode: string
}

const triOf = (v: boolean | null | undefined): Tri => (v === true ? "on" : v === false ? "off" : "unset")
const boolOf = (t: Tri): boolean | null => (t === "on" ? true : t === "off" ? false : null)

function toDraft(p: McpDeploymentPolicy): Draft {
  return {
    groups: Object.fromEntries(p.groups.map((g) => [g.key, triOf(g.approved_enabled)])),
    tools: Object.fromEntries(p.tools.map((t) => [t.name, { blocked: t.blocked, requires_approval: triOf(t.requires_approval) }])),
    scopes: p.approved_scopes.map((s) => ({
      resource_type: s.resource_type, action: s.action, effect: s.effect, patterns: s.patterns, match: s.match,
      description: s.description,
    })),
    limits: Object.entries(p.approved_limits ?? {}).map(([key, value]) => ({ key, value: String(value) })),
    approved_version: p.approved_version ?? "",
    policy_mode: p.policy_mode,
  }
}

function parseLimit(v: string): unknown {
  const t = v.trim()
  if (t === "true" || t === "false") return t === "true"
  const n = Number(t)
  return t !== "" && !Number.isNaN(n) ? n : t
}

function OnOff({ value }: { value: boolean | null | undefined }) {
  if (value === true) return <span className="text-xs font-medium text-emerald-700 dark:text-emerald-400">켜짐</span>
  if (value === false) return <span className="text-xs text-muted-foreground">꺼짐</span>
  return <span className="text-xs text-muted-foreground">미확인</span>
}

export function PolicyPanel({ name, onChanged, canEdit = true }: {
  name: string
  onChanged: () => Promise<void>
  /** 배포 등록자(+기능 권한 mcp.policy)·관리자만 정책을 바꾼다(서버 _guard_policy). 아니면 읽기 전용으로 보여준다. */
  canEdit?: boolean
}) {
  const ro = !canEdit
  const [policy, setPolicy] = useState<McpDeploymentPolicy | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [note, setNote] = useState("")
  const [saving, setSaving] = useState(false)
  const [showAllTools, setShowAllTools] = useState(false)
  const [adoptOpen, setAdoptOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const p = await fetchMcpPolicy(name)
      setPolicy(p)
      setDraft(toDraft(p))
      setErr(null)
    } catch (e) {
      setErr(errorMessage(e) ?? "승인 정책을 불러오지 못했습니다.")
    }
  }, [name])

  useEffect(() => {
    load()
  }, [load])

  const dirty = useMemo(
    () => (policy && draft ? JSON.stringify(toDraft(policy)) !== JSON.stringify(draft) : false),
    [policy, draft],
  )

  if (err) return <p className="text-sm text-destructive">{err}</p>
  if (!policy || !draft) return <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />

  const setGroup = (k: string, v: Tri) => setDraft({ ...draft, groups: { ...draft.groups, [k]: v } })
  const setTool = (k: string, v: Partial<ToolEdit>) =>
    setDraft({ ...draft, tools: { ...draft.tools, [k]: { ...draft.tools[k]!, ...v } } })

  const save = async () => {
    const base = toDraft(policy)
    const payload: McpPolicyUpdate = { note: note.trim() || null }
    const groups = Object.fromEntries(
      Object.entries(draft.groups).filter(([k, v]) => base.groups[k] !== v).map(([k, v]) => [k, boolOf(v)]),
    )
    if (Object.keys(groups).length) payload.groups = groups
    const tools = Object.fromEntries(
      Object.entries(draft.tools)
        .filter(([k, v]) => JSON.stringify(base.tools[k]) !== JSON.stringify(v))
        .map(([k, v]) => [k, { blocked: v.blocked, requires_approval: boolOf(v.requires_approval) }]),
    )
    if (Object.keys(tools).length) payload.tools = tools
    if (JSON.stringify(base.scopes) !== JSON.stringify(draft.scopes)) {
      payload.scopes = draft.scopes.filter((s) => s.resource_type.trim() && s.patterns.length)
    }
    if (JSON.stringify(base.limits) !== JSON.stringify(draft.limits)) {
      const lim = Object.fromEntries(draft.limits.filter((l) => l.key.trim()).map((l) => [l.key.trim(), parseLimit(l.value)]))
      payload.limits = Object.keys(lim).length ? lim : null
    }
    if (base.approved_version !== draft.approved_version) payload.approved_version = draft.approved_version.trim() || null
    if (base.policy_mode !== draft.policy_mode) payload.policy_mode = draft.policy_mode
    setSaving(true)
    try {
      const p = await saveMcpPolicy(name, payload)
      setPolicy(p)
      setDraft(toDraft(p))
      setNote("")
      toast.success(`승인 정책을 저장했습니다 (${p.policy_version})`)
      await onChanged()
    } catch (e) {
      notifyError(e, "승인 정책을 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  const tools = showAllTools ? policy.tools : policy.tools.filter((t) => t.mutating || draft.tools[t.name]?.blocked)
  const observedLimits = policy.observed_limits ?? {}

  return (
    <div className="flex flex-col gap-4">
      {/* 헤더 */}
      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-6">
          <div className="space-y-1 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-violet-600" />
              <span className="font-medium">승인 정책</span>
              <OriginBadge origin="argus" />
              <span className="font-mono text-xs text-muted-foreground">{policy.policy_version ?? "미설정"}</span>
            </div>
            <p className="text-xs text-muted-foreground">
              {policy.policy_updated_at
                ? `${fmtDate(policy.policy_updated_at)} · ${policy.policy_updated_by ?? "-"}`
                : "아직 승인 정책이 없습니다. “관측값 채택”으로 현재 상태를 기준선으로 삼을 수 있습니다."}
              {" "}관측값과 다르면 finding 이 생기고 알림 규칙(MCP_POLICY_DRIFT)으로 발행됩니다.
              {policy.policy_mode === "enforce" ? " 강제 모드 — 서버가 이 정책으로 도구 호출을 거부합니다." : " 관측 모드 — 서버에 강제하지는 않습니다."}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Select disabled={ro} value={draft.policy_mode} onValueChange={(v) => setDraft({ ...draft, policy_mode: v })}>
              <SelectTrigger size="sm" className="w-[150px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="observe">{POLICY_MODE_LABEL.observe}</SelectItem>
                <SelectItem value="enforce">{POLICY_MODE_LABEL.enforce}</SelectItem>
              </SelectContent>
            </Select>
            <Button size="sm" variant="outline" onClick={() => setHistoryOpen(true)}>
              <History className="mr-1 h-4 w-4" /> 이력
            </Button>
            {!ro && (
              <Button size="sm" variant="outline" onClick={() => setAdoptOpen(true)}>
                <Wand2 className="mr-1 h-4 w-4" /> 관측값 채택
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <EnforcementCard name={name} policy={policy} mode={draft.policy_mode} canEdit={canEdit} />

      {/* 그룹 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">도구 그룹</CardTitle>
          <p className="text-xs text-muted-foreground">
            승인 “꺼짐”인 그룹이 서버에서 켜져 있으면 finding 이 됩니다. “미지정”은 비교하지 않습니다.
          </p>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>그룹</TableHead>
                <TableHead>설정 키</TableHead>
                <TableHead>위험도</TableHead>
                <TableHead>관측 <OriginBadge origin="server_config" className="ml-1" /></TableHead>
                <TableHead className="w-[150px]">승인 <OriginBadge origin="argus" className="ml-1" /></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {policy.groups.map((g) => {
                const appr = draft.groups[g.key] ?? "unset"
                const drift = g.observed_enabled === true && appr === "off"
                return (
                  <TableRow key={g.key} className={cn(drift && "bg-red-50/60 dark:bg-red-950/20")}>
                    <TableCell>
                      <div className="font-medium">{g.label || g.key}</div>
                      <div className="font-mono text-[11px] text-muted-foreground">{g.key}{g.mutating ? " · 변경" : ""}</div>
                    </TableCell>
                    <TableCell className="font-mono text-[11px] text-muted-foreground">{g.config_key || "-"}</TableCell>
                    <TableCell><RiskBadge risk={g.risk} /></TableCell>
                    <TableCell>
                      <OnOff value={g.observed_enabled} />
                      {g.observed_basis && <span className="ml-1 text-[11px] text-muted-foreground">({g.observed_basis})</span>}
                    </TableCell>
                    <TableCell>
                      <Select disabled={ro} value={appr} onValueChange={(v) => setGroup(g.key, v as Tri)}>
                        <SelectTrigger size="sm" className="h-8"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="on">승인: 켜짐 허용</SelectItem>
                          <SelectItem value="off">승인: 꺼짐</SelectItem>
                          <SelectItem value="unset">미지정</SelectItem>
                        </SelectContent>
                      </Select>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* 도구 */}
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-2">
          <div>
            <CardTitle className="text-sm">도구 차단 · 사람 승인</CardTitle>
            <p className="text-xs text-muted-foreground">
              차단한 도구가 노출되거나 호출되면 finding(치명) 이 됩니다. 사람 승인 필요 여부는 에이전트 정책 번들에 반영됩니다.
            </p>
          </div>
          <label className="flex shrink-0 items-center gap-2 text-xs">
            <Switch checked={showAllTools} onCheckedChange={setShowAllTools} /> 읽기 도구도 표시
          </label>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>도구</TableHead>
                <TableHead>그룹</TableHead>
                <TableHead>위험도</TableHead>
                <TableHead>노출 <OriginBadge origin="mcp" className="ml-1" /></TableHead>
                <TableHead className="w-[80px]">차단</TableHead>
                <TableHead className="w-[150px]">사람 승인</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tools.length === 0 ? (
                <TableRow><TableCell colSpan={6} className="h-12 text-center text-muted-foreground">변경 도구가 없습니다.</TableCell></TableRow>
              ) : tools.map((t) => {
                const d = draft.tools[t.name] ?? { blocked: false, requires_approval: "unset" as Tri }
                return (
                  <TableRow key={t.id} className={cn(d.blocked && t.observed_exposed && "bg-red-50/60 dark:bg-red-950/20")}>
                    <TableCell className="font-mono text-xs">{t.name}</TableCell>
                    <TableCell className="text-xs">{t.group_key ?? "-"}</TableCell>
                    <TableCell>
                      <RiskBadge risk={t.risk} />
                      {!t.risk_confirmed && t.mutating && <span className="ml-1 text-[10px] text-amber-600">미확정</span>}
                    </TableCell>
                    <TableCell><OnOff value={t.observed_exposed} /></TableCell>
                    <TableCell>
                      <Switch disabled={ro} checked={d.blocked} onCheckedChange={(v) => setTool(t.name, { blocked: v })} aria-label="차단" />
                    </TableCell>
                    <TableCell>
                      <Select disabled={ro} value={d.requires_approval} onValueChange={(v) => setTool(t.name, { requires_approval: v as Tri })}>
                        <SelectTrigger size="sm" className="h-8"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="unset">정의 따름 ({t.tool_requires_approval ? "필요" : "불필요"})</SelectItem>
                          <SelectItem value="on">필요</SelectItem>
                          <SelectItem value="off">불필요</SelectItem>
                        </SelectContent>
                      </Select>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* 리소스 범위 */}
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">승인 리소스 범위 <OriginBadge origin="argus" /></CardTitle>
            <p className="text-xs text-muted-foreground">
              승인 deny/protect 가 서버 설정에 없거나, 서버 allow 목록이 승인 allow 보다 넓으면 finding 입니다.
              감사 이벤트 판정에도 쓰입니다(없으면 관측 범위 사용).
            </p>
          </CardHeader>
          <CardContent className="space-y-2">
            {draft.scopes.map((s, i) => (
              <ScopeRow
                key={i}
                readOnly={ro}
                value={s}
                onChange={(v) => setDraft({ ...draft, scopes: draft.scopes.map((x, j) => (j === i ? v : x)) })}
                onRemove={() => setDraft({ ...draft, scopes: draft.scopes.filter((_, j) => j !== i) })}
              />
            ))}
            {!ro && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setDraft({ ...draft, scopes: [...draft.scopes, { resource_type: "", action: "write", effect: "allow", patterns: [] }] })}
              >
                <Plus className="mr-1 h-4 w-4" /> 규칙 추가
              </Button>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">관측 리소스 범위 <OriginBadge origin="server_config" /></CardTitle>
            <p className="text-xs text-muted-foreground">서버 설정·manifest 에서 수집한 규칙 (읽기 전용 비교용)</p>
          </CardHeader>
          <CardContent>
            {policy.observed_scopes.length === 0 ? (
              <p className="text-xs text-muted-foreground">관측된 규칙이 없습니다.</p>
            ) : (
              <div className="space-y-1.5">
                {policy.observed_scopes.map((s) => (
                  <div key={s.id} className="flex flex-wrap items-center gap-1.5 text-xs">
                    <code className="font-mono">{s.resource_type}</code>
                    <span className="text-muted-foreground">{s.action}</span>
                    <EffectBadge effect={s.effect} />
                    {s.patterns.map((p) => <code key={p} className="rounded bg-muted px-1 text-[11px]">{p}</code>)}
                    <RowSourceBadge source={s.source} />
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* limits · 버전 */}
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">제한값(limits)</CardTitle>
            <p className="text-xs text-muted-foreground">관측값이 승인값보다 크면 finding 입니다 (숫자만 비교).</p>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="grid grid-cols-[1fr_110px_110px_auto] gap-2 text-[11px] text-muted-foreground">
              <span>키</span><span>관측</span><span>승인</span><span />
            </div>
            {draft.limits.map((l, i) => (
              <div key={i} className="grid grid-cols-[1fr_110px_110px_auto] items-center gap-2">
                <Input disabled={ro} className="h-8 font-mono text-xs" value={l.key} list="mcp-limit-keys"
                  onChange={(e) => setDraft({ ...draft, limits: draft.limits.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)) })} />
                <span className="font-mono text-xs">{String(observedLimits[l.key] ?? "-")}</span>
                <Input disabled={ro} className="h-8 text-xs" value={l.value}
                  onChange={(e) => setDraft({ ...draft, limits: draft.limits.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)) })} />
                {ro ? <span /> : (
                  <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="삭제"
                    onClick={() => setDraft({ ...draft, limits: draft.limits.filter((_, j) => j !== i) })}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            ))}
            <datalist id="mcp-limit-keys">
              {Object.keys(observedLimits).map((k) => <option key={k} value={k} />)}
            </datalist>
            {!ro && (
              <Button size="sm" variant="outline" onClick={() => setDraft({ ...draft, limits: [...draft.limits, { key: "", value: "" }] })}>
                <Plus className="mr-1 h-4 w-4" /> 제한 추가
              </Button>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">승인 버전</CardTitle>
            <p className="text-xs text-muted-foreground">배포 버전이 승인 버전과 다르면 finding(낮음) 입니다.</p>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1">
              <Label className="flex items-center gap-1.5 text-xs">배포 버전 <OriginBadge origin="mcp" /></Label>
              <span className="font-mono text-sm">{policy.deployed_version ?? "-"}</span>
            </div>
            <div className="grid gap-1">
              <Label className="flex items-center gap-1.5 text-xs">승인 버전 <OriginBadge origin="argus" /></Label>
              <Input disabled={ro} className="h-8" value={draft.approved_version} placeholder="예: 1.2.0"
                onChange={(e) => setDraft({ ...draft, approved_version: e.target.value })} />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* 저장 바 */}
      {ro ? (
        <p className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
          읽기 전용 — 승인 정책은 배포 등록자(기능 권한 “MCP 승인 정책 변경” 필요) 또는 관리자만 바꿀 수 있습니다.
        </p>
      ) : (
      <div className="sticky bottom-0 z-10 flex flex-wrap items-center justify-end gap-2 rounded-md border bg-background/95 p-3 backdrop-blur">
        {dirty && <span className="mr-auto text-xs text-amber-700 dark:text-amber-400">저장하지 않은 변경이 있습니다.</span>}
        <Input className="h-8 max-w-sm" placeholder="변경 메모 (이력에 남음)" value={note} onChange={(e) => setNote(e.target.value)} />
        <Button size="sm" variant="outline" disabled={!dirty || saving} onClick={() => setDraft(toDraft(policy))}>되돌리기</Button>
        <Button size="sm" disabled={!dirty || saving} onClick={save}>
          {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />} 정책 저장
        </Button>
      </div>
      )}

      <AdoptDialog open={adoptOpen} onOpenChange={setAdoptOpen} name={name}
        onDone={async (p) => { setPolicy(p); setDraft(toDraft(p)); await onChanged() }} />
      <HistoryDialog open={historyOpen} onOpenChange={setHistoryOpen} name={name} />
    </div>
  )
}

export function ScopeRow({ value, onChange, onRemove, readOnly = false }: {
  readOnly?: boolean
  value: McpScopeInput
  onChange: (v: McpScopeInput) => void
  onRemove: () => void
}) {
  const [patterns, setPatterns] = useState(value.patterns.join(", "))
  useEffect(() => {
    setPatterns(value.patterns.join(", "))
  }, [value.patterns])
  return (
    <div className="grid gap-2 rounded-md border p-2 sm:grid-cols-[1fr_90px_95px_2fr_auto] sm:items-center">
      <Input disabled={readOnly} className="h-8 font-mono text-xs" placeholder="리소스 유형 (예: topic)" value={value.resource_type}
        onChange={(e) => onChange({ ...value, resource_type: e.target.value })} />
      <Select disabled={readOnly} value={value.action} onValueChange={(v) => onChange({ ...value, action: v })}>
        <SelectTrigger size="sm" className="h-8"><SelectValue /></SelectTrigger>
        <SelectContent>{["read", "write", "delete", "any"].map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}</SelectContent>
      </Select>
      <Select disabled={readOnly} value={value.effect} onValueChange={(v) => onChange({ ...value, effect: v })}>
        <SelectTrigger size="sm" className="h-8"><SelectValue /></SelectTrigger>
        <SelectContent>{["allow", "deny", "protect"].map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}</SelectContent>
      </Select>
      <Input disabled={readOnly} className="h-8 text-xs" placeholder="패턴 (쉼표 구분, fnmatch)" value={patterns}
        onChange={(e) => setPatterns(e.target.value)}
        onBlur={() => onChange({ ...value, patterns: patterns.split(",").map((p) => p.trim()).filter(Boolean) })} />
      {readOnly ? <span /> : (
        <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="삭제" onClick={onRemove}>
          <Trash2 className="h-4 w-4" />
        </Button>
      )}
    </div>
  )
}

function AdoptDialog({ open, onOpenChange, name, onDone }: {
  open: boolean
  onOpenChange: (v: boolean) => void
  name: string
  onDone: (p: McpDeploymentPolicy) => Promise<void>
}) {
  const [opts, setOpts] = useState({ groups: true, scopes: true, limits: true, version: true })
  const [busy, setBusy] = useState(false)
  const items: [keyof typeof opts, string][] = [
    ["groups", "도구 그룹 on/off"], ["scopes", "리소스 범위 (승인 범위를 관측 범위로 교체)"],
    ["limits", "제한값(limits)"], ["version", "배포 버전 → 승인 버전"],
  ]
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader><DialogTitle>관측값을 승인 기준선으로 채택</DialogTitle></DialogHeader>
        <p className="text-xs text-muted-foreground">
          현재 서버에서 관측된 값을 그대로 승인합니다. 처음 정책을 만들 때 쓰고, 이후에는 필요한 항목만 좁혀 저장하세요.
        </p>
        <div className="space-y-2">
          {items.map(([k, label]) => (
            <label key={k} className="flex items-center gap-2 text-sm">
              <Checkbox checked={opts[k]} onCheckedChange={(v) => setOpts({ ...opts, [k]: v === true })} /> {label}
            </label>
          ))}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>취소</Button>
          <Button
            disabled={busy || !Object.values(opts).some(Boolean)}
            onClick={async () => {
              setBusy(true)
              try {
                const p = await adoptMcpObserved(name, opts)
                toast.success("관측값을 채택했습니다.")
                onOpenChange(false)
                await onDone(p)
              } catch (e) {
                notifyError(e, "관측값을 채택하지 못했습니다.")
              } finally {
                setBusy(false)
              }
            }}
          >
            채택
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function HistoryDialog({ open, onOpenChange, name }: { open: boolean; onOpenChange: (v: boolean) => void; name: string }) {
  const [rows, setRows] = useState<McpPolicyHistory[] | null>(null)
  const [openId, setOpenId] = useState<number | null>(null)
  useEffect(() => {
    if (!open) return
    setRows(null)
    fetchMcpPolicyHistory(name).then(setRows).catch((e) => notifyError(e, "정책 변경 이력을 불러오지 못했습니다."))
  }, [open, name])
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader><DialogTitle>승인 정책 이력</DialogTitle></DialogHeader>
        <div className="max-h-[60vh] overflow-y-auto">
          {!rows ? <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /> : rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">이력이 없습니다.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>정책 버전</TableHead>
                  <TableHead>변경자</TableHead>
                  <TableHead>시각</TableHead>
                  <TableHead>메모</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <Fragment key={r.id}>
                    <TableRow className="cursor-pointer" onClick={() => setOpenId(openId === r.id ? null : r.id)}>
                      <TableCell className="font-mono text-xs">{r.policy_version}</TableCell>
                      <TableCell className="text-xs">{r.changed_by ?? "-"}</TableCell>
                      <TableCell className="text-xs">{fmtDate(r.changed_at)}</TableCell>
                      <TableCell className="text-xs">{r.note ?? ""}</TableCell>
                    </TableRow>
                    {openId === r.id && (
                      <TableRow>
                        <TableCell colSpan={4}><JsonBlock value={r.snapshot} /></TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
