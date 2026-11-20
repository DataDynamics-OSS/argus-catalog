"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Library, Plus } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { cn } from "@workspace/ui/lib/utils"
import { DashboardHeader } from "@/components/dashboard-header"
import { SearchInput } from "@/components/search-input"
import { fetchCatalogs, syncConnection, type Catalog } from "@/features/catalogs/api"
import {
  CATALOG_TABS,
  CatalogDetail,
  CatalogSourceBadge,
  type CatalogTab,
} from "@/features/catalogs/components/catalog-detail"
import { CatalogFormDialog } from "@/features/catalogs/components/catalog-form-dialog"
import { usePermissions } from "@/features/permissions/use-permissions"
import { useResizableWidth } from "@/hooks/use-resizable-width"
import { errorMessage } from "@/lib/notify"

/**
 * 데이터 카탈로그 > 카탈로그 (TODO-CAT-001, design/catalog-registration-design.md 9.3).
 * 왼쪽 목록(직접 등록 · 데이터 소스와 함께 생긴 카탈로그 모두), 오른쪽 상세(개요 · 데이터셋 · 데이터 소스 연결).
 * 선택 · 탭은 주소(?catalog=<catalog_key>&tab=)에 남긴다.
 */
export default function CatalogsPage() {
  const [catalogs, setCatalogs] = useState<Catalog[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [tab, setTab] = useState<CatalogTab>("overview")
  const [query, setQuery] = useState("")
  const [form, setForm] = useState<{ open: boolean; session: number; editing: Catalog | null }>({
    open: false, session: 0, editing: null,
  })
  const urlApplied = useRef(false)

  const { loaded, canUseFeature } = usePermissions()
  const canManage = canUseFeature("catalogs.manage") || canUseFeature("datasources.manage")
  const canCreateDataset = loaded && canUseFeature("datasets.create")

  const { width: asideWidth, startResize } = useResizableWidth("argus.catalogs.asideWidth", {
    defaultWidth: 300, min: 220, max: 560,
  })

  const load = useCallback(async () => {
    try {
      setCatalogs(await fetchCatalogs())
      setLoadError(null)
    } catch (e) {
      setLoadError(errorMessage(e) ?? "카탈로그 목록을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  // 주소로 들어오면 그 카탈로그 · 탭을 연다(최초 로드 뒤 한 번)
  useEffect(() => {
    if (urlApplied.current || loading) return
    urlApplied.current = true
    const params = new URLSearchParams(window.location.search)
    const key = params.get("catalog")
    const t = params.get("tab")
    if (t && (CATALOG_TABS as readonly string[]).includes(t)) setTab(t as CatalogTab)
    const match = key ? catalogs.find((c) => c.catalog_key === key || String(c.id) === key) : undefined
    if (match) setSelectedId(match.id)
  }, [loading, catalogs])

  const selected = useMemo(() => catalogs.find((c) => c.id === selectedId) ?? null, [catalogs, selectedId])

  useEffect(() => {
    if (!urlApplied.current) return
    const q = new URLSearchParams()
    if (selected) {
      q.set("catalog", selected.catalog_key)
      if (tab !== "overview") q.set("tab", tab)
    }
    const qs = q.toString()
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname)
  }, [selected, tab])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const rows = q
      ? catalogs.filter((c) => [c.name, c.kind, c.summary, c.owner, c.catalog_key]
        .some((v) => v?.toLowerCase().includes(q)))
      : catalogs
    // 직접 등록한 카탈로그를 위로
    return [...rows].sort((a, b) =>
      (a.source_type === "ARGUS" ? 0 : 1) - (b.source_type === "ARGUS" ? 0 : 1) || a.name.localeCompare(b.name))
  }, [catalogs, query])

  const replaceCatalog = (c: Catalog) =>
    setCatalogs((rows) => (rows.some((r) => r.id === c.id) ? rows.map((r) => (r.id === c.id ? c : r)) : [...rows, c]))

  return (
    <>
      <DashboardHeader title="카탈로그" />
      <div className="flex h-[calc(100svh-4rem)] shrink-0 flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm">
            데이터셋을 묶는 카탈로그 — 데이터 소스에서 수집한 카탈로그와 데이터 소스 없이 직접 등록한 카탈로그
            {canManage ? "" : " (조회 전용)"}
          </p>
          {canManage && (
            <Button size="sm" className="ml-auto" onClick={() => setForm((f) => ({ open: true, session: f.session + 1, editing: null }))}>
              <Plus className="mr-1 h-3.5 w-3.5" /> 카탈로그 등록
            </Button>
          )}
        </div>
        {loadError && (
          <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <span className="flex-1">목록을 불러오지 못했습니다: {loadError}</span>
            <Button size="sm" variant="outline" className="h-7" onClick={() => void load()}>다시 시도</Button>
          </div>
        )}

        <div className="flex min-h-0 flex-1 gap-1">
          <aside className="flex shrink-0 flex-col gap-2 overflow-hidden rounded-md border p-2" style={{ width: asideWidth }}>
            <SearchInput size="xs" value={query} onValueChange={setQuery} placeholder="카탈로그 검색…" />
            <div className="min-h-0 flex-1 overflow-auto">
              {loading ? (
                <p className="py-8 text-center text-sm text-muted-foreground">불러오는 중…</p>
              ) : filtered.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted-foreground">카탈로그가 없습니다.</p>
              ) : (
                <ul className="grid gap-0.5">
                  {filtered.map((c) => {
                    const sync = syncConnection(c)
                    return (
                      <li key={c.id}>
                        <button
                          type="button"
                          onClick={() => setSelectedId(c.id)}
                          className={cn(
                            "flex w-full flex-col items-start gap-0.5 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
                            c.id === selectedId && "bg-muted font-medium",
                          )}
                        >
                          <span className="flex w-full items-center gap-2">
                            <Library className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                            <span className="truncate">{c.name}</span>
                            <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">{c.dataset_count}</span>
                          </span>
                          <span className="flex items-center gap-1.5 pl-5.5 text-xs text-muted-foreground">
                            <CatalogSourceBadge catalog={c} />
                            {sync && <span className="truncate">{sync.datasource_name}</span>}
                          </span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          </aside>

          <div
            role="separator"
            aria-orientation="vertical"
            onMouseDown={startResize}
            className="group flex w-1.5 shrink-0 cursor-col-resize items-stretch justify-center"
          >
            <span className="w-px bg-border/40 transition-colors group-hover:bg-border/80" />
          </div>

          <main className="flex-1 overflow-auto px-4 pb-4 pt-1">
            {selected ? (
              <CatalogDetail
                key={selected.id}
                catalog={selected}
                tab={tab}
                onTabChange={setTab}
                canManage={canManage}
                canCreateDataset={canCreateDataset}
                onEdit={() => setForm((f) => ({ open: true, session: f.session + 1, editing: selected }))}
                onChanged={replaceCatalog}
                onDeleted={() => { setSelectedId(null); void load() }}
              />
            ) : (
              <p className="py-16 text-center text-sm text-muted-foreground">왼쪽에서 카탈로그를 고르세요.</p>
            )}
          </main>
        </div>
      </div>

      <CatalogFormDialog
        key={form.session}
        open={form.open}
        onOpenChange={(open) => setForm((f) => ({ ...f, open }))}
        catalog={form.editing}
        onSaved={(c) => { replaceCatalog(c); setSelectedId(c.id) }}
      />
    </>
  )
}
