"use client"

import { useCallback, useEffect, useRef } from "react"
import {
  ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight,
  LayoutGrid, List, MoreHorizontal, Plus, Search, Webhook,
} from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import { Input } from "@workspace/ui/components/input"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@workspace/ui/components/table"
import { cn } from "@workspace/ui/lib/utils"

import { type ApiSummary } from "../api"
import { useApis } from "./apis-provider"

const STATUS_OPTIONS = ["draft", "published", "deprecated", "retired"]
export const API_STATUS_LABEL: Record<string, string> = {
  draft: "초안", published: "게시", deprecated: "사용 중단", retired: "폐기",
}
export const API_STATUS_VARIANTS: Record<string, string> = {
  draft: "bg-muted text-foreground",
  published: "bg-emerald-600 text-white",
  deprecated: "bg-amber-500 text-white",
  retired: "bg-zinc-500 text-white",
}

export const SPEC_FORMAT_LABEL: Record<string, string> = {
  openapi: "OpenAPI", openapi2: "OpenAPI 2", openapi3: "OpenAPI 3", asyncapi: "AsyncAPI",
}

// 프로토콜 표시 라벨(저장값 정규화) — 예: graphql→GraphQL, grpc→gRPC
export const PROTOCOL_LABEL: Record<string, string> = { rest: "REST", graphql: "GraphQL", grpc: "gRPC", soap: "SOAP", webhook: "Webhook", asyncapi: "AsyncAPI" }
export const protocolLabel = (p?: string | null) => (p ? (PROTOCOL_LABEL[p.toLowerCase()] ?? p) : null)

// 포맷 표시 — 스펙 등록 API 는 스펙 포맷(OpenAPI 2/3 등), 수동 API 는 프로토콜(gRPC/SOAP/Webhook 등).
function formatLabel(a: { spec_format: string | null; protocol: string | null }): string {
  if (a.spec_format) return SPEC_FORMAT_LABEL[a.spec_format] ?? a.spec_format
  return protocolLabel(a.protocol) || "-"
}

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", API_STATUS_VARIANTS[status] ?? API_STATUS_VARIANTS.draft, className)}>
      {API_STATUS_LABEL[status] ?? status}
    </span>
  )
}

function getPageNumbers(current: number, totalPages: number): (number | "...")[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1)
  if (current <= 4) return [1, 2, 3, 4, 5, "...", totalPages]
  if (current >= totalPages - 3) return [1, "...", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages]
  return [1, "...", current - 1, current, current + 1, "...", totalPages]
}

