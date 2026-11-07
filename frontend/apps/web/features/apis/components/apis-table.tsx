"use client"

import { useEffect, useRef, useState, type ReactNode } from "react"
import {
  ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Copy, ExternalLink, FileCode2, Globe,
  LayoutGrid, Link2, List, MoreHorizontal, Plus, Radio, Search, Share2, Webhook, X, Zap,
  type LucideIcon,
} from "lucide-react"
import { toast } from "@/lib/notify"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import { Input } from "@workspace/ui/components/input"
import { Skeleton } from "@workspace/ui/components/skeleton"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@workspace/ui/components/table"
import { cn } from "@workspace/ui/lib/utils"

import { type ApiSort, type ApiSummary } from "../api"
import { OwnerValue } from "./api-owner"
import {
  API_STATUS_LABEL, API_STATUS_VARIANTS, CERT_LABEL, SPEC_FORMAT_LABEL, STATUS_ORDER, TIER_LABEL, protocolLabel,
} from "./apis-constants"
import { TabError } from "./apis-detail-shared"
import { useApis } from "./apis-provider"
import { PAGE_SIZE_OPTIONS, hasActiveFilters } from "./apis-url-state"
import { usePermissions } from "@/features/permissions/use-permissions"

// 프로토콜 아이콘 — 표·카드에서 프로토콜을 한눈에 구분(미지정/기타는 Webhook).
const PROTOCOL_ICON: Record<string, LucideIcon> = {
  rest: Globe, graphql: Share2, grpc: Zap, soap: FileCode2, webhook: Webhook, asyncapi: Radio,
}

// 인증 상태·등급 배지 색상 (라벨은 apis-constants)
const CERT_BADGE: Record<string, string> = {
  CERTIFIED: "border-emerald-600/40 text-emerald-700",
  IN_REVIEW: "border-sky-600/40 text-sky-700",
  DEPRECATED: "border-amber-600/40 text-amber-700",
}
const TIER_BADGE: Record<string, string> = {
  GOLD: "border-yellow-500/50 bg-yellow-50 text-yellow-800",
  SILVER: "border-slate-400/50 bg-slate-50 text-slate-700",
  BRONZE: "border-orange-400/50 bg-orange-50 text-orange-800",
}

const SORT_LABEL: Record<ApiSort, string> = { updated: "최근 수정순", created: "최근 등록순", name: "이름순" }
const ALL = "__all__"  // Select 의 "전체" 값(빈 문자열은 Radix Select 값으로 쓸 수 없음)
const SEARCH_DEBOUNCE_MS = 400

const CARD_GRID = "grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-3"

/** 날짜를 'yyyy-MM-dd' 로 포맷. */
function fmtDate(value: string | null | undefined): string | null {
  if (!value) return null
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return null
  const p = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

// 포맷 표시 — 스펙 등록 API 는 스펙 포맷(OpenAPI 2/3 등), 수동 API 는 프로토콜(gRPC/SOAP/Webhook 등).
function formatLabel(a: { spec_format: string | null; protocol: string | null }): string {
  if (a.spec_format) return SPEC_FORMAT_LABEL[a.spec_format] ?? a.spec_format
  return protocolLabel(a.protocol) || "-"
}

function ProtocolIcon({ protocol, size = "md" }: { protocol: string | null; size?: "sm" | "md" }) {
  const Icon = PROTOCOL_ICON[(protocol ?? "").toLowerCase()] ?? Webhook
  const label = protocolLabel(protocol) ?? "프로토콜 미지정"
  return (
    <span
      className={cn("flex shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground", size === "sm" ? "h-7 w-7" : "h-8 w-8")}
      title={label}
      aria-label={label}
    >
      <Icon className="h-4 w-4" />
    </span>
  )
}

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", API_STATUS_VARIANTS[status] ?? API_STATUS_VARIANTS.draft, className)}>
      {API_STATUS_LABEL[status] ?? status}
    </span>
  )
}

