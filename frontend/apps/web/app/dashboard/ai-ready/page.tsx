"use client"

/**
 * 거버넌스 › AI-Ready 점검 — 활성 데이터셋별 AI-Ready 체크리스트 충족률 (TODO-096 2단계, 화면 정리 TODO-101).
 *
 * 데이터셋마다 「공공데이터의 인공지능 친화적 관리 가이드라인」 부록 4 체크리스트를 판정해 필수·권장 충족률과
 * 공유 최소 요건 충족 여부를 보여 준다. 기본 정렬은 필수 충족률이 낮은 순 — 먼저 손볼 데이터셋이 위로 온다.
 * 범위는 카탈로그 → DB → 데이터셋 검색 순으로 좁힌다. 조건 · 정렬 · 쪽은 주소에 남아 상세에 다녀와도 그대로다.
 * 표 모양은 데이터셋 목록과 같다(카드 없이 테두리 한 겹, 이름 열은 표시 이름 + 아래 경로, 쪽 번호 · 쪽 크기).
 * 항목별 판정·수동 판정은 데이터셋 상세의 AI-Ready 탭에서 한다.
 */

import { InfoTip, Tip } from "@/components/tip"
import { Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react"
import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { ArrowDown, ArrowUp, ChevronRight, ChevronsUpDown, X } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import { Label } from "@workspace/ui/components/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import { Switch } from "@workspace/ui/components/switch"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@workspace/ui/components/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@workspace/ui/components/tooltip"
import { cn } from "@workspace/ui/lib/utils"

import { DashboardHeader } from "@/components/dashboard-header"
import { GridPagination, PAGE_SIZE_OPTIONS } from "@/components/grid-pagination"
import { errorMessage } from "@/lib/notify"
import {
  MinRequirementsBadge, PctText, RATE_LEGEND, RateText,
} from "@/features/datasets/components/ai-ready-check-tab"
import {
  fetchAiReadyItems, fetchAiReadyOverview, fetchAiReadyScopes,
  type AiReadyBreakdownGroup, type AiReadyItemDef, type AiReadyOverview, type AiReadyOverviewRow, type AiReadyScope,
} from "@/features/datasets/api"
import { datasetHref } from "@/features/datasets/links"
import { SearchInput } from "@/components/search-input"

const ALL = "__all__"
const NO_CATALOG = "0"     // 카탈로그가 없는 데이터셋 (catalog_id=0)
const NO_DB = "__none__"   // DB 단계가 없는 데이터셋 (db="")
const DEFAULT_SIZE = 20
const SEARCH_DELAY_MS = 300
const BREAKDOWN_LIMIT = 5  // 카탈로그 · DB 현황은 처음에 이만큼만 — 나머지는 "모두 보기"
const UNMET_SHOWN = 3      // 적정이 아닌 필수 항목은 앞 몇 개만 배지로, 나머지는 "외 n"

type Sort = "required" | "recommended" | "name"
const SORTS: Sort[] = ["required", "recommended", "name"]

/** 주소 ↔ 화면 조건. 기본값은 주소에 넣지 않는다. */
type View = { catalog: string; db: string; q: string; sort: Sort; desc: boolean; unmet: boolean; page: number; size: number }

function parseView(sp: { get(name: string): string | null }): View {
  const page = Number(sp.get("page"))
  const size = Number(sp.get("size"))
  const sort = sp.get("sort") as Sort | null
  const catalog = sp.get("catalog")
  return {
    catalog: catalog && /^\d+$/.test(catalog) ? catalog : ALL,
    db: catalog && sp.get("db") !== null ? (sp.get("db") || NO_DB) : ALL,
    q: sp.get("q")?.trim() ?? "",
    sort: sort && SORTS.includes(sort) ? sort : "required",
    desc: sp.get("desc") === "1",
    unmet: sp.get("unmet") === "1",
    page: Number.isInteger(page) && page > 1 ? page : 1,
    size: PAGE_SIZE_OPTIONS.includes(size) ? size : DEFAULT_SIZE,
  }
}

function viewQuery(v: View): string {
  const sp = new URLSearchParams()
  if (v.catalog !== ALL) {
    sp.set("catalog", v.catalog)
    if (v.db !== ALL) sp.set("db", v.db === NO_DB ? "" : v.db)
  }
  if (v.q) sp.set("q", v.q)
  if (v.sort !== "required") sp.set("sort", v.sort)
  if (v.desc) sp.set("desc", "1")
  if (v.unmet) sp.set("unmet", "1")
  if (v.page > 1) sp.set("page", String(v.page))
  if (v.size !== DEFAULT_SIZE) sp.set("size", String(v.size))
  return sp.toString()
}

function scopeValue(s: AiReadyScope): string {
  return s.catalog_id === null ? NO_CATALOG : String(s.catalog_id)
}

function dbValue(name: string): string {
  return name === "" ? NO_DB : name
}

function dbLabel(name: string): string {
  return name === "" ? "(DB 없음)" : name
}

/** 정렬되는 열 머리 — 누를 때마다 오름 ↔ 내림. 데이터셋 목록의 열 머리와 같은 화살표. */
function SortHead({ label, active, desc, onClick, className }: {
  label: string; active: boolean; desc: boolean; onClick: () => void; className?: string
}) {
  const Icon = !active ? ChevronsUpDown : desc ? ArrowDown : ArrowUp
  return (
    <TableHead className={className} aria-sort={active ? (desc ? "descending" : "ascending") : "none"}>
      <button type="button" onClick={onClick}
        className="-mx-2 inline-flex h-8 items-center gap-1.5 rounded-md px-2 hover:bg-accent hover:text-accent-foreground">
        {label}
        <Icon className={cn("h-3.5 w-3.5", !active && "text-muted-foreground/60")} />
      </button>
    </TableHead>
  )
}

export default function AiReadyOverviewPage() {
  // 조건이 주소(useSearchParams)에 있어 Suspense 로 감싼다
  return (
    <Suspense>
      <AiReadyOverviewContent />
    </Suspense>
  )
}

function AiReadyOverviewContent() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const view = useMemo(() => parseView(searchParams), [searchParams])
  const { catalog, db, q, sort, desc, unmet, page, size } = view

  const setView = useCallback((patch: Partial<View>) => {
    const next = { ...parseView(new URLSearchParams(window.location.search)), ...patch }
    const qs = viewQuery(next)
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false })
  }, [pathname, router])

  const [data, setData] = useState<AiReadyOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [scopes, setScopes] = useState<AiReadyScope[]>([])
  const [items, setItems] = useState<AiReadyItemDef[]>([])
  const [query, setQuery] = useState(q)

  useEffect(() => {
    // 범위 선택지와 항목 이름 — 실패해도 목록은 보인다(선택지 없음, 항목은 번호만)
    fetchAiReadyScopes().then(setScopes).catch(() => setScopes([]))
    fetchAiReadyItems().then(setItems).catch(() => setItems([]))
  }, [])

  // 입력을 멈추면 검색(타자마다 조회하지 않게) — 주소의 q 가 바뀌면(뒤로 가기 등) 입력칸도 따라간다
  // (입력 중 끝 공백은 지우지 않게 — 다듬은 값이 같으면 그대로 둔다)
  useEffect(() => { setQuery((prev) => (prev.trim() === q ? prev : q)) }, [q])
  useEffect(() => {
    const v = query.trim()
    if (v === q) return
    const t = setTimeout(() => setView({ q: v, page: 1 }), SEARCH_DELAY_MS)
    return () => clearTimeout(t)
  }, [query, q, setView])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await fetchAiReadyOverview({
        q: q || undefined,
        catalogId: catalog === ALL ? undefined : Number(catalog),
        db: catalog === ALL || db === ALL ? undefined : db === NO_DB ? "" : db,
        onlyUnmet: unmet, sort, desc, page, size,
      }))
    } catch (e) {
      setError(errorMessage(e) ?? "AI-Ready 충족률 목록을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [q, catalog, db, unmet, sort, desc, page, size])

  useEffect(() => { load() }, [load])

  const selectedScope = useMemo(
    () => (catalog === ALL ? undefined : scopes.find((s) => scopeValue(s) === catalog)),
    [catalog, scopes],
  )
  const itemDef = useMemo(() => new Map(items.map((i) => [i.key, i])), [items])
  const pages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1
  const s = data?.summary

  const pickCatalog = (v: string) => setView({ catalog: v, db: ALL, page: 1 })
  const pickDb = (v: string) => setView({ db: v, page: 1 })
  const pickGroup = (g: AiReadyBreakdownGroup) => {
    if (data?.breakdown.by === "db") pickDb(dbValue(g.db ?? ""))
    else pickCatalog(g.catalog_id === null ? NO_CATALOG : String(g.catalog_id))
  }
  const toggleSort = (key: Sort) => setView(key === sort ? { desc: !desc, page: 1 } : { sort: key, desc: false, page: 1 })

  const catalogName = selectedScope?.name ?? (catalog === NO_CATALOG ? "(카탈로그 없음)" : undefined)
  const dbName = db === ALL ? undefined : dbLabel(db === NO_DB ? "" : db)
  const scopeLabel = catalog === ALL ? "전체 카탈로그" : [catalogName ?? "카탈로그", dbName].filter(Boolean).join(" › ")
  const hasFilter = catalog !== ALL || !!q || unmet

  return (
    <>
      <DashboardHeader title="AI-Ready 점검" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <p className="text-sm">
          「공공데이터의 인공지능 친화적 관리 가이드라인」 체크리스트(필수 15 · 권장 18 · 유형별 5)를 데이터셋마다 판정한 결과입니다.
          카탈로그 → DB 를 고르고 데이터셋을 검색해 범위를 좁힙니다. 항목별 근거와 수동 판정은 데이터셋 상세의 AI-Ready 탭에서 봅니다.
        </p>

        {/* 조건 — 데이터셋 목록처럼 라벨이 든 선택 상자 한 줄 */}
        <div className="flex flex-wrap items-center gap-2">
          <Select value={catalog} onValueChange={pickCatalog}>
            <SelectTrigger className="h-9 w-60 text-sm"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL} className="text-sm">카탈로그: 전체</SelectItem>
              {scopes.map((sc) => (
                <SelectItem key={scopeValue(sc)} value={scopeValue(sc)} className="text-sm">
                  카탈로그: {sc.name} <span >({sc.datasets})</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={db} onValueChange={pickDb} disabled={!selectedScope}>
            <SelectTrigger className="h-9 w-52 text-sm" title={selectedScope ? undefined : "카탈로그를 먼저 고르세요"}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL} className="text-sm">DB: 전체</SelectItem>
              {selectedScope?.dbs.map((d) => (
                <SelectItem key={dbValue(d.name)} value={dbValue(d.name)} className="text-sm">
                  DB: {dbLabel(d.name)} <span >({d.datasets})</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <SearchInput aria-label="데이터셋 검색" className="w-64" value={query} onValueChange={setQuery}
            placeholder={selectedScope ? "테이블 이름 검색…" : "db.dataset 또는 이름 검색…"} />
          <div className="flex h-9 items-center gap-2 px-1">
            <Switch id="ai-ready-only-unmet" checked={unmet} onCheckedChange={(v) => setView({ unmet: v, page: 1 })} />
            <Label htmlFor="ai-ready-only-unmet" className="text-sm font-normal">필수 미충족만</Label>
          </div>
        </div>

        {/* 걸린 조건 — 칩의 X 로 하나씩 푼다 */}
        {hasFilter && (
          <div className="-mt-2 flex flex-wrap items-center gap-2 text-sm">
            <span >조건</span>
            {catalog !== ALL && (
              <FilterChip label={`카탈로그: ${catalogName ?? catalog}`} onClear={() => pickCatalog(ALL)} />
            )}
            {dbName && <FilterChip label={`DB: ${dbName}`} onClear={() => pickDb(ALL)} />}
            {q && <FilterChip label={`검색: ${q}`} onClear={() => setQuery("")} />}
            {unmet && <FilterChip label="필수 미충족만" onClear={() => setView({ unmet: false, page: 1 })} />}
            <Button size="sm" variant="ghost" className="h-7 px-2"
              onClick={() => { setQuery(""); setView({ catalog: ALL, db: ALL, q: "", unmet: false, page: 1 }) }}>
              모두 지우기
            </Button>
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <SummaryCard title="점검 데이터셋" value={s ? `${s.datasets}개` : "-"} note={scopeLabel} />
          <SummaryCard title="필수 충족률 평균" value={<PctText value={s?.required_rate ?? null} />}
            note={s ? `필수 항목을 모두 충족한 데이터셋 ${s.fully_required}개` : ""} />
          <SummaryCard title="권장 충족률 평균" value={<PctText value={s?.recommended_rate ?? null} />} note="권장 항목은 경고로만" />
          <SummaryCard title="공유 최소 요건 충족" value={s ? `${s.min_met}개` : "-"}
            note="결측률 · 필수 메타데이터 · 갱신 주기 · 표준 포맷" />
        </div>

        {/* DB 까지 고르면 더 좁힐 곳이 없어 현황 표를 감춘다 */}
        {data && data.breakdown.groups.length > 0 && !(catalog !== ALL && db !== ALL) && (
          <BreakdownSection
            by={data.breakdown.by}
            groups={data.breakdown.groups}
            catalogName={catalogName}
            onAll={() => pickCatalog(ALL)}
            onPick={pickGroup}
          />
        )}

        <section className="space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-base font-semibold">
              데이터셋 {data && <span className="text-sm font-normal">{data.total.toLocaleString()}개</span>}
            </h3>
            <span className="text-xs">{RATE_LEGEND}</span>
          </div>

          {error ? (
            <div className="flex items-center justify-between gap-4 rounded-md border px-4 py-3 text-sm">
              <span className="text-destructive">{error}</span>
              <Button size="sm" variant="outline" onClick={load}>다시 시도</Button>
            </div>
          ) : (
            <div className="overflow-hidden rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <SortHead label="데이터셋" active={sort === "name"} desc={desc} onClick={() => toggleSort("name")} />
                    <SortHead label="필수 충족률" className="w-[130px] text-center" active={sort === "required"} desc={desc}
                      onClick={() => toggleSort("required")} />
                    <SortHead label="권장 충족률" className="w-[130px] text-center" active={sort === "recommended"} desc={desc}
                      onClick={() => toggleSort("recommended")} />
                    <TableHead className="w-[100px] text-center"><InfoTip tip="공유 최소 요건 4가지 — 결측률 기준 미만 · 필수 메타데이터 · 갱신 주기 기준 이내 · 표준 포맷. 기준값은 AI-Ready 설정을 따릅니다.">최소 요건</InfoTip></TableHead>
                    <TableHead className="w-[38%]"><InfoTip tip="AI-Ready 필수 점검 항목 중 판정이 '적정'이 아닌 항목입니다. 마우스를 올리면 항목 설명이 보입니다.">적정이 아닌 필수 항목</InfoTip></TableHead>
                  </TableRow>
                </TableHeader>
                {/* 다시 불러오는 동안 이전 결과를 흐리게 남겨 자리가 튀지 않게 */}
                <TableBody className={cn(loading && data && "opacity-60 transition-opacity")}>
                  {!data ? (
                    <TableRow>
                      <TableCell colSpan={5} className="h-24 text-center text-muted-foreground">불러오는 중…</TableCell>
                    </TableRow>
                  ) : data.items.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="h-24 text-center text-muted-foreground">
                        조건에 맞는 데이터셋이 없습니다.
                      </TableCell>
                    </TableRow>
                  ) : data.items.map((r) => (
                    <TableRow key={r.dataset_id}>
                      <TableCell className="whitespace-normal">
                        <DatasetName row={r} showCatalog={catalog === ALL} />
                      </TableCell>
                      <TableCell className="text-center"><RateText rate={r.required} /></TableCell>
                      <TableCell className="text-center"><RateText rate={r.recommended} /></TableCell>
                      <TableCell className="text-center">
                        <MinRequirementsBadge met={r.min_requirements_met} />
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        <UnmetItems keys={r.unmet_required} defs={itemDef} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {data && data.total > 0 && (
            <GridPagination
              page={page - 1}
              pageCount={pages}
              pageSize={size}
              onPage={(p) => setView({ page: p + 1 })}
              onPageSize={(n) => setView({ size: n, page: 1 })}
            />
          )}
        </section>
      </div>
    </>
  )
}

function FilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <Badge variant="secondary" className="gap-1 pr-1 font-normal">
      {label}
      <Tip label={`${label} 조건 지우기`}><button type="button" aria-label={`${label} 조건 지우기`} onClick={onClear}
        className="rounded-sm p-0.5 hover:bg-muted-foreground/20">
        <X className="h-3 w-3" />
      </button></Tip>
    </Badge>
  )
}

/** 이름 열 — 데이터셋 목록과 같은 규칙: 표시 이름(없으면 경로)이 앞, 경로 · 카탈로그는 아래 회색. */
function DatasetName({ row, showCatalog }: { row: AiReadyOverviewRow; showCatalog: boolean }) {
  const sub = [
    row.display_name ? row.path : null,
    showCatalog ? row.catalog_name ?? "(카탈로그 없음)" : null,
  ].filter(Boolean)
  return (
    <div className="min-w-0">
      <Link href={datasetHref(row.dataset_id, "ai-ready")} className="break-all text-sm font-medium hover:underline">
        {row.display_name || row.path}
      </Link>
      {sub.length > 0 && <p className="break-all text-xs">{sub.join(" · ")}</p>}
    </div>
  )
}

/** 적정이 아닌 필수 항목 — 앞 몇 개는 "번호 짧은 이름" 배지, 나머지는 "외 n"(마우스를 올리면 목록). */
function UnmetItems({ keys, defs }: { keys: string[]; defs: Map<string, AiReadyItemDef> }) {
  if (keys.length === 0) return <span className="text-xs text-muted-foreground">없음</span>
  const shown = keys.slice(0, UNMET_SHOWN)
  const rest = keys.slice(UNMET_SHOWN)
  const label = (k: string) => (defs.get(k)?.short ? `${k} ${defs.get(k)?.short}` : k)
  return (
    <div className="flex flex-wrap items-center gap-1">
      {shown.map((k) => (
        <Tooltip key={k}>
          <TooltipTrigger asChild>
            <Badge size="sm" variant="secondary" className="cursor-default font-normal">{label(k)}</Badge>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">{defs.get(k)?.text ?? k}</TooltipContent>
        </Tooltip>
      ))}
      {rest.length > 0 && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge size="sm" variant="outline" className="cursor-default font-normal">외 {rest.length}개</Badge>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">
            <ul className="space-y-0.5">{rest.map((k) => <li key={k}>{label(k)}</li>)}</ul>
          </TooltipContent>
        </Tooltip>
      )}
      <span className="sr-only">모두 {keys.length}개</span>
    </div>
  )
}

type GroupSort = "name" | "datasets" | "required" | "recommended" | "min"

/**
 * 카탈로그별(카탈로그를 고르면 DB 별) 현황 — 이름을 누르면 그 범위로 좁힌다. 제목은 경로라 앞 단계를 눌러 돌아간다.
 * 처음엔 ``BREAKDOWN_LIMIT`` 개만, 열 머리로 정렬(기본: 필수 충족률 낮은 순).
 */
function BreakdownSection({ by, groups, catalogName, onAll, onPick }: {
  by: "catalog" | "db"
  groups: AiReadyBreakdownGroup[]
  catalogName?: string
  onAll: () => void
  onPick: (g: AiReadyBreakdownGroup) => void
}) {
  const [sort, setSort] = useState<GroupSort>("required")
  const [desc, setDesc] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const nameOf = useCallback(
    (g: AiReadyBreakdownGroup) => (by === "db" ? dbLabel(g.db ?? "") : g.catalog_name ?? "(카탈로그 없음)"),
    [by],
  )

  const sorted = useMemo(() => {
    const val = (g: AiReadyBreakdownGroup): number | string => {
      switch (sort) {
        case "name": return nameOf(g).toLowerCase()
        case "datasets": return g.datasets
        case "required": return g.required_rate ?? -1
        case "recommended": return g.recommended_rate ?? -1
        case "min": return g.datasets ? g.min_met / g.datasets : -1
      }
    }
    const out = [...groups].sort((a, b) => {
      const x = val(a), y = val(b)
      const c = x < y ? -1 : x > y ? 1 : nameOf(a).localeCompare(nameOf(b))
      return desc ? -c : c
    })
    return out
  }, [groups, sort, desc, nameOf])
  const rows = expanded ? sorted : sorted.slice(0, BREAKDOWN_LIMIT)
  const toggle = (k: GroupSort) => {
    if (k === sort) setDesc(!desc)
    else { setSort(k); setDesc(false) }
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        {/* 제목 = 지금 범위의 경로. 앞 단계를 누르면 돌아간다 */}
        <h3 className="flex flex-wrap items-center gap-1 text-base font-semibold">
          {by === "db" ? (
            <>
              <button type="button" onClick={onAll} className="text-muted-foreground hover:text-foreground hover:underline">
                전체 카탈로그
              </button>
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
              {catalogName ?? "카탈로그"} — DB 별
            </>
          ) : "카탈로그별"}
        </h3>
        <span className="text-xs">
          {by === "db" ? "DB" : "카탈로그"} 이름을 누르면 그 범위로 좁힙니다
        </span>
      </div>
      <div className="overflow-hidden rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <SortHead label={by === "db" ? "DB" : "카탈로그"} active={sort === "name"} desc={desc} onClick={() => toggle("name")} />
              <SortHead label="데이터셋" className="w-[110px] text-center" active={sort === "datasets"} desc={desc}
                onClick={() => toggle("datasets")} />
              <SortHead label="필수 충족률" className="w-[130px] text-center" active={sort === "required"} desc={desc}
                onClick={() => toggle("required")} />
              <SortHead label="권장 충족률" className="w-[130px] text-center" active={sort === "recommended"} desc={desc}
                onClick={() => toggle("recommended")} />
              <SortHead label="최소 요건" className="w-[150px] text-center" active={sort === "min"} desc={desc}
                onClick={() => toggle("min")} />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((g) => (
              <TableRow key={`${g.catalog_id}-${g.db}`}>
                <TableCell>
                  <button type="button" onClick={() => onPick(g)}
                    className={cn("inline-flex items-center gap-1 text-left text-sm font-medium hover:underline",
                      by === "db" && "font-mono text-[13px]")}>
                    {nameOf(g)}
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                  </button>
                </TableCell>
                <TableCell className="text-center tabular-nums">{g.datasets.toLocaleString()}</TableCell>
                <TableCell className="text-center"><PctText value={g.required_rate} /></TableCell>
                <TableCell className="text-center"><PctText value={g.recommended_rate} /></TableCell>
                <TableCell className="text-center tabular-nums">
                  {g.min_met} / {g.datasets}
                  <span className="ml-1 text-xs">
                    ({g.datasets ? Math.round((g.min_met / g.datasets) * 100) : 0}%)
                  </span>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {groups.length > BREAKDOWN_LIMIT && (
        <Button size="sm" variant="ghost" className="h-7 px-2 text-muted-foreground" onClick={() => setExpanded(!expanded)}>
          {expanded ? "접기" : `모두 보기 (${groups.length})`}
        </Button>
      )}
    </section>
  )
}

function SummaryCard({ title, value, note }: { title: string; value: ReactNode; note: string }) {
  return (
    <Card>
      <CardContent className="space-y-1 p-4">
        <div className="text-sm">{title}</div>
        <div className="text-2xl font-semibold">{value}</div>
        <div className="truncate text-xs" title={note}>{note}</div>
      </CardContent>
    </Card>
  )
}
