"use client"

import { useCallback, useEffect, useRef } from "react"
import {
  Bot,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Cpu,
  LayoutGrid,
  List,
  MoreHorizontal,
  Plus,
  Search,
} from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import { Input } from "@workspace/ui/components/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { cn } from "@workspace/ui/lib/utils"

import { AGENT_STATUS_VARIANTS, type AIAgentSummary } from "../data/schema"
import { useAIAgents } from "./ai-agents-provider"

type AIAgentsTableProps = {
  data: AIAgentSummary[]
  isLoading?: boolean
}

const STATUS_OPTIONS = ["draft", "staging", "active", "blocked", "deprecated", "retired"]
const STATUS_LABEL: Record<string, string> = {
  draft: "초안", staging: "스테이징", active: "활성",
  blocked: "차단", deprecated: "사용 중단", retired: "폐기",
}

// 데이터셋 목록 페이지네이션과 동일한 페이지 번호 생성(말줄임 포함)
function getPageNumbers(currentPage: number, totalPages: number): (number | "...")[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1)
  if (currentPage <= 4) return [1, 2, 3, 4, 5, "...", totalPages]
  if (currentPage >= totalPages - 3)
    return [1, "...", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages]
  return [1, "...", currentPage - 1, currentPage, currentPage + 1, "...", totalPages]
}

function StatusBadge({ status, className }: { status: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        AGENT_STATUS_VARIANTS[status] ?? AGENT_STATUS_VARIANTS.draft,
        className,
      )}
    >
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

