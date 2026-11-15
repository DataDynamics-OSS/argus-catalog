"use client"

import { DisabledReason, InfoTip, Tip } from "@/components/tip"
import { useCallback, useEffect, useRef } from "react"
import {
  Bot,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Cpu,
  Import,
  LayoutGrid,
  List,
  ListChecks,
  Plus,
  UserX,
} from "lucide-react"

import { LinkMenuItems } from "@/components/link-menu-items"

import { Badge } from "@workspace/ui/components/badge"
import { StatusBadge } from "@workspace/ui/components/status-badge"
import { RiskBadge } from "@/components/risk-badge"
import { LIFECYCLE_STATUS } from "@/lib/lifecycle"
import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
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
import { Tabs, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { cn } from "@workspace/ui/lib/utils"

import {
  AGENT_STATUS_LABEL,
  MANAGED_BY_LABEL,
  OWNER_STATE_HINT,
  OWNER_STATE_LABEL,
  type AIAgentSummary,
} from "../data/schema"
import { AIAgentsDiscoveries } from "./ai-agents-discoveries"
import { AIAgentsFilters } from "./ai-agents-filters"
import { useAIAgents } from "./ai-agents-provider"
import { AIAgentsStats } from "./ai-agents-stats"
import { RiskTierBadge } from "./ai-agents-skills-data"
import { useAuth } from "@/features/auth"
import { usePermissions } from "@/features/permissions/use-permissions"
import { OwnerValue } from "@/features/apis/components/api-owner"
import { EntityCard, EntityCardGrid, EntityCardRaised, EntityCardText } from "@/components/entity-card"
import { SearchInput } from "@/components/search-input"

type AIAgentsTableProps = {
  data: AIAgentSummary[]
  isLoading?: boolean
}

const STATUS_LABEL = AGENT_STATUS_LABEL

/** 정보 완성도 막대(0~100). */
export function CompletenessBar({ value, missing }: { value?: number | null; missing?: number }) {
  if (value == null) return <span className="text-sm text-muted-foreground">-</span>
  const color = value >= 80 ? "bg-emerald-500" : value >= 50 ? "bg-amber-500" : "bg-red-500"
  return (
    <div className="flex items-center gap-2" title={missing ? `빠진 항목 ${missing}개` : "모든 항목 채움"}>
      <div className="h-1.5 w-14 overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full", color)} style={{ width: `${value}%` }} />
      </div>
      <span className="text-xs tabular-nums">{value}</span>
    </div>
  )
}

function OwnerCell({ agent }: { agent: AIAgentSummary }) {
  const state = agent.owner_state
  return (
    <div className="flex flex-col">
      <OwnerValue email={agent.owner_email ?? null} owner={agent.owner} className="text-sm text-muted-foreground" />
      {state && state !== "ok" && (
        <span
          className="flex w-fit items-center gap-1 text-[11px] text-amber-700 dark:text-amber-400"
          title={OWNER_STATE_HINT[state]}
        >
          <UserX className="h-3 w-3" /> {OWNER_STATE_LABEL[state] ?? state}
        </span>
      )}
      {agent.department && <span className="text-[11px]">{agent.department}</span>}
    </div>
  )
}


// 데이터셋 목록 페이지네이션과 동일한 페이지 번호 생성(말줄임 포함)
function getPageNumbers(currentPage: number, totalPages: number): (number | "...")[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1)
  if (currentPage <= 4) return [1, 2, 3, 4, 5, "...", totalPages]
  if (currentPage >= totalPages - 3)
    return [1, "...", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages]
  return [1, "...", currentPage - 1, currentPage, currentPage + 1, "...", totalPages]
}

function AgentStatusBadge({ status }: { status: string }) {
  return <StatusBadge {...(LIFECYCLE_STATUS[status] ?? { label: STATUS_LABEL[status] ?? status, tone: "neutral" as const })} />
}