export function ApisTable({ data, isLoading }: { data: ApiSummary[]; isLoading?: boolean }) {
  const {
    total, page, pageSize, setPage, setPageSize, searchApis, setSelectedApiName, setOpen,
    viewMode: view, setViewMode: setView, searchQuery, setSearchQuery, statusFilter, setStatusFilter,
    listScrollRef,
  } = useApis()
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const id = requestAnimationFrame(() => window.scrollTo(0, listScrollRef.current))
    const onScroll = () => { listScrollRef.current = window.scrollY }
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => { cancelAnimationFrame(id); window.removeEventListener("scroll", onScroll) }
  }, [listScrollRef])

  const runSearch = useCallback(
    (overrides?: Partial<{ search: string; status: string }>) => {
      searchApis({
        search: overrides?.search ?? searchQuery,
        status: (overrides?.status ?? statusFilter) === "all" ? "" : (overrides?.status ?? statusFilter),
        category: "",
      })
    },
    [searchQuery, statusFilter, searchApis],
  )

  function handleSearchChange(value: string) {
    setSearchQuery(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => runSearch({ search: value }), 400)
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <div className="flex flex-1 flex-col gap-4">
      {/* Toolbar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-1 flex-wrap items-center gap-2">
          <div className="relative max-w-xs flex-1">
            <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="API 검색..."
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && runSearch()}
              className="h-9 pl-8"
            />
          </div>
          <Select value={statusFilter} onValueChange={(v) => { setStatusFilter(v); runSearch({ status: v }) }}>
            <SelectTrigger size="sm" className="w-[150px]"><SelectValue placeholder="상태" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">전체 상태</SelectItem>
              {STATUS_OPTIONS.map((s) => <SelectItem key={s} value={s}>{API_STATUS_LABEL[s]}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center rounded-md border p-0.5">
            <Button size="icon" variant={view === "table" ? "secondary" : "ghost"} className="h-7 w-7" onClick={() => setView("table")} title="테이블 보기"><List className="h-4 w-4" /></Button>
            <Button size="icon" variant={view === "card" ? "secondary" : "ghost"} className="h-7 w-7" onClick={() => setView("card")} title="카드 보기"><LayoutGrid className="h-4 w-4" /></Button>
          </div>
          <Button size="sm" onClick={() => setOpen("add")}><Plus className="mr-1 h-4 w-4" /> API 등록</Button>
        </div>
      </div>

      {/* 목록 */}
      {view === "table" ? (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>이름</TableHead>
                <TableHead className="w-24">상태</TableHead>
                <TableHead className="w-24">버전</TableHead>
                <TableHead className="w-28">포맷</TableHead>
                <TableHead className="w-20 text-right">엔드포인트</TableHead>
                <TableHead>소유자</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow><TableCell colSpan={6} className="h-24 text-center text-muted-foreground">불러오는 중...</TableCell></TableRow>
              ) : data.length === 0 ? (
                <TableRow><TableCell colSpan={6} className="h-24 text-center text-muted-foreground">등록된 API가 없습니다.</TableCell></TableRow>
              ) : (
                data.map((a) => (
                  <TableRow key={a.id} className="cursor-pointer" onClick={() => setSelectedApiName(a.name)}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Webhook className="h-4 w-4 shrink-0 text-muted-foreground" />
                        <div className="flex flex-col">
                          <span className="font-medium">{a.display_name || a.name}</span>
                          <span className="text-xs text-muted-foreground">{a.name}</span>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell><StatusBadge status={a.status} className="text-sm" /></TableCell>
                    <TableCell className="text-sm">{a.version}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{formatLabel(a)}</TableCell>
                    <TableCell className="text-right text-sm tabular-nums">{a.endpoint_count}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{a.owner_email || "-"}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      ) : isLoading ? (
        <div className="flex h-24 items-center justify-center rounded-md border text-sm text-muted-foreground">불러오는 중...</div>
      ) : data.length === 0 ? (
        <div className="flex h-24 items-center justify-center rounded-md border text-sm text-muted-foreground">등록된 API가 없습니다.</div>
      ) : (
        <div className="flex flex-wrap gap-3">
          {data.map((a) => (
            <Card key={a.id} className="flex max-h-[300px] w-[350px] shrink-0 flex-col overflow-hidden transition-colors hover:bg-muted/40">
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <CardTitle className="truncate text-sm">
                      <button type="button" onClick={() => setSelectedApiName(a.name)} className="text-left hover:underline">
                        {a.display_name || a.name}
                      </button>
                    </CardTitle>
                    <p className="mt-0.5 truncate text-sm text-muted-foreground" title={a.name}>{a.name}</p>
                    {a.description && <p className="mt-0.5 line-clamp-4 text-sm text-muted-foreground break-words">{a.description}</p>}
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" className="h-7 w-7 p-0" aria-label="액션"><MoreHorizontal className="h-4 w-4" /></Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => setSelectedApiName(a.name)}>상세 보기</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </CardHeader>
              <CardContent className="space-y-2 pt-0">
                <div className="flex flex-wrap items-center gap-1.5">
                  <StatusBadge status={a.status} className="text-sm" />
                  <Badge variant="outline" className="text-sm">{a.version}</Badge>
                  <Badge variant="outline" className="text-sm">{formatLabel(a)}</Badge>
                  <Badge variant="secondary" className="text-sm">엔드포인트 {a.endpoint_count}</Badge>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Pagination */}
      <div className="flex flex-col-reverse items-center justify-between gap-4 overflow-clip px-2 sm:flex-row">
        <div className="flex items-center gap-2">
          <Select value={`${pageSize}`} onValueChange={(v) => setPageSize(Number(v))}>
            <SelectTrigger className="h-8 w-[70px]"><SelectValue placeholder={pageSize} /></SelectTrigger>
            <SelectContent side="top">{[10, 20, 30, 40, 50].map((s) => <SelectItem key={s} value={`${s}`}>{s}</SelectItem>)}</SelectContent>
          </Select>
          <p className="hidden text-sm font-medium sm:block">페이지당 행 수</p>
          <span className="text-sm text-muted-foreground">· 총 {total}개</span>
        </div>
        <div className="flex items-center space-x-2">
          <div className="flex w-[100px] items-center justify-center text-sm font-medium">{page} / {totalPages} 페이지</div>
          <Button variant="outline" className="size-8 p-0" onClick={() => setPage(1)} disabled={page <= 1}><ChevronsLeft className="h-4 w-4" /></Button>
          <Button variant="outline" className="size-8 p-0" onClick={() => setPage(page - 1)} disabled={page <= 1}><ChevronLeft className="h-4 w-4" /></Button>
          {getPageNumbers(page, totalPages).map((n, i) => (
            <div key={`${n}-${i}`} className="flex items-center">
              {n === "..." ? <span className="px-1 text-sm text-muted-foreground">...</span> : (
                <Button variant={page === n ? "default" : "outline"} className="h-8 min-w-8 px-2" onClick={() => setPage(n as number)}>{n}</Button>
              )}
            </div>
          ))}
          <Button variant="outline" className="size-8 p-0" onClick={() => setPage(page + 1)} disabled={page >= totalPages}><ChevronRight className="h-4 w-4" /></Button>
          <Button variant="outline" className="size-8 p-0" onClick={() => setPage(totalPages)} disabled={page >= totalPages}><ChevronsRight className="h-4 w-4" /></Button>
        </div>
      </div>
    </div>
  )
}
