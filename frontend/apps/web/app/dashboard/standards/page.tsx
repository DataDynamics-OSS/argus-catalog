"use client"

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react"
import { AgGridReact } from "ag-grid-react"
import {
  AllCommunityModule, ModuleRegistry,
  type CellValueChangedEvent, type ColDef, type ICellRendererParams,
} from "ag-grid-community"
import { errorMessage, toast } from "@/lib/notify"
import { DashboardHeader } from "@/components/dashboard-header"
import { ConfirmDialog } from "@/components/confirm-dialog"
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
import {
  BookOpen, ChevronLeft, ChevronRight, FileCode, Grid3X3, Hash, History, Pencil, Plus, Search, Trash2, Type,
} from "lucide-react"
import { useAuth } from "@/features/auth"
import { authFetch, throwOnError } from "@/features/auth/auth-fetch"
import { usePermissions } from "@/features/permissions/use-permissions"

ModuleRegistry.registerModules([AllCommunityModule])

const BASE = "/api/v1/standards"
const STANDARDS_EDIT_FEATURE = "standards.edit"
const STATUS_VALUES = ["ACTIVE", "INACTIVE", "DEPRECATED"]
const STATUS_LABEL: Record<string, string> = { ACTIVE: "활성", INACTIVE: "비활성", DEPRECATED: "사용중지" }
const GRID_STYLE = { "--ag-font-family": "var(--font-sans)", "--ag-font-size": "13px" } as React.CSSProperties

// ---------------------------------------------------------------------------
// 공통 유틸
// ---------------------------------------------------------------------------

function errMsg(e: unknown, fallback = "요청을 처리하지 못했습니다."): string {
  return errorMessage(e) ?? fallback
}

