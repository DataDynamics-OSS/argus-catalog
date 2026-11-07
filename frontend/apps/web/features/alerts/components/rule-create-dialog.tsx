"use client"

import { useCallback, useEffect, useState } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@workspace/ui/components/dialog"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Textarea } from "@workspace/ui/components/textarea"
import { Checkbox } from "@workspace/ui/components/checkbox"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import {
  Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@workspace/ui/components/command"
import { Popover, PopoverContent, PopoverTrigger } from "@workspace/ui/components/popover"
import { Badge } from "@workspace/ui/components/badge"
import { Separator } from "@workspace/ui/components/separator"
import { Check, ChevronsUpDown } from "lucide-react"
import { authFetch, throwOnError } from "@/features/auth/auth-fetch"
import { MCP_TRIGGERS, SCOPE_LABEL, SEVERITY_LABEL, TRIGGER_LABEL, labelOf } from "@/features/alerts/labels"
import { errorMessage } from "@/lib/notify"

const BASE = "/api/v1"

type EditRule = {
  id: number
  rule_name: string
  scope_type: string
  scope_id: number | null
  scope_name?: string | null
  trigger_type: string
  trigger_config: string
  severity_override: string | null
  channels: string
  notify_owners: string
  webhook_url: string | null
  subscribers: string | null
  description: string | null
}

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: () => void
  presetScope?: { type: string; id: number; name: string }
  editRule?: EditRule | null
}

type Tag = { id: number; name: string; color: string }
type DatasetSummary = { id: number; name: string; datasource_type: string; datasource_name: string }
type Datasource = { id: number; datasource_id: string; name: string; type: string }
/** 저장된 trigger_config 를 안전하게 읽는다(깨진 값이면 빈 설정). */
function parseConfig(raw: string | null | undefined): Record<string, unknown> {
  if (!raw) return {}
  try {
    const v = JSON.parse(raw)
    return v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {}
  } catch {
    return {}
  }
}

function strList(v: unknown): string[] | null {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : null
}

const isHttpUrl = (u: string) => /^https?:\/\/[^\s/]+/i.test(u.trim())

type LineageSummary = {
  id: number
  source_dataset_name: string | null
  target_dataset_name: string | null
  source_datasource_type: string | null
  target_datasource_type: string | null
}

