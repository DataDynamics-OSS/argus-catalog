"use client"

import { Tip } from "@/components/tip"
import { CodeEditor } from "@/components/code-editor"
import { useCallback, useEffect, useMemo, useState } from "react"
import { ChevronDown, ChevronRight, Loader2, X } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Label } from "@workspace/ui/components/label"
import { RadioGroup, RadioGroupItem } from "@workspace/ui/components/radio-group"
import {
  browseDatasource,
  previewSyncScope,
  saveSyncScope,
  type SyncBrowseItem,
  type SyncScope,
  type SyncScopeItem,
  type SyncScopePreview,
  type SyncSettings,
} from "@/features/datasources/api"
import { excludesToItems, isExcluded, itemsToExcludes, scopeToText, textToScope } from "@/features/datasources/sync-format"
import { errorMessage, toast, notifyError } from "@/lib/notify"
import { SearchInput } from "@/components/search-input"
import { TagInput } from "@/components/tag-input"

// 많이 고르면 저장 전에 한 번 더 확인한다(설계 §4.2)
const MANY_CONTAINERS = 200

type Level = { items: SyncBrowseItem[]; total: number; next: number | null; loading: boolean; error: string | null }
const EMPTY_LEVEL: Level = { items: [], total: 0, next: null, loading: true, error: null }
const keyOf = (path: string[]) => path.join(".")

/** 일부 테이블만 고른 컨테이너 — 컨테이너 경로와 고른 테이블(상대 이름). */
type TablePick = { path: string[]; tables: Set<string> }

/**
 * 수집 범위 선택 — 원본을 열어 컨테이너(데이터베이스·스키마·네임스페이스·카탈로그)를 고른다.
 * - 컨테이너를 체크하면 그 안의 테이블 전체(앞으로 생기는 테이블 포함), 펼쳐서 테이블을 체크하면 그 테이블만.
 * - Iceberg 는 네임스페이스를 펼쳐 하위를 따로 고를 수 있다(고른 네임스페이스만 수집 — 하위는 포함하지 않음).
 * - 제외 패턴(glob)은 어느 쪽이든 다시 뺀다.
 * 원본에 닿지 못하면 이름을 직접 입력할 수 있다. 저장 전 범위 밖에 남는 데이터셋을 어떻게 할지 묻는다.
 * ``once`` 모드는 저장하지 않고 고른 범위로 한 번만 수집한다(``onRunOnce``).
 */