function QualityBadges({ api: a, className }: { api: ApiSummary; className?: string }) {
  return (
    <>
      {a.certification && a.certification !== "NONE" && (
        <Badge variant="outline" className={cn(className, CERT_BADGE[a.certification])}>{CERT_LABEL[a.certification] ?? a.certification}</Badge>
      )}
      {a.tier && <Badge variant="outline" className={cn(className, TIER_BADGE[a.tier])}>{TIER_LABEL[a.tier] ?? a.tier}</Badge>}
    </>
  )
}

function getPageNumbers(current: number, totalPages: number): (number | "...")[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1)
  if (current <= 4) return [1, 2, 3, 4, 5, "...", totalPages]
  if (current >= totalPages - 3) return [1, "...", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages]
  return [1, "...", current - 1, current, current + 1, "...", totalPages]
}

/** 상세 화면 주소 — 주소에 ?api= 가 있으면 상세가 열린다. */
function detailUrl(name: string): string {
  return `${window.location.origin}${window.location.pathname}?api=${encodeURIComponent(name)}`
}

// what 은 조사까지 포함해 넘긴다(예: "링크를", "URN을")
async function copyText(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success(`${what} 복사했습니다.`)
  } catch { toast.error("클립보드에 복사하지 못했습니다.") }
}

// 행·카드 공용 "…" 메뉴 — 행을 누르면 상세가 열리므로 메뉴에는 그 외 동작만 둔다.
function ApiItemMenu({ api: a }: { api: ApiSummary }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className="h-7 w-7 p-0"
          aria-label={`${a.display_name || a.name} 메뉴`}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
        {/* noopener 를 쓰지 않는다 — 로그인 토큰이 sessionStorage(탭 단위)에 있어, opener 관계로 열어야
            브라우저가 새 탭에 sessionStorage 를 복사해 다시 로그인하지 않는다(같은 사이트 주소라 opener 노출 위험 없음). */}
        <DropdownMenuItem onSelect={() => window.open(detailUrl(a.name), "_blank")}>
          <ExternalLink className="mr-2 h-4 w-4" /> 새 탭에서 열기
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => copyText(detailUrl(a.name), "링크를")}>
          <Link2 className="mr-2 h-4 w-4" /> 링크 복사
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => copyText(a.urn, "URN을")}>
          <Copy className="mr-2 h-4 w-4" /> URN 복사
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

// 행·카드 키보드 열기 — Enter/Space (안쪽 버튼에서 올라온 키는 무시)
function openOnKey(e: React.KeyboardEvent, open: () => void) {
  if (e.target !== e.currentTarget) return
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open() }
}

