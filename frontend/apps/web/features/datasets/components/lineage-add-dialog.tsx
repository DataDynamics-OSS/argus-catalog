"use client"

// Dataset 상세 페이지 "리니지" 탭에서 호출되는 3-step 다이얼로그 — 방향 → 데이터셋 선택 →
// 컬럼 매핑 순으로 수동 리니지를 등록한다.

import { useCallback, useEffect, useState } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@workspace/ui/components/dialog"
import { Button } from "@workspace/ui/components/button"
import { Label } from "@workspace/ui/components/label"
import { Textarea } from "@workspace/ui/components/textarea"
import { Badge } from "@workspace/ui/components/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@workspace/ui/components/command"
import { Popover, PopoverContent, PopoverTrigger } from "@workspace/ui/components/popover"
import { Separator } from "@workspace/ui/components/separator"
import { ArrowDown, ArrowUp, ArrowRight, Plus, Trash2, Check, ChevronsUpDown } from "lucide-react"
import { authFetch } from "@/features/auth/auth-fetch"

const BASE = "/api/v1/catalog"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Datasource = {
  id: number
  datasource_id: string
  name: string
  type: string
}

type DatasetSummary = {
  id: number
  urn: string
  name: string
  datasource_name: string
  datasource_type: string
}

type SchemaField = {
  id: number
  field_path: string
  field_type: string
}

type PipelineSummary = {
  id: number
  pipeline_name: string
  pipeline_type: string
}

