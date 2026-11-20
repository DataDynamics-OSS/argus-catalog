"use client"

import { Tip } from "@/components/tip"
import { useEffect, useRef, useState } from "react"
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  FileCode2,
  Globe,
  LayoutGrid,
  List,
  MoreHorizontal,
  Plus,
  Radio,
  Share2,
  Webhook,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import { Skeleton } from "@workspace/ui/components/skeleton"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@workspace/ui/components/table"
import { StatusBadge, type StatusTone } from "@workspace/ui/components/status-badge"
import { cn } from "@workspace/ui/lib/utils"

import { type ApiSort, type ApiSummary } from "../api"
import { OwnerValue } from "./api-owner"
import {
  API_STATUS_LABEL, apiStatus, CERT_LABEL, SPEC_FORMAT_LABEL, STATUS_ORDER, TIER_LABEL, protocolLabel,
} from "./apis-constants"
import { TabError } from "./apis-detail-shared"
import { LinkMenuItems } from "@/components/link-menu-items"
import { EntityCard, EntityCardGrid, EntityCardRaised, EntityCardText } from "@/components/entity-card"
import { useApis } from "./apis-provider"
import { PAGE_SIZE_OPTIONS, hasActiveFilters } from "./apis-url-state"
import { usePermissions } from "@/features/permissions/use-permissions"
import { SearchInput } from "@/components/search-input"

// 프로토콜 아이콘 — 표·카드에서 프로토콜을 한눈에 구분(미지정/기타는 Webhook).
const PROTOCOL_ICON: Record<string, LucideIcon> = {
  rest: Globe, graphql: Share2, grpc: Zap, soap: FileCode2, webhook: Webhook, asyncapi: Radio,
}

// 인증 상태·등급 배지 색상 (라벨은 apis-constants)
/** 인증 상태 톤(TODO-109) — 인증됨 성공 · 검토 중 진행 · 사용 중단 주의 */
const CERT_TONE: Record<string, StatusTone> = {
  CERTIFIED: "success",
  IN_REVIEW: "info",
  DEPRECATED: "warning",
}
/** 등급은 메달 색 자체가 뜻이라 팔레트 색 그대로 */
const TIER_BADGE: Record<string, string> = {
  GOLD: "border-yellow-500/50 bg-yellow-50 text-yellow-800",
  SILVER: "border-slate-400/50 bg-slate-50 text-slate-700",
  BRONZE: "border-orange-400/50 bg-orange-50 text-orange-800",
}

const SORT_LABEL: Record<ApiSort, string> = { updated: "최근 수정순", created: "최근 등록순", name: "이름순" }
const ALL = "__all__"  // Select 의 "전체" 값(빈 문자열은 Radix Select 값으로 쓸 수 없음)
const SEARCH_DEBOUNCE_MS = 400

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

export function ApiStatusBadge({ status, className }: { status: string; className?: string }) {
  return <StatusBadge {...apiStatus(status)} className={className} />
}