/** 에이전트 카드(TODO-114) — 데이터셋 카드와 같은 틀(`EntityCard`): 이름 크기 · 경로 · 설명 두 줄 · 바닥 줄 */
export function AgentCard({ agent, onOpen }: { agent: AIAgentSummary; onOpen: () => void }) {
  const title = agent.display_name || agent.name
  const crumb = [agent.department, agent.category].filter(Boolean).join(" › ")
  const meta = [agent.framework, agent.version && `v${agent.version.replace(/^v/i, "")}`].filter(Boolean).join(" · ")
  const state = agent.owner_state
  return (
    <EntityCard
      crumb={crumb || null}
      title={title}
      prominent={!!agent.display_name}
      identifier={agent.display_name ? agent.name : null}
      onOpen={onOpen}
      menuLabel={`${title} 메뉴`}
      menu={<LinkMenuItems href={`/dashboard/ai-agents?agent=${encodeURIComponent(agent.name)}`} />}
      footer={
        <>
          <span className="flex min-w-0 items-center gap-1.5">
            {agent.owner_email ? (
              <EntityCardRaised className="min-w-0 truncate">
                <OwnerValue email={agent.owner_email} owner={agent.owner} />
              </EntityCardRaised>
            ) : (
              <span className="text-amber-700 dark:text-amber-400">소유자 없음</span>
            )}
            {agent.owner_email && state && state !== "ok" && (
              <span className="shrink-0 text-amber-700 dark:text-amber-400" title={OWNER_STATE_HINT[state]}>
                <UserX className="h-3.5 w-3.5" aria-label={OWNER_STATE_LABEL[state] ?? state} />
              </span>
            )}
          </span>
          <span className="ml-auto flex shrink-0 items-center gap-1.5">
            완성도
            <CompletenessBar value={agent.completeness} missing={agent.missing_fields?.length} />
          </span>
        </>
      }
    >
      {/* 상태(활성이 아닐 때만) · 도구 위험도 · 프레임워크 · 버전 */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        {agent.status !== "active" && <AgentStatusBadge status={agent.status} />}
        {agent.max_risk && (
          <span className="inline-flex items-center gap-1" title="보유 도구의 최고 위험도">
            도구 위험 <RiskBadge risk={agent.max_risk} />
          </span>
        )}
        {meta && <span>{meta}</span>}
      </div>

      <EntityCardText text={agent.description} />

      {/* 기저 모델 */}
      <div className="flex min-w-0 items-center gap-1.5 text-xs">
        <Cpu className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-label="기저 모델" />
        {agent.base_model ? <span className="truncate">{agent.base_model}</span> : <span >모델 미지정</span>}
      </div>
    </EntityCard>
  )
}

export function AIAgentsTable({ data, isLoading }: AIAgentsTableProps) {
  const { canUseFeature } = usePermissions()
  const canRegister = canUseFeature("agents.register")
  // 일괄 변경은 고칠 수 있는 에이전트(관리자 · 등록자 · 소유자 — 서버 manifest._can_edit 와 같은 기준)만 고른다.
  // 역할에 수정(agents.edit) · 일괄 변경(agents.bulk-edit) 권한이 없으면 고를 수 없다(TODO-122).
  const canBulk = canUseFeature("agents.edit") && canUseFeature("agents.bulk-edit")
  const { user } = useAuth()
  const canEditAgent = (a: AIAgentSummary) =>
    canBulk && !!user && (user.is_admin || (!!a.created_by && a.created_by === user.username)
      || (!!a.owner_email && !!user.email && a.owner_email.toLowerCase() === user.email.toLowerCase()))
  const editable = data.filter(canEditAgent)
  const {
    total,
    page,
    pageSize,
    setPage,
    setPageSize,
    query,
    applyQuery,
    selectedNames,
    setSelectedNames,
    setSelectedAgentName,
    setOpen,
    // 상세 복귀 시에도 유지되도록 provider 에서 관리
    viewMode: view,
    setViewMode: setView,
    searchQuery,
    setSearchQuery,
    listScrollRef,
    listTab,
    setListTab,
    statsKey,
    bumpStats,
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
    (search?: string) => applyQuery({ ...query, search: search ?? searchQuery }),
    [query, searchQuery, applyQuery],
  )

  function handleSearchChange(value: string) {
    setSearchQuery(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => runSearch(value), 400)
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <div className="flex flex-1 flex-col gap-4">
      {/* 상단 지표 — 등록·삭제로 전체 수가 바뀌면 다시 조회 */}
      <AIAgentsStats reloadKey={`${total}-${statsKey}`} />

      <Tabs value={listTab} onValueChange={(v) => setListTab(v as "agents" | "discoveries")}>
        <TabsList>
          <TabsTrigger value="agents">에이전트</TabsTrigger>
          <TabsTrigger value="discoveries">미등록 에이전트</TabsTrigger>
        </TabsList>
      </Tabs>

      {listTab === "discoveries" ? (
        <AIAgentsDiscoveries onChanged={bumpStats} />
      ) : (
      <>

      {/* Toolbar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-1 flex-wrap items-center gap-2">
          <SearchInput
            className="max-w-xs flex-1"
            placeholder="AI Agent 검색…"
            value={searchQuery}
            onValueChange={handleSearchChange}
            onSearch={runSearch}
          />
          {selectedNames.length > 0 && (
            <>
              <Button size="sm" variant="secondary" onClick={() => setOpen("bulk")}>
                <ListChecks className="mr-1 h-4 w-4" />
                선택 {selectedNames.length}개 일괄 변경
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSelectedNames([])}>
                선택 해제
              </Button>
            </>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* 뷰 토글 — 테이블 / 카드 */}
          <div className="flex items-center rounded-md border p-0.5">
            <Tip label="테이블 보기"><Button
              size="icon"
              variant={view === "table" ? "secondary" : "ghost"}
              className="h-7 w-7"
              onClick={() => setView("table")}
             
              aria-label="테이블 보기"
            >
              <List className="h-4 w-4" />
            </Button></Tip>
            <Tip label="카드 보기"><Button
              size="icon"
              variant={view === "card" ? "secondary" : "ghost"}
              className="h-7 w-7"
              onClick={() => setView("card")}
             
              aria-label="카드 보기"
            >
              <LayoutGrid className="h-4 w-4" />
            </Button></Tip>
          </div>
          {canRegister && (
            <>
              <Button size="sm" variant="outline" onClick={() => setOpen("import")}>
                <Import className="mr-1 h-4 w-4" />
                가져오기
              </Button>
              <Button size="sm" onClick={() => setOpen("add")}>
                <Plus className="mr-1 h-4 w-4" />
                에이전트 등록
              </Button>
            </>
          )}
        </div>
      </div>

      <AIAgentsFilters />

      {/* 목록 — 테이블 / 카드 */}
      {view === "table" ? (
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              {canBulk && <TableHead className="w-8">
                <Checkbox
                  aria-label="이 페이지 전체 선택"
                  checked={editable.length > 0 && editable.every((a) => selectedNames.includes(a.name))}
                  disabled={editable.length === 0}
                  onCheckedChange={(c) =>
                    setSelectedNames((prev) =>
                      c
                        ? Array.from(new Set([...prev, ...editable.map((a) => a.name)]))
                        : prev.filter((n) => !data.some((a) => a.name === n)),
                    )
                  }
                />
              </TableHead>}
              <TableHead>이름</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>버전</TableHead>
              <TableHead>프레임워크</TableHead>
              <TableHead>분류</TableHead>
              <TableHead>소유자 · 부서</TableHead>
              <TableHead title="도구 최고 위험도 · 에이전트 위험 등급">위험도 · 등급</TableHead>
              <TableHead><InfoTip tip="정보 완성도(100점) — 소유자 · 부서 · 분류 25, 설명(20자 이상) 15, 호출 주소 · 기반 모델 · 도구 위험도 · 입출력 형식 · 안전장치 · 평가 기록 각 10.">완성도</InfoTip></TableHead>
              <TableHead>관리 방식</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={canBulk ? 10 : 9} className="h-24 text-center text-muted-foreground">
                  불러오는 중…
                </TableCell>
              </TableRow>
            ) : data.length === 0 ? (
              <TableRow>
                <TableCell colSpan={canBulk ? 10 : 9} className="h-24 text-center text-muted-foreground">
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
                  {canBulk && <TableCell onClick={(e) => e.stopPropagation()}>
                    <DisabledReason reason={canEditAgent(agent) ? undefined : "등록자·소유자 또는 관리자만 고칠 수 있습니다."}>
                      <Checkbox
                        aria-label={`${agent.name} 선택`}
                        disabled={!canEditAgent(agent)}
                        checked={selectedNames.includes(agent.name)}
                        onCheckedChange={(c) =>
                          setSelectedNames((prev) =>
                            c ? [...prev, agent.name] : prev.filter((n) => n !== agent.name),
                          )
                        }
                      />
                    </DisabledReason>
                  </TableCell>}
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <Bot className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <div className="flex flex-col">
                        <span className="font-medium">
                          {agent.display_name || agent.name}
                        </span>
                        <span className="text-xs">{agent.name}</span>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>
                    <AgentStatusBadge status={agent.status} />
                  </TableCell>
                  <TableCell className="text-sm">{agent.version}</TableCell>
                  <TableCell>
                    {agent.framework ? (
                      <Badge variant="outline">{agent.framework}</Badge>
                    ) : (
                      <span className="text-sm text-muted-foreground">-</span>
                    )}
                  </TableCell>
                  <TableCell className="text-sm">{agent.category || "-"}</TableCell>
                  <TableCell>
                    <OwnerCell agent={agent} />
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap items-center gap-1">
                      <RiskBadge risk={agent.max_risk} />
                      {agent.risk_tier && <RiskTierBadge tier={agent.risk_tier} />}
                    </div>
                  </TableCell>
                  <TableCell>
                    <CompletenessBar value={agent.completeness} missing={agent.missing_fields?.length} />
                  </TableCell>
                  <TableCell>
                    {agent.managed_by && agent.managed_by !== "ui" ? (
                      <Badge variant="secondary">
                        {MANAGED_BY_LABEL[agent.managed_by] ?? agent.managed_by}
                      </Badge>
                    ) : (
                      <span className="text-sm">화면</span>
                    )}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      ) : isLoading ? (
        <div className="flex h-24 items-center justify-center rounded-md border text-sm text-muted-foreground">
          불러오는 중…
        </div>
      ) : data.length === 0 ? (
        <div className="flex h-24 items-center justify-center rounded-md border text-sm text-muted-foreground">
          등록된 AI Agent가 없습니다.
        </div>
      ) : (
        <EntityCardGrid>
          {data.map((agent) => (
            <AgentCard key={agent.id} agent={agent} onOpen={() => setSelectedAgentName(agent.name)} />
          ))}
        </EntityCardGrid>
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
          <span className="text-sm">· 총 {total}개</span>
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
                <span className="px-1 text-sm">...</span>
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
      </>
      )}
    </div>
  )
}