export function RuleCreateDialog({ open, onOpenChange, onCreated, presetScope, editRule }: Props) {
  const isEdit = !!editRule
  const [step, setStep] = useState(1)

  // Step 1
  const [ruleName, setRuleName] = useState("")
  const [scopeType, setScopeType] = useState("ALL")
  const [scopeId, setScopeId] = useState<number | null>(null)
  const [triggerType, setTriggerType] = useState("ANY")
  const [changeTypes, setChangeTypes] = useState<string[]>(["DROP", "MODIFY"])
  const [watchColumns, setWatchColumns] = useState("")
  // MCP_POLICY_DRIFT / MCP_SECURITY_WARNING 조건
  const [mcpMinSeverity, setMcpMinSeverity] = useState("medium")
  const [mcpEnvs, setMcpEnvs] = useState<string[]>([])
  // QUALITY_FAILED 조건
  const [qualityMinSeverity, setQualityMinSeverity] = useState("INFO")
  const [severityOverride, setSeverityOverride] = useState("auto")

  // Step 2
  const [channels, setChannels] = useState<string[]>(["IN_APP"])
  const [webhookUrl, setWebhookUrl] = useState("")
  const [subscribers, setSubscribers] = useState("")
  const [notifyOwners, setNotifyOwners] = useState(true)
  const [description, setDescription] = useState("")

  // Lookup data
  const [tags, setTags] = useState<Tag[]>([])
  const [datasources, setDatasources] = useState<Datasource[]>([])
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [lineages, setLineages] = useState<LineageSummary[]>([])
  const [scopePopoverOpen, setScopePopoverOpen] = useState(false)
  const [scopeSearch, setScopeSearch] = useState("")

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset on open — populate from editRule if editing
  useEffect(() => {
    if (!open) return
    setStep(1)
    setError(null)
    if (editRule) {
      setRuleName(editRule.rule_name)
      setScopeType(editRule.scope_type)
      setScopeId(editRule.scope_id)
      setTriggerType(editRule.trigger_type)
      const config = parseConfig(editRule.trigger_config)
      const isMcp = MCP_TRIGGERS.has(editRule.trigger_type)
      setChangeTypes(strList(config.change_types) ?? ["DROP", "MODIFY", "ADD"])
      setWatchColumns((strList(config.columns) ?? []).join(", "))
      setMcpMinSeverity(isMcp && typeof config.min_severity === "string" ? config.min_severity : "medium")
      setMcpEnvs(strList(config.environments) ?? [])
      setQualityMinSeverity(!isMcp && typeof config.min_severity === "string" ? config.min_severity : "INFO")
      setSeverityOverride(editRule.severity_override || "auto")
      // 지원하지 않는 채널(과거 EMAIL 등)은 빼고 불러온다
      const chans = (editRule.channels || "").split(",").map(c => c.trim()).filter(c => c === "IN_APP" || c === "WEBHOOK")
      setChannels(chans.length ? chans : ["IN_APP"])
      setWebhookUrl(editRule.webhook_url || "")
      setSubscribers(editRule.subscribers || "")
      setNotifyOwners(editRule.notify_owners === "true")
      setDescription(editRule.description || "")
    } else {
      setRuleName("")
      setScopeType(presetScope?.type || "ALL")
      setScopeId(presetScope?.id || null)
      setTriggerType(presetScope?.type === "LINEAGE" ? "MAPPING_BROKEN" : "ANY")
      setChangeTypes(["DROP", "MODIFY"])
      setWatchColumns("")
      setMcpMinSeverity("medium")
      setMcpEnvs([])
      setQualityMinSeverity("INFO")
      setSeverityOverride("auto")
      setChannels(["IN_APP"])
      setWebhookUrl("")
      setSubscribers("")
      setNotifyOwners(true)
      setDescription("")
    }
  }, [open, presetScope, editRule])

  // Load lookup data
  useEffect(() => {
    if (!open) return
    const load = <T,>(url: string, set: (v: T) => void) =>
      authFetch(url).then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status))))).then(set)
        .catch(() => setError("감시 대상 목록을 불러오지 못했습니다. 다시 열어 주세요."))
    load<Tag[]>(`${BASE}/catalog/tags`, setTags)
    load<Datasource[]>(`${BASE}/catalog/datasources`, setDatasources)
  }, [open])

  // Load scope-specific data
  useEffect(() => {
    if (!open) return
    if (scopeType === "DATASET") {
      const params = new URLSearchParams({ page_size: "100" })
      if (scopeSearch) params.set("search", scopeSearch)
      authFetch(`${BASE}/catalog/datasets?${params}`)
        .then(r => r.json())
        .then(d => setDatasets(d.items || []))
        .catch(() => {})
    } else if (scopeType === "LINEAGE") {
      authFetch(`${BASE}/catalog/lineage`)
        .then(r => r.json())
        .then(setLineages)
        .catch(() => {})
    }
  }, [open, scopeType, scopeSearch])

  const toggleChangeType = (type: string) => {
    setChangeTypes(prev =>
      prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]
    )
  }

  const toggleChannel = (ch: string) => {
    setChannels(prev =>
      prev.includes(ch) ? prev.filter(c => c !== ch) : [...prev, ch]
    )
  }

  // 적용 범위(scope) 표시 이름 — 목록에 없으면(검색 결과 밖) 수정 대상 규칙의 scope_name 으로
  const scopeDisplayName = useCallback(() => {
    if (scopeType === "ALL") return "전체"
    const editName = editRule && editRule.scope_type === scopeType && editRule.scope_id === scopeId
      ? editRule.scope_name : null
    if (scopeType === "TAG" && scopeId) {
      const tag = tags.find(t => t.id === scopeId)
      return tag ? `태그: ${tag.name}` : editName ? `태그: ${editName}` : `태그 ID: ${scopeId}`
    }
    if (scopeType === "DATASET" && scopeId) {
      const ds = datasets.find(d => d.id === scopeId)
      return ds ? `${ds.datasource_type}.${ds.name}` : editName ?? `데이터셋 ID: ${scopeId}`
    }
    if (scopeType === "LINEAGE" && scopeId) {
      const l = lineages.find(l => l.id === scopeId)
      return l ? `${l.source_datasource_type}.${l.source_dataset_name} → ${l.target_datasource_type}.${l.target_dataset_name}` : editName ?? `리니지 ID: ${scopeId}`
    }
    if (scopeType === "DATASOURCE" && scopeId) {
      const p = datasources.find(p => p.id === scopeId)
      return p ? `데이터 소스: ${p.name}` : editName ? `데이터 소스: ${editName}` : `데이터 소스 ID: ${scopeId}`
    }
    return "선택..."
  }, [scopeType, scopeId, tags, datasets, lineages, datasources, editRule])

  const isMcpTrigger = MCP_TRIGGERS.has(triggerType)
  const usesChangeTypes = triggerType === "SCHEMA_CHANGE" || triggerType === "COLUMN_WATCH"
  const watchList = watchColumns.split(",").map(c => c.trim()).filter(Boolean)

  // 단계별 검증 — 버튼 옆에 이유를 보여준다
  const step1Problem = !ruleName.trim() ? "규칙 이름을 입력하세요."
    : scopeType !== "ALL" && !scopeId ? "감시 대상을 선택하세요."
    : isMcpTrigger && scopeType !== "ALL" && scopeType !== "DATASOURCE" ? "MCP 트리거는 범위를 '전체' 또는 '데이터 소스'로만 지정할 수 있습니다."
    : usesChangeTypes && changeTypes.length === 0 ? "변경 유형을 하나 이상 선택하세요."
    : triggerType === "COLUMN_WATCH" && watchList.length === 0 ? "감시할 컬럼을 하나 이상 입력하세요."
    : null
  const step2Problem = channels.length === 0 ? "알림 채널을 하나 이상 선택하세요."
    : channels.includes("WEBHOOK") && !isHttpUrl(webhookUrl) ? "Webhook URL 을 http(s):// 주소로 입력하세요."
    : null

  const changeTrigger = (t: string) => {
    setTriggerType(t)
    // MCP 트리거로 바꾸면 의미 없는 범위(데이터셋·태그·리니지)는 전체로 되돌린다
    if (MCP_TRIGGERS.has(t) && scopeType !== "ALL" && scopeType !== "DATASOURCE") {
      setScopeType("ALL")
      setScopeId(null)
    }
  }

  const handleSave = async () => {
    if (step1Problem || step2Problem) {
      setError(step1Problem ?? step2Problem)
      return
    }
    setSaving(true)
    setError(null)

    const triggerConfig: Record<string, unknown> = {}
    if (triggerType === "SCHEMA_CHANGE") {
      triggerConfig.change_types = changeTypes
    } else if (triggerType === "COLUMN_WATCH") {
      triggerConfig.columns = watchList
      triggerConfig.change_types = changeTypes
    } else if (triggerType === "QUALITY_FAILED") {
      triggerConfig.min_severity = qualityMinSeverity
    } else if (isMcpTrigger) {
      triggerConfig.min_severity = mcpMinSeverity
      if (mcpEnvs.length) triggerConfig.environments = mcpEnvs
    }

    const body = {
      rule_name: ruleName.trim(),
      description: description || null,
      scope_type: scopeType,
      scope_id: scopeType === "ALL" ? null : scopeId,
      trigger_type: triggerType,
      trigger_config: JSON.stringify(triggerConfig),
      severity_override: severityOverride === "auto" ? null : severityOverride,
      channels: channels.join(","),
      notify_owners: notifyOwners ? "true" : "false",
      webhook_url: channels.includes("WEBHOOK") ? webhookUrl.trim() || null : null,
      subscribers: subscribers || null,
    }

    try {
      const url = isEdit ? `${BASE}/alerts/rules/${editRule!.id}` : `${BASE}/alerts/rules`
      const method = isEdit ? "PUT" : "POST"
      const resp = await authFetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!resp.ok) await throwOnError(resp, "알림 규칙을 저장하지 못했습니다.")
      onCreated()
      onOpenChange(false)
    } catch (e) {
      setError(errorMessage(e) ?? "알림 규칙을 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "알림 규칙 수정" : "알림 규칙 추가"}</DialogTitle>
        </DialogHeader>

        {/* 단계 표시기 */}
        <div className="flex items-center gap-2 text-sm mb-2">
          <StepDot n={1} label="대상 & 조건" current={step} />
          <div className="h-px flex-1 bg-border" />
          <StepDot n={2} label="알림 대상 & 방식" current={step} />
        </div>

        {error && (
          <p className="text-sm text-destructive bg-destructive/10 rounded px-3 py-2">{error}</p>
        )}

        {/* ===== Step 1: What to Watch + Trigger ===== */}
        {step === 1 && (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label className="text-sm">규칙 이름</Label>
              <Input
                value={ruleName}
                onChange={e => setRuleName(e.target.value)}
                placeholder="PII 데이터 컬럼 삭제 감시"
                className="h-9"
              />
            </div>

            <Separator />
            <Label className="text-sm font-medium">감시 대상 (Scope)</Label>

            {/* 대상 유형 버튼 */}
            <div className="flex gap-1.5 flex-wrap">
              {["DATASET", "TAG", "LINEAGE", "DATASOURCE", "ALL"].map(t => (
                <Button
                  key={t}
                  variant={scopeType === t ? "default" : "outline"}
                  size="sm"
                  className="text-sm h-8"
                  disabled={isMcpTrigger && t !== "ALL" && t !== "DATASOURCE"}
                  title={isMcpTrigger && t !== "ALL" && t !== "DATASOURCE" ? "MCP 트리거는 전체·데이터 소스 범위만 지원합니다" : undefined}
                  onClick={() => { setScopeType(t); setScopeId(null) }}
                >
                  {SCOPE_LABEL[t]}
                </Button>
              ))}
            </div>

            {/* 대상 항목 선택기 */}
            {scopeType === "TAG" && (
              <Select value={scopeId ? String(scopeId) : ""} onValueChange={v => setScopeId(Number(v))}>
                <SelectTrigger className="h-9"><SelectValue placeholder="태그 선택..." /></SelectTrigger>
                <SelectContent>
                  {tags.map(t => (
                    <SelectItem key={t.id} value={String(t.id)}>
                      <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ backgroundColor: t.color }} />
                      {t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {scopeType === "DATASOURCE" && (
              <Select value={scopeId ? String(scopeId) : ""} onValueChange={v => setScopeId(Number(v))}>
                <SelectTrigger className="h-9"><SelectValue placeholder="데이터 소스 선택..." /></SelectTrigger>
                <SelectContent>
                  {datasources.map(p => (
                    <SelectItem key={p.id} value={String(p.id)}>{p.name} ({p.type})</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {scopeType === "DATASET" && (
              <Popover open={scopePopoverOpen} onOpenChange={setScopePopoverOpen}>
                <PopoverTrigger asChild>
                  <Button variant="outline" className="w-full justify-between h-9 font-normal text-sm">
                    {scopeId ? scopeDisplayName() : "데이터셋 검색·선택..."}
                    <ChevronsUpDown className="ml-2 h-3.5 w-3.5 opacity-50" />
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-[480px] p-0" align="start">
                  <Command shouldFilter={false}>
                    <CommandInput placeholder="검색..." value={scopeSearch} onValueChange={setScopeSearch} />
                    <CommandList>
                      <CommandEmpty>데이터셋이 없습니다.</CommandEmpty>
                      <CommandGroup>
                        {datasets.map(d => (
                          <CommandItem key={d.id} value={String(d.id)} onSelect={() => { setScopeId(d.id); setScopePopoverOpen(false) }}>
                            <Check className={`mr-2 h-3.5 w-3.5 ${scopeId === d.id ? "opacity-100" : "opacity-0"}`} />
                            <Badge variant="outline" className="mr-1.5 text-[9px] px-1 py-0">{d.datasource_type}</Badge>
                            <span className="truncate">{d.name}</span>
                          </CommandItem>
                        ))}
                      </CommandGroup>
                    </CommandList>
                  </Command>
                </PopoverContent>
              </Popover>
            )}

            {scopeType === "LINEAGE" && (
              <Select value={scopeId ? String(scopeId) : ""} onValueChange={v => setScopeId(Number(v))}>
                <SelectTrigger className="h-9 text-sm"><SelectValue placeholder="리니지 선택..." /></SelectTrigger>
                <SelectContent>
                  {lineages.map(l => (
                    <SelectItem key={l.id} value={String(l.id)} className="text-sm">
                      {l.source_datasource_type}.{l.source_dataset_name} → {l.target_datasource_type}.{l.target_dataset_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            <Separator />
            <Label className="text-sm font-medium">트리거 (Trigger 조건)</Label>

            <Select value={triggerType} onValueChange={changeTrigger}>
              <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="ANY">모든 스키마 변경</SelectItem>
                <SelectItem value="SCHEMA_CHANGE">스키마 변경 (변경 유형 선택)</SelectItem>
                <SelectItem value="COLUMN_WATCH">컬럼 감시 (특정 컬럼)</SelectItem>
                <SelectItem value="MAPPING_BROKEN">매핑 깨짐 (매핑 컬럼 변경)</SelectItem>
                <SelectItem value="QUALITY_FAILED">품질 검증 실패</SelectItem>
                <SelectItem value="MCP_POLICY_DRIFT">MCP 배포 정책 위반·드리프트</SelectItem>
                <SelectItem value="MCP_SECURITY_WARNING">MCP 배포 보안 경고</SelectItem>
              </SelectContent>
            </Select>

            {isMcpTrigger && (
              <div className="space-y-2 rounded-md border p-3">
                <p className="text-xs text-muted-foreground">
                  MCP 배포의 finding 이 새로 열리거나 재오픈될 때 발행됩니다. 범위는 “전체” 또는 “데이터 소스”(배포에 연결된 데이터 소스)만 적용됩니다.
                  배포 소유자는 “소유자 알림”이 켜져 있으면 함께 받습니다.
                </p>
                <div className="flex flex-wrap items-center gap-4">
                  <div className="flex items-center gap-2">
                    <Label className="text-sm text-muted-foreground">최소 심각도</Label>
                    <Select value={mcpMinSeverity} onValueChange={setMcpMinSeverity}>
                      <SelectTrigger className="h-8 w-[110px]"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="low">낮음</SelectItem>
                        <SelectItem value="medium">중간</SelectItem>
                        <SelectItem value="high">높음</SelectItem>
                        <SelectItem value="critical">치명</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex items-center gap-3">
                    <Label className="text-sm text-muted-foreground">환경 (비우면 전체)</Label>
                    {["DEV", "STG", "PROD"].map(env => (
                      <label key={env} className="flex items-center gap-1.5 text-sm">
                        <Checkbox
                          checked={mcpEnvs.includes(env)}
                          onCheckedChange={() => setMcpEnvs(prev => prev.includes(env) ? prev.filter(e => e !== env) : [...prev, env])}
                        />
                        {env}
                      </label>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {triggerType === "QUALITY_FAILED" && (
              <div className="flex items-center gap-2">
                <Label className="text-sm text-muted-foreground">최소 실패 심각도</Label>
                <Select value={qualityMinSeverity} onValueChange={setQualityMinSeverity}>
                  <SelectTrigger className="h-8 w-[140px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="INFO">정보 이상(전체)</SelectItem>
                    <SelectItem value="WARNING">경고 이상</SelectItem>
                    <SelectItem value="BREAKING">심각만</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}

            {usesChangeTypes && (
              <div className="flex items-center gap-4">
                <Label className="text-sm text-muted-foreground">변경 유형:</Label>
                {["DROP", "MODIFY", "ADD"].map(ct => (
                  <label key={ct} className="flex items-center gap-1.5 text-sm">
                    <Checkbox
                      checked={changeTypes.includes(ct)}
                      onCheckedChange={() => toggleChangeType(ct)}
                    />
                    {ct === "DROP" ? "삭제" : ct === "MODIFY" ? "변경" : "추가"}
                  </label>
                ))}
              </div>
            )}

            {triggerType === "COLUMN_WATCH" && (
              <div className="space-y-1.5">
                <Label className="text-sm">감시 컬럼 (콤마 구분)</Label>
                <Input
                  value={watchColumns}
                  onChange={e => setWatchColumns(e.target.value)}
                  placeholder="amount, currency, status"
                  className="h-9 text-sm"
                />
              </div>
            )}

            <div className="space-y-1.5">
              <Label className="text-sm">심각도</Label>
              <Select value={severityOverride} onValueChange={setSeverityOverride}>
                <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">자동 판정</SelectItem>
                  <SelectItem value="BREAKING">심각 (강제)</SelectItem>
                  <SelectItem value="WARNING">경고 (강제)</SelectItem>
                  <SelectItem value="INFO">정보 (강제)</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-center justify-end gap-3">
              {step1Problem && <span className="text-sm text-muted-foreground">{step1Problem}</span>}
              <Button onClick={() => { setError(null); setStep(2) }} disabled={!!step1Problem}>
                다음
              </Button>
            </div>
          </div>
        )}

        {/* ===== Step 2: 알림 대상 ===== */}
        {step === 2 && (
          <div className="space-y-4">
            {/* 요약 */}
            <div className="rounded-lg border px-4 py-3 bg-muted/30 text-sm">
              <p className="font-medium">{ruleName}</p>
              <p className="text-sm text-muted-foreground mt-1">
                대상: {scopeDisplayName()} | 트리거: {labelOf(TRIGGER_LABEL, triggerType)}
                {severityOverride !== "auto" && ` | 심각도: ${labelOf(SEVERITY_LABEL, severityOverride)}(강제)`}
              </p>
            </div>

            <Label className="text-sm font-medium">알림 채널</Label>
            <div className="flex items-center gap-4">
              {["IN_APP", "WEBHOOK"].map(ch => (
                <label key={ch} className="flex items-center gap-1.5 text-sm">
                  <Checkbox
                    checked={channels.includes(ch)}
                    onCheckedChange={() => toggleChannel(ch)}
                  />
                  {ch === "IN_APP" ? "앱 내" : "Webhook"}
                </label>
              ))}
              <span className="text-sm text-muted-foreground">이메일은 아직 지원하지 않습니다</span>
            </div>

            {channels.includes("WEBHOOK") && (
              <div className="space-y-1.5">
                <Label className="text-sm">Webhook URL</Label>
                <Input
                  value={webhookUrl}
                  onChange={e => setWebhookUrl(e.target.value)}
                  placeholder="https://hooks.slack.com/services/..."
                  className="h-9 text-sm"
                />
                <p className="text-sm text-muted-foreground">
                  알림마다 JSON 을 POST 합니다(Slack 호환 <code>text</code> 필드 포함). 전송 결과는 기록됩니다.
                </p>
              </div>
            )}

            <div className="space-y-1.5">
              <Label className="text-sm">구독자 (사용자 이름, 콤마 구분)</Label>
              <Textarea
                value={subscribers}
                onChange={e => setSubscribers(e.target.value)}
                placeholder="alice, security-team"
                rows={2}
                className="text-sm"
              />
            </div>

            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={notifyOwners}
                onCheckedChange={(v) => setNotifyOwners(!!v)}
              />
              {isMcpTrigger ? "MCP 배포 소유자에게도 알림" : "데이터셋 소유자에게도 알림"}
            </label>

            <div className="space-y-1.5">
              <Label className="text-sm">설명 (선택)</Label>
              <Textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="이 규칙의 목적을 설명하세요..."
                rows={2}
                className="text-sm"
              />
            </div>

            <Separator />
            <div className="flex items-center justify-between gap-3">
              <Button variant="outline" onClick={() => setStep(1)}>이전</Button>
              {step2Problem && <span className="ml-auto text-sm text-muted-foreground">{step2Problem}</span>}
              <Button onClick={handleSave} disabled={saving || !!step2Problem}>
                {saving ? "저장 중..." : isEdit ? "규칙 수정" : "규칙 저장"}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function StepDot({ n, label, current }: { n: number; label: string; current: number }) {
  const done = current > n
  const active = current === n
  return (
    <div className="flex items-center gap-1.5">
      <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-medium ${
        done || active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"
      }`}>
        {done ? <Check className="h-3 w-3" /> : n}
      </div>
      <span className={active ? "text-foreground font-medium" : "text-muted-foreground"}>{label}</span>
    </div>
  )
}
