"use client"

// Dataset 상세 페이지의 "표준 용어" 탭 — 선택한 사전과 컬럼 매핑 상태(준수도)를 관리하고
// 자동/수동 매핑 및 초기화 기능을 제공한다.

import { Tip } from "@/components/tip"
import { useCallback, useEffect, useState } from "react"
import { StatusBadge } from "@workspace/ui/components/status-badge"
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
import { toast, notifyError } from "@/lib/notify"
import { authFetch, throwOnError } from "@/features/auth/auth-fetch"

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
  created: number; updated: number; removed: number; matched: number
  similar: number; violation: number; unmapped: number
}

type TermSummary = {
  id: number; term_name: string; physical_name: string
  domain_name: string | null; domain_data_type: string | null
}

type Props = {
  datasetId: number
  /** 매핑 변경(자동·수동 매핑, 초기화, 기본 사전 저장) 가능 여부 — 데이터셋 소유자(생성자 · 소유자 목록) 또는 관리자 */
  canEdit?: boolean
}


// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function TermsTab({ datasetId, canEdit = false }: Props) {
  const [dictionaries, setDictionaries] = useState<Dictionary[] | null>(null)
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
      authFetch(`${BASE}/dictionaries`).then(async (r) => {
        if (!r.ok) await throwOnError(r, "사전 목록을 불러오지 못했습니다.")
        return r.json()
      }),
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
        setDictionaries([])
        notifyError(err, "사전 목록을 불러오지 못했습니다.")
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
      if (!resp.ok) await throwOnError(resp, "용어 매핑을 불러오지 못했습니다.")
      setMapping(await resp.json())
    } catch (err) {
      notifyError(err, "용어 매핑을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [selectedDictId, datasetId])

  useEffect(() => { loadMapping() }, [loadMapping])

  // 매핑 초기화 — 선택한 사전의 매핑만 일괄 삭제(다른 사전 매핑은 유지). 되돌릴 수 없음.
  const handleResetMappings = async () => {
    if (!selectedDictId) return
    setResetting(true)
    try {
      const resp = await authFetch(
        `${BASE}/datasets/${datasetId}/mappings?dictionary_id=${selectedDictId}`, { method: "DELETE" },
      )
      if (!resp.ok) await throwOnError(resp, "용어 매핑을 초기화하지 못했습니다.")
      const r = await resp.json().catch(() => ({ deleted: 0 }))
      toast.success(`매핑 ${r.deleted ?? 0}건을 초기화했습니다.`)
      setAutoMapResult(null)
      setResetOpen(false)
      await loadMapping()
    } catch (err) {
      notifyError(err, "용어 매핑을 초기화하지 못했습니다.")
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
      if (!resp.ok) await throwOnError(resp, "용어를 자동 매핑하지 못했습니다.")
      setAutoMapResult(await resp.json())
      await loadMapping()
    } catch (err) {
      notifyError(err, "용어를 자동 매핑하지 못했습니다.")
    } finally {
      setAutoMapping(false)
    }
  }

  // 사용자가 popover 에서 직접 선택한 용어를 컬럼(schema_id) 에 SIMILAR 로 연결.
  const handleManualMap = async (schemaId: number, termId: number) => {
    try {
      const resp = await authFetch(`${BASE}/mappings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          term_id: termId,
          dataset_id: datasetId,
          schema_id: schemaId,
          mapping_type: "SIMILAR",
        }),
      })
      if (!resp.ok) await throwOnError(resp, "용어를 매핑하지 못했습니다.")
    } catch (err) {
      notifyError(err, "용어를 매핑하지 못했습니다.")
    }
    await loadMapping()
  }

  const handleDeleteMapping = async (mappingId: number) => {
    try {
      const resp = await authFetch(`${BASE}/mappings/${mappingId}`, { method: "DELETE" })
      if (!resp.ok) await throwOnError(resp, "용어 매핑을 제거하지 못했습니다.")
    } catch (err) {
      notifyError(err, "용어 매핑을 제거하지 못했습니다.")
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
            // 선택을 데이터셋 기본 사전으로 저장(편집 권한이 있을 때만) — 실패해도 화면 선택은 유지.
            if (!canEdit) return
            authFetch(`${BASE}/datasets/${datasetId}/dictionary`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ dictionary_id: id }),
            }).then(async (r) => {
              if (!r.ok) await throwOnError(r, "기본 사전을 저장하지 못했습니다.")
            }).catch((err) => notifyError(err, "기본 사전 선택을 저장하지 못했습니다."))
          }}
        >
          <SelectTrigger className="w-64 h-9">
            <SelectValue placeholder="사전 선택…" />
          </SelectTrigger>
          <SelectContent>
            {(dictionaries ?? []).map(d => (
              <SelectItem key={d.id} value={String(d.id)}>
                {d.dict_name} {d.version && `(${d.version})`}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {canEdit && (<>
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
          {autoMapping ? "매핑 중…" : "자동 매핑"}
        </Button>
        </>)}

        {autoMapResult && (
          <div className="flex items-center gap-2 text-xs">
            <Check className="h-3.5 w-3.5 text-green-500" />
            신규 {autoMapResult.created}건, 갱신 {autoMapResult.updated}건
            {autoMapResult.removed > 0 && `, 제거 ${autoMapResult.removed}건`}
            {" "}· 일치 {autoMapResult.matched} · 위반 {autoMapResult.violation} · 미매핑 {autoMapResult.unmapped}
          </div>
        )}
      </div>

      {/* Compliance bar */}
      {stats && stats.total_columns > 0 && (
        <div className="py-1">
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
        </div>
      )}

      {/* Column mapping table */}
      {loading ? (
        <p className="py-12 text-center text-sm text-muted-foreground">불러오는 중…</p>
      ) : !mapping || mapping.columns.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 gap-3">
            <Shield className="h-10 w-10 text-muted-foreground/40" />
            <p className="text-sm">
              {selectedDictId
                ? "스키마 컬럼이 없습니다. 스키마를 추가한 뒤 자동 매핑을 실행하세요."
                : dictionaries && dictionaries.length === 0
                  ? "등록된 표준 사전이 없습니다. '데이터 표준' 메뉴에서 사전을 먼저 만드세요."
                  : "용어 매핑을 보려면 사전을 선택하세요."}
            </p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-md border">
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
                      <span className="text-sm font-mono" style={{ fontFamily: "var(--font-d2coding), 'D2Coding', Consolas, monospace" }}>
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
                          <span className="text-sm ml-1.5">
                            ({col.term_physical_name})
                          </span>
                        </div>
                      ) : !canEdit ? (
                        <span className="text-sm text-muted-foreground">—</span>
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
                        {canEdit && col.mapping_id && (
                          <Tip label="매핑 제거"><Button
                            variant="ghost"
                            size="icon"
                            className="h-5 w-5 text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40"
                            onClick={() => handleDeleteMapping(col.mapping_id!)}
                           
                            aria-label="매핑 제거"
                          >
                            <X className="h-3.5 w-3.5" />
                          </Button></Tip>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
        </div>
      )}

      {/* Violations summary */}
      {mapping && mapping.columns.some(c => c.mapping_type === "VIOLATION") && (
        <div className="rounded-md border border-red-200 px-4 py-3 dark:border-red-900">
            <div className="flex items-center gap-2 mb-2">
              <AlertTriangle className="h-4 w-4 text-red-500" />
              <span className="text-sm font-medium text-red-600 dark:text-red-400">
                표준 위반
              </span>
            </div>
            <div className="space-y-1">
              {mapping.columns
                .filter(c => c.mapping_type === "VIOLATION")
                .map(c => (
                  <p key={c.schema_id} className="text-xs">
                    <code className="font-mono text-red-600 dark:text-red-400">{c.column_name}</code>
                    : 실제 {c.native_type || c.column_type} → 표준{" "}
                    <code className="font-mono">
                      {c.term_data_type}{c.term_data_length && `(${c.term_data_length})`}
                    </code>
                    {" "}— 용어 &quot;{c.term_name}&quot; 의 도메인 타입과 다릅니다.
                  </p>
                ))}
            </div>
        </div>
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
              {`'${dictionaries?.find((d) => d.id === selectedDictId)?.dict_name ?? "선택한 사전"}' 기준 매핑이 모두 삭제됩니다(다른 사전의 매핑은 유지). 되돌릴 수 없습니다.`}
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setResetOpen(false)} disabled={resetting}>
              취소
            </Button>
            <Button variant="destructive" onClick={handleResetMappings} disabled={resetting}>
              {resetting ? "초기화 중…" : "초기화"}
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

  // popover 열림/검색어 변경 시 용어 검색 (사전 범위로 한정, 입력 300ms 디바운스).
  useEffect(() => {
    if (!open) return
    let alive = true
    const t = setTimeout(() => {
      const params = new URLSearchParams({ dictionary_id: String(dictionaryId) })
      if (search.trim()) params.set("search", search.trim())
      authFetch(`${BASE}/terms?${params}`)
        .then(async (r) => {
          if (!r.ok) await throwOnError(r, "용어를 검색하지 못했습니다.")
          return r.json()
        })
        .then((d) => { if (alive) setTerms(d) })
        .catch((err) => notifyError(err, "용어를 검색하지 못했습니다."))
    }, search ? 300 : 0)
    return () => { alive = false; clearTimeout(t) }
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
            placeholder="용어 검색…"
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
                    <code className="text-[11px]">{t.physical_name}</code>
                    {t.domain_name && (
                      <span className="ml-auto text-[11px]">
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
  if (type === "MATCHED") return <StatusBadge tone="success">일치</StatusBadge>
  if (type === "SIMILAR") return <StatusBadge tone="warning">유사</StatusBadge>
  if (type === "VIOLATION") return <StatusBadge tone="danger">위반</StatusBadge>
  return <StatusBadge tone="muted">미연결</StatusBadge>
}