// 필터 Select — "전체" 를 포함한 단일 선택
function FilterSelect({ value, onChange, allLabel, options, width = "w-[140px]", ariaLabel }: {
  value: string
  onChange: (v: string) => void
  allLabel: string
  options: { value: string; label: string }[]
  width?: string
  ariaLabel: string
}) {
  return (
    <Select value={value || ALL} onValueChange={(v) => onChange(v === ALL ? "" : v)}>
      <SelectTrigger size="sm" className={width} aria-label={ariaLabel}><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{allLabel}</SelectItem>
        {options.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
      </SelectContent>
    </Select>
  )
}

export function ApisTable() {
  const {
    apis: data, total, page, pageSize, isLoading, hasLoaded, error, retry, setPage, setPageSize,
    filters, setFilters, resetFilters, stats, setSelectedApiName, setOpen,
    viewMode: view, setViewMode: setView, listScrollRef,
  } = useApis()
  const canRegister = usePermissions().canUseFeature("apis.register")

  // 검색 입력 — 입력 중에는 로컬 값, 멈추면(디바운스) 또는 Enter 시 조건에 반영.
  // 다른 곳(필터 초기화·지표 카드)에서 검색어가 바뀌면 입력란도 따라간다.
  const [query, setQuery] = useState(filters.search)
  const [syncedSearch, setSyncedSearch] = useState(filters.search)
  if (filters.search !== syncedSearch) {
    setSyncedSearch(filters.search)
    setQuery(filters.search)
  }
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelDebounce = () => { if (debounceRef.current) { clearTimeout(debounceRef.current); debounceRef.current = null } }
  useEffect(() => cancelDebounce, [])

  useEffect(() => {
    const id = requestAnimationFrame(() => window.scrollTo(0, listScrollRef.current))
    const onScroll = () => { listScrollRef.current = window.scrollY }
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => { cancelAnimationFrame(id); window.removeEventListener("scroll", onScroll) }
  }, [listScrollRef])

  function handleSearchChange(value: string) {
    setQuery(value)
    cancelDebounce()
    debounceRef.current = setTimeout(() => { debounceRef.current = null; setFilters({ search: value }) }, SEARCH_DEBOUNCE_MS)
  }
  function applySearchNow() {
    cancelDebounce()
    if (query !== filters.search) setFilters({ search: query })
  }
  function handleReset() {
    cancelDebounce()
    resetFilters()
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const filtered = hasActiveFilters(filters)
  // 최초 로딩만 스켈레톤 — 이후 조회는 기존 목록을 흐리게 유지(깜빡임 방지)
  const initialLoading = !hasLoaded && isLoading && !error
  const refetching = hasLoaded && isLoading

  const categoryOptions = (stats?.by_category ?? []).map((c) => ({ value: c.name, label: `${c.name} (${c.count})` }))
  if (filters.category && !categoryOptions.some((o) => o.value === filters.category)) {
    categoryOptions.push({ value: filters.category, label: filters.category })
  }
  const protocolOptions = (stats?.by_protocol ?? [])
    .filter((p) => p.name && p.name !== "unknown")
    .map((p) => ({ value: p.name.toLowerCase(), label: `${protocolLabel(p.name)} (${p.count})` }))
  if (filters.protocol && !protocolOptions.some((o) => o.value === filters.protocol)) {
    protocolOptions.push({ value: filters.protocol, label: protocolLabel(filters.protocol) ?? filters.protocol })
  }

  // 지표 카드로 거는 조건(소유자 미지정·미확인 변경)은 드롭다운이 없어 칩으로 표시·해제
  const chips: { key: string; label: string; clear: () => void }[] = []
  if (filters.owner === "unowned") chips.push({ key: "owner", label: "소유자 미지정", clear: () => setFilters({ owner: "" }) })
  if (filters.alert) chips.push({ key: "alert", label: "미확인 Breaking 변경", clear: () => setFilters({ alert: false }) })

  const emptyContent = filtered ? (
    <div className="flex flex-col items-center gap-2">
      <span>검색 조건에 맞는 API가 없습니다.</span>
      <Button size="sm" variant="outline" onClick={handleReset}>필터 초기화</Button>
    </div>
  ) : "등록된 API가 없습니다."

  const open = (a: ApiSummary) => setSelectedApiName(a.name)

  return (
    <div className="flex flex-1 flex-col gap-4">
      {/* Toolbar */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex flex-1 flex-wrap items-center gap-2">
          <div className="relative w-full max-w-sm sm:w-72">
            <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="이름·설명·태그·카테고리·소유자 검색"
              aria-label="API 검색"
              title="이름·표시명·설명·카테고리·태그·소유자(이름·username·이메일)에서 찾습니다."
              value={query}
              onChange={(e) => handleSearchChange(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && applySearchNow()}
              className="h-9 pl-8"
            />
          </div>
          <FilterSelect
            ariaLabel="상태"
            value={filters.status}
            onChange={(v) => setFilters({ status: v })}
            allLabel="전체 상태"
            options={STATUS_ORDER.map((s) => ({ value: s, label: API_STATUS_LABEL[s] ?? s }))}
            width="w-[120px]"
          />
          <FilterSelect
            ariaLabel="카테고리"
            value={filters.category}
            onChange={(v) => setFilters({ category: v })}
            allLabel="전체 카테고리"
            options={categoryOptions}
          />
          <FilterSelect
            ariaLabel="프로토콜"
            value={filters.protocol}
            onChange={(v) => setFilters({ protocol: v })}
            allLabel="전체 프로토콜"
            options={protocolOptions}
          />
          <FilterSelect
            ariaLabel="인증 상태"
            value={filters.certification}
            onChange={(v) => setFilters({ certification: v })}
            allLabel="전체 인증 상태"
            options={["CERTIFIED", "IN_REVIEW", "DEPRECATED", "NONE"].map((c) => ({ value: c, label: CERT_LABEL[c] ?? c }))}
          />
          {chips.map((c) => (
            <Badge key={c.key} variant="secondary" className="h-8 gap-1 pl-2.5 pr-1 text-xs">
              {c.label}
              <button type="button" onClick={c.clear} className="rounded p-0.5 hover:bg-muted-foreground/20" aria-label={`${c.label} 조건 해제`}>
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
          {filtered && (
            <Button size="sm" variant="ghost" className="h-8 text-muted-foreground" onClick={handleReset}>
              <X className="mr-1 h-3.5 w-3.5" /> 필터 초기화
            </Button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Select value={filters.sort} onValueChange={(v) => setFilters({ sort: v as ApiSort })}>
            <SelectTrigger size="sm" className="w-[130px]" aria-label="정렬"><SelectValue /></SelectTrigger>
            <SelectContent>
              {(Object.keys(SORT_LABEL) as ApiSort[]).map((s) => <SelectItem key={s} value={s}>{SORT_LABEL[s]}</SelectItem>)}
            </SelectContent>
          </Select>
          <div className="flex items-center rounded-md border p-0.5">
            <Button size="icon" variant={view === "table" ? "secondary" : "ghost"} className="h-7 w-7" onClick={() => setView("table")} title="테이블 보기" aria-label="테이블 보기" aria-pressed={view === "table"}><List className="h-4 w-4" /></Button>
            <Button size="icon" variant={view === "card" ? "secondary" : "ghost"} className="h-7 w-7" onClick={() => setView("card")} title="카드 보기" aria-label="카드 보기" aria-pressed={view === "card"}><LayoutGrid className="h-4 w-4" /></Button>
          </div>
          {canRegister && <Button size="sm" onClick={() => setOpen("add")}><Plus className="mr-1 h-4 w-4" /> API 등록</Button>}
        </div>
      </div>

      {error && <TabError message={`API 목록을 불러오지 못했습니다. ${error}`} onRetry={retry} />}

      {/* 목록 — 재조회 중에는 기존 목록을 흐리게 유지 */}
      <div className={cn("transition-opacity", refetching && "pointer-events-none opacity-60")} aria-busy={isLoading}>
        {view === "table" ? (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>이름</TableHead>
                  <TableHead className="w-24">상태</TableHead>
                  <TableHead className="w-24">버전</TableHead>
                  <TableHead className="w-28">포맷</TableHead>
                  <TableHead className="w-32">카테고리</TableHead>
                  <TableHead className="w-24 text-right">엔드포인트</TableHead>
                  <TableHead>소유자</TableHead>
                  <TableHead className="w-28">수정일</TableHead>
                  <TableHead className="w-10"><span className="sr-only">메뉴</span></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {initialLoading ? (
                  Array.from({ length: Math.min(pageSize, 8) }, (_, i) => (
                    <TableRow key={i}>
                      <TableCell><div className="flex items-center gap-2"><Skeleton className="h-7 w-7" /><div className="space-y-1.5"><Skeleton className="h-4 w-40" /><Skeleton className="h-3 w-24" /></div></div></TableCell>
                      {Array.from({ length: 7 }, (_, j) => <TableCell key={j}><Skeleton className="h-4 w-full max-w-20" /></TableCell>)}
                      <TableCell />
                    </TableRow>
                  ))
                ) : data.length === 0 ? (
                  <TableRow><TableCell colSpan={9} className="h-24 text-center text-muted-foreground">{error ? "목록을 표시할 수 없습니다." : emptyContent}</TableCell></TableRow>
                ) : (
                  data.map((a) => (
                    <TableRow
                      key={a.id}
                      tabIndex={0}
                      aria-label={`${a.display_name || a.name} 상세 보기`}
                      className="cursor-pointer outline-none focus-visible:bg-muted/50 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
                      onClick={() => open(a)}
                      onKeyDown={(e) => openOnKey(e, () => open(a))}
                    >
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <ProtocolIcon protocol={a.protocol} size="sm" />
                          <div className="flex min-w-0 flex-col">
                            <span className="flex flex-wrap items-center gap-1.5">
                              <span className="font-medium">{a.display_name || a.name}</span>
                              <QualityBadges api={a} className="px-1.5 py-0 text-[11px]" />
                            </span>
                            {a.display_name && a.display_name !== a.name && <span className="text-xs text-muted-foreground">{a.name}</span>}
                          </div>
                        </div>
                      </TableCell>
                      <TableCell><StatusBadge status={a.status} className="text-sm" /></TableCell>
                      <TableCell className="text-sm">{a.version}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">{formatLabel(a)}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">{a.category || "-"}</TableCell>
                      <TableCell className="text-right text-sm tabular-nums">{a.endpoint_count}</TableCell>
                      <TableCell className="text-sm text-muted-foreground"><OwnerValue email={a.owner_email} owner={a.owner} /></TableCell>
                      <TableCell className="text-sm text-muted-foreground tabular-nums">{fmtDate(a.updated_at) ?? "-"}</TableCell>
                      <TableCell className="text-right"><ApiItemMenu api={a} /></TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        ) : initialLoading ? (
          // 로딩 — 카드 모양 스켈레톤(로딩 전후 레이아웃 흔들림 최소화)
          <div className={CARD_GRID} aria-label="불러오는 중">
            {Array.from({ length: 6 }, (_, i) => (
              <Card key={i} className="gap-4">
                <CardHeader className="pb-2">
                  <div className="flex items-start gap-2">
                    <Skeleton className="h-8 w-8 shrink-0" />
                    <div className="flex-1 space-y-2">
                      <Skeleton className="h-4 w-2/3" />
                      <Skeleton className="h-3 w-1/2" />
                      <Skeleton className="h-3 w-full" />
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-2 pt-0">
                  <div className="flex gap-1.5">
                    <Skeleton className="h-5 w-12" /><Skeleton className="h-5 w-12" /><Skeleton className="h-5 w-20" />
                  </div>
                  <Skeleton className="h-3 w-3/4" />
                </CardContent>
              </Card>
            ))}
          </div>
        ) : data.length === 0 ? (
          <div className="flex min-h-24 items-center justify-center rounded-md border py-6 text-sm text-muted-foreground">{error ? "목록을 표시할 수 없습니다." : emptyContent}</div>
        ) : (
          // 고정 폭(flex-wrap) 대신 auto-fill grid — 화면 폭을 균등하게 채우고 같은 행 카드 높이를 맞춘다.
          <div className={CARD_GRID}>
            {data.map((a) => (
              // 카드 전체가 상세로 이동. hover 는 배경 대신 테두리·그림자 강조(배경을 칠하면 옅은 ring 테두리와 구분이 사라짐).
              <Card
                key={a.id}
                role="button"
                tabIndex={0}
                aria-label={`${a.display_name || a.name} 상세 보기`}
                onClick={() => open(a)}
                onKeyDown={(e) => openOnKey(e, () => open(a))}
                className="flex max-h-[300px] cursor-pointer flex-col overflow-hidden outline-none transition-shadow hover:shadow-md hover:ring-primary/40 focus-visible:ring-2 focus-visible:ring-primary"
              >
                <CardHeader className="pb-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex min-w-0 items-start gap-2">
                      <ProtocolIcon protocol={a.protocol} />
                      <div className="min-w-0">
                        <CardTitle className="truncate text-sm group-hover/card:underline" title={a.display_name || a.name}>
                          {a.display_name || a.name}
                        </CardTitle>
                        {a.display_name && a.display_name !== a.name && (
                          <p className="mt-0.5 truncate text-sm text-muted-foreground" title={a.name}>{a.name}</p>
                        )}
                        {a.description && <p className="mt-0.5 line-clamp-4 text-sm text-muted-foreground break-words">{a.description}</p>}
                      </div>
                    </div>
                    <ApiItemMenu api={a} />
                  </div>
                </CardHeader>
                <CardContent className="mt-auto space-y-2 pt-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <StatusBadge status={a.status} className="text-sm" />
                    <Badge variant="outline" className="text-sm">{a.version}</Badge>
                    <Badge variant="outline" className="text-sm">{formatLabel(a)}</Badge>
                    <Badge variant="secondary" className="text-sm">엔드포인트 {a.endpoint_count}</Badge>
                    <QualityBadges api={a} className="text-sm" />
                  </div>
                  {(() => {
                    // 하단 메타 — 카테고리 · 소유자 · 수정일 (값 있는 항목만)
                    const updated = fmtDate(a.updated_at)
                    const items: ReactNode[] = []
                    if (a.category) items.push(<span key="category">{a.category}</span>)
                    if (a.owner_email) items.push(<OwnerValue key="owner" email={a.owner_email} owner={a.owner} />)
                    if (updated) items.push(<span key="updated">수정 {updated}</span>)
                    if (items.length === 0) return null
                    return (
                      <p className="truncate text-xs text-muted-foreground">
                        {items.flatMap((el, i) => (i === 0 ? [el] : [<span key={`sep-${i}`} aria-hidden="true"> · </span>, el]))}
                      </p>
                    )
                  })()}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      {/* Pagination */}
      <div className="flex flex-col-reverse items-center justify-between gap-4 overflow-clip px-2 sm:flex-row">
        <div className="flex items-center gap-2">
          <Select value={`${pageSize}`} onValueChange={(v) => setPageSize(Number(v))}>
            <SelectTrigger className="h-8 w-[70px]" aria-label="페이지당 항목 수"><SelectValue placeholder={pageSize} /></SelectTrigger>
            <SelectContent side="top">{PAGE_SIZE_OPTIONS.map((s) => <SelectItem key={s} value={`${s}`}>{s}</SelectItem>)}</SelectContent>
          </Select>
          <p className="hidden text-sm font-medium sm:block">{view === "card" ? "페이지당 항목 수" : "페이지당 행 수"}</p>
          <span className="text-sm text-muted-foreground">· 총 {total}개</span>
        </div>
        <div className="flex items-center space-x-2">
          <div className="flex w-[100px] items-center justify-center text-sm font-medium">{page} / {totalPages} 페이지</div>
          <Button variant="outline" className="size-8 p-0" onClick={() => setPage(1)} disabled={page <= 1} aria-label="첫 페이지"><ChevronsLeft className="h-4 w-4" /></Button>
          <Button variant="outline" className="size-8 p-0" onClick={() => setPage(page - 1)} disabled={page <= 1} aria-label="이전 페이지"><ChevronLeft className="h-4 w-4" /></Button>
          {getPageNumbers(page, totalPages).map((n, i) => (
            <div key={`${n}-${i}`} className="flex items-center">
              {n === "..." ? <span className="px-1 text-sm text-muted-foreground">...</span> : (
                <Button variant={page === n ? "default" : "outline"} className="h-8 min-w-8 px-2" onClick={() => setPage(n as number)} aria-current={page === n ? "page" : undefined}>{n}</Button>
              )}
            </div>
          ))}
          <Button variant="outline" className="size-8 p-0" onClick={() => setPage(page + 1)} disabled={page >= totalPages} aria-label="다음 페이지"><ChevronRight className="h-4 w-4" /></Button>
          <Button variant="outline" className="size-8 p-0" onClick={() => setPage(totalPages)} disabled={page >= totalPages} aria-label="마지막 페이지"><ChevronsRight className="h-4 w-4" /></Button>
        </div>
      </div>
    </div>
  )
}