/** JSON 요청 — 실패하면 서버 메시지(detail)로 Error 를 던진다. */
async function api<T = unknown>(url: string, fallback: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {}
  const res = await authFetch(url, json === undefined ? rest : {
    ...rest, headers: { "Content-Type": "application/json" }, body: JSON.stringify(json),
  })
  if (!res.ok) await throwOnError(res, fallback)
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

/** 표준 사전 편집 — admin 이거나 standards.edit 기능 권한이 명시적으로 허용된 역할. */
function useCanEditStandards(): boolean {
  const { user } = useAuth()
  const { isFeatureGranted } = usePermissions()
  return !!user?.is_admin || isFeatureGranted(STANDARDS_EDIT_FEATURE)
}

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

/** 편집 권한이 없으면 셀 편집을 끄고 삭제 버튼 열(cellRenderer "deleteRenderer")을 뺀다. */
function readOnlyUnless(cols: ColDef[], canEdit: boolean): ColDef[] {
  if (canEdit) return cols
  return cols
    .filter((c) => c.cellRenderer !== "deleteRenderer")
    .map((c) => ({ ...c, editable: false }))
}

/** 사전/그룹 안에서 유일해야 하는 이름의 자리표시 값 — "(new)", "(new 2)", "(new 3)" … */
function nextPlaceholder(used: Iterable<string | null | undefined>): string {
  const taken = new Set(used)
  if (!taken.has("(new)")) return "(new)"
  let n = 2
  while (taken.has(`(new ${n})`)) n++
  return `(new ${n})`
}

const NUMERIC_FIELDS = new Set(["data_length", "data_precision", "data_scale", "synonym_group_id", "sort_order"])
const NULLABLE_TEXT_FIELDS = new Set(["description", "domain_group", "group_english", "code_english"])
const REQUIRED_NUMERIC_FIELDS = new Set(["sort_order"])

/** 셀 값 → 전송 값. 숫자 필드는 정수(비우면 null), 선택 텍스트는 빈 값이면 null. 잘못된 값이면 Error. */
function toPayloadValue(field: string, v: unknown): unknown {
  if (NUMERIC_FIELDS.has(field)) {
    if (v === null || v === undefined || v === "") {
      if (REQUIRED_NUMERIC_FIELDS.has(field)) throw new Error("값을 입력하세요.")
      return null
    }
    const n = Number(v)
    if (!Number.isInteger(n)) throw new Error("정수를 입력하세요.")
    if (field !== "sort_order" && n < (field === "synonym_group_id" ? 1 : 0)) {
      throw new Error(field === "synonym_group_id" ? "1 이상의 정수를 입력하세요." : "0 이상의 정수를 입력하세요.")
    }
    return n
  }
  if (NULLABLE_TEXT_FIELDS.has(field) && (v ?? "") === "") return null
  return v
}

/** 그리드 셀 편집을 PUT 으로 저장(바뀐 필드만). 성공하면 서버 응답으로 행을 갱신(파생 값 반영),
 *  실패하면 오류를 알리고 셀을 원래 값으로 되돌린다.
 *  행 갱신·원복은 setData 로 한다 — setDataValue 는 cellValueChanged 를 다시 일으켜 PUT 이 또 나간다. */
async function saveCellEdit(event: CellValueChangedEvent, url: string): Promise<boolean> {
  const { data, colDef, oldValue, newValue } = event
  const field = colDef.field
  if (!field || !data?.id || newValue === oldValue) return false
  try {
    const updated = await api<Record<string, unknown>>(url, "변경 내용을 저장하지 못했습니다.", {
      method: "PUT", json: { [field]: toPayloadValue(field, newValue) },
    })
    event.node.setData(updated)
    return true
  } catch (e) {
    event.node.setData({ ...data, [field]: oldValue })
    toast.error(errMsg(e, "변경 내용을 저장하지 못했습니다."))
    return false
  }
}

/** 방금 추가한 행으로 스크롤해 이름 셀 편집을 시작한다 (서버 정렬 순서와 무관하게 id 로 찾는다). */
function focusNewRow(grid: AgGridReact | null, id: number, colKey: string) {
  setTimeout(() => {
    const gridApi = grid?.api
    const node = gridApi?.getRowNode(String(id))
    if (!gridApi || node?.rowIndex == null) return
    gridApi.ensureIndexVisible(node.rowIndex)
    gridApi.startEditingCell({ rowIndex: node.rowIndex, colKey })
  }, 100)
}

// --- 삭제 확인 ---------------------------------------------------------------

type ConfirmOpts = { title: string; desc: React.ReactNode; confirmText?: string; run: () => Promise<void> }

function useConfirm() {
  const [opts, setOpts] = useState<ConfirmOpts | null>(null)
  const [busy, setBusy] = useState(false)
  const dialog = (
    <ConfirmDialog
      open={!!opts}
      onOpenChange={(o) => { if (!o && !busy) setOpts(null) }}
      title={opts?.title ?? ""}
      desc={<div className="space-y-2 text-sm">{opts?.desc}</div>}
      confirmText={opts?.confirmText ?? "삭제"}
      destructive
      isLoading={busy}
      handleConfirm={async () => {
        if (!opts) return
        setBusy(true)
        try {
          await opts.run()
          setOpts(null)
        } catch (e) {
          toast.error(errMsg(e, "삭제하지 못했습니다."))
        } finally {
          setBusy(false)
        }
      }}
    />
  )
  return { dialog, ask: setOpts }
}

type UsageRef = { id: number; name: string }
type Usage = { terms: UsageRef[]; domains: UsageRef[]; value_count: number; mapping_count: number }

function UsageList({ label, items }: { label: string; items: UsageRef[] }) {
  if (items.length === 0) return null
  const shown = items.slice(0, 8)
  return (
    <p>
      {label} {items.length}개: <span className="text-foreground">{shown.map((u) => u.name).join(", ")}</span>
      {items.length > shown.length && ` 외 ${items.length - shown.length}개`}
    </p>
  )
}

// --- 그리드 삭제 버튼 (가장 가까운 Provider 의 핸들러를 쓴다) ------------------

const DeleteCtx = createContext<(row: { id: number }) => void>(() => {})

function DeleteRenderer(props: ICellRendererParams) {
  const onDelete = useContext(DeleteCtx)
  return (
    <button
      type="button"
      aria-label="삭제"
      onClick={() => props.data && onDelete(props.data)}
      className="flex items-center justify-center w-full h-full text-muted-foreground hover:text-destructive transition-colors cursor-pointer"
    >
      <Trash2 className="h-3.5 w-3.5" />
    </button>
  )
}

const GRID_COMPONENTS = { deleteRenderer: DeleteRenderer }
const DEFAULT_COL: ColDef = { resizable: true, sortable: true, filter: false, minWidth: 50 }

const rowNumberCol: ColDef = {
  headerName: "#", valueGetter: (p) => (p.node?.rowIndex ?? 0) + 1, width: 50, maxWidth: 55,
  editable: false, sortable: false, cellStyle: { color: "#9ca3af", textAlign: "right" },
}
const statusCol: ColDef = {
  headerName: "상태", field: "status", width: 90, editable: true,
  cellEditor: "agSelectCellEditor", cellEditorParams: { values: STATUS_VALUES },
  valueFormatter: (p) => STATUS_LABEL[p.value] ?? p.value,
}
const deleteCol: ColDef = {
  headerName: "", field: "id", width: 45, maxWidth: 45, editable: false, sortable: false, cellRenderer: "deleteRenderer",
}
const numberCol = (headerName: string, field: string, width: number): ColDef => ({
  headerName, field, width, editable: true, cellStyle: { textAlign: "right" },
  // 숫자 편집기 대신 텍스트로 받아 저장 시 검증 — 비우면 null 로 보낼 수 있게
  valueParser: (p) => (p.newValue === "" || p.newValue == null ? null : p.newValue),
})

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Dictionary = {
  id: number; dict_name: string; description: string | null; version: string | null
  status: string; word_count: number; domain_count: number; term_count: number; code_group_count: number
  mapping_count: number; created_by: string | null
}

type Word = {
  id: number; dictionary_id: number; word_name: string; word_english: string; word_abbr: string
  description: string | null; word_type: string; is_forbidden: string; synonym_group_id: number | null
  status: string; term_count: number
}

type Domain = {
  id: number; dictionary_id: number; domain_name: string; domain_group: string | null
  data_type: string; data_length: number | null; data_precision: number | null; data_scale: number | null
  description: string | null; code_group_id: number | null; code_group_name: string | null; status: string
  term_count: number
}

type TermWord = { word_id: number; word_name: string; word_abbr: string; word_type: string; ordinal: number }

type Term = {
  id: number; dictionary_id: number; term_name: string; term_english: string; term_abbr: string
  physical_name: string; domain_id: number | null; domain_name: string | null; domain_data_type: string | null
  description: string | null; status: string; words: TermWord[]; mapping_count: number
}

type CodeValue = { id: number; code_value: string; code_name: string; code_english: string | null; sort_order: number }

type CodeGroup = {
  id: number; group_name: string; group_english: string | null; status: string
  values: CodeValue[]; domain_count: number
}

type MorphemeResult = {
  words: TermWord[]; term_english: string; term_abbr: string; physical_name: string
  recommended_domain: Domain | null; unmatched_parts: string[]
  forbidden_words: { word_name: string; suggestion: string | null }[]
}

type ComplianceStats = {
  total_columns: number; matched: number; similar: number; violation: number
  unmapped: number; compliance_rate: number; dataset_count: number
}

type ChangeLog = {
  id: number; entity_type: string; entity_id: number; entity_name: string | null; change_type: string
  field_name: string | null; old_value: string | null; new_value: string | null
  changed_by: string | null; changed_at: string | null
}

type DictDialogState = { mode: "create" } | { mode: "edit"; dict: Dictionary } | null

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function StandardsPage() {
  const [dictionaries, setDictionaries] = useState<Dictionary[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selectedDictId, setSelectedDictId] = useState<number | null>(null)
  const [tab, setTab] = useState("words")
  const [dictDialog, setDictDialog] = useState<DictDialogState>(null)
  const canEdit = useCanEditStandards()
  const { dialog: confirmDialog, ask } = useConfirm()

  const fetchDicts = useCallback(async () => {
    try {
      const data = await api<Dictionary[]>(`${BASE}/dictionaries`, "사전 목록을 불러오지 못했습니다.")
      setDictionaries(data)
      setLoadError(null)
      // 선택한 사전이 사라졌으면(삭제 등) 첫 사전으로
      setSelectedDictId((cur) => (cur && data.some((d) => d.id === cur) ? cur : data[0]?.id ?? null))
    } catch (e) {
      setLoadError(errMsg(e))
    }
  }, [])

  useEffect(() => { fetchDicts() }, [fetchDicts])

  const selectedDict = dictionaries?.find((d) => d.id === selectedDictId)

  const confirmDeleteDict = (d: Dictionary) => ask({
    title: `사전 '${d.dict_name}' 삭제`,
    desc: (
      <>
        <p>사전에 속한 모든 항목이 함께 삭제되며 되돌릴 수 없습니다.</p>
        <p>
          단어 {d.word_count} · 도메인 {d.domain_count} · 용어 {d.term_count} · 코드 그룹 {d.code_group_count}
          {d.mapping_count > 0 && <> · <span className="text-destructive">컬럼 매핑 {d.mapping_count}</span></>}
        </p>
      </>
    ),
    run: async () => {
      await api(`${BASE}/dictionaries/${d.id}`, "사전을 삭제하지 못했습니다.", { method: "DELETE" })
      toast.success(`사전 '${d.dict_name}' 을(를) 삭제했습니다.`)
      await fetchDicts()
    },
  })

  return (
    <>
      <DashboardHeader title="데이터 표준" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        {loadError && <p className="text-sm text-destructive">{loadError}</p>}

        {dictionaries && dictionaries.length === 0 && (
          <Card>
            <CardContent className="py-12 text-center space-y-3">
              <BookOpen className="h-8 w-8 mx-auto text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                등록된 표준 사전이 없습니다.
                {canEdit ? " 사전을 만든 뒤 단어·도메인·용어를 등록하세요." : " 관리자에게 사전 등록을 요청하세요."}
              </p>
              {canEdit && (
                <Button size="sm" onClick={() => setDictDialog({ mode: "create" })}>
                  <Plus className="h-3.5 w-3.5 mr-1" /> 새 사전
                </Button>
              )}
            </CardContent>
          </Card>
        )}

        {dictionaries && dictionaries.length > 0 && (
          <div className="flex flex-wrap items-center gap-3">
            <Label className="text-sm font-medium">사전:</Label>
            <Select
              value={selectedDictId ? String(selectedDictId) : ""}
              onValueChange={(v) => setSelectedDictId(Number(v))}
            >
              <SelectTrigger className="w-72 h-9">
                <SelectValue placeholder="사전 선택..." />
              </SelectTrigger>
              <SelectContent>
                {dictionaries.map((d) => (
                  <SelectItem key={d.id} value={String(d.id)}>
                    {d.dict_name}
                    {d.version && <span className="text-muted-foreground ml-1">({d.version})</span>}
                    {d.status !== "ACTIVE" && <span className="text-muted-foreground ml-1">· {STATUS_LABEL[d.status] ?? d.status}</span>}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {canEdit && (
              <>
                {selectedDict && (
                  <>
                    <Button variant="ghost" size="sm" onClick={() => setDictDialog({ mode: "edit", dict: selectedDict })}>
                      <Pencil className="h-3.5 w-3.5 mr-1" /> 편집
                    </Button>
                    <Button variant="ghost" size="sm" className="text-destructive" onClick={() => confirmDeleteDict(selectedDict)}>
                      <Trash2 className="h-3.5 w-3.5 mr-1" /> 삭제
                    </Button>
                  </>
                )}
                <Button variant="outline" size="sm" onClick={() => setDictDialog({ mode: "create" })}>
                  <Plus className="h-3.5 w-3.5 mr-1" /> 새 사전
                </Button>
              </>
            )}

            {selectedDict && (
              <div className="ml-auto flex items-center gap-3 text-sm text-muted-foreground">
                <span>단어: <span className="text-foreground font-medium">{selectedDict.word_count}</span></span>
                <span>도메인: <span className="text-foreground font-medium">{selectedDict.domain_count}</span></span>
                <span>용어: <span className="text-foreground font-medium">{selectedDict.term_count}</span></span>
                <span>코드: <span className="text-foreground font-medium">{selectedDict.code_group_count}</span></span>
              </div>
            )}
          </div>
        )}
        {selectedDict?.description && (
          <p className="text-sm text-muted-foreground -mt-2">{selectedDict.description}</p>
        )}

        {selectedDictId && (
          <Tabs value={tab} onValueChange={setTab} className="flex flex-1 flex-col min-h-0">
            <TabsList>
              <TabsTrigger value="words"><Type className="h-3.5 w-3.5 mr-1" />단어</TabsTrigger>
              <TabsTrigger value="domains"><Grid3X3 className="h-3.5 w-3.5 mr-1" />도메인</TabsTrigger>
              <TabsTrigger value="terms"><FileCode className="h-3.5 w-3.5 mr-1" />용어</TabsTrigger>
              <TabsTrigger value="codes"><Hash className="h-3.5 w-3.5 mr-1" />코드</TabsTrigger>
              <TabsTrigger value="compliance"><BookOpen className="h-3.5 w-3.5 mr-1" />준수율</TabsTrigger>
              <TabsTrigger value="history"><History className="h-3.5 w-3.5 mr-1" />변경 이력</TabsTrigger>
            </TabsList>

            {/* key 로 사전이 바뀌면 탭 상태(선택 그룹·검색어 등)를 초기화한다 */}
            <TabsContent value="words" className="mt-4 flex-1 flex flex-col min-h-0 h-0">
              <WordsTab key={selectedDictId} dictId={selectedDictId} canEdit={canEdit} onChanged={fetchDicts} />
            </TabsContent>
            <TabsContent value="domains" className="mt-4 flex-1 flex flex-col min-h-0 h-0">
              <DomainsTab key={selectedDictId} dictId={selectedDictId} canEdit={canEdit} onChanged={fetchDicts} />
            </TabsContent>
            <TabsContent value="terms" className="mt-4 flex-1 flex flex-col min-h-0 h-0">
              <TermsTab key={selectedDictId} dictId={selectedDictId} canEdit={canEdit} onChanged={fetchDicts} />
            </TabsContent>
            <TabsContent value="codes" className="mt-4 flex-1 flex flex-col min-h-0 h-0">
              <CodesTab key={selectedDictId} dictId={selectedDictId} canEdit={canEdit} onChanged={fetchDicts} />
            </TabsContent>
            <TabsContent value="compliance" className="mt-4">
              <ComplianceTab key={selectedDictId} dictId={selectedDictId} />
            </TabsContent>
            <TabsContent value="history" className="mt-4 flex-1 flex flex-col min-h-0">
              {tab === "history" && <HistoryTab key={selectedDictId} dictId={selectedDictId} />}
            </TabsContent>
          </Tabs>
        )}
      </div>

      <DictionaryDialog
        state={dictDialog}
        onClose={() => setDictDialog(null)}
        onSaved={async (d) => { await fetchDicts(); setSelectedDictId(d.id) }}
      />
      {confirmDialog}
    </>
  )
}

// ---------------------------------------------------------------------------
// Dictionary Dialog (생성·편집)
// ---------------------------------------------------------------------------

function DictionaryDialog({ state, onClose, onSaved }: {
  state: DictDialogState
  onClose: () => void
  onSaved: (d: Dictionary) => void | Promise<void>
}) {
  const [name, setName] = useState("")
  const [version, setVersion] = useState("")
  const [desc, setDesc] = useState("")
  const [status, setStatus] = useState("ACTIVE")
  const [saving, setSaving] = useState(false)
  const editing = state?.mode === "edit" ? state.dict : null

  useEffect(() => {
    if (!state) return
    const d = state.mode === "edit" ? state.dict : null
    setName(d?.dict_name ?? "")
    setVersion(d?.version ?? "")
    setDesc(d?.description ?? "")
    setStatus(d?.status ?? "ACTIVE")
  }, [state])

  const save = async () => {
    setSaving(true)
    try {
      const body = { dict_name: name.trim(), version: version.trim() || null, description: desc.trim() || null }
      const saved = editing
        ? await api<Dictionary>(`${BASE}/dictionaries/${editing.id}`, "사전을 수정하지 못했습니다.", { method: "PUT", json: { ...body, status } })
        : await api<Dictionary>(`${BASE}/dictionaries`, "사전을 만들지 못했습니다.", { method: "POST", json: body })
      toast.success(editing ? "사전을 수정했습니다." : `사전 '${saved.dict_name}' 을(를) 만들었습니다.`)
      await onSaved(saved)
      onClose()
    } catch (e) {
      toast.error(errMsg(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={!!state} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-md">
        <DialogHeader><DialogTitle>{editing ? "사전 편집" : "새 사전"}</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div><Label className="text-xs">이름</Label><Input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} className="h-9" /></div>
          <div><Label className="text-xs">버전</Label><Input value={version} maxLength={50} onChange={(e) => setVersion(e.target.value)} placeholder="v1.0" className="h-9" /></div>
          {editing && (
            <div>
              <Label className="text-xs">상태</Label>
              <Select value={status} onValueChange={setStatus}>
                <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {STATUS_VALUES.map((s) => <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}
          <div><Label className="text-xs">설명</Label><Textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={2} /></div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose} disabled={saving}>취소</Button>
            <Button onClick={save} disabled={saving || !name.trim()}>{saving ? "저장 중..." : editing ? "저장" : "생성"}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

type TabProps = { dictId: number; canEdit: boolean; onChanged: () => void }

// ---------------------------------------------------------------------------
// Words Tab (AG Grid — inline row editing)
// ---------------------------------------------------------------------------

function WordsTab({ dictId, canEdit, onChanged }: TabProps) {
  const [words, setWords] = useState<Word[]>([])
  const gridRef = useRef<AgGridReact>(null)
  const { dialog, ask } = useConfirm()

  const fetchWords = useCallback(async () => {
    try {
      setWords(await api<Word[]>(`${BASE}/words?dictionary_id=${dictId}`, "단어 목록을 불러오지 못했습니다."))
    } catch (e) { toast.error(errMsg(e)) }
  }, [dictId])
  useEffect(() => { fetchWords() }, [fetchWords])

  const addWord = useCallback(async () => {
    try {
      const w = await api<Word>(`${BASE}/words`, "단어를 추가하지 못했습니다.", {
        method: "POST",
        json: {
          dictionary_id: dictId, word_name: nextPlaceholder(words.map((x) => x.word_name)),
          word_english: "(new)", word_abbr: "NEW", word_type: "GENERAL",
        },
      })
      await fetchWords()
      onChanged()
      focusNewRow(gridRef.current, w.id, "word_name")
    } catch (e) { toast.error(errMsg(e)) }
  }, [dictId, fetchWords, words, onChanged])

  const deleteWord = useCallback(async (row: { id: number }) => {
    const w = words.find((x) => x.id === row.id)
    if (!w) return
    let usage: Usage | null = null
    try { usage = await api<Usage>(`${BASE}/words/${w.id}/usage`, "사용 현황을 불러오지 못했습니다.") } catch { /* 안내만 생략 */ }
    ask({
      title: `단어 '${w.word_name}' 삭제`,
      desc: usage && usage.terms.length > 0 ? (
        <>
          <UsageList label="이 단어로 구성된 용어" items={usage.terms} />
          <p>용어는 남지만 구성 단어 연결에서 이 단어가 빠집니다.</p>
        </>
      ) : <p>이 단어를 쓰는 용어가 없습니다.</p>,
      run: async () => {
        await api(`${BASE}/words/${w.id}`, "단어를 삭제하지 못했습니다.", { method: "DELETE" })
        toast.success("단어를 삭제했습니다.")
        await fetchWords()
        onChanged()
      },
    })
  }, [words, ask, fetchWords, onChanged])

  const onCellValueChanged = useCallback(async (event: CellValueChangedEvent) => {
    await saveCellEdit(event, `${BASE}/words/${event.data.id}`)
  }, [])

  const columnDefs = useMemo<ColDef[]>(() => [
    rowNumberCol,
    { headerName: "이름 (한글)", field: "word_name", minWidth: 120, editable: true, cellStyle: { fontWeight: 500 } },
    { headerName: "영문", field: "word_english", minWidth: 130, editable: true },
    { headerName: "약어", field: "word_abbr", width: 120, editable: true },
    {
      headerName: "유형", field: "word_type", width: 100, editable: true,
      cellEditor: "agSelectCellEditor", cellEditorParams: { values: ["GENERAL", "SUFFIX", "PREFIX"] },
      valueFormatter: (p) => (p.value === "SUFFIX" ? "접미사" : p.value === "PREFIX" ? "접두사" : "일반"),
    },
    {
      headerName: "금칙어", field: "is_forbidden", width: 80, editable: true,
      cellEditor: "agSelectCellEditor", cellEditorParams: { values: ["true", "false"] },
      valueFormatter: (p) => (p.value === "true" ? "예" : ""),
      cellStyle: (p) => ({
        textAlign: "center",
        color: p.value === "true" ? "#ef4444" : undefined,
        fontWeight: p.value === "true" ? 600 : undefined,
      }),
    },
    {
      ...numberCol("동의어 그룹", "synonym_group_id", 100),
      headerTooltip: "같은 번호의 단어끼리 이음동의어. 금칙어가 쓰이면 같은 그룹의 표준 단어를 대체어로 제안합니다.",
    },
    { headerName: "설명", field: "description", minWidth: 180, flex: 1, editable: true },
    { headerName: "용어", field: "term_count", width: 70, editable: false, cellStyle: { textAlign: "center" }, headerTooltip: "이 단어로 구성된 용어 수" },
    statusCol,
    deleteCol,
  ] as ColDef[], [])

  const gridCols = useMemo(() => readOnlyUnless(columnDefs, canEdit), [columnDefs, canEdit])

  return (
    <DeleteCtx.Provider value={deleteWord}>
      <div className="flex flex-col flex-1 min-h-0 h-full">
        <div className="flex items-center justify-between mb-3 flex-shrink-0">
          <p className="text-sm text-muted-foreground">단어 {words.length}개</p>
          {canEdit && (
            <Button variant="outline" size="sm" onClick={addWord}>
              <Plus className="h-3.5 w-3.5 mr-1" />추가
            </Button>
          )}
        </div>
        <div className="ag-theme-alpine flex-1 min-h-0" style={GRID_STYLE}>
          <AgGridReact
            ref={gridRef}
            columnDefs={gridCols}
            rowData={words}
            defaultColDef={DEFAULT_COL}
            headerHeight={32}
            rowHeight={30}
            stopEditingWhenCellsLoseFocus
            onCellValueChanged={onCellValueChanged}
            animateRows={false}
            getRowId={(params) => String(params.data.id)}
            components={GRID_COMPONENTS}
            overlayNoRowsTemplate="등록된 단어가 없습니다."
          />
        </div>
      </div>
      {dialog}
    </DeleteCtx.Provider>
  )
}

// ---------------------------------------------------------------------------
// Domains Tab (AG Grid — inline editing)
// ---------------------------------------------------------------------------

function DomainsTab({ dictId, canEdit, onChanged }: TabProps) {
  const [domains, setDomains] = useState<Domain[]>([])
  const [groups, setGroups] = useState<{ id: number; group_name: string }[]>([])
  const gridRef = useRef<AgGridReact>(null)
  const { dialog, ask } = useConfirm()

  const fetchDomains = useCallback(async () => {
    try {
      const [ds, gs] = await Promise.all([
        api<Domain[]>(`${BASE}/domains?dictionary_id=${dictId}`, "도메인 목록을 불러오지 못했습니다."),
        api<CodeGroup[]>(`${BASE}/code-groups?dictionary_id=${dictId}`, "코드 그룹 목록을 불러오지 못했습니다."),
      ])
      setDomains(ds)
      setGroups(gs.map((g) => ({ id: g.id, group_name: g.group_name })))
    } catch (e) { toast.error(errMsg(e)) }
  }, [dictId])
  useEffect(() => { fetchDomains() }, [fetchDomains])

  const addDomain = useCallback(async () => {
    try {
      const d = await api<Domain>(`${BASE}/domains`, "도메인을 추가하지 못했습니다.", {
        method: "POST",
        json: { dictionary_id: dictId, domain_name: nextPlaceholder(domains.map((x) => x.domain_name)), data_type: "VARCHAR" },
      })
      await fetchDomains()
      onChanged()
      focusNewRow(gridRef.current, d.id, "domain_name")
    } catch (e) { toast.error(errMsg(e)) }
  }, [dictId, fetchDomains, domains, onChanged])

  const deleteDomain = useCallback(async (row: { id: number }) => {
    const d = domains.find((x) => x.id === row.id)
    if (!d) return
    let usage: Usage | null = null
    try { usage = await api<Usage>(`${BASE}/domains/${d.id}/usage`, "사용 현황을 불러오지 못했습니다.") } catch { /* 안내만 생략 */ }
    ask({
      title: `도메인 '${d.domain_name}' 삭제`,
      desc: usage && usage.terms.length > 0 ? (
        <>
          <UsageList label="이 도메인을 쓰는 용어" items={usage.terms} />
          <p>해당 용어들의 도메인이 비워지고, 자동 매핑의 타입 검사 기준도 사라집니다.</p>
        </>
      ) : <p>이 도메인을 쓰는 용어가 없습니다.</p>,
      run: async () => {
        await api(`${BASE}/domains/${d.id}`, "도메인을 삭제하지 못했습니다.", { method: "DELETE" })
        toast.success("도메인을 삭제했습니다.")
        await fetchDomains()
        onChanged()
      },
    })
  }, [domains, ask, fetchDomains, onChanged])

  const onCellValueChanged = useCallback(async (event: CellValueChangedEvent) => {
    await saveCellEdit(event, `${BASE}/domains/${event.data.id}`)
  }, [])

  const groupNames = useMemo(() => new Map(groups.map((g) => [g.id, g.group_name])), [groups])

  const columnDefs = useMemo<ColDef[]>(() => [
    rowNumberCol,
    { headerName: "도메인 이름", field: "domain_name", minWidth: 120, editable: true, cellStyle: { fontWeight: 500 } },
    { headerName: "그룹", field: "domain_group", width: 110, editable: true },
    { headerName: "데이터 타입", field: "data_type", width: 120, editable: true },
    numberCol("길이", "data_length", 80),
    numberCol("정밀도", "data_precision", 80),
    numberCol("스케일", "data_scale", 80),
    {
      headerName: "코드 그룹", field: "code_group_id", width: 130, editable: true,
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: [null, ...groups.map((g) => g.id)] },
      valueFormatter: (p) => (p.value == null ? "" : groupNames.get(p.value) ?? `#${p.value}`),
    },
    { headerName: "설명", field: "description", minWidth: 160, flex: 1, editable: true },
    { headerName: "용어", field: "term_count", width: 70, editable: false, cellStyle: { textAlign: "center" }, headerTooltip: "이 도메인을 쓰는 용어 수" },
    statusCol,
    deleteCol,
  ] as ColDef[], [groups, groupNames])

  const gridCols = useMemo(() => readOnlyUnless(columnDefs, canEdit), [columnDefs, canEdit])

  return (
    <DeleteCtx.Provider value={deleteDomain}>
      <div className="flex flex-col flex-1 min-h-0 h-full">
        <div className="flex items-center justify-between mb-3 flex-shrink-0">
          <p className="text-sm text-muted-foreground">도메인 {domains.length}개</p>
          {canEdit && <Button variant="outline" size="sm" onClick={addDomain}><Plus className="h-3.5 w-3.5 mr-1" />추가</Button>}
        </div>
        <div className="ag-theme-alpine flex-1 min-h-0" style={GRID_STYLE}>
          <AgGridReact ref={gridRef} columnDefs={gridCols} rowData={domains}
            defaultColDef={DEFAULT_COL}
            headerHeight={32} rowHeight={30} stopEditingWhenCellsLoseFocus
            onCellValueChanged={onCellValueChanged} animateRows={false}
            getRowId={(params) => String(params.data.id)} components={GRID_COMPONENTS}
            overlayNoRowsTemplate="등록된 도메인이 없습니다." />
        </div>
      </div>
      {dialog}
    </DeleteCtx.Provider>
  )
}

// ---------------------------------------------------------------------------
// Terms Tab (AG Grid + morpheme analysis dialog)
// ---------------------------------------------------------------------------

function TermsTab({ dictId, canEdit, onChanged }: TabProps) {
  const [terms, setTerms] = useState<Term[]>([])
  const [domains, setDomains] = useState<Domain[]>([])
  const [search, setSearch] = useState("")
  const debouncedSearch = useDebounced(search.trim())
  const [addOpen, setAddOpen] = useState(false)
  const { dialog, ask } = useConfirm()

  const fetchTerms = useCallback(async () => {
    const params = new URLSearchParams({ dictionary_id: String(dictId) })
    if (debouncedSearch) params.set("search", debouncedSearch)
    try {
      setTerms(await api<Term[]>(`${BASE}/terms?${params}`, "용어 목록을 불러오지 못했습니다."))
    } catch (e) { toast.error(errMsg(e)) }
  }, [dictId, debouncedSearch])
  useEffect(() => { fetchTerms() }, [fetchTerms])

  useEffect(() => {
    api<Domain[]>(`${BASE}/domains?dictionary_id=${dictId}`, "도메인 목록을 불러오지 못했습니다.")
      .then(setDomains).catch(() => setDomains([]))
  }, [dictId])

  const deleteTerm = useCallback(async (row: { id: number }) => {
    const t = terms.find((x) => x.id === row.id)
    if (!t) return
    ask({
      title: `용어 '${t.term_name}' 삭제`,
      desc: t.mapping_count > 0
        ? <p>이 용어로 매핑된 컬럼 <span className="text-destructive font-medium">{t.mapping_count}개</span>의 매핑도 함께 삭제되어 데이터셋 준수율이 바뀝니다.</p>
        : <p>이 용어로 매핑된 컬럼이 없습니다.</p>,
      run: async () => {
        await api(`${BASE}/terms/${t.id}`, "용어를 삭제하지 못했습니다.", { method: "DELETE" })
        toast.success("용어를 삭제했습니다.")
        await fetchTerms()
        onChanged()
      },
    })
  }, [terms, ask, fetchTerms, onChanged])

  const onCellValueChanged = useCallback(async (event: CellValueChangedEvent) => {
    await saveCellEdit(event, `${BASE}/terms/${event.data.id}`)
  }, [])

  const domainNames = useMemo(() => new Map(domains.map((d) => [d.id, d.domain_name])), [domains])

  const columnDefs = useMemo<ColDef[]>(() => [
    rowNumberCol,
    { headerName: "용어 이름", field: "term_name", minWidth: 130, editable: true, cellStyle: { fontWeight: 500 } },
    { headerName: "영문", field: "term_english", minWidth: 150, editable: true },
    { headerName: "약어", field: "term_abbr", width: 130, editable: true },
    { headerName: "물리명", field: "physical_name", width: 140, editable: true },
    {
      headerName: "도메인", field: "domain_id", width: 120, editable: true,
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: [null, ...domains.map((d) => d.id)] },
      valueFormatter: (p) => (p.value == null ? "" : domainNames.get(p.value) ?? p.data?.domain_name ?? `#${p.value}`),
    },
    { headerName: "타입", field: "domain_data_type", width: 90, editable: false, cellStyle: { color: "#6b7280" } },
    {
      headerName: "구성 단어", field: "words", minWidth: 160, editable: false, sortable: false,
      valueFormatter: (p) => ((p.value as TermWord[] | undefined) ?? []).map((w) => w.word_name).join(" + "),
      cellStyle: { color: "#6b7280", fontSize: "12px" },
    },
    { headerName: "매핑수", field: "mapping_count", width: 70, editable: false, cellStyle: { textAlign: "center" } },
    statusCol,
    deleteCol,
  ] as ColDef[], [domains, domainNames])

  const gridCols = useMemo(() => readOnlyUnless(columnDefs, canEdit), [columnDefs, canEdit])

  return (
    <DeleteCtx.Provider value={deleteTerm}>
      <div className="flex flex-col flex-1 min-h-0 h-full">
        <div className="flex items-center gap-3 mb-3 flex-shrink-0">
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-2 h-4 w-4 text-muted-foreground" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="용어명·물리명·영문명 검색..." className="h-8 pl-8 text-xs" />
          </div>
          <p className="text-sm text-muted-foreground">용어 {terms.length}개</p>
          {canEdit && (
            <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>
              <Plus className="h-3.5 w-3.5 mr-1" />추가
            </Button>
          )}
        </div>
        <div className="ag-theme-alpine flex-1 min-h-0" style={GRID_STYLE}>
          <AgGridReact columnDefs={gridCols} rowData={terms}
            defaultColDef={DEFAULT_COL}
            headerHeight={32} rowHeight={30} stopEditingWhenCellsLoseFocus
            onCellValueChanged={onCellValueChanged} animateRows={false}
            getRowId={(params) => String(params.data.id)} components={GRID_COMPONENTS}
            overlayNoRowsTemplate={debouncedSearch ? "검색 결과가 없습니다." : "등록된 용어가 없습니다."} />
        </div>
      </div>
      <TermAddDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        dictId={dictId}
        domains={domains}
        onCreated={async () => { await fetchTerms(); onChanged() }}
      />
      {dialog}
    </DeleteCtx.Provider>
  )
}

function TermAddDialog({ open, onOpenChange, dictId, domains, onCreated }: {
  open: boolean; onOpenChange: (o: boolean) => void; dictId: number; domains: Domain[]
  onCreated: () => void | Promise<void>
}) {
  const [termName, setTermName] = useState("")
  const [analysis, setAnalysis] = useState<MorphemeResult | null>(null)
  const [analyzedName, setAnalyzedName] = useState("")
  const [domainId, setDomainId] = useState<string>("none")
  const [physical, setPhysical] = useState("")
  const [analyzing, setAnalyzing] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open) { setTermName(""); setAnalysis(null); setAnalyzedName(""); setDomainId("none"); setPhysical("") }
  }, [open])

  // 분석 후 이름을 바꾸면 이전 분석 결과로 저장되지 않게 무효화
  const stale = !!analysis && analyzedName !== termName.trim()

  const analyze = async () => {
    const name = termName.trim()
    if (!name) return
    setAnalyzing(true)
    try {
      const params = new URLSearchParams({ dictionary_id: String(dictId), term_name: name })
      const r = await api<MorphemeResult>(`${BASE}/terms/analyze?${params}`, "용어명을 형태소 분석하지 못했습니다.")
      setAnalysis(r)
      setAnalyzedName(name)
      setPhysical(r.physical_name)
      setDomainId(r.recommended_domain ? String(r.recommended_domain.id) : "none")
    } catch (e) {
      toast.error(errMsg(e))
    } finally {
      setAnalyzing(false)
    }
  }

  const save = async () => {
    if (!analysis || stale) return
    setSaving(true)
    try {
      await api(`${BASE}/terms`, "용어를 저장하지 못했습니다.", {
        method: "POST",
        json: {
          dictionary_id: dictId, term_name: analyzedName,
          term_english: analysis.term_english, term_abbr: analysis.term_abbr,
          physical_name: physical.trim() || null,
          domain_id: domainId === "none" ? null : Number(domainId),
        },
      })
      toast.success(`용어 '${analyzedName}' 을(를) 추가했습니다.`)
      await onCreated()
      onOpenChange(false)
    } catch (e) {
      toast.error(errMsg(e))
    } finally {
      setSaving(false)
    }
  }

  const forbidden = analysis?.forbidden_words ?? []

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader><DialogTitle>용어 추가 (형태소 분석)</DialogTitle></DialogHeader>
        <div className="space-y-4">
          <div className="flex gap-2">
            <div className="flex-1">
              <Label className="text-xs">용어 이름 (한글)</Label>
              <Input
                value={termName} maxLength={200}
                onChange={(e) => setTermName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) analyze() }}
                placeholder="고객전화번호" className="h-9"
              />
            </div>
            <div className="flex items-end">
              <Button onClick={analyze} disabled={analyzing || !termName.trim()} variant="outline" size="sm">
                {analyzing ? "분석 중..." : "분석"}
              </Button>
            </div>
          </div>

          {stale && <p className="text-xs text-amber-600">이름이 바뀌었습니다. 다시 분석하세요.</p>}

          {analysis && !stale && (
            <Card className="bg-muted/30">
              <CardContent className="p-4 space-y-3">
                <div>
                  <span className="text-xs text-muted-foreground">단어 분해</span>
                  <div className="flex flex-wrap items-center gap-1 mt-1">
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
                        사전에 없는 부분: {analysis.unmatched_parts.join(", ")}
                      </Badge>
                    )}
                  </div>
                </div>
                {forbidden.length > 0 && (
                  <div className="text-xs text-destructive">
                    금칙어가 포함되어 저장할 수 없습니다:{" "}
                    {forbidden.map((f) => `'${f.word_name}'${f.suggestion ? ` → '${f.suggestion}'` : ""}`).join(", ")}
                  </div>
                )}
                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div><span className="text-muted-foreground">영문</span><p className="font-medium">{analysis.term_english}</p></div>
                  <div><span className="text-muted-foreground">약어</span><p className="font-mono font-medium">{analysis.term_abbr}</p></div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <Label className="text-xs">물리명</Label>
                    <Input value={physical} maxLength={100} onChange={(e) => setPhysical(e.target.value)} className="h-8 font-mono text-xs" />
                  </div>
                  <div>
                    <Label className="text-xs">도메인{analysis.recommended_domain ? " (추천 적용됨)" : ""}</Label>
                    <Select value={domainId} onValueChange={setDomainId}>
                      <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">(없음)</SelectItem>
                        {domains.map((d) => (
                          <SelectItem key={d.id} value={String(d.id)}>
                            {d.domain_name} · {d.data_type}{d.data_length ? `(${d.data_length})` : ""}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>취소</Button>
            <Button onClick={save} disabled={!analysis || stale || saving || forbidden.length > 0}>
              {saving ? "저장 중..." : "용어 저장"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Codes Tab
// ---------------------------------------------------------------------------

function CodesTab({ dictId, canEdit, onChanged }: TabProps) {
  const [groups, setGroups] = useState<CodeGroup[]>([])
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null)
  const [values, setValues] = useState<CodeValue[]>([])
  const groupGridRef = useRef<AgGridReact>(null)
  const { dialog, ask } = useConfirm()

  const fetchGroups = useCallback(async () => {
    try {
      const data = await api<CodeGroup[]>(`${BASE}/code-groups?dictionary_id=${dictId}`, "코드 그룹 목록을 불러오지 못했습니다.")
      setGroups(data)
      // 선택이 없거나 사라졌으면 첫 그룹
      setSelectedGroupId((cur) => (cur && data.some((g) => g.id === cur) ? cur : data[0]?.id ?? null))
    } catch (e) { toast.error(errMsg(e)) }
  }, [dictId])
  useEffect(() => { fetchGroups() }, [fetchGroups])

  const fetchValues = useCallback(async () => {
    if (!selectedGroupId) { setValues([]); return }
    try {
      const g = await api<CodeGroup>(`${BASE}/code-groups/${selectedGroupId}`, "코드 값을 불러오지 못했습니다.")
      setValues(g.values || [])
    } catch (e) { toast.error(errMsg(e)) }
  }, [selectedGroupId])
  useEffect(() => { fetchValues() }, [fetchValues])

  const addGroup = useCallback(async () => {
    try {
      const g = await api<CodeGroup>(`${BASE}/code-groups`, "코드 그룹을 추가하지 못했습니다.", {
        method: "POST", json: { dictionary_id: dictId, group_name: nextPlaceholder(groups.map((x) => x.group_name)) },
      })
      await fetchGroups()
      setSelectedGroupId(g.id)
      onChanged()
      focusNewRow(groupGridRef.current, g.id, "group_name")
    } catch (e) { toast.error(errMsg(e)) }
  }, [dictId, groups, fetchGroups, onChanged])

  const deleteGroup = useCallback(async (row: { id: number }) => {
    const g = groups.find((x) => x.id === row.id)
    if (!g) return
    let usage: Usage | null = null
    try { usage = await api<Usage>(`${BASE}/code-groups/${g.id}/usage`, "사용 현황을 불러오지 못했습니다.") } catch { /* 안내만 생략 */ }
    ask({
      title: `코드 그룹 '${g.group_name}' 삭제`,
      desc: (
        <>
          <p>코드 값 {usage?.value_count ?? g.values.length}개가 함께 삭제됩니다.</p>
          {usage && usage.domains.length > 0 && (
            <>
              <UsageList label="이 코드 그룹을 쓰는 도메인" items={usage.domains} />
              <p>해당 도메인의 코드 그룹 연결이 해제됩니다.</p>
            </>
          )}
        </>
      ),
      run: async () => {
        await api(`${BASE}/code-groups/${g.id}`, "코드 그룹을 삭제하지 못했습니다.", { method: "DELETE" })
        toast.success("코드 그룹을 삭제했습니다.")
        await fetchGroups()
        onChanged()
      },
    })
  }, [groups, ask, fetchGroups, onChanged])

  const onGroupCellChanged = useCallback(async (event: CellValueChangedEvent) => {
    const field = event.colDef.field
    if (field && await saveCellEdit(event, `${BASE}/code-groups/${event.data.id}`)) {
      // 오른쪽 패널 제목(그룹명)도 맞춰 둔다
      setGroups((cur) => cur.map((g) => (g.id === event.data.id ? { ...g, [field]: event.newValue } : g)))
    }
  }, [])

  const addValue = useCallback(async () => {
    if (!selectedGroupId) return
    // 그룹 내 코드는 유일해야 하므로 비어 있는 자리표시 코드(NEW1, NEW2, …)를 고른다.
    const used = new Set(values.map((v) => v.code_value))
    let n = 1
    while (used.has(`NEW${n}`)) n++
    try {
      await api(`${BASE}/code-groups/${selectedGroupId}/values`, "코드 값을 추가하지 못했습니다.", {
        method: "POST", json: { code_value: `NEW${n}`, code_name: "(new)", sort_order: values.length + 1 },
      })
      await fetchValues()
    } catch (e) { toast.error(errMsg(e)) }
  }, [selectedGroupId, values, fetchValues])

  const deleteValue = useCallback(async (row: { id: number }) => {
    const v = values.find((x) => x.id === row.id)
    if (!v) return
    ask({
      title: `코드 '${v.code_value}' 삭제`,
      desc: <p>코드 값 &lsquo;{v.code_value} — {v.code_name}&rsquo; 을(를) 삭제합니다.</p>,
      run: async () => {
        await api(`${BASE}/code-values/${v.id}`, "코드 값을 삭제하지 못했습니다.", { method: "DELETE" })
        toast.success("코드 값을 삭제했습니다.")
        await fetchValues()
      },
    })
  }, [values, ask, fetchValues])

  const onValueCellChanged = useCallback(async (event: CellValueChangedEvent) => {
    await saveCellEdit(event, `${BASE}/code-values/${event.data.id}`)
  }, [])

  const onGroupRowClicked = useCallback((event: { data?: { id: number } }) => {
    if (event.data?.id) setSelectedGroupId(event.data.id)
  }, [])

  const selectedGroup = groups.find((g) => g.id === selectedGroupId)

  const groupColDefs = useMemo<ColDef[]>(() => [
    { ...rowNumberCol, width: 45, maxWidth: 45 },
    { headerName: "그룹 이름", field: "group_name", flex: 1, minWidth: 120, editable: true, cellStyle: { fontWeight: 500 } },
    { headerName: "영문", field: "group_english", flex: 1, minWidth: 100, editable: true },
    { headerName: "도메인", field: "domain_count", width: 70, editable: false, cellStyle: { textAlign: "center" }, headerTooltip: "이 코드 그룹을 쓰는 도메인 수" },
    { ...statusCol, width: 85 },
    { ...deleteCol, width: 40, maxWidth: 40 },
  ] as ColDef[], [])

  const valueColDefs = useMemo<ColDef[]>(() => [
    { ...rowNumberCol, width: 45, maxWidth: 45 },
    { headerName: "코드", field: "code_value", width: 100, editable: true, cellStyle: { fontWeight: 500 } },
    { headerName: "이름 (한글)", field: "code_name", flex: 1, minWidth: 120, editable: true },
    { headerName: "영문", field: "code_english", flex: 1, minWidth: 100, editable: true },
    numberCol("순서", "sort_order", 70),
    { ...deleteCol, width: 40, maxWidth: 40 },
  ] as ColDef[], [])

  const groupGridCols = useMemo(() => readOnlyUnless(groupColDefs, canEdit), [groupColDefs, canEdit])
  const valueGridCols = useMemo(() => readOnlyUnless(valueColDefs, canEdit), [valueColDefs, canEdit])
  const noSort = useMemo<ColDef>(() => ({ ...DEFAULT_COL, sortable: false, minWidth: 40 }), [])

  return (
    <div className="flex flex-col flex-1 min-h-0 h-full">
      <div className="flex flex-1 gap-4 min-h-0">
        <div className="flex flex-col w-2/5 min-h-0">
          <div className="flex items-center justify-between mb-2 flex-shrink-0">
            <span className="text-sm font-medium text-muted-foreground">코드 그룹 {groups.length}개</span>
            {canEdit && (
              <Button variant="outline" size="sm" onClick={addGroup}>
                <Plus className="h-3.5 w-3.5 mr-1" />추가
              </Button>
            )}
          </div>
          <DeleteCtx.Provider value={deleteGroup}>
            <div className="ag-theme-alpine flex-1 min-h-0" style={{
              ...GRID_STYLE, "--ag-selected-row-background-color": "rgba(59, 130, 246, 0.1)",
            } as React.CSSProperties}>
              <AgGridReact
                ref={groupGridRef}
                columnDefs={groupGridCols}
                rowData={groups}
                defaultColDef={noSort}
                headerHeight={32} rowHeight={30}
                stopEditingWhenCellsLoseFocus
                onCellValueChanged={onGroupCellChanged}
                onRowClicked={onGroupRowClicked}
                rowSelection="single"
                animateRows={false}
                getRowId={(params) => String(params.data.id)}
                components={GRID_COMPONENTS}
                overlayNoRowsTemplate="등록된 코드 그룹이 없습니다."
              />
            </div>
          </DeleteCtx.Provider>
        </div>

        <div className="flex flex-col w-3/5 min-h-0">
          <div className="flex items-center justify-between mb-2 flex-shrink-0">
            <span className="text-sm font-medium text-muted-foreground">
              {selectedGroup ? `${selectedGroup.group_name} — 값 ${values.length}개` : "그룹 선택"}
            </span>
            {canEdit && selectedGroupId && (
              <Button variant="outline" size="sm" onClick={addValue}>
                <Plus className="h-3.5 w-3.5 mr-1" />값 추가
              </Button>
            )}
          </div>
          <DeleteCtx.Provider value={deleteValue}>
            <div className="ag-theme-alpine flex-1 min-h-0" style={GRID_STYLE}>
              {selectedGroupId ? (
                <AgGridReact
                  columnDefs={valueGridCols}
                  rowData={values}
                  defaultColDef={noSort}
                  headerHeight={32} rowHeight={30}
                  stopEditingWhenCellsLoseFocus
                  onCellValueChanged={onValueCellChanged}
                  animateRows={false}
                  getRowId={(params) => String(params.data.id)}
                  components={GRID_COMPONENTS}
                  overlayNoRowsTemplate="코드 값이 없습니다."
                />
              ) : (
                <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
                  값을 조회할 코드 그룹을 선택하세요
                </div>
              )}
            </div>
          </DeleteCtx.Provider>
        </div>
      </div>
      {dialog}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Compliance Tab
// ---------------------------------------------------------------------------

function ComplianceTab({ dictId }: { dictId: number }) {
  const [stats, setStats] = useState<ComplianceStats | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api<ComplianceStats>(`${BASE}/compliance?dictionary_id=${dictId}`, "준수율을 불러오지 못했습니다.")
      .then((s) => { if (alive) setStats(s) })
      .catch((e) => { if (alive) setError(errMsg(e)) })
    return () => { alive = false }
  }, [dictId])

  if (error) return <p className="text-sm text-destructive text-center py-8">{error}</p>
  if (!stats) return <p className="text-sm text-muted-foreground text-center py-8">불러오는 중...</p>
  if (stats.dataset_count === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-8">
        이 사전을 적용한 데이터셋이 없습니다.<br />
        데이터셋 상세의 &lsquo;표준 용어&rsquo; 탭에서 이 사전을 선택하고 자동 매핑을 실행하세요.
      </p>
    )
  }
  if (stats.total_columns === 0) {
    return <p className="text-sm text-muted-foreground text-center py-8">이 사전을 적용한 데이터셋에 컬럼이 없습니다.</p>
  }

  const pct = stats.compliance_rate

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="p-6">
          <div className="text-center mb-4">
            <p className="text-4xl font-bold">{pct}%</p>
            <p className="text-sm text-muted-foreground">표준 준수율</p>
            <p className="text-xs text-muted-foreground mt-1">
              이 사전을 적용한 데이터셋 {stats.dataset_count}개의 컬럼 {stats.total_columns}개 중 표준과 일치(매칭)하는 비율
            </p>
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

// ---------------------------------------------------------------------------
// History Tab (변경 이력)
// ---------------------------------------------------------------------------

const ENTITY_LABEL: Record<string, string> = {
  DICTIONARY: "사전", WORD: "단어", DOMAIN: "도메인", TERM: "용어", CODE_GROUP: "코드 그룹", CODE_VALUE: "코드 값",
}
const CHANGE_LABEL: Record<string, string> = { CREATE: "생성", UPDATE: "수정", DELETE: "삭제" }
const PAGE_SIZE = 50

function HistoryTab({ dictId }: { dictId: number }) {
  const [entityType, setEntityType] = useState("all")
  const [page, setPage] = useState(1)
  const [data, setData] = useState<{ items: ChangeLog[]; total: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const params = new URLSearchParams({ dictionary_id: String(dictId), page: String(page), page_size: String(PAGE_SIZE) })
    if (entityType !== "all") params.set("entity_type", entityType)
    api<{ items: ChangeLog[]; total: number }>(`${BASE}/change-logs?${params}`, "변경 이력을 불러오지 못했습니다.")
      .then((d) => { if (alive) { setData(d); setError(null) } })
      .catch((e) => { if (alive) setError(errMsg(e)) })
    return () => { alive = false }
  }, [dictId, entityType, page])

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1

  return (
    <div className="flex flex-col gap-3 min-h-0">
      <div className="flex items-center gap-3">
        <Select value={entityType} onValueChange={(v) => { setEntityType(v); setPage(1) }}>
          <SelectTrigger className="w-40 h-8 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">전체 대상</SelectItem>
            {Object.entries(ENTITY_LABEL).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}
          </SelectContent>
        </Select>
        <span className="text-sm text-muted-foreground">{data ? `${data.total}건` : ""}</span>
        <div className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} aria-label="이전 페이지">
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="text-xs text-muted-foreground">{page} / {pages}</span>
          <Button variant="ghost" size="sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)} aria-label="다음 페이지">
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {!error && !data && <p className="text-sm text-muted-foreground text-center py-8">불러오는 중...</p>}
      {data && data.items.length === 0 && (
        <p className="text-sm text-muted-foreground text-center py-8">변경 이력이 없습니다.</p>
      )}
      {data && data.items.length > 0 && (
        <div className="overflow-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-muted-foreground">
              <tr className="text-left">
                <th className="px-3 py-2 font-medium whitespace-nowrap">시각</th>
                <th className="px-3 py-2 font-medium">대상</th>
                <th className="px-3 py-2 font-medium">변경</th>
                <th className="px-3 py-2 font-medium">필드</th>
                <th className="px-3 py-2 font-medium">이전 → 이후</th>
                <th className="px-3 py-2 font-medium">변경자</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((r) => (
                <tr key={r.id} className="border-t align-top">
                  <td className="px-3 py-2 whitespace-nowrap text-muted-foreground">
                    {r.changed_at ? new Date(r.changed_at).toLocaleString() : "-"}
                  </td>
                  <td className="px-3 py-2">
                    <Badge variant="outline" className="mr-1.5 text-xs">{ENTITY_LABEL[r.entity_type] ?? r.entity_type}</Badge>
                    {r.entity_name ?? <span className="text-muted-foreground">#{r.entity_id}</span>}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className={r.change_type === "DELETE" ? "text-destructive" : r.change_type === "CREATE" ? "text-green-600" : ""}>
                      {CHANGE_LABEL[r.change_type] ?? r.change_type}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{r.field_name ?? ""}</td>
                  <td className="px-3 py-2 break-all">
                    {r.change_type === "UPDATE" && (
                      <>
                        <span className="text-muted-foreground line-through">{r.old_value ?? "(없음)"}</span>
                        {" → "}
                        <span>{r.new_value ?? "(없음)"}</span>
                      </>
                    )}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">{r.changed_by ?? <span className="text-muted-foreground">-</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