export function AIAgentsTable({ data, isLoading }: AIAgentsTableProps) {
  const {
    total,
    page,
    pageSize,
    setPage,
    setPageSize,
    searchAgents,
    setSelectedAgentName,
    setOpen,
    // 상세 복귀 시에도 유지되도록 provider 에서 관리
    viewMode: view,
    setViewMode: setView,
    searchQuery,
    setSearchQuery,
    statusFilter,
    setStatusFilter,
    listScrollRef,
  } = useAIAgents()

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 상세 복귀 시 스크롤 위치 복원 + 목록에 있는 동안 위치 추적.
  useEffect(() => {
    const id = requestAnimationFrame(() => window.scrollTo(0, listScrollRef.current))
    const onScroll = () => {
      listScrollRef.current = window.scrollY
    }
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => {
      cancelAnimationFrame(id)
      window.removeEventListener("scroll", onScroll)
    }
  }, [listScrollRef])

  const runSearch = useCallback(
    (overrides?: Partial<{ search: string; status: string }>) => {
      searchAgents({
        search: overrides?.search ?? searchQuery,
        status:
          (overrides?.status ?? statusFilter) === "all"
            ? ""
            : (overrides?.status ?? statusFilter),
        framework: "",
        category: "",
      })
    },
    [searchQuery, statusFilter, searchAgents],
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
              placeholder="AI Agent 검색..."
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && runSearch()}
              className="h-9 pl-8"
            />
          </div>
          <Select
            value={statusFilter}
            onValueChange={(v) => {
              setStatusFilter(v)
              runSearch({ status: v })
            }}
          >
            <SelectTrigger size="sm" className="w-[150px]">
              <SelectValue placeholder="상태" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">전체 상태</SelectItem>
              {STATUS_OPTIONS.map((s) => (
                <SelectItem key={s} value={s}>
                  {STATUS_LABEL[s] ?? s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          {/* 뷰 토글 — 테이블 / 카드 */}
          <div className="flex items-center rounded-md border p-0.5">
            <Button
              size="icon"
              variant={view === "table" ? "secondary" : "ghost"}
              className="h-7 w-7"
              onClick={() => setView("table")}
              title="테이블 보기"
              aria-label="테이블 보기"
            >
              <List className="h-4 w-4" />
            </Button>
            <Button
              size="icon"
              variant={view === "card" ? "secondary" : "ghost"}
              className="h-7 w-7"
              onClick={() => setView("card")}
              title="카드 보기"
              aria-label="카드 보기"
            >
              <LayoutGrid className="h-4 w-4" />
            </Button>
          </div>
          <Button size="sm" onClick={() => setOpen("add")}>
            <Plus className="mr-1 h-4 w-4" />
            에이전트 등록
          </Button>
        </div>
      </div>

      {/* 목록 — 테이블 / 카드 */}
      {view === "table" ? (
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>이름</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>버전</TableHead>
              <TableHead>프레임워크</TableHead>
              <TableHead>기저 모델</TableHead>
              <TableHead>카테고리</TableHead>
              <TableHead>소유자</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={7} className="h-24 text-center text-muted-foreground">
                  불러오는 중...
                </TableCell>
              </TableRow>
            ) : data.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="h-24 text-center text-muted-foreground">
                  등록된 AI Agent가 없습니다.
                </TableCell>
              </TableRow>
            ) : (
              data.map((agent) => (
                <TableRow
                  key={agent.id}
                  className="cursor-pointer"
                  onClick={() => setSelectedAgentName(agent.name)}
                >
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <Bot className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <div className="flex flex-col">
                        <span className="font-medium">
                          {agent.display_name || agent.name}
                        </span>
                        <span className="text-xs text-muted-foreground">{agent.name}</span>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={agent.status} className="text-sm" />
                  </TableCell>
                  <TableCell className="text-sm">{agent.version}</TableCell>
                  <TableCell>
                    {agent.framework ? (
                      <Badge variant="outline" className="text-sm">{agent.framework}</Badge>
                    ) : (
                      <span className="text-sm text-muted-foreground">-</span>
                    )}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {agent.base_model || "-"}
                  </TableCell>
                  <TableCell className="text-sm">{agent.category || "-"}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {agent.owner_email || "-"}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      ) : isLoading ? (
        <div className="flex h-24 items-center justify-center rounded-md border text-sm text-muted-foreground">
          불러오는 중...
        </div>
      ) : data.length === 0 ? (
        <div className="flex h-24 items-center justify-center rounded-md border text-sm text-muted-foreground">
          등록된 AI Agent가 없습니다.
        </div>
      ) : (
        <div className="flex flex-wrap gap-3">
          {data.map((agent) => (
            <Card key={agent.id} className="w-[350px] shrink-0 transition-colors hover:bg-muted/40">
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <CardTitle className="truncate text-sm">
                      <button
                        type="button"
                        onClick={() => setSelectedAgentName(agent.name)}
                        className="text-left hover:underline"
                      >
                        {agent.display_name || agent.name}
                      </button>
                    </CardTitle>
                    {agent.display_name && (
                      <p className="mt-0.5 truncate text-[11px] text-muted-foreground" title={agent.name}>
                        {agent.name}
                      </p>
                    )}
                    {agent.description && (
                      <p className="mt-0.5 text-xs text-muted-foreground break-words">
                        {agent.description}
                      </p>
                    )}
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" className="h-7 w-7 p-0" aria-label="액션">
                        <MoreHorizontal className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => setSelectedAgentName(agent.name)}>
                        상세 보기
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </CardHeader>
              <CardContent className="space-y-2 pt-0">
                {/* 상태 + 버전 + 프레임워크 + 카테고리 */}
                <div className="flex flex-wrap items-center gap-1.5">
                  <StatusBadge status={agent.status} />
                  <Badge variant="outline" className="text-[10px]">{agent.version}</Badge>
                  {agent.framework && <Badge variant="outline" className="text-[10px]">{agent.framework}</Badge>}
                  {agent.category && <Badge variant="secondary" className="text-[10px]">{agent.category}</Badge>}
                </div>
                {/* 기저 모델 */}
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Cpu className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{agent.base_model || "-"}</span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Pagination — 데이터셋 목록과 동일한 형식 */}
      <div className="flex flex-col-reverse items-center justify-between gap-4 overflow-clip px-2 sm:flex-row">
        <div className="flex items-center gap-2">
          <Select value={`${pageSize}`} onValueChange={(v) => setPageSize(Number(v))}>
            <SelectTrigger className="h-8 w-[70px]">
              <SelectValue placeholder={pageSize} />
            </SelectTrigger>
            <SelectContent side="top">
              {[10, 20, 30, 40, 50].map((s) => (
                <SelectItem key={s} value={`${s}`}>{s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="hidden text-sm font-medium sm:block">페이지당 행 수</p>
          <span className="text-sm text-muted-foreground">· 총 {total}개</span>
        </div>

        <div className="flex items-center space-x-2">
          <div className="flex w-[100px] items-center justify-center text-sm font-medium">
            {page} / {totalPages} 페이지
          </div>
          <Button variant="outline" className="size-8 p-0" onClick={() => setPage(1)} disabled={page <= 1}>
            <span className="sr-only">첫 페이지로</span>
            <ChevronsLeft className="h-4 w-4" />
          </Button>
          <Button variant="outline" className="size-8 p-0" onClick={() => setPage(page - 1)} disabled={page <= 1}>
            <span className="sr-only">이전 페이지</span>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          {getPageNumbers(page, totalPages).map((n, i) => (
            <div key={`${n}-${i}`} className="flex items-center">
              {n === "..." ? (
                <span className="px-1 text-sm text-muted-foreground">...</span>
              ) : (
                <Button
                  variant={page === n ? "default" : "outline"}
                  className="h-8 min-w-8 px-2"
                  onClick={() => setPage(n as number)}
                >
                  {n}
                </Button>
              )}
            </div>
          ))}
          <Button variant="outline" className="size-8 p-0" onClick={() => setPage(page + 1)} disabled={page >= totalPages}>
            <span className="sr-only">다음 페이지</span>
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button variant="outline" className="size-8 p-0" onClick={() => setPage(totalPages)} disabled={page >= totalPages}>
            <span className="sr-only">마지막 페이지로</span>
            <ChevronsRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