function QualityBadges({ api: a, size }: { api: ApiSummary; size?: "default" | "sm" }) {
  return (
    <>
      {a.certification && a.certification !== "NONE" && (
        <StatusBadge tone={CERT_TONE[a.certification] ?? "neutral"} size={size} label={CERT_LABEL[a.certification] ?? a.certification} />
      )}
      {a.tier && <Badge variant="outline" size={size} className={TIER_BADGE[a.tier]}>{TIER_LABEL[a.tier] ?? a.tier}</Badge>}
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


// 행·카드 공용 "…" 메뉴 — 행을 누르면 상세가 열리므로 메뉴에는 그 외 동작만 둔다.
function ApiItemMenu({ api: a }: { api: ApiSummary }) {
  return (
    <DropdownMenu>
      <Tip label={`${a.display_name || a.name} 메뉴`}><DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className="h-7 w-7 p-0"
          aria-label={`${a.display_name || a.name} 메뉴`}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger></Tip>
      <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
        <LinkMenuItems href={`${window.location.pathname}?api=${encodeURIComponent(a.name)}`} urn={a.urn} />
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/**
 * API 카드(TODO-116) — 데이터셋 · AI 에이전트 · MCP 카드와 같은 틀(`EntityCard`).
 * 배지 줄: 상태(게시가 아닐 때) · 인증 · 등급, 그 옆 포맷 · 버전 글자. 설명 두 줄, 기본 주소, 바닥 줄에 엔드포인트 수 · 소유자 · 수정일.
 */
export function ApiCard({ api: a, onOpen }: { api: ApiSummary; onOpen: () => void }) {
  const named = a.display_name && a.display_name !== a.name ? a.display_name : null
  const title = named || a.name
  const crumb = [a.department, a.category].filter(Boolean).join(" › ")
  const meta = [formatLabel(a), a.version && `v${a.version.replace(/^v/i, "")}`].filter((v) => v && v !== "-").join(" · ")
  // base_url_overridden 은 주소가 아니라 "직접 입력했는지" 표시("true" / "false")다(DEF-186).
  const baseUrl = a.base_url
  const updated = fmtDate(a.updated_at)
  const Icon = PROTOCOL_ICON[(a.protocol ?? "").toLowerCase()] ?? Webhook
  return (
    <EntityCard
      crumb={crumb || null}
      title={title}
      prominent={!!named}
      identifier={named ? a.name : null}
      onOpen={onOpen}
      menuLabel={`${title} 메뉴`}
      menu={<LinkMenuItems href={`${window.location.pathname}?api=${encodeURIComponent(a.name)}`} urn={a.urn} />}
      footer={
        <>
          <span className="flex min-w-0 items-center gap-1">
            <span className="shrink-0 tabular-nums">엔드포인트 {a.endpoint_count}</span>
            {a.owner_email && (
              <>
                <span aria-hidden="true">·</span>
                <EntityCardRaised className="min-w-0 truncate"><OwnerValue email={a.owner_email} owner={a.owner} /></EntityCardRaised>
              </>
            )}
          </span>
          {updated && <span className="ml-auto shrink-0 tabular-nums">수정 {updated}</span>}
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        {a.status !== "published" && <ApiStatusBadge status={a.status} />}
        <QualityBadges api={a} size="sm" />
        {meta && <span>{meta}</span>}
      </div>

      <EntityCardText text={a.description} />

      {/* 기본 주소 — 프로토콜 아이콘과 함께 */}
      <div className="flex min-w-0 items-center gap-1.5 text-xs" title={protocolLabel(a.protocol) ?? "프로토콜 미지정"}>
        <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-label={protocolLabel(a.protocol) ?? "프로토콜 미지정"} />
        {baseUrl ? <span className="truncate font-mono">{baseUrl}</span> : <span >주소 미지정</span>}
      </div>
    </EntityCard>
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
          <SearchInput
            className="w-full max-w-sm sm:w-72"
            placeholder="이름·설명·태그·카테고리·소유자 검색…"
            aria-label="API 검색"
            title="이름·표시명·설명·카테고리·태그·소유자(이름·username·이메일)에서 찾습니다."
            value={query}
            onValueChange={handleSearchChange}
            onSearch={applySearchNow}
          />
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
            <Badge key={c.key} variant="secondary" className="h-8 gap-1 pl-2.5 pr-1">
              {c.label}
              <Tip label={`${c.label} 조건 해제`}><button type="button" onClick={c.clear} className="rounded p-0.5 hover:bg-muted-foreground/20" aria-label={`${c.label} 조건 해제`}>
                <X className="h-3 w-3" />
              </button></Tip>
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
            <Tip label="테이블 보기"><Button size="icon" variant={view === "table" ? "secondary" : "ghost"} className="h-7 w-7" onClick={() => setView("table")} aria-label="테이블 보기" aria-pressed={view === "table"}><List className="h-4 w-4" /></Button></Tip>
            <Tip label="카드 보기"><Button size="icon" variant={view === "card" ? "secondary" : "ghost"} className="h-7 w-7" onClick={() => setView("card")} aria-label="카드 보기" aria-pressed={view === "card"}><LayoutGrid className="h-4 w-4" /></Button></Tip>
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
                              <QualityBadges api={a} size="sm" />
                            </span>
                            {a.display_name && a.display_name !== a.name && <span className="text-xs">{a.name}</span>}
                          </div>
                        </div>
                      </TableCell>
                      <TableCell><ApiStatusBadge status={a.status} /></TableCell>
                      <TableCell className="text-sm">{a.version}</TableCell>
                      <TableCell className="text-sm">{formatLabel(a)}</TableCell>
                      <TableCell className="text-sm">{a.category || "-"}</TableCell>
                      <TableCell className="text-right text-sm tabular-nums">{a.endpoint_count}</TableCell>
                      <TableCell className="text-sm"><OwnerValue email={a.owner_email} owner={a.owner} /></TableCell>
                      <TableCell className="text-sm tabular-nums">{fmtDate(a.updated_at) ?? "-"}</TableCell>
                      <TableCell className="text-right"><ApiItemMenu api={a} /></TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        ) : initialLoading ? (
          // 로딩 — 카드 모양 스켈레톤(로딩 전후 레이아웃 흔들림 최소화)
          <div aria-label="불러오는 중">
            <EntityCardGrid>
              {Array.from({ length: 6 }, (_, i) => (
                <div key={i} className="flex h-full flex-col gap-3 rounded-xl border bg-card p-4">
                  <Skeleton className="h-3 w-1/3" />
                  <Skeleton className="h-5 w-2/3" />
                  <div className="flex gap-1.5"><Skeleton className="h-5 w-12" /><Skeleton className="h-5 w-20" /></div>
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="mt-auto h-4 w-3/4" />
                </div>
              ))}
            </EntityCardGrid>
          </div>
        ) : data.length === 0 ? (
          <div className="flex min-h-24 items-center justify-center rounded-md border py-6 text-sm text-muted-foreground">{error ? "목록을 표시할 수 없습니다." : emptyContent}</div>
        ) : (
          <EntityCardGrid>
            {data.map((a) => <ApiCard key={a.id} api={a} onOpen={() => open(a)} />)}
          </EntityCardGrid>
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
          <span className="text-sm">· 총 {total}개</span>
        </div>
        <div className="flex items-center space-x-2">
          <div className="flex w-[100px] items-center justify-center text-sm font-medium">{page} / {totalPages} 페이지</div>
          <Tip label="첫 페이지"><Button variant="outline" className="size-8 p-0" onClick={() => setPage(1)} disabled={page <= 1} aria-label="첫 페이지"><ChevronsLeft className="h-4 w-4" /></Button></Tip>
          <Tip label="이전 페이지"><Button variant="outline" className="size-8 p-0" onClick={() => setPage(page - 1)} disabled={page <= 1} aria-label="이전 페이지"><ChevronLeft className="h-4 w-4" /></Button></Tip>
          {getPageNumbers(page, totalPages).map((n, i) => (
            <div key={`${n}-${i}`} className="flex items-center">
              {n === "..." ? <span className="px-1 text-sm">...</span> : (
                <Button variant={page === n ? "default" : "outline"} className="h-8 min-w-8 px-2" onClick={() => setPage(n as number)} aria-current={page === n ? "page" : undefined}>{n}</Button>
              )}
            </div>
          ))}
          <Tip label="다음 페이지"><Button variant="outline" className="size-8 p-0" onClick={() => setPage(page + 1)} disabled={page >= totalPages} aria-label="다음 페이지"><ChevronRight className="h-4 w-4" /></Button></Tip>
          <Tip label="마지막 페이지"><Button variant="outline" className="size-8 p-0" onClick={() => setPage(totalPages)} disabled={page >= totalPages} aria-label="마지막 페이지"><ChevronsRight className="h-4 w-4" /></Button></Tip>
        </div>
      </div>
    </div>
  )
}
