"use client"

import { useCallback, useContext, useEffect, useMemo, useRef, useState, createContext } from "react"
import { AgGridReact } from "ag-grid-react"
import { AllCommunityModule, ModuleRegistry, type ColDef, type CellValueChangedEvent } from "ag-grid-community"
import { DashboardHeader } from "@/components/dashboard-header"

ModuleRegistry.registerModules([AllCommunityModule])
import { Card, CardContent } from "@workspace/ui/components/card"
import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Textarea } from "@workspace/ui/components/textarea"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@workspace/ui/components/dialog"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import { Plus, Trash2, Search, BookOpen, Type, Grid3X3, FileCode, Hash } from "lucide-react"
import { authFetch } from "@/features/auth/auth-fetch"

const BASE = "/api/v1/standards"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Dictionary = {
  id: number; dict_name: string; description: string | null; version: string | null
  status: string; word_count: number; domain_count: number; term_count: number; code_group_count: number
}

type Word = {
  id: number; dictionary_id: number; word_name: string; word_english: string; word_abbr: string
  description: string | null; word_type: string; is_forbidden: string; status: string
}

type Domain = {
  id: number; dictionary_id: number; domain_name: string; domain_group: string | null
  data_type: string; data_length: number | null; data_precision: number | null; data_scale: number | null
  description: string | null; code_group_name: string | null; status: string
}

type TermWord = { word_id: number; word_name: string; word_abbr: string; word_type: string; ordinal: number }

type Term = {
  id: number; dictionary_id: number; term_name: string; term_english: string; term_abbr: string
  physical_name: string; domain_name: string | null; domain_data_type: string | null
  description: string | null; status: string; words: TermWord[]; mapping_count: number
}

type CodeGroup = {
  id: number; group_name: string; group_english: string | null; status: string
  values: { id: number; code_value: string; code_name: string; code_english: string | null }[]
}

type MorphemeResult = {
  words: TermWord[]; term_english: string; term_abbr: string; physical_name: string
  recommended_domain: Domain | null; unmatched_parts: string[]
}