export function SyncScopeDialog({
  open,
  onOpenChange,
  datasource,
  settings,
  onSaved,
  once = false,
  onRunOnce,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  datasource: { id: number; name?: string; type: string }
  settings: SyncSettings
  onSaved: (s: SyncSettings, runNow: boolean) => void
  once?: boolean
  onRunOnce?: (scope: SyncScope) => void
}) {
  const label = settings.container_label
  const saved = settings.scope
  const [mode, setMode] = useState<"all" | "some">("some")
  const [selected, setSelected] = useState<Map<string, string[]>>(new Map())
  const [partial, setPartial] = useState<Map<string, TablePick>>(new Map())
  const [excludeItems, setExcludeItems] = useState<string[]>([])
  const [levels, setLevels] = useState<Record<string, Level>>({})
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [q, setQ] = useState("")
  const [query, setQuery] = useState("")
  const [manual, setManual] = useState(false)
  const [manualText, setManualText] = useState("")
  const [confirm, setConfirm] = useState<{ preview: SyncScopePreview; runNow: boolean } | null>(null)
  const [removeOutside, setRemoveOutside] = useState(false)
  const [saving, setSaving] = useState(false)

  const load = useCallback(
    async (path: string[], cursor = 0, search = "") => {
      const key = keyOf(path)
      setLevels((prev) => ({
        ...prev,
        [key]: { ...(cursor ? prev[key] ?? EMPTY_LEVEL : EMPTY_LEVEL), loading: true, error: null },
      }))
      try {
        const r = await browseDatasource(datasource.id, path, { q: search || undefined, cursor })
        setLevels((prev) => ({
          ...prev,
          [key]: {
            items: cursor ? [...(prev[key]?.items ?? []), ...r.items] : r.items,
            total: r.total,
            next: r.next_cursor,
            loading: false,
            error: null,
          },
        }))
      } catch (e) {
        const msg = errorMessage(e) ?? String(e)
        setLevels((prev) => ({ ...prev, [key]: { ...EMPTY_LEVEL, loading: false, error: msg } }))
      }
    },
    [datasource.id],
  )

  // 열 때마다 저장된 범위로 초기화하고 최상위를 불러온다
  useEffect(() => {
    if (!open) return
    setMode(saved?.include_all || !settings.supports_containers ? "all" : "some")
    const whole = (saved?.include ?? []).filter((i) => !i.tables?.length)
    setSelected(new Map(whole.map((i) => [keyOf(i.path), i.path])))
    setPartial(new Map((saved?.include ?? []).filter((i) => i.tables?.length)
      .map((i) => [keyOf(i.path), { path: i.path, tables: new Set(i.tables) }])))
    setExcludeItems(excludesToItems(saved?.exclude))
    setManualText(scopeToText(saved))
    setManual(false)
    setConfirm(null)
    setRemoveOutside(false)
    setExpanded(new Set())
    setLevels({})
    setQ("")
    setQuery("")
    if (settings.supports_containers) void load([])
  }, [open, saved, settings.supports_containers, load])

  const root = levels[""] ?? EMPTY_LEVEL

  const excludes = useMemo(() => itemsToExcludes(excludeItems), [excludeItems])
  const draft: SyncScope = useMemo(() => {
    const exclude = excludes
    if (mode === "all") return { include_all: true, include: [], exclude }
    const wholes: SyncScopeItem[] = manual ? textToScope(manualText).include : [...selected.values()].map((path) => ({ path }))
    const wholeKeys = new Set(wholes.map((i) => keyOf(i.path)))
    const parts: SyncScopeItem[] = [...partial.values()]
      .filter((p) => p.tables.size > 0 && !wholeKeys.has(keyOf(p.path)))
      .map((p) => ({ path: p.path, tables: [...p.tables].sort() }))
    return { include_all: false, include: [...wholes, ...parts], exclude }
  }, [mode, manual, manualText, selected, partial, excludes])
  const valid = draft.include_all || draft.include.length > 0
  const tableCount = draft.include.reduce((n, i) => n + (i.tables?.length ?? 0), 0)
  const wholeCount = draft.include.length - draft.include.filter((i) => i.tables?.length).length

  // 컨테이너 체크 — 전체로 바꾸면 그 컨테이너의 개별 테이블 선택은 지운다
  const toggle = (item: { path: string[] }, on: boolean) => {
    const key = keyOf(item.path)
    setSelected((prev) => {
      const next = new Map(prev)
      if (on) next.set(key, item.path)
      else next.delete(key)
      return next
    })
    setPartial((prev) => {
      if (!prev.has(key)) return prev
      const next = new Map(prev)
      next.delete(key)
      return next
    })
  }

  const toggleTable = (item: SyncBrowseItem, on: boolean) => {
    const container = item.container ?? item.path.slice(0, -1)
    const table = item.table ?? item.name
    const key = keyOf(container)
    setPartial((prev) => {
      const next = new Map(prev)
      const cur = new Set(next.get(key)?.tables ?? [])
      if (on) cur.add(table)
      else cur.delete(table)
      if (cur.size) next.set(key, { path: container, tables: cur })
      else next.delete(key)
      return next
    })
  }

  const toggleExpand = (item: SyncBrowseItem) => {
    const key = keyOf(item.path)
    const opening = !expanded.has(key)
    if (opening && !levels[key]) void load(item.path)
    setExpanded((prev) => {
      const next = new Set(prev)
      if (opening) next.add(key)
      else next.delete(key)
      return next
    })
  }

  const search = () => {
    setQuery(q.trim())
    void load([], 0, q.trim())
  }

  const save = async (runNow: boolean, preview?: SyncScopePreview) => {
    setSaving(true)
    try {
      if (once) {
        onRunOnce?.(draft)
        onOpenChange(false)
        return
      }
      if (!preview) {
        if (!draft.include_all && draft.include.length >= MANY_CONTAINERS) {
          toast.info(`${draft.include.length}개 ${label}을 골랐습니다. 수집이 오래 걸릴 수 있습니다.`)
        }
        const p = await previewSyncScope(datasource.id, draft)
        if (p.outside_count > 0) {
          setConfirm({ preview: p, runNow })
          return
        }
      }
      const s = await saveSyncScope(datasource.id, draft, removeOutside)
      toast.success(
        s.removed_outside ? `수집 범위를 저장하고 범위 밖 데이터셋 ${s.removed_outside}개를 제거로 표시했습니다.`
          : "수집 범위를 저장했습니다.",
      )
      onSaved(s, runNow)
      onOpenChange(false)
    } catch (e) {
      notifyError(e, "수집 범위를 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  // 트리에 보이지 않는 선택(검색으로 가려졌거나, 원본에서 사라졌거나, 하위 네임스페이스)
  const visible = new Set<string>()
  for (const lv of Object.values(levels)) for (const i of lv.items) if (i.kind !== "table") visible.add(keyOf(i.path))
  const hiddenSelected = [...selected.entries()].filter(([k]) => !visible.has(k))
  const hiddenPartial = [...partial.entries()].filter(([k]) => !visible.has(k))

  const renderLevel = (path: string[], depth: number) => {
    const lv = levels[keyOf(path)] ?? EMPTY_LEVEL
    return (
      <>
        {lv.items.map((item) => {
          const key = keyOf(item.path)
          const isOpen = expanded.has(key)
          const kind = item.kind ?? "container"
          const containerKey = keyOf(item.container ?? item.path.slice(0, -1))
          // 테이블: 컨테이너 전체가 골라져 있으면 체크된 채로 잠금
          const wholeParent = kind === "table" && selected.has(containerKey)
          const checked = kind === "table"
            ? wholeParent || (partial.get(containerKey)?.tables.has(item.table ?? item.name) ?? false)
            : selected.has(key) ? true : partial.has(key) ? "indeterminate" : false
          const canExpand = item.expandable
          const excluded = kind === "table" && excludes.length > 0 &&
            isExcluded(excludes, item.container ?? item.path.slice(0, -1), item.table ?? item.name)
          return (
            <div key={`${kind}:${key}`}>
              <div className="flex items-center gap-2 py-0.5 text-sm" style={{ paddingLeft: depth * 16 }}>
                {canExpand ? (
                  <Tip label={isOpen ? "접기" : "펼치기"}><button type="button" className="text-muted-foreground" onClick={() => toggleExpand(item)}
                    aria-label={isOpen ? "접기" : "펼치기"}>
                    {isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                  </button></Tip>
                ) : (
                  <span className="w-4" />
                )}
                {kind === "group" ? (
                  <span className="w-4" />
                ) : (
                  <Checkbox
                    checked={checked}
                    disabled={mode === "all" || wholeParent}
                    onCheckedChange={(v) => (kind === "table" ? toggleTable(item, v === true) : toggle(item, v === true))}
                    aria-label={item.name}
                  />
                )}
                <span className={item.system || excluded ? "text-sm text-muted-foreground" : "text-sm"}>
                  {item.name}
                </span>
                {item.system && <Badge size="sm" variant="outline">시스템</Badge>}
                {excluded && (
                  <Badge size="sm" variant="outline" title="제외 패턴에 걸려 수집하지 않습니다">제외</Badge>
                )}
                {kind === "container" && partial.has(key) && (
                  <Badge size="sm" variant="secondary">일부 {partial.get(key)?.tables.size}</Badge>
                )}
                <span className="ml-auto text-xs">
                  {kind === "table"
                    ? item.collected > 0 ? "수집됨" : item.removed > 0 ? "제거됨" : ""
                    : <>{item.collected > 0 && `수집됨 ${item.collected}`}{item.removed > 0 && ` · 제거됨 ${item.removed}`}</>}
                </span>
              </div>
              {isOpen && renderLevel(item.path, depth + 1)}
            </div>
          )
        })}
        {lv.loading && (
          <p className="flex items-center gap-1 py-1 text-xs text-muted-foreground" style={{ paddingLeft: depth * 16 + 24 }}>
            <Loader2 className="size-3 animate-spin" /> 불러오는 중…
          </p>
        )}
        {lv.error && depth > 0 && (
          <p className="py-1 text-xs text-destructive" style={{ paddingLeft: depth * 16 + 24 }}>{lv.error}</p>
        )}
        {!lv.loading && !lv.error && lv.items.length === 0 && depth > 0 && (
          <p className="py-1 text-xs text-muted-foreground" style={{ paddingLeft: depth * 16 + 24 }}>하위 항목 없음</p>
        )}
        {lv.next != null && !lv.loading && (
          <Button variant="ghost" size="sm" className="h-6 text-xs" style={{ marginLeft: depth * 16 + 24 }}
            onClick={() => void load(path, lv.next ?? 0, depth === 0 ? query : "")}>
            더 보기 ({lv.items.length} / {lv.total})
          </Button>
        )}
      </>
    )
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !saving && onOpenChange(o)}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {once ? "이번 한 번만 수집" : "수집 범위 선택"}{datasource.name ? ` — ${datasource.name}` : ""}
          </DialogTitle>
          <DialogDescription>
            {label}을 체크하면 그 안의 테이블 전체(나중에 생기는 테이블 포함), 펼쳐서 테이블을 체크하면 그 테이블만 수집합니다.
            {once && " 저장된 범위는 바뀌지 않습니다."}
          </DialogDescription>
        </DialogHeader>

        {confirm ? (
          <div className="grid gap-3 text-sm">
            <p>
              새 범위 밖에 이미 수집된 데이터셋 <b>{confirm.preview.outside_count}개</b>가 남습니다
              {confirm.preview.outside_containers.length > 0 && (
                <> ({confirm.preview.outside_containers.slice(0, 5).join(", ")}
                  {confirm.preview.outside_containers.length > 5 ? " …" : ""})</>
              )}
              . 어떻게 할까요?
            </p>
            <RadioGroup value={removeOutside ? "remove" : "keep"} onValueChange={(v) => setRemoveOutside(v === "remove")}>
              <label className="flex items-center gap-2">
                <RadioGroupItem value="keep" /> 그대로 둠 (기본 — 더 이상 갱신되지 않음)
              </label>
              <label className="flex items-center gap-2">
                <RadioGroupItem value="remove" /> 제거로 표시
              </label>
            </RadioGroup>
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirm(null)} disabled={saving}>이전</Button>
              <Button onClick={() => void save(confirm.runNow, confirm.preview)} disabled={saving}>
                {confirm.runNow ? "저장하고 지금 수집" : "저장"}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="grid gap-3">
            <RadioGroup value={mode} onValueChange={(v) => setMode(v as "all" | "some")} className="flex gap-4">
              <label className="flex items-center gap-2 text-sm">
                <RadioGroupItem value="some" disabled={!settings.supports_containers} /> 선택한 {label}만
              </label>
              <label className="flex items-center gap-2 text-sm">
                <RadioGroupItem value="all" /> 전체 (새로 생기는 {label} 포함)
              </label>
            </RadioGroup>
            {!settings.supports_containers && (
              <p className="text-xs text-muted-foreground">이 유형은 아직 전체 수집만 지원합니다.</p>
            )}

            {mode === "some" && settings.supports_containers && (
              manual ? (
                <div className="grid gap-1">
                  <Label className="text-xs text-muted-foreground">수집할 {label} (한 줄에 하나)</Label>
                  <CodeEditor value={manualText} onChange={setManualText} aria-label={`수집할 ${label}`}
                    minHeight={128} maxHeight={240} placeholder={settings.nested_containers ? "sales\nsales.emea" : "sales\nhr"} />
                  <Button variant="link" size="sm" className="h-auto justify-start p-0 text-xs" onClick={() => {
                    const wholes = textToScope(manualText).include
                    setSelected(new Map(wholes.map((i) => [keyOf(i.path), i.path])))
                    // 직접 입력에서 전체로 고른 컨테이너는 개별 테이블 선택을 덮어쓴다
                    setPartial((prev) => new Map([...prev].filter(([k]) => !wholes.some((i) => keyOf(i.path) === k))))
                    setManual(false)
                    if (!levels[""] || levels[""].error) void load([])
                  }}>
                    원본에서 고르기
                  </Button>
                </div>
              ) : (
                <div className="grid gap-2">
                  <SearchInput size="sm" value={q} onValueChange={setQ} placeholder={`${label} 이름 검색…`}
                    onSearch={search}
                    onClear={() => {
                      setQ("")
                      setQuery("")
                      void load([], 0, "")
                    }} />
                  <div className="max-h-[45vh] overflow-auto rounded border p-2">
                    {root.error ? (
                      <div className="grid gap-1 text-sm">
                        <p className="text-destructive">{root.error}</p>
                        <Button variant="link" size="sm" className="h-auto justify-start p-0 text-xs" onClick={() => {
                          setManualText([...selected.keys()].join("\n"))
                          setManual(true)
                        }}>
                          이름을 직접 입력하기
                        </Button>
                      </div>
                    ) : !root.loading && root.items.length === 0 ? (
                      <p className="text-sm text-muted-foreground">{query ? "검색 결과가 없습니다." : `${label}이 없습니다.`}</p>
                    ) : (
                      renderLevel([], 0)
                    )}
                  </div>
                  {hiddenPartial.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1 text-xs">
                      <span className="text-muted-foreground">일부 테이블:</span>
                      {hiddenPartial.map(([k, p]) => (
                        <Badge key={k} variant="secondary" className="gap-1">
                          {k} ({p.tables.size})
                          <Tip label={`${k} 테이블 선택 해제`}><button type="button" aria-label={`${k} 테이블 선택 해제`} onClick={() => toggle(p, false)}>
                            <X className="size-3" />
                          </button></Tip>
                        </Badge>
                      ))}
                    </div>
                  )}
                  {hiddenSelected.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1 text-xs">
                      <span className="text-muted-foreground">목록에 안 보이는 선택:</span>
                      {hiddenSelected.map(([k, p]) => (
                        <Badge key={k} variant="secondary" className="gap-1">
                          {k}
                          <Tip label={`${k} 선택 해제`}><button type="button" aria-label={`${k} 선택 해제`}
                            onClick={() => toggle({ path: p }, false)}>
                            <X className="size-3" />
                          </button></Tip>
                        </Badge>
                      ))}
                    </div>
                  )}
                  <Button variant="link" size="sm" className="h-auto justify-start p-0 text-xs" onClick={() => {
                    setManualText([...selected.keys()].join("\n"))
                    setManual(true)
                  }}>
                    이름을 직접 입력하기
                  </Button>
                </div>
              )
            )}

            <div className="grid gap-1">
              <Label htmlFor="sync-exclude" className="text-xs text-muted-foreground">
                제외 패턴 (입력 후 Enter, * ? 사용 — 특정 {label}에서만 빼려면 <code>{label === "카탈로그" ? "hive" : "sales"}:tmp_*</code>)
              </Label>
              <TagInput id="sync-exclude" size="sm" mono value={excludeItems} onChange={setExcludeItems}
                placeholder="예: tmp_* 입력 후 Enter" />
            </div>

            <DialogFooter className="items-center sm:justify-between">
              <span className="text-xs">
                {draft.include_all
                  ? `전체 ${label}`
                  : `선택: ${label} ${wholeCount}개 전체${tableCount ? ` + 테이블 ${tableCount}개` : ""}`}
                {draft.exclude?.length ? ` · 제외 패턴 ${draft.exclude.length}개` : ""}
                {settings.nested_containers && !draft.include_all && " (하위 네임스페이스는 따로 선택)"}
              </span>
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>취소</Button>
                {once ? (
                  <Button onClick={() => void save(true)} disabled={saving || !valid}>이 범위로 한 번 수집</Button>
                ) : (
                  <>
                    <Button variant="outline" onClick={() => void save(false)} disabled={saving || !valid}>저장</Button>
                    <Button onClick={() => void save(true)} disabled={saving || !valid}>저장하고 지금 수집</Button>
                  </>
                )}
              </div>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
