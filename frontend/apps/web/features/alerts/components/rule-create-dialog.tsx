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
import { authFetch } from "@/features/auth/auth-fetch"

const BASE = "/api/v1"

type EditRule = {
  id: number
  rule_name: string
  scope_type: string
  scope_id: number | null
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
      const config = editRule.trigger_config ? JSON.parse(editRule.trigger_config) : {}
      setChangeTypes(config.change_types || ["DROP", "MODIFY"])
      setWatchColumns((config.columns || []).join(", "))
      setSeverityOverride(editRule.severity_override || "auto")
      setChannels(editRule.channels ? editRule.channels.split(",") : ["IN_APP"])
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
    authFetch(`${BASE}/catalog/tags`).then(r => r.json()).then(setTags).catch(() => {})
    authFetch(`${BASE}/catalog/datasources`).then(r => r.json()).then(setDatasources).catch(() => {})
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

  // 적용 범위(scope) 표시 이름
  const scopeDisplayName = useCallback(() => {
    if (scopeType === "ALL") return "전체 데이터셋"
    if (scopeType === "TAG" && scopeId) {
      const tag = tags.find(t => t.id === scopeId)
      return tag ? `태그: ${tag.name}` : `태그 ID: ${scopeId}`
    }
    if (scopeType === "DATASET" && scopeId) {
      const ds = datasets.find(d => d.id === scopeId)
      return ds ? `${ds.datasource_type}.${ds.name}` : `데이터셋 ID: ${scopeId}`
    }
    if (scopeType === "LINEAGE" && scopeId) {
      const l = lineages.find(l => l.id === scopeId)
      return l ? `${l.source_datasource_type}.${l.source_dataset_name} → ${l.target_datasource_type}.${l.target_dataset_name}` : `Lineage ID: ${scopeId}`
    }
    if (scopeType === "DATASOURCE" && scopeId) {
      const p = datasources.find(p => p.id === scopeId)
      return p ? `데이터 소스: ${p.name}` : `데이터 소스 ID: ${scopeId}`
    }
    return "선택..."
  }, [scopeType, scopeId, tags, datasets, lineages, datasources])

  const handleSave = async () => {
    if (!ruleName.trim()) return
    setSaving(true)
    setError(null)

    const triggerConfig: Record<string, unknown> = {}
    if (triggerType === "SCHEMA_CHANGE") {
      triggerConfig.change_types = changeTypes
    } else if (triggerType === "COLUMN_WATCH") {
      triggerConfig.columns = watchColumns.split(",").map(c => c.trim()).filter(Boolean)
      triggerConfig.change_types = changeTypes
    }

    const body = {
      rule_name: ruleName,
      description: description || null,
      scope_type: scopeType,
      scope_id: scopeType === "ALL" ? null : scopeId,
      trigger_type: triggerType,
      trigger_config: JSON.stringify(triggerConfig),
      severity_override: severityOverride === "auto" ? null : severityOverride,
      channels: channels.join(","),
      notify_owners: notifyOwners ? "true" : "false",
      webhook_url: channels.includes("WEBHOOK") ? webhookUrl || null : null,
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
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}))
        throw new Error(data.detail || `HTTP ${resp.status}`)
      }
      onCreated()
      onOpenChange(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장에 실패했습니다.")
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
                  onClick={() => { setScopeType(t); setScopeId(null) }}
                >
                  {t === "DATASET" && "데이터셋"}
                  {t === "TAG" && "태그"}
                  {t === "LINEAGE" && "리니지"}
                  {t === "DATASOURCE" && "데이터 소스"}
                  {t === "ALL" && "전체"}
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

            <Select value={triggerType} onValueChange={setTriggerType}>
              <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="ANY">모든 변경 (Any Change)</SelectItem>
                <SelectItem value="SCHEMA_CHANGE">스키마 변경 (변경 유형 선택)</SelectItem>
                <SelectItem value="COLUMN_WATCH">컬럼 감시 (특정 컬럼)</SelectItem>
                <SelectItem value="MAPPING_BROKEN">매핑 깨짐 (매핑 컬럼 변경)</SelectItem>
                <SelectItem value="QUALITY_FAILED">품질 검증 실패</SelectItem>
              </SelectContent>
            </Select>

            {(triggerType === "SCHEMA_CHANGE" || triggerType === "COLUMN_WATCH") && (
              <div className="flex items-center gap-4">
                <Label className="text-sm text-muted-foreground">변경 유형:</Label>
                {["DROP", "MODIFY", "ADD"].map(ct => (
                  <label key={ct} className="flex items-center gap-1.5 text-sm">
                    <Checkbox
                      checked={changeTypes.includes(ct)}
                      onCheckedChange={() => toggleChangeType(ct)}
                    />
                    {ct === "DROP" ? "삭제" : ct === "MODIFY" ? "수정" : "추가"}
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

            <div className="flex justify-end">
              <Button onClick={() => setStep(2)} disabled={!ruleName.trim() || (scopeType !== "ALL" && !scopeId)}>
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
                대상: {scopeType} {scopeId ? `(${scopeDisplayName()})` : ""} | 트리거: {triggerType}
              </p>
            </div>

            <Label className="text-sm font-medium">알림 채널 (Channels)</Label>
            <div className="flex items-center gap-4">
              {["IN_APP", "WEBHOOK", "EMAIL"].map(ch => (
                <label key={ch} className="flex items-center gap-1.5 text-sm">
                  <Checkbox
                    checked={channels.includes(ch)}
                    onCheckedChange={() => toggleChannel(ch)}
                  />
                  {ch === "IN_APP" ? "앱 내" : ch === "WEBHOOK" ? "Webhook" : "이메일"}
                </label>
              ))}
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
              </div>
            )}

            <div className="space-y-1.5">
              <Label className="text-sm">구독자 (콤마 구분)</Label>
              <Textarea
                value={subscribers}
                onChange={e => setSubscribers(e.target.value)}
                placeholder="security-team@company.com, dpo@company.com"
                rows={2}
                className="text-sm"
              />
            </div>

            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={notifyOwners}
                onCheckedChange={(v) => setNotifyOwners(!!v)}
              />
              데이터셋 소유자에게도 알림
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
            <div className="flex justify-between">
              <Button variant="outline" onClick={() => setStep(1)}>이전</Button>
              <Button onClick={handleSave} disabled={saving}>
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