type ComplianceStats = {
  total_columns: number; matched: number; similar: number; violation: number
  unmapped: number; compliance_rate: number
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function StandardsPage() {
  const [dictionaries, setDictionaries] = useState<Dictionary[]>([])
  const [selectedDictId, setSelectedDictId] = useState<number | null>(null)
  const [tab, setTab] = useState("words")
  const [dictDialogOpen, setDictDialogOpen] = useState(false)

  const fetchDicts = useCallback(async () => {
    const resp = await authFetch(`${BASE}/dictionaries`)
    if (resp.ok) {
      const data = await resp.json()
      setDictionaries(data)
      if (data.length > 0 && !selectedDictId) setSelectedDictId(data[0].id)
    }
  }, [selectedDictId])

  useEffect(() => { fetchDicts() }, [fetchDicts])

  const selectedDict = dictionaries.find(d => d.id === selectedDictId)

  return (
    <>
      <DashboardHeader title="데이터 표준" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        {/* 사전 선택기 */}
        <div className="flex items-center gap-3">
          <Label className="text-sm font-medium">사전:</Label>
          <Select
            value={selectedDictId ? String(selectedDictId) : ""}
            onValueChange={v => setSelectedDictId(Number(v))}
          >
            <SelectTrigger className="w-72 h-9">
              <SelectValue placeholder="사전 선택..." />
            </SelectTrigger>
            <SelectContent>
              {dictionaries.map(d => (
                <SelectItem key={d.id} value={String(d.id)}>
                  {d.dict_name}
                  {d.version && <span className="text-muted-foreground ml-1">({d.version})</span>}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" size="sm" onClick={() => setDictDialogOpen(true)}>
            <Plus className="h-3.5 w-3.5 mr-1" /> 새 사전
          </Button>

          {selectedDict && (
            <div className="ml-auto flex items-center gap-3 text-sm text-muted-foreground">
              <span>단어: <span className="text-foreground font-medium">{selectedDict.word_count}</span></span>
              <span>도메인: <span className="text-foreground font-medium">{selectedDict.domain_count}</span></span>
              <span>용어: <span className="text-foreground font-medium">{selectedDict.term_count}</span></span>
              <span>코드: <span className="text-foreground font-medium">{selectedDict.code_group_count}</span></span>
            </div>
          )}
        </div>

        {selectedDictId && (
          <Tabs value={tab} onValueChange={setTab} className="flex flex-1 flex-col min-h-0">
            <TabsList>
              <TabsTrigger value="words"><Type className="h-3.5 w-3.5 mr-1" />단어</TabsTrigger>
              <TabsTrigger value="domains"><Grid3X3 className="h-3.5 w-3.5 mr-1" />도메인</TabsTrigger>
              <TabsTrigger value="terms"><FileCode className="h-3.5 w-3.5 mr-1" />용어</TabsTrigger>
              <TabsTrigger value="codes"><Hash className="h-3.5 w-3.5 mr-1" />코드</TabsTrigger>
              <TabsTrigger value="compliance"><BookOpen className="h-3.5 w-3.5 mr-1" />준수율</TabsTrigger>
            </TabsList>

            <TabsContent value="words" className="mt-4 flex-1 flex flex-col min-h-0 h-0">
              <WordsTab dictId={selectedDictId} />
            </TabsContent>
            <TabsContent value="domains" className="mt-4 flex-1 flex flex-col min-h-0 h-0">
              <DomainsTab dictId={selectedDictId} />
            </TabsContent>
            <TabsContent value="terms" className="mt-4 flex-1 flex flex-col min-h-0 h-0">
              <TermsTab dictId={selectedDictId} />
            </TabsContent>
            <TabsContent value="codes" className="mt-4 flex-1 flex flex-col min-h-0 h-0">
              <CodesTab dictId={selectedDictId} />
            </TabsContent>
            <TabsContent value="compliance" className="mt-4">
              <ComplianceTab dictId={selectedDictId} />
            </TabsContent>
          </Tabs>
        )}
      </div>

      {/* New Dictionary Dialog */}
      <DictionaryDialog open={dictDialogOpen} onOpenChange={setDictDialogOpen} onCreated={fetchDicts} />
    </>
  )
}

// ---------------------------------------------------------------------------
// Dictionary Dialog
// ---------------------------------------------------------------------------

function DictionaryDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: () => void }) {
  const [name, setName] = useState("")
  const [version, setVersion] = useState("")
  const [desc, setDesc] = useState("")
  const [saving, setSaving] = useState(false)

  useEffect(() => { if (open) { setName(""); setVersion(""); setDesc("") } }, [open])

  const save = async () => {
    setSaving(true)
    await authFetch(`${BASE}/dictionaries`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dict_name: name, version: version || null, description: desc || null }),
    })
    setSaving(false)
    onCreated()
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader><DialogTitle>새 사전</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div><Label className="text-xs">이름</Label><Input value={name} onChange={e => setName(e.target.value)} className="h-9" /></div>
          <div><Label className="text-xs">버전</Label><Input value={version} onChange={e => setVersion(e.target.value)} placeholder="v1.0" className="h-9" /></div>
          <div><Label className="text-xs">설명</Label><Textarea value={desc} onChange={e => setDesc(e.target.value)} rows={2} /></div>
          <div className="flex justify-end"><Button onClick={save} disabled={saving || !name.trim()}>{saving ? "저장 중..." : "생성"}</Button></div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Words Tab (AG Grid — inline row editing)
// ---------------------------------------------------------------------------

// Context for delete handler
const WordDeleteCtx = createContext<(id: number) => void>(() => {})

function WordDeleteRenderer(props: { value: number }) {
  const onDelete = useContext(WordDeleteCtx)
  return (
    <button
      type="button"
      onClick={() => onDelete(props.value)}
      className="flex items-center justify-center w-full h-full text-muted-foreground hover:text-destructive transition-colors cursor-pointer"
    >
      <Trash2 className="h-3.5 w-3.5" />
    </button>
  )
}

function WordsTab({ dictId }: { dictId: number }) {
  const [words, setWords] = useState<Word[]>([])
  const gridRef = useRef<AgGridReact>(null)

  const fetchWords = useCallback(async () => {
    const r = await authFetch(`${BASE}/words?dictionary_id=${dictId}`)
    if (r.ok) setWords(await r.json())
  }, [dictId])
  useEffect(() => { fetchWords() }, [fetchWords])

  // Add new empty row
  const addWord = useCallback(async () => {
    const resp = await authFetch(`${BASE}/words`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dictionary_id: dictId,
        word_name: "(new)",
        word_english: "(new)",
        word_abbr: "NEW",
        word_type: "GENERAL",
      }),
    })
    if (resp.ok) {
      await fetchWords()
      // Focus the new row for editing
      setTimeout(() => {
        const api = gridRef.current?.api
        if (api) {
          const lastIdx = words.length  // after fetch, this is the new last row
          api.ensureIndexVisible(lastIdx)
          api.startEditingCell({ rowIndex: lastIdx, colKey: "word_name" })
        }
      }, 200)
    }
  }, [dictId, fetchWords, words.length])

  // Delete row
  const deleteWord = useCallback(async (id: number) => {
    await authFetch(`${BASE}/words/${id}`, { method: "DELETE" })
    fetchWords()
  }, [fetchWords])

  // Save on cell edit
  const onCellValueChanged = useCallback(async (event: CellValueChangedEvent) => {
    const { data, colDef, newValue, oldValue } = event
    if (newValue === oldValue) return

    const field = colDef.field
    if (!field || !data.id) return

    await authFetch(`${BASE}/words/${data.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: newValue }),
    })
  }, [])

  const columnDefs = useMemo<ColDef[]>(() => [
    {
      headerName: "#",
      valueGetter: (p) => (p.node?.rowIndex ?? 0) + 1,
      width: 50, maxWidth: 55,
      editable: false,
      sortable: false,
      cellStyle: { color: "#9ca3af", textAlign: "right" },
    },
    {
      headerName: "이름 (한글)",
      field: "word_name",
      minWidth: 120,
      editable: true,
      cellStyle: { fontWeight: 500 },
    },
    {
      headerName: "영문",
      field: "word_english",
      minWidth: 130,
      editable: true,
    },
    {
      headerName: "약어",
      field: "word_abbr",
      width: 120,
      editable: true,
      cellStyle: { fontFamily: "inherit" },
    },
    {
      headerName: "유형",
      field: "word_type",
      width: 110,
      editable: true,
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: ["GENERAL", "SUFFIX", "PREFIX"] },
      cellRenderer: (p: { value: string }) => {
        if (p.value === "SUFFIX") return "접미사"
        if (p.value === "PREFIX") return "접두사"
        return "일반"
      },
    },
    {
      headerName: "금지어",
      field: "is_forbidden",
      width: 90,
      editable: true,
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: ["true", "false"] },
      cellRenderer: (p: { value: string }) => p.value === "true" ? "예" : "",
      cellStyle: (p) => ({
        textAlign: "center",
        color: p.value === "true" ? "#ef4444" : undefined,
        fontWeight: p.value === "true" ? 600 : undefined,
      }),
    },
    {
      headerName: "설명",
      field: "description",
      minWidth: 200,
      flex: 1,
      editable: true,
    },
    {
      headerName: "상태",
      field: "status",
      width: 90,
      editable: true,
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: ["ACTIVE", "INACTIVE", "DEPRECATED"] },
      cellRenderer: (p: { value: string }) => p.value === "ACTIVE" ? "활성" : p.value === "INACTIVE" ? "비활성" : p.value === "DEPRECATED" ? "사용중지" : p.value,
    },
    {
      headerName: "",
      field: "id",
      width: 45,
      maxWidth: 45,
      editable: false,
      sortable: false,
      cellRenderer: "deleteRenderer",
    },
  ] as ColDef[], [])

  const components = useMemo(() => ({ deleteRenderer: WordDeleteRenderer }), [])

  return (
    <WordDeleteCtx.Provider value={deleteWord}>
    <div className="flex flex-col flex-1 min-h-0 h-full">
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <p className="text-sm text-muted-foreground">단어 {words.length}개</p>
        <Button variant="outline" size="sm" onClick={addWord}>
          <Plus className="h-3.5 w-3.5 mr-1" />추가
        </Button>
      </div>
      <div
        className="ag-theme-alpine flex-1 min-h-0"
        style={{
          "--ag-font-family": "var(--font-sans)",
          "--ag-font-size": "13px",
        } as React.CSSProperties}
      >
        <AgGridReact
          ref={gridRef}
          columnDefs={columnDefs}
          rowData={words}
          defaultColDef={{
            resizable: true,
            sortable: true,
            filter: false,
            minWidth: 50,
          }}
          headerHeight={32}
          rowHeight={30}
          stopEditingWhenCellsLoseFocus
          onCellValueChanged={onCellValueChanged}
          animateRows={false}
          getRowId={(params) => String(params.data.id)}
          components={components}
        />
      </div>
    </div>
    </WordDeleteCtx.Provider>
  )
}

// ---------------------------------------------------------------------------
// Domains Tab (AG Grid — inline editing)
// ---------------------------------------------------------------------------

const DomainDeleteCtx = createContext<(id: number) => void>(() => {})

function DomainDeleteRenderer(props: { value: number }) {
  const onDelete = useContext(DomainDeleteCtx)
  return (
    <button type="button" onClick={() => onDelete(props.value)}
      className="flex items-center justify-center w-full h-full text-muted-foreground hover:text-destructive transition-colors cursor-pointer">
      <Trash2 className="h-3.5 w-3.5" />
    </button>
  )
}

function DomainsTab({ dictId }: { dictId: number }) {
  const [domains, setDomains] = useState<Domain[]>([])
  const gridRef = useRef<AgGridReact>(null)

  const fetchDomains = useCallback(async () => {
    const r = await authFetch(`${BASE}/domains?dictionary_id=${dictId}`)
    if (r.ok) setDomains(await r.json())
  }, [dictId])
  useEffect(() => { fetchDomains() }, [fetchDomains])

  const addDomain = useCallback(async () => {
    await authFetch(`${BASE}/domains`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dictionary_id: dictId, domain_name: "(new)", data_type: "VARCHAR" }),
    })
    await fetchDomains()
    setTimeout(() => {
      const api = gridRef.current?.api
      if (api) {
        const lastIdx = domains.length
        api.ensureIndexVisible(lastIdx)
        api.startEditingCell({ rowIndex: lastIdx, colKey: "domain_name" })
      }
    }, 200)
  }, [dictId, fetchDomains, domains.length])

  const deleteDomain = useCallback(async (id: number) => {
    await authFetch(`${BASE}/domains/${id}`, { method: "DELETE" })
    fetchDomains()
  }, [fetchDomains])

  const onCellValueChanged = useCallback(async (event: CellValueChangedEvent) => {
    const { data, colDef, newValue, oldValue } = event
    if (newValue === oldValue) return
    const field = colDef.field
    if (!field || !data.id) return
    await authFetch(`${BASE}/domains/${data.id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: newValue }),
    })
  }, [])

  const columnDefs = useMemo<ColDef[]>(() => [
    { headerName: "#", valueGetter: (p) => (p.node?.rowIndex ?? 0) + 1, width: 50, maxWidth: 55, editable: false, sortable: false, cellStyle: { color: "#9ca3af", textAlign: "right" } },
    { headerName: "도메인 이름", field: "domain_name", minWidth: 120, editable: true, cellStyle: { fontWeight: 500 } },
    { headerName: "그룹", field: "domain_group", width: 110, editable: true },
    { headerName: "데이터 타입", field: "data_type", width: 120, editable: true, cellStyle: { fontFamily: "inherit" } },
    { headerName: "길이", field: "data_length", width: 80, editable: true, cellStyle: { textAlign: "right" } },
    { headerName: "정밀도", field: "data_precision", width: 90, editable: true, cellStyle: { textAlign: "right" } },
    { headerName: "스케일", field: "data_scale", width: 70, editable: true, cellStyle: { textAlign: "right" } },
    { headerName: "설명", field: "description", minWidth: 180, flex: 1, editable: true },
    { headerName: "상태", field: "status", width: 90, editable: true, cellEditor: "agSelectCellEditor", cellEditorParams: { values: ["ACTIVE", "INACTIVE", "DEPRECATED"] } },
    { headerName: "", field: "id", width: 45, maxWidth: 45, editable: false, sortable: false, cellRenderer: "deleteRenderer" },
  ] as ColDef[], [])

  const components = useMemo(() => ({ deleteRenderer: DomainDeleteRenderer }), [])

  return (
    <DomainDeleteCtx.Provider value={deleteDomain}>
    <div className="flex flex-col flex-1 min-h-0 h-full">
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <p className="text-sm text-muted-foreground">도메인 {domains.length}개</p>
        <Button variant="outline" size="sm" onClick={addDomain}><Plus className="h-3.5 w-3.5 mr-1" />추가</Button>
      </div>
      <div className="ag-theme-alpine flex-1 min-h-0" style={{ "--ag-font-family": "var(--font-sans)", "--ag-font-size": "13px" } as React.CSSProperties}>
        <AgGridReact ref={gridRef} columnDefs={columnDefs} rowData={domains}
          defaultColDef={{ resizable: true, sortable: true, filter: false, minWidth: 50 }}
          headerHeight={32} rowHeight={30} stopEditingWhenCellsLoseFocus
          onCellValueChanged={onCellValueChanged} animateRows={false}
          getRowId={(params) => String(params.data.id)} components={components} />
      </div>
    </div>
    </DomainDeleteCtx.Provider>
  )
}