type ColumnMapping = {
  source_column: string
  target_column: string
  transform_type: string
  transform_expr: string
}

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  datasetId: number
  datasetName: string
  onCreated: () => void
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function LineageAddDialog({
  open,
  onOpenChange,
  datasetId,
  datasetName,
  onCreated,
}: Props) {
  const [step, setStep] = useState(1)

  // Step 1: Direction
  const [direction, setDirection] = useState<"source" | "target">("source")

  // Step 2: Dataset selection
  const [datasources, setDatasources] = useState<Datasource[]>([])
  const [selectedDatasourceId, setSelectedDatasourceId] = useState<string>("all")
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [searchQuery, setSearchQuery] = useState("")
  const [selectedDatasetId, setSelectedDatasetId] = useState<number | null>(null)
  const [relationType, setRelationType] = useState("ETL")
  const [pipelines, setPipelines] = useState<PipelineSummary[]>([])
  const [selectedPipelineId, setSelectedPipelineId] = useState<string>("none")
  const [description, setDescription] = useState("")
  const [datasetPopoverOpen, setDatasetPopoverOpen] = useState(false)

  // Step 3: Column mappings
  const [sourceFields, setSourceFields] = useState<SchemaField[]>([])
  const [targetFields, setTargetFields] = useState<SchemaField[]>([])
  const [columnMappings, setColumnMappings] = useState<ColumnMapping[]>([])

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 다이얼로그가 열릴 때마다 step/입력값을 초기 상태로 되돌린다. 이전 세션 잔재 방지.
  useEffect(() => {
    if (open) {
      setStep(1)
      setDirection("source")
      setSelectedDatasetId(null)
      setRelationType("ETL")
      setSelectedPipelineId("none")
      setDescription("")
      setColumnMappings([])
      setSearchQuery("")
      setError(null)
    }
  }, [open])

  // 다이얼로그 오픈 시 데이터 소스·파이프라인 목록을 미리 로드 (step 1 에서 선택지로 노출).
  useEffect(() => {
    if (!open) return
    authFetch(`${BASE}/datasources`).then(r => r.json()).then(setDatasources).catch((err) => {
      console.error("Failed to load datasources", err)
    })
    authFetch(`${BASE}/pipelines`).then(r => r.json()).then(setPipelines).catch((err) => {
      console.error("Failed to load pipelines", err)
    })
  }, [open])

  // step 2 진입 후 데이터 소스/검색어가 변할 때 데이터셋 후보 재조회.
  // 자기 자신은 결과에서 제외하여 self-loop 리니지 생성을 막는다.
  useEffect(() => {
    if (!open || step < 2) return
    const params = new URLSearchParams()
    if (searchQuery) params.set("search", searchQuery)
    if (selectedDatasourceId && selectedDatasourceId !== "all") {
      params.set("datasource", selectedDatasourceId)
    }
    params.set("page_size", "50")

    authFetch(`${BASE}/datasets?${params}`)
      .then(r => r.json())
      .then(data => {
        const items = (data.items || []).filter(
          (d: DatasetSummary) => d.id !== datasetId
        )
        setDatasets(items)
      })
      .catch((err) => {
        console.error("Failed to search datasets", { searchQuery, selectedDatasourceId, err })
      })
  }, [open, step, searchQuery, selectedDatasourceId, datasetId])

  // step 3 진입 시 양쪽 데이터셋의 스키마 필드를 한 번에 받아와 Select 옵션으로 사용.
  // direction 에 따라 source/target 의 ID 가 뒤바뀌므로 매핑할 때 주의.
  const loadSchemaFields = useCallback(async () => {
    if (!selectedDatasetId) return

    const sourceId = direction === "source" ? datasetId : selectedDatasetId
    const targetId = direction === "source" ? selectedDatasetId : datasetId

    try {
      const [srcResp, tgtResp] = await Promise.all([
        authFetch(`${BASE}/datasets/${sourceId}`),
        authFetch(`${BASE}/datasets/${targetId}`),
      ])
      const srcData = await srcResp.json()
      const tgtData = await tgtResp.json()
      setSourceFields(srcData.schema_fields || [])
      setTargetFields(tgtData.schema_fields || [])
    } catch (err) {
      console.error("Failed to load schema fields for lineage mapping", { sourceId, targetId, err })
      setSourceFields([])
      setTargetFields([])
    }
  }, [datasetId, selectedDatasetId, direction])

  const selectedDataset = datasets.find(d => d.id === selectedDatasetId)

  // Step navigation
  const goToStep2 = () => setStep(2)
  const goToStep3 = () => {
    loadSchemaFields()
    setStep(3)
  }

  // Column mapping helpers
  const addMappingRow = () => {
    setColumnMappings(prev => [
      ...prev,
      { source_column: "", target_column: "", transform_type: "DIRECT", transform_expr: "" },
    ])
  }

  const updateMapping = (index: number, field: keyof ColumnMapping, value: string) => {
    setColumnMappings(prev =>
      prev.map((m, i) => (i === index ? { ...m, [field]: value } : m))
    )
  }

  const removeMapping = (index: number) => {
    setColumnMappings(prev => prev.filter((_, i) => i !== index))
  }

  // 최종 저장 — pipeline 이 지정된 경우 lineage_source=PIPELINE, 아니면 MANUAL.
  // 빈 컬럼 매핑은 서버에 보내지 않고 클라이언트에서 걸러낸다.
  const handleSave = async () => {
    if (!selectedDatasetId) return
    setSaving(true)
    setError(null)

    const sourceId = direction === "source" ? datasetId : selectedDatasetId
    const targetId = direction === "source" ? selectedDatasetId : datasetId

    const validMappings = columnMappings.filter(
      m => m.source_column && m.target_column
    )

    const body = {
      source_dataset_id: sourceId,
      target_dataset_id: targetId,
      relation_type: relationType,
      lineage_source: selectedPipelineId !== "none" ? "PIPELINE" : "MANUAL",
      pipeline_id: selectedPipelineId !== "none" ? Number(selectedPipelineId) : null,
      description: description || null,
      column_mappings: validMappings,
    }

    try {
      const resp = await authFetch(`${BASE}/lineage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}))
        throw new Error(data.detail || `HTTP ${resp.status}`)
      }
      console.info("Lineage created", { sourceId, targetId, relationType, mappings: validMappings.length })
      onCreated()
      onOpenChange(false)
    } catch (e) {
      console.error("Failed to create lineage", { sourceId, targetId, err: e })
      setError(e instanceof Error ? e.message : "저장에 실패했습니다")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>리니지 추가</DialogTitle>
        </DialogHeader>

        {/* Step indicator */}
        <div className="flex items-center gap-2 text-sm mb-2">
          <StepIndicator n={1} label="방향" current={step} />
          <div className="h-px flex-1 bg-border" />
          <StepIndicator n={2} label="데이터셋" current={step} />
          <div className="h-px flex-1 bg-border" />
          <StepIndicator n={3} label="컬럼" current={step} />
        </div>

        {error && (
          <p className="text-sm text-destructive bg-destructive/10 rounded px-3 py-2">
            {error}
          </p>
        )}

        {/* ============ Step 1: Direction ============ */}
        {step === 1 && (
          <div className="space-y-4">
            <div className="rounded-lg border px-4 py-3 bg-muted/30">
              <p className="text-sm font-medium">{datasetName}</p>
              <p className="text-sm text-muted-foreground">현재 데이터셋</p>
            </div>

            <Label className="text-sm font-medium">이 데이터셋의 역할:</Label>

            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setDirection("source")}
                className={`rounded-lg border-2 p-4 text-left transition-colors ${
                  direction === "source"
                    ? "border-primary bg-primary/5"
                    : "border-border hover:border-muted-foreground/50"
                }`}
              >
                <div className="flex items-center gap-2 mb-2">
                  <ArrowDown className="h-5 w-5 text-blue-500" />
                  <span className="font-medium text-sm">소스 (상위)</span>
                </div>
                <p className="text-sm text-muted-foreground">
                  이 데이터셋이 다른 데이터셋에 데이터를 제공합니다
                </p>
              </button>

              <button
                type="button"
                onClick={() => setDirection("target")}
                className={`rounded-lg border-2 p-4 text-left transition-colors ${
                  direction === "target"
                    ? "border-primary bg-primary/5"
                    : "border-border hover:border-muted-foreground/50"
                }`}
              >
                <div className="flex items-center gap-2 mb-2">
                  <ArrowUp className="h-5 w-5 text-green-500" />
                  <span className="font-medium text-sm">타겟 (하위)</span>
                </div>
                <p className="text-sm text-muted-foreground">
                  이 데이터셋이 다른 데이터셋으로부터 데이터를 받습니다
                </p>
              </button>
            </div>

            <div className="flex justify-end">
              <Button onClick={goToStep2}>다음</Button>
            </div>
          </div>
        )}

        {/* ============ Step 2: Dataset Selection ============ */}
        {step === 2 && (
          <div className="space-y-4">
            {/* Direction summary */}
            <div className="flex items-center gap-2 text-sm rounded-lg border px-4 py-3 bg-muted/30">
              {direction === "source" ? (
                <>
                  <span className="font-medium">{datasetName}</span>
                  <ArrowRight className="h-4 w-4 text-muted-foreground" />
                  <span className="text-muted-foreground">?</span>
                </>
              ) : (
                <>
                  <span className="text-muted-foreground">?</span>
                  <ArrowRight className="h-4 w-4 text-muted-foreground" />
                  <span className="font-medium">{datasetName}</span>
                </>
              )}
            </div>

            {/* Datasource filter */}
            <div className="space-y-1.5">
              <Label className="text-sm">데이터 소스</Label>
              <Select value={selectedDatasourceId} onValueChange={setSelectedDatasourceId}>
                <SelectTrigger className="h-9">
                  <SelectValue placeholder="전체 데이터 소스" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">전체 데이터 소스</SelectItem>
                  {datasources.map(p => (
                    <SelectItem key={p.id} value={p.datasource_id}>
                      {p.name} ({p.type})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Dataset search */}
            <div className="space-y-1.5">
              <Label className="text-sm">
                {direction === "source" ? "타겟 데이터셋" : "소스 데이터셋"}
              </Label>
              <Popover open={datasetPopoverOpen} onOpenChange={setDatasetPopoverOpen}>
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    role="combobox"
                    className="w-full justify-between h-9 font-normal"
                  >
                    {selectedDataset
                      ? `${selectedDataset.datasource_type} > ${selectedDataset.name}`
                      : "데이터셋 검색 후 선택..."}
                    <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-[560px] p-0" align="start">
                  <Command shouldFilter={false}>
                    <CommandInput
                      placeholder="데이터셋 검색..."
                      value={searchQuery}
                      onValueChange={setSearchQuery}
                    />
                    <CommandList>
                      <CommandEmpty>데이터셋을 찾을 수 없습니다.</CommandEmpty>
                      <CommandGroup>
                        {datasets.map(d => (
                          <CommandItem
                            key={d.id}
                            value={String(d.id)}
                            onSelect={() => {
                              setSelectedDatasetId(d.id)
                              setDatasetPopoverOpen(false)
                            }}
                          >
                            <Check
                              className={`mr-2 h-4 w-4 ${
                                selectedDatasetId === d.id ? "opacity-100" : "opacity-0"
                              }`}
                            />
                            <Badge variant="outline" className="mr-2 text-sm px-1.5 py-0">
                              {d.datasource_type}
                            </Badge>
                            <span className="truncate">{d.name}</span>
                            <span className="ml-auto text-sm text-muted-foreground truncate">
                              {d.datasource_name}
                            </span>
                          </CommandItem>
                        ))}
                      </CommandGroup>
                    </CommandList>
                  </Command>
                </PopoverContent>
              </Popover>
            </div>

            {/* Relation type */}
            <div className="space-y-1.5">
              <Label className="text-sm">관계 유형</Label>
              <Select value={relationType} onValueChange={setRelationType}>
                <SelectTrigger className="h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ETL">ETL</SelectItem>
                  <SelectItem value="FILE_EXPORT">파일 내보내기</SelectItem>
                  <SelectItem value="CDC">CDC</SelectItem>
                  <SelectItem value="REPLICATION">복제</SelectItem>
                  <SelectItem value="DERIVED">파생</SelectItem>
                  <SelectItem value="READ_WRITE">읽기/쓰기</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Pipeline (optional) */}
            <div className="space-y-1.5">
              <Label className="text-sm">파이프라인 (선택)</Label>
              <Select value={selectedPipelineId} onValueChange={setSelectedPipelineId}>
                <SelectTrigger className="h-9">
                  <SelectValue placeholder="없음" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">없음</SelectItem>
                  {pipelines.map(p => (
                    <SelectItem key={p.id} value={String(p.id)}>
                      {p.pipeline_name} ({p.pipeline_type})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Description */}
            <div className="space-y-1.5">
              <Label className="text-sm">설명 (선택)</Label>
              <Textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="예: HR 데이터를 Parquet 으로 내보내 Impala 에 적재"
                rows={2}
                className="text-sm"
              />
            </div>

            <div className="flex justify-between">
              <Button variant="outline" onClick={() => setStep(1)}>이전</Button>
              <Button onClick={goToStep3} disabled={!selectedDatasetId}>
                다음
              </Button>
            </div>
          </div>
        )}

        {/* ============ Step 3: Column Mapping ============ */}
        {step === 3 && (
          <div className="space-y-4">
            {/* Summary */}
            <div className="flex items-center gap-2 text-sm rounded-lg border px-4 py-3 bg-muted/30">
              <span className="font-medium">
                {direction === "source" ? datasetName : selectedDataset?.name}
              </span>
              <ArrowRight className="h-4 w-4 text-muted-foreground" />
              <span className="font-medium">
                {direction === "source" ? selectedDataset?.name : datasetName}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">
                컬럼 매핑
                <span className="text-sm text-muted-foreground font-normal ml-2">
                  (선택 — 영향 분석에 사용)
                </span>
              </Label>
              <Button variant="outline" size="sm" onClick={addMappingRow}>
                <Plus className="h-3.5 w-3.5 mr-1" />
                행 추가
              </Button>
            </div>

            {columnMappings.length > 0 && (
              <div className="space-y-2">
                {/* Header */}
                <div className="grid grid-cols-[1fr_auto_1fr_100px_32px] gap-2 items-center text-sm text-muted-foreground px-1">
                  <span>소스 컬럼</span>
                  <span />
                  <span>타겟 컬럼</span>
                  <span>유형</span>
                  <span />
                </div>

                {columnMappings.map((m, i) => (
                  <div
                    key={i}
                    className="grid grid-cols-[1fr_auto_1fr_100px_32px] gap-2 items-center"
                  >
                    {/* Source column */}
                    <Select
                      value={m.source_column}
                      onValueChange={v => updateMapping(i, "source_column", v)}
                    >
                      <SelectTrigger className="h-9 text-sm">
                        <SelectValue placeholder="선택..." />
                      </SelectTrigger>
                      <SelectContent>
                        {sourceFields.map(f => (
                          <SelectItem key={f.field_path} value={f.field_path}>
                            {f.field_path}
                            <span className="text-muted-foreground ml-1">({f.field_type})</span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>

                    <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />

                    {/* Target column */}
                    <Select
                      value={m.target_column}
                      onValueChange={v => updateMapping(i, "target_column", v)}
                    >
                      <SelectTrigger className="h-9 text-sm">
                        <SelectValue placeholder="선택..." />
                      </SelectTrigger>
                      <SelectContent>
                        {targetFields.map(f => (
                          <SelectItem key={f.field_path} value={f.field_path}>
                            {f.field_path}
                            <span className="text-muted-foreground ml-1">({f.field_type})</span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>

                    {/* Transform type */}
                    <Select
                      value={m.transform_type}
                      onValueChange={v => updateMapping(i, "transform_type", v)}
                    >
                      <SelectTrigger className="h-9 text-sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="DIRECT">직접</SelectItem>
                        <SelectItem value="CAST">캐스팅</SelectItem>
                        <SelectItem value="EXPRESSION">표현식</SelectItem>
                        <SelectItem value="DERIVED">파생</SelectItem>
                      </SelectContent>
                    </Select>

                    {/* Delete */}
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8"
                      onClick={() => removeMapping(i)}
                    >
                      <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
                    </Button>
                  </div>
                ))}
              </div>
            )}

            {columnMappings.length === 0 && (
              <div className="text-center py-6 text-sm text-muted-foreground border rounded-lg border-dashed">
                추가된 컬럼 매핑이 없습니다.
                <br />
                <span className="text-sm">
                  이 단계는 건너뛰고 나중에 매핑을 추가할 수 있습니다.
                </span>
              </div>
            )}

            <Separator />

            <div className="flex justify-between">
              <Button variant="outline" onClick={() => setStep(2)}>이전</Button>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  onClick={handleSave}
                  disabled={saving}
                >
                  건너뛰고 저장
                </Button>
                <Button onClick={handleSave} disabled={saving}>
                  {saving ? "저장 중..." : "저장"}
                </Button>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Step indicator
// ---------------------------------------------------------------------------

function StepIndicator({
  n,
  label,
  current,
}: {
  n: number
  label: string
  current: number
}) {
  const done = current > n
  const active = current === n

  return (
    <div className="flex items-center gap-1.5">
      <div
        className={`w-5 h-5 rounded-full flex items-center justify-center text-sm font-medium ${
          done
            ? "bg-primary text-primary-foreground"
            : active
              ? "bg-primary text-primary-foreground"
              : "bg-muted text-muted-foreground"
        }`}
      >
        {done ? <Check className="h-3 w-3" /> : n}
      </div>
      <span
        className={`${
          active ? "text-foreground font-medium" : "text-muted-foreground"
        }`}
      >
        {label}
      </span>
    </div>
  )
}
