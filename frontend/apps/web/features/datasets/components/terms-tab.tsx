"use client"

// Dataset 상세 페이지의 "표준 용어" 탭 — 선택한 사전과 컬럼 매핑 상태(준수도)를 관리하고
// 자동/수동 매핑 및 초기화 기능을 제공한다.

import { useCallback, useEffect, useState } from "react"
import { Card, CardContent } from "@workspace/ui/components/card"
import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import {
  Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@workspace/ui/components/command"
import { Popover, PopoverContent, PopoverTrigger } from "@workspace/ui/components/popover"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@workspace/ui/components/table"
import { Label } from "@workspace/ui/components/label"
import { Separator } from "@workspace/ui/components/separator"
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import {
  AlertTriangle, ArrowRight, Check, ChevronsUpDown, RotateCcw, Shield, X, Zap,
} from "lucide-react"
import { toast } from "sonner"
import { authFetch } from "@/features/auth/auth-fetch"

const BASE = "/api/v1/standards"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Dictionary = {
  id: number; dict_name: string; version: string | null
}

type ColumnTermStatus = {
  schema_id: number
  column_name: string
  column_type: string
  native_type: string | null
  mapping_id: number | null
  mapping_type: string | null
  term_id: number | null
  term_name: string | null
  term_physical_name: string | null
  term_data_type: string | null
  term_data_length: number | null
}

type ComplianceStats = {
  total_columns: number
  matched: number
  similar: number
  violation: number
  unmapped: number
  compliance_rate: number
}

type DatasetTermMapping = {
  dataset_id: number
  dictionary_id: number
  columns: ColumnTermStatus[]
  compliance: ComplianceStats
}

type AutoMapResult = {
  created: number; updated: number; matched: number
  similar: number; violation: number; unmapped: number
}

type TermSummary = {
  id: number; term_name: string; physical_name: string
  domain_name: string | null; domain_data_type: string | null
}

type Props = {
  datasetId: number
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function TermsTab({ datasetId }: Props) {
  const [dictionaries, setDictionaries] = useState<Dictionary[]>([])
  const [selectedDictId, setSelectedDictId] = useState<number | null>(null)
  const [mapping, setMapping] = useState<DatasetTermMapping | null>(null)
  const [loading, setLoading] = useState(false)
  const [autoMapping, setAutoMapping] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [autoMapResult, setAutoMapResult] = useState<AutoMapResult | null>(null)

  // 최초 마운트 시 사전 목록과 데이터셋에 저장된 사전 선택을 함께 로드.
  // 저장된 선택이 유효하면 우선 적용, 없으면 첫 번째 사전을 기본으로 지정.
  useEffect(() => {
    Promise.all([
      authFetch(`${BASE}/dictionaries`).then(r => r.json()),
      authFetch(`${BASE}/datasets/${datasetId}/dictionary`)
        .then(r => (r.ok ? r.json() : { dictionary_id: null }))
        .catch(() => ({ dictionary_id: null })),
    ])
      .then(([data, saved]: [Dictionary[], { dictionary_id: number | null }]) => {
        setDictionaries(data)
        const savedId = saved?.dictionary_id
        if (savedId && data.some(d => d.id === savedId)) {
          setSelectedDictId(savedId)
        } else if (data.length > 0) {
          setSelectedDictId(data[0]!.id)
        }
      })
      .catch((err) => {
        console.error("Failed to load dictionaries", err)
      })
  }, [datasetId])

  // 사전 변경 시 컬럼 매핑/준수도 다시 로드.
  const loadMapping = useCallback(async () => {
    if (!selectedDictId) return
    setLoading(true)
    try {
      const resp = await authFetch(
        `${BASE}/mappings/dataset?dictionary_id=${selectedDictId}&dataset_id=${datasetId}`
      )
      if (resp.ok) setMapping(await resp.json())
    } catch (err) {
      console.error("Failed to load term mappings", { datasetId, selectedDictId, err })
    } finally {
      setLoading(false)
    }
  }, [selectedDictId, datasetId])

  useEffect(() => { loadMapping() }, [loadMapping])

  // 매핑 초기화 — dataset_id 기준 일괄 삭제. 되돌릴 수 없음.
  const handleResetMappings = async () => {
    setResetting(true)
    try {
      const resp = await authFetch(`${BASE}/datasets/${datasetId}/mappings`, { method: "DELETE" })
      if (!resp.ok) {
        console.error("Failed to reset term mappings", { datasetId, status: resp.status })
        toast.error("매핑 초기화에 실패했습니다.")
        return
      }
      const r = await resp.json().catch(() => ({ deleted: 0 }))
      console.info("Term mappings reset", { datasetId, deleted: r.deleted ?? 0 })
      toast.success(`매핑 ${r.deleted ?? 0}건을 초기화했습니다.`)
      setAutoMapResult(null)
      setResetOpen(false)
      await loadMapping()
    } finally {
      setResetting(false)
    }
  }

  // 자동 매핑 — 선택된 사전을 기준으로 서버에서 컬럼-용어 자동 매칭 수행.
  const handleAutoMap = async () => {
    if (!selectedDictId) return
    setAutoMapping(true)
    setAutoMapResult(null)
    try {
      const resp = await authFetch(
        `${BASE}/mappings/auto-map?dictionary_id=${selectedDictId}&dataset_id=${datasetId}`,
        { method: "POST" },
      )
      if (resp.ok) {
        const result = await resp.json()
        setAutoMapResult(result)
        await loadMapping()
      } else {
        console.error("Failed to auto-map terms", { datasetId, selectedDictId, status: resp.status })
      }
    } catch (err) {
      console.error("Failed to auto-map terms", { datasetId, selectedDictId, err })
    } finally {
      setAutoMapping(false)
    }
  }

  // 사용자가 popover 에서 직접 선택한 용어를 컬럼(schema_id) 에 SIMILAR 로 연결.
  const handleManualMap = async (schemaId: number, termId: number) => {
    try {
      await authFetch(`${BASE}/mappings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          term_id: termId,
          dataset_id: datasetId,
          schema_id: schemaId,
          mapping_type: "SIMILAR",
        }),
      })
    } catch (err) {
      console.error("Failed to create manual term mapping", { schemaId, termId, err })
    }
    await loadMapping()
  }

  const handleDeleteMapping = async (mappingId: number) => {
    try {
      await authFetch(`${BASE}/mappings/${mappingId}`, { method: "DELETE" })
    } catch (err) {
      console.error("Failed to delete term mapping", { mappingId, err })
    }
    await loadMapping()
  }

  const stats = mapping?.compliance

  return (
    <div className="space-y-4">
      {/* Header: Dictionary selector + Auto Map */}
      <div className="flex items-center gap-3">
        <Label className="text-sm">사전:</Label>
        <Select
          value={selectedDictId ? String(selectedDictId) : ""}
          onValueChange={v => {
            const id = Number(v)
            setSelectedDictId(id)
            setAutoMapResult(null)
            // 선택을 데이터셋에 영속화 — 실패해도 화면 선택은 유지(다음 방문 때 이전 값으로 복원될 뿐).
            authFetch(`${BASE}/datasets/${datasetId}/dictionary`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ dictionary_id: id }),
            }).catch((err) => console.error("Failed to save dictionary selection", err))
          }}
        >
          <SelectTrigger className="w-64 h-9">
            <SelectValue placeholder="사전 선택..." />
          </SelectTrigger>
          <SelectContent>
            {dictionaries.map(d => (
              <SelectItem key={d.id} value={String(d.id)}>
                {d.dict_name} {d.version && `(${d.version})`}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Button
          variant="outline"
          size="sm"
          onClick={() => setResetOpen(true)}
          disabled={resetting || !mapping || mapping.columns.every((c) => !c.mapping_id)}
          className="gap-1.5"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          매핑 초기화
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={handleAutoMap}
          disabled={autoMapping || !selectedDictId}
          className="gap-1.5"
        >
          <Zap className="h-3.5 w-3.5" />
          {autoMapping ? "매핑 중..." : "자동 매핑"}
        </Button>

        {autoMapResult && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Check className="h-3.5 w-3.5 text-green-500" />
            신규 {autoMapResult.created}건, 갱신 {autoMapResult.updated}건
          </div>
        )}
      </div>

      {/* Compliance bar */}
      {stats && stats.total_columns > 0 && (
        <Card>
          <CardContent className="py-3 px-4">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <Shield className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-medium">준수도</span>
              </div>
              <div className="flex-1">
                <div className="w-full bg-muted rounded-full h-2.5">
                  <div
                    className={`h-2.5 rounded-full transition-all ${
                      stats.compliance_rate >= 80 ? "bg-green-500"
                        : stats.compliance_rate >= 50 ? "bg-amber-500"
                          : "bg-red-500"
                    }`}
                    style={{ width: `${stats.compliance_rate}%` }}
                  />
                </div>
              </div>
              <span className="text-sm font-bold min-w-[48px] text-right">
                {stats.compliance_rate}%
              </span>
              <Separator orientation="vertical" className="h-5" />
              <div className="flex items-center gap-3 text-xs">
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-green-500" />
                  {stats.matched}
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-amber-500" />
                  {stats.similar}
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-red-500" />
                  {stats.violation}
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-gray-300" />
                  {stats.unmapped}
                </span>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Column mapping table */}
      {loading ? (
        <Card>
          <CardContent className="flex items-center justify-center py-12">
            <p className="text-sm text-muted-foreground">불러오는 중...</p>
          </CardContent>
        </Card>
      ) : !mapping || mapping.columns.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12 gap-3">
            <Shield className="h-10 w-10 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">
              {selectedDictId
                ? "스키마 컬럼이 없습니다. 스키마를 추가한 뒤 자동 매핑을 실행하세요."
                : "용어 매핑을 보려면 사전을 선택하세요."}
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10" />
                  <TableHead>컬럼</TableHead>
                  <TableHead className="w-32">실제 타입</TableHead>
                  <TableHead className="w-8" />
                  <TableHead>표준 용어</TableHead>
                  <TableHead className="w-36">표준 타입</TableHead>
                  <TableHead className="w-24">상태</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {mapping.columns.map(col => (
                  <TableRow key={col.schema_id}>
                    {/* Status icon */}
                    <TableCell className="text-center">
                      <StatusIcon type={col.mapping_type} />
                    </TableCell>

                    {/* Column name */}
                    <TableCell>
                      <code className="text-sm font-mono" style={{ fontFamily: "var(--font-d2coding), 'D2Coding', Consolas, monospace" }}>{col.column_name}</code>
                    </TableCell>

                    {/* Actual type */}
                    <TableCell>
                      <span className="text-sm text-muted-foreground font-mono" style={{ fontFamily: "var(--font-d2coding), 'D2Coding', Consolas, monospace" }}>
                        {col.native_type || col.column_type}
                      </span>
                    </TableCell>

                    {/* Arrow */}
                    <TableCell>
                      {col.term_id && (
                        <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
                      )}
                    </TableCell>

                    {/* Standard term */}
                    <TableCell>
                      {col.term_name ? (
                        <div>
                          <span className="text-sm font-medium">{col.term_name}</span>
                          <span className="text-sm text-muted-foreground ml-1.5">
                            ({col.term_physical_name})
                          </span>
                        </div>
                      ) : (
                        <ManualMapPicker
                          dictionaryId={selectedDictId!}
                          schemaId={col.schema_id}
                          columnName={col.column_name}
                          onSelect={(termId) => handleManualMap(col.schema_id, termId)}
                        />
                      )}
                    </TableCell>

                    {/* Standard type */}
                    <TableCell>
                      {col.term_data_type && (
                        <code className="text-sm" style={{ fontFamily: "var(--font-d2coding), 'D2Coding', Consolas, monospace" }}>
                          {col.term_data_type}
                          {col.term_data_length && `(${col.term_data_length})`}
                        </code>
                      )}
                    </TableCell>

                    {/* Status badge + 매핑 해제 X 버튼 */}
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <MappingBadge type={col.mapping_type} />
                        {col.mapping_id && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-5 w-5 text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40"
                            onClick={() => handleDeleteMapping(col.mapping_id!)}
                            title="이 컬럼의 매핑 제거"
                            aria-label="매핑 제거"
                          >
                            <X className="h-3.5 w-3.5" />
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
      )}

      {/* Violations summary */}
      {mapping && mapping.columns.some(c => c.mapping_type === "VIOLATION") && (
        <Card className="border-red-200 dark:border-red-900">
          <CardContent className="py-3 px-4">
            <div className="flex items-center gap-2 mb-2">
              <AlertTriangle className="h-4 w-4 text-red-500" />
              <span className="text-sm font-medium text-red-600 dark:text-red-400">
                Standard Violations
              </span>
            </div>
            <div className="space-y-1">
              {mapping.columns
                .filter(c => c.mapping_type === "VIOLATION")
                .map(c => (
                  <p key={c.schema_id} className="text-xs text-muted-foreground">
                    <code className="font-mono text-red-600 dark:text-red-400">{c.column_name}</code>
                    : {c.native_type || c.column_type} → standard{" "}
                    <code className="font-mono">
                      {c.term_data_type}{c.term_data_length && `(${c.term_data_length})`}
                    </code>
                    . Type mismatch with term &quot;{c.term_name}&quot;.
                  </p>
                ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 매핑 초기화 확인 다이얼로그 */}
      <Dialog open={resetOpen} onOpenChange={(o) => { if (!o && !resetting) setResetOpen(false) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-destructive">
              <AlertTriangle className="h-5 w-5" />
              매핑 초기화
            </DialogTitle>
            <DialogDescription>
              매핑 정보가 초기화됩니다. 다시 되돌릴 수 없습니다.
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setResetOpen(false)} disabled={resetting}>
              취소
            </Button>
            <Button variant="destructive" onClick={handleResetMappings} disabled={resetting}>
              {resetting ? "초기화 중..." : "초기화"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Manual Map Picker
// ---------------------------------------------------------------------------

function ManualMapPicker({
  dictionaryId,
  schemaId: _schemaId,
  columnName: _columnName,
  onSelect,
}: {
  dictionaryId: number
  schemaId: number
  columnName: string
  onSelect: (termId: number) => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const [terms, setTerms] = useState<TermSummary[]>([])

  // popover 열림/검색어 변경 시 용어 검색 (사전 범위로 한정).
  useEffect(() => {
    if (!open) return
    const params = new URLSearchParams({ dictionary_id: String(dictionaryId) })
    if (search) params.set("search", search)
    authFetch(`${BASE}/terms?${params}`)
      .then(r => r.json())
      .then(setTerms)
      .catch((err) => {
        console.error("Failed to search terms", { dictionaryId, err })
      })
  }, [open, search, dictionaryId])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="h-7 text-sm gap-1 font-normal">
          <ChevronsUpDown className="h-3 w-3" />
          용어 매핑
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[400px] p-0" align="start">
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="용어 검색..."
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            <CommandEmpty>용어를 찾을 수 없습니다.</CommandEmpty>
            <CommandGroup>
              {terms.map(t => (
                <CommandItem
                  key={t.id}
                  value={String(t.id)}
                  onSelect={() => {
                    onSelect(t.id)
                    setOpen(false)
                  }}
                >
                  <div className="flex items-center gap-2 w-full">
                    <span className="font-medium">{t.term_name}</span>
                    <code className="text-[10px] text-muted-foreground">{t.physical_name}</code>
                    {t.domain_name && (
                      <span className="ml-auto text-[10px] text-muted-foreground">
                        {t.domain_name} ({t.domain_data_type})
                      </span>
                    )}
                  </div>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function StatusIcon({ type }: { type: string | null }) {
  if (type === "MATCHED") return <span className="flex justify-center"><span className="w-2.5 h-2.5 rounded-full bg-green-500" /></span>
  if (type === "SIMILAR") return <span className="flex justify-center"><span className="w-2.5 h-2.5 rounded-full bg-amber-500" /></span>
  if (type === "VIOLATION") return <span className="flex justify-center"><span className="w-2.5 h-2.5 rounded-full bg-red-500" /></span>
  return <span className="flex justify-center"><span className="w-2.5 h-2.5 rounded-full bg-gray-200 dark:bg-gray-700" /></span>
}

function MappingBadge({ type }: { type: string | null }) {
  if (type === "MATCHED") return <Badge className="bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200 text-xs px-2 py-0.5 border-0">일치</Badge>
  if (type === "SIMILAR") return <Badge className="bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200 text-xs px-2 py-0.5 border-0">유사</Badge>
  if (type === "VIOLATION") return <Badge className="bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200 text-xs px-2 py-0.5 border-0">위반</Badge>
  return <Badge variant="outline" className="text-xs px-2 py-0.5 text-muted-foreground">미연결</Badge>
}