// ---------------------------------------------------------------------------
// Terms Tab (AG Grid + morpheme analysis dialog)
// ---------------------------------------------------------------------------

const TermDeleteCtx = createContext<(id: number) => void>(() => {})

function TermDeleteRenderer(props: { value: number }) {
  const onDelete = useContext(TermDeleteCtx)
  return (
    <button type="button" onClick={() => onDelete(props.value)}
      className="flex items-center justify-center w-full h-full text-muted-foreground hover:text-destructive transition-colors cursor-pointer">
      <Trash2 className="h-3.5 w-3.5" />
    </button>
  )
}

function TermsTab({ dictId }: { dictId: number }) {
  const [terms, setTerms] = useState<Term[]>([])
  const [search, setSearch] = useState("")
  const [addOpen, setAddOpen] = useState(false)
  const [termName, setTermName] = useState("")
  const [analysis, setAnalysis] = useState<MorphemeResult | null>(null)
  const [analyzing, setAnalyzing] = useState(false)

  const fetchTerms = useCallback(async () => {
    const params = new URLSearchParams({ dictionary_id: String(dictId) })
    if (search) params.set("search", search)
    const r = await authFetch(`${BASE}/terms?${params}`)
    if (r.ok) setTerms(await r.json())
  }, [dictId, search])
  useEffect(() => { fetchTerms() }, [fetchTerms])

  const deleteTerm = useCallback(async (id: number) => {
    await authFetch(`${BASE}/terms/${id}`, { method: "DELETE" })
    fetchTerms()
  }, [fetchTerms])

  const onCellValueChanged = useCallback(async (event: CellValueChangedEvent) => {
    const { data, colDef, newValue, oldValue } = event
    if (newValue === oldValue) return
    const field = colDef.field
    if (!field || !data.id) return
    await authFetch(`${BASE}/terms/${data.id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: newValue }),
    })
  }, [])

  const analyze = async () => {
    if (!termName.trim()) return
    setAnalyzing(true)
    const r = await authFetch(`${BASE}/terms/analyze?dictionary_id=${dictId}&term_name=${encodeURIComponent(termName)}`)
    if (r.ok) setAnalysis(await r.json())
    setAnalyzing(false)
  }

  const saveTerm = async () => {
    await authFetch(`${BASE}/terms`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dictionary_id: dictId, term_name: termName,
        term_english: analysis?.term_english, term_abbr: analysis?.term_abbr,
        physical_name: analysis?.physical_name,
        domain_id: analysis?.recommended_domain?.id || null,
      }),
    })
    setAddOpen(false); setTermName(""); setAnalysis(null); fetchTerms()
  }

  const columnDefs = useMemo<ColDef[]>(() => [
    { headerName: "#", valueGetter: (p) => (p.node?.rowIndex ?? 0) + 1, width: 50, maxWidth: 55, editable: false, sortable: false, cellStyle: { color: "#9ca3af", textAlign: "right" } },
    { headerName: "용어 이름", field: "term_name", minWidth: 130, editable: true, cellStyle: { fontWeight: 500 } },
    { headerName: "영문", field: "term_english", minWidth: 150, editable: true },
    { headerName: "약어", field: "term_abbr", width: 130, editable: true, cellStyle: { fontFamily: "inherit" } },
    { headerName: "물리명", field: "physical_name", width: 140, editable: true, cellStyle: { fontFamily: "inherit" } },
    { headerName: "도메인", field: "domain_name", width: 100, editable: false, cellStyle: { color: "#6b7280" } },
    { headerName: "타입", field: "domain_data_type", width: 90, editable: false, cellStyle: { fontFamily: "inherit", color: "#6b7280" } },
    {
      headerName: "구성 단어",
      field: "words",
      minWidth: 160,
      editable: false,
      cellRenderer: (p: { value: TermWord[] }) => {
        if (!p.value || p.value.length === 0) return ""
        return p.value.map((w) => w.word_name).join(" + ")
      },
      cellStyle: { color: "#6b7280", fontSize: "12px" },
    },
    { headerName: "매핑수", field: "mapping_count", width: 65, editable: false, cellStyle: { textAlign: "center" } },
    { headerName: "상태", field: "status", width: 85, editable: true, cellEditor: "agSelectCellEditor", cellEditorParams: { values: ["ACTIVE", "INACTIVE", "DEPRECATED"] } },
    { headerName: "", field: "id", width: 45, maxWidth: 45, editable: false, sortable: false, cellRenderer: "deleteRenderer" },
  ] as ColDef[], [])

  const components = useMemo(() => ({ deleteRenderer: TermDeleteRenderer }), [])

  return (
    <TermDeleteCtx.Provider value={deleteTerm}>
    <div className="flex flex-col flex-1 min-h-0 h-full">
      <div className="flex items-center gap-3 mb-3 flex-shrink-0">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2 h-4 w-4 text-muted-foreground" />
          <Input value={search} onChange={e => setSearch(e.target.value)} placeholder="용어 검색..." className="h-8 pl-8 text-xs" />
        </div>
        <p className="text-sm text-muted-foreground">용어 {terms.length}개</p>
        <Button variant="outline" size="sm" onClick={() => { setAddOpen(true); setTermName(""); setAnalysis(null) }}>
          <Plus className="h-3.5 w-3.5 mr-1" />추가
        </Button>
      </div>
      <div className="ag-theme-alpine flex-1 min-h-0" style={{ "--ag-font-family": "var(--font-sans)", "--ag-font-size": "13px" } as React.CSSProperties}>
        <AgGridReact columnDefs={columnDefs} rowData={terms}
          defaultColDef={{ resizable: true, sortable: true, filter: false, minWidth: 50 }}
          headerHeight={32} rowHeight={30} stopEditingWhenCellsLoseFocus
          onCellValueChanged={onCellValueChanged} animateRows={false}
          getRowId={(params) => String(params.data.id)} components={components} />
      </div>

      {/* Add Term Dialog with Morpheme Analysis */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle>용어 추가 (형태소 분석)</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="flex gap-2">
              <div className="flex-1">
                <Label className="text-xs">용어 이름 (한글)</Label>
                <Input value={termName} onChange={e => setTermName(e.target.value)} placeholder="고객전화번호" className="h-9" />
              </div>
              <div className="flex items-end">
                <Button onClick={analyze} disabled={analyzing || !termName.trim()} variant="outline" size="sm">
                  {analyzing ? "분석 중..." : "분석"}
                </Button>
              </div>
            </div>

            {analysis && (
              <Card className="bg-muted/30">
                <CardContent className="p-4 space-y-3">
                  <div>
                    <span className="text-xs text-muted-foreground">단어 분해</span>
                    <div className="flex items-center gap-1 mt-1">
                      {analysis.words.map((w, i) => (
                        <span key={i} className="flex items-center gap-1">
                          {i > 0 && <span className="text-muted-foreground">+</span>}
                          <Badge variant={w.word_type === "SUFFIX" ? "secondary" : "outline"} className="text-xs px-2 py-0.5">
                            {w.word_name} ({w.word_abbr})
                          </Badge>
                        </span>
                      ))}
                      {analysis.unmatched_parts.length > 0 && (
                        <Badge variant="destructive" className="text-xs px-2 py-0.5">
                          매칭 안 됨: {analysis.unmatched_parts.join("")}
                        </Badge>
                      )}
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-3 text-xs">
                    <div><span className="text-muted-foreground">영문</span><p className="font-medium">{analysis.term_english}</p></div>
                    <div><span className="text-muted-foreground">약어</span><p className="font-mono font-medium">{analysis.term_abbr}</p></div>
                    <div><span className="text-muted-foreground">물리명</span><p className="font-mono font-medium">{analysis.physical_name}</p></div>
                  </div>
                  {analysis.recommended_domain && (
                    <div className="text-xs">
                      <span className="text-muted-foreground">추천 도메인</span>
                      <p className="font-medium">
                        {analysis.recommended_domain.domain_name}
                        <span className="text-muted-foreground ml-1">
                          ({analysis.recommended_domain.data_type}{analysis.recommended_domain.data_length ? `(${analysis.recommended_domain.data_length})` : ""})
                        </span>
                      </p>
                    </div>
                  )}
                </CardContent>
              </Card>
            )}

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setAddOpen(false)}>취소</Button>
              <Button onClick={saveTerm} disabled={!analysis}>용어 저장</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
    </TermDeleteCtx.Provider>
  )
}

// ---------------------------------------------------------------------------
// Codes Tab
// ---------------------------------------------------------------------------

// Delete renderers for code grids
const CodeGroupDeleteCtx = createContext<(id: number) => void>(() => {})
function CodeGroupDeleteRenderer(props: { value: number }) {
  const onDelete = useContext(CodeGroupDeleteCtx)
  return (
    <button type="button" onClick={() => onDelete(props.value)}
      className="flex items-center justify-center w-full h-full text-muted-foreground hover:text-destructive transition-colors cursor-pointer">
      <Trash2 className="h-3.5 w-3.5" />
    </button>
  )
}

const CodeValueDeleteCtx = createContext<(id: number) => void>(() => {})
function CodeValueDeleteRenderer(props: { value: number }) {
  const onDelete = useContext(CodeValueDeleteCtx)
  return (
    <button type="button" onClick={() => onDelete(props.value)}
      className="flex items-center justify-center w-full h-full text-muted-foreground hover:text-destructive transition-colors cursor-pointer">
      <Trash2 className="h-3.5 w-3.5" />
    </button>
  )
}

function CodesTab({ dictId }: { dictId: number }) {
  const [groups, setGroups] = useState<CodeGroup[]>([])
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null)
  const [values, setValues] = useState<CodeGroup["values"]>([])

  // Fetch groups
  const fetchGroups = useCallback(async () => {
    const r = await authFetch(`${BASE}/code-groups?dictionary_id=${dictId}`)
    if (r.ok) {
      const data = await r.json()
      setGroups(data)
      // Auto-select first group if none selected
      if (data.length > 0 && !selectedGroupId) setSelectedGroupId(data[0].id)
    }
  }, [dictId, selectedGroupId])
  useEffect(() => { fetchGroups() }, [fetchGroups])

  // Fetch values when group changes
  const fetchValues = useCallback(async () => {
    if (!selectedGroupId) { setValues([]); return }
    const r = await authFetch(`${BASE}/code-groups/${selectedGroupId}`)
    if (r.ok) {
      const data = await r.json()
      setValues(data.values || [])
    }
  }, [selectedGroupId])
  useEffect(() => { fetchValues() }, [fetchValues])

  // Group CRUD
  const addGroup = useCallback(async () => {
    await authFetch(`${BASE}/code-groups`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dictionary_id: dictId, group_name: "(new)" }),
    })
    await fetchGroups()
  }, [dictId, fetchGroups])

  const deleteGroup = useCallback(async (id: number) => {
    await authFetch(`${BASE}/code-groups/${id}`, { method: "DELETE" })
    if (selectedGroupId === id) setSelectedGroupId(null)
    fetchGroups()
  }, [selectedGroupId, fetchGroups])

  const onGroupCellChanged = useCallback(async (event: CellValueChangedEvent) => {
    const { data, colDef, newValue, oldValue } = event
    if (newValue === oldValue) return
    const field = colDef.field
    if (!field || !data.id) return
    await authFetch(`${BASE}/code-groups/${data.id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: newValue }),
    })
  }, [])

  // Value CRUD
  const addValue = useCallback(async () => {
    if (!selectedGroupId) return
    await authFetch(`${BASE}/code-groups/${selectedGroupId}/values`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code_value: "(new)", code_name: "(new)" }),
    })
    fetchValues()
  }, [selectedGroupId, fetchValues])

  const deleteValue = useCallback(async (id: number) => {
    await authFetch(`${BASE}/code-values/${id}`, { method: "DELETE" })
    fetchValues()
  }, [fetchValues])

  const onValueCellChanged = useCallback(async (event: CellValueChangedEvent) => {
    const { data, colDef, newValue, oldValue } = event
    if (newValue === oldValue) return
    const field = colDef.field
    if (!field || !data.id) return
    await authFetch(`${BASE}/code-values/${data.id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: newValue }),
    })
  }, [])

  // Group row click → select
  const onGroupRowClicked = useCallback((event: { data: { id: number } }) => {
    if (event.data?.id) setSelectedGroupId(event.data.id)
  }, [])

  const selectedGroup = groups.find(g => g.id === selectedGroupId)

  // Column defs
  const groupColDefs = useMemo<ColDef[]>(() => [
    { headerName: "#", valueGetter: (p) => (p.node?.rowIndex ?? 0) + 1, width: 45, maxWidth: 45, editable: false, sortable: false, cellStyle: { color: "#9ca3af", textAlign: "right" } },
    { headerName: "그룹 이름", field: "group_name", flex: 1, minWidth: 120, editable: true, cellStyle: { fontWeight: 500 } },
    { headerName: "영문", field: "group_english", flex: 1, minWidth: 100, editable: true },
    { headerName: "상태", field: "status", width: 85, editable: true, cellEditor: "agSelectCellEditor", cellEditorParams: { values: ["ACTIVE", "INACTIVE"] } },
    { headerName: "", field: "id", width: 40, maxWidth: 40, editable: false, sortable: false, cellRenderer: "deleteRenderer" },
  ] as ColDef[], [])

  const valueColDefs = useMemo<ColDef[]>(() => [
    { headerName: "#", valueGetter: (p) => (p.node?.rowIndex ?? 0) + 1, width: 45, maxWidth: 45, editable: false, sortable: false, cellStyle: { color: "#9ca3af", textAlign: "right" } },
    { headerName: "코드", field: "code_value", width: 100, editable: true, cellStyle: { fontFamily: "inherit", fontWeight: 500 } },
    { headerName: "이름 (한글)", field: "code_name", flex: 1, minWidth: 120, editable: true },
    { headerName: "영문", field: "code_english", flex: 1, minWidth: 100, editable: true },
    { headerName: "순서", field: "sort_order", width: 70, editable: true, cellStyle: { textAlign: "right" } },
    { headerName: "", field: "id", width: 40, maxWidth: 40, editable: false, sortable: false, cellRenderer: "deleteRenderer" },
  ] as ColDef[], [])

  const groupComponents = useMemo(() => ({ deleteRenderer: CodeGroupDeleteRenderer }), [])
  const valueComponents = useMemo(() => ({ deleteRenderer: CodeValueDeleteRenderer }), [])

  return (
    <CodeGroupDeleteCtx.Provider value={deleteGroup}>
    <CodeValueDeleteCtx.Provider value={deleteValue}>
    <div className="flex flex-col flex-1 min-h-0 h-full">
      <div className="flex flex-1 gap-4 min-h-0">
        {/* Left: Code Groups */}
        <div className="flex flex-col w-2/5 min-h-0">
          <div className="flex items-center justify-between mb-2 flex-shrink-0">
            <span className="text-sm font-medium text-muted-foreground">코드 그룹</span>
            <Button variant="outline" size="sm" onClick={addGroup}>
              <Plus className="h-3.5 w-3.5 mr-1" />추가
            </Button>
          </div>
          <div className="ag-theme-alpine flex-1 min-h-0" style={{
            "--ag-font-family": "var(--font-sans)",
            "--ag-font-size": "13px",
            "--ag-selected-row-background-color": "rgba(59, 130, 246, 0.1)",
          } as React.CSSProperties}>
            <AgGridReact
              columnDefs={groupColDefs}
              rowData={groups}
              defaultColDef={{ resizable: true, sortable: false, filter: false, minWidth: 40 }}
              headerHeight={32} rowHeight={30}
              stopEditingWhenCellsLoseFocus
              onCellValueChanged={onGroupCellChanged}
              onRowClicked={onGroupRowClicked}
              rowSelection="single"
              animateRows={false}
              getRowId={(params) => String(params.data.id)}
              components={groupComponents}
            />
          </div>
        </div>

        {/* Right: Code Values */}
        <div className="flex flex-col w-3/5 min-h-0">
          <div className="flex items-center justify-between mb-2 flex-shrink-0">
            <span className="text-sm font-medium text-muted-foreground">
              {selectedGroup ? `${selectedGroup.group_name} — 값` : "그룹 선택"}
            </span>
            {selectedGroupId && (
              <Button variant="outline" size="sm" onClick={addValue}>
                <Plus className="h-3.5 w-3.5 mr-1" />값 추가
              </Button>
            )}
          </div>
          <div className="ag-theme-alpine flex-1 min-h-0" style={{
            "--ag-font-family": "var(--font-sans)",
            "--ag-font-size": "13px",
          } as React.CSSProperties}>
            {selectedGroupId ? (
              <AgGridReact
                columnDefs={valueColDefs}
                rowData={values}
                defaultColDef={{ resizable: true, sortable: false, filter: false, minWidth: 40 }}
                headerHeight={32} rowHeight={30}
                stopEditingWhenCellsLoseFocus
                onCellValueChanged={onValueCellChanged}
                animateRows={false}
                getRowId={(params) => String(params.data.id)}
                components={valueComponents}
              />
            ) : (
              <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
                값을 조회할 코드 그룹을 선택하세요
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
    </CodeValueDeleteCtx.Provider>
    </CodeGroupDeleteCtx.Provider>
  )
}

// ---------------------------------------------------------------------------
// Compliance Tab
// ---------------------------------------------------------------------------

function ComplianceTab({ dictId }: { dictId: number }) {
  const [stats, setStats] = useState<ComplianceStats | null>(null)

  useEffect(() => {
    authFetch(`${BASE}/compliance?dictionary_id=${dictId}`)
      .then(r => r.json()).then(setStats).catch(() => {})
  }, [dictId])

  if (!stats) return <p className="text-sm text-muted-foreground text-center py-8">불러오는 중...</p>
  if (stats.total_columns === 0) return <p className="text-sm text-muted-foreground text-center py-8">아직 매핑된 컬럼이 없습니다.</p>

  const pct = stats.compliance_rate

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="p-6">
          <div className="text-center mb-4">
            <p className="text-4xl font-bold">{pct}%</p>
            <p className="text-sm text-muted-foreground">표준 준수율</p>
          </div>
          <div className="w-full bg-muted rounded-full h-4 mb-4">
            <div className="bg-green-500 h-4 rounded-full transition-all" style={{ width: `${pct}%` }} />
          </div>
          <div className="grid grid-cols-5 gap-4 text-center text-sm">
            <div><p className="text-lg font-semibold">{stats.total_columns}</p><p className="text-muted-foreground">전체</p></div>
            <div><p className="text-lg font-semibold text-green-600">{stats.matched}</p><p className="text-muted-foreground">매칭</p></div>
            <div><p className="text-lg font-semibold text-amber-600">{stats.similar}</p><p className="text-muted-foreground">유사</p></div>
            <div><p className="text-lg font-semibold text-red-600">{stats.violation}</p><p className="text-muted-foreground">위반</p></div>
            <div><p className="text-lg font-semibold text-gray-400">{stats.unmapped}</p><p className="text-muted-foreground">미매핑</p></div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

