"use client"

import { InfoTip, Tip } from "@/components/tip"
import { useCallback, useEffect, useRef, useState } from "react"
import { AlertTriangle, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, FileJson, Import, LayoutGrid, Link2, List, Plug, Plus, Server, ShieldCheck, Sparkles, Wrench } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
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

import {
  fetchMcpDeployments,
  fetchMcpServers,
  fetchMcpStats,
  fetchUnregisteredMcp,
  seedForgeServers,
} from "../api"
import {
  ENVIRONMENTS,
  FRAMEWORK_LABEL,
  PROVIDER_LABEL,
  PROVIDER_TYPES,
  STATUS_LABEL,
  STATUS_OPTIONS,
  authMethodLabel,
  type McpDeploymentSummary,
  type McpServerSummary,
  type McpStats,
  type McpUnregistered,
} from "../data/schema"
import { useAuth } from "@/features/auth"
import { StatCard } from "@/components/stat-card"
import { HealthDot, OriginBadge, RiskBadge, StatusBadge, fmtDate } from "./mcp-badges"
import { useMcpServers, type McpTab } from "./mcp-provider"
import { ControlOverview } from "./tabs/control-overview"
import { usePermissions } from "@/features/permissions/use-permissions"
import { OriginText } from "@/features/datasets/components/origin-text"
import { MutatingBadge } from "@/components/mutating-badge"
import { EntityCard, EntityCardGrid, EntityCardText } from "@/components/entity-card"
import { LinkMenuItems } from "@/components/link-menu-items"
import { SearchInput } from "@/components/search-input"

function getPageNumbers(currentPage: number, totalPages: number): (number | "...")[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1)
  if (currentPage <= 4) return [1, 2, 3, 4, 5, "...", totalPages]
  if (currentPage >= totalPages - 3)
    return [1, "...", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages]
  return [1, "...", currentPage - 1, currentPage, currentPage + 1, "...", totalPages]
}

function Pagination({
  page, total, pageSize, setPage, setPageSize,
}: {
  page: number
  total: number
  pageSize: number
  setPage: (p: number) => void
  setPageSize: (s: number) => void
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  return (
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
  )
}

export function McpList() {
  const { tab, setTab, setOpen, reloadKey, reload } = useMcpServers()
  const { user } = useAuth()
  const canRegister = usePermissions().canUseFeature("mcp.register")
  const [stats, setStats] = useState<McpStats | null>(null)

  useEffect(() => {
    fetchMcpStats().then(setStats).catch(() => setStats(null))
  }, [reloadKey])

  const handleSeedForge = useCallback(async () => {
    try {
      const r = await seedForgeServers()
      const created = r.servers.filter((s) => s.action === "created").length
      const updated = r.servers.filter((s) => s.action === "updated").length
      toast.success("Argus Forge MCP 서버 정의를 가져왔습니다.", { description: `새로 등록 ${created}개 · 기존 정의 갱신 ${updated}개` })
      reload()
    } catch (e) {
      notifyError(e, "Forge 서버를 등록하지 못했습니다.")
    }
  }, [reload])

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatCard icon={Server} label="서버 정의" value={stats?.server_count ?? "-"} />
        <StatCard icon={Plug} label="배포" value={stats?.deployment_count ?? "-"} />
        <StatCard icon={Wrench} label="도구" value={stats?.tool_count ?? "-"} />
        <StatCard
          icon={AlertTriangle}
          label="변경 도구 활성 배포"
          value={stats?.deployments_with_mutating ?? "-"}
          hint="쓰기/삭제 그룹이 켜진 배포"
          tone={stats?.deployments_with_mutating ? "warn" : undefined}
        />
        <StatCard
          icon={AlertTriangle}
          label="보안 경고 배포"
          value={stats?.deployments_with_warnings ?? "-"}
          tone={stats?.deployments_with_warnings ? "warn" : undefined}
        />
        <StatCard
          icon={Link2}
          label="미등록 MCP 연결"
          value={stats?.unregistered_agent_mcp_count ?? "-"}
          hint="에이전트에 직접 입력된 연결"
          tip="AI 에이전트에 이름 · URL 로 직접 입력했고 이 카탈로그의 배포와 연결되지 않은 MCP 연결 수입니다. 배포와 연결해야 실효 도구 · 정책 번들에 반영됩니다."
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Tabs value={tab} onValueChange={(v) => setTab(v as McpTab)}>
          <TabsList>
            <TabsTrigger value="servers">서버 정의</TabsTrigger>
            <TabsTrigger value="deployments">배포</TabsTrigger>
            <TabsTrigger value="control">
              <ShieldCheck className="mr-1 h-3.5 w-3.5" />통제 현황
            </TabsTrigger>
            <TabsTrigger value="unregistered">
              미등록 연결
              {stats?.unregistered_agent_mcp_count ? (
                <Badge size="sm" variant="secondary" className="ml-1">
                  {stats.unregistered_agent_mcp_count}
                </Badge>
              ) : null}
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="flex items-center gap-2">
          {/* 항목이 하나도 없으면 단추도 숨긴다 (DEF-161) */}
          {(canRegister || user?.is_admin) && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" variant="outline">
                <Import className="mr-1 h-4 w-4" />
                가져오기
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {canRegister && (
                <DropdownMenuItem onSelect={() => setOpen("import")}>
                  <FileJson className="h-4 w-4" />
                  server.json · mcp.json 가져오기
                </DropdownMenuItem>
              )}
              {user?.is_admin && (
                <>
                  {canRegister && <DropdownMenuSeparator />}
                  <DropdownMenuItem onSelect={handleSeedForge}>
                    <Sparkles className="h-4 w-4" />
                    Argus Forge 서버 정의 일괄 등록
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
          )}
          {canRegister && (
            <>
              <Button size="sm" variant="outline" onClick={() => setOpen("add-deployment")}>
                <Plus className="mr-1 h-4 w-4" />
                배포 등록
              </Button>
              <Button size="sm" onClick={() => setOpen("add-server")}>
                <Plus className="mr-1 h-4 w-4" />
                서버 등록
              </Button>
            </>
          )}
        </div>
      </div>

      {tab === "servers" && <ServersTable />}
      {tab === "deployments" && <DeploymentsTable />}
      {tab === "unregistered" && <UnregisteredTable />}
      {tab === "control" && <ControlOverview />}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 서버 정의 목록
// ---------------------------------------------------------------------------

type ListView = "table" | "card"

/** 목록 보기(표 / 카드) — 탭마다 브라우저에 기억한다. 첫 렌더는 표(서버 렌더와 같게), 마운트 뒤 기억한 값으로 */
function useListView(key: string): [ListView, (v: ListView) => void] {
  const [view, setViewState] = useState<ListView>("table")
  useEffect(() => {
    try {
      if (window.localStorage.getItem(key) === "card") setViewState("card")
    } catch { /* 저장소를 못 쓰면 표로 */ }
  }, [key])
  const setView = useCallback((v: ListView) => {
    setViewState(v)
    try { window.localStorage.setItem(key, v) } catch { /* 무시 */ }
  }, [key])
  return [view, setView]
}

/** 필터 줄 오른쪽 표 / 카드 전환 */
function ViewToggle({ view, onChange }: { view: ListView; onChange: (v: ListView) => void }) {
  return (
    <div className="ml-auto flex items-center rounded-md border p-0.5">
      <Tip label="테이블 보기"><Button size="icon" variant={view === "table" ? "secondary" : "ghost"} className="h-7 w-7"
        onClick={() => onChange("table")} aria-label="테이블 보기">
        <List className="h-4 w-4" />
      </Button></Tip>
      <Tip label="카드 보기"><Button size="icon" variant={view === "card" ? "secondary" : "ghost"} className="h-7 w-7"
        onClick={() => onChange("card")} aria-label="카드 보기">
        <LayoutGrid className="h-4 w-4" />
      </Button></Tip>
    </div>
  )
}

function CardListMessage({ children }: { children: React.ReactNode }) {
  return <div className="flex h-24 items-center justify-center rounded-md border text-sm">{children}</div>
}

/** MCP 서버 카드(TODO-114) — 데이터셋 · 에이전트 카드와 같은 틀(`EntityCard`) */
export function ServerCard({ server: s, onOpen }: { server: McpServerSummary; onOpen: () => void }) {
  const named = s.display_name || s.title
  const title = named || s.name
  const crumb = [s.department || s.vendor, s.category].filter(Boolean).join(" › ")
  const meta = [FRAMEWORK_LABEL[s.framework] ?? s.framework, PROVIDER_LABEL[s.provider_type] ?? s.provider_type, s.version && `v${s.version.replace(/^v/i, "")}`]
    .filter(Boolean).join(" · ")
  return (
    <EntityCard
      crumb={crumb || null}
      title={title}
      prominent={!!named}
      identifier={named ? s.name : null}
      onOpen={onOpen}
      menuLabel={`${title} 메뉴`}
      menu={<LinkMenuItems href={`/dashboard/mcp-servers?server=${encodeURIComponent(s.name)}`} urn={s.urn} />}
      footer={
        <>
          <span className="tabular-nums">
            도구 {s.tool_count} · 배포 {s.deployment_count}
          </span>
          <span className="ml-auto min-w-0 truncate" title={s.owner_email ?? undefined}>
            {s.owner_email || <span className="text-muted-foreground">소유자 없음</span>}
          </span>
        </>
      }
    >
      {/* 상태(활성이 아닐 때만) · 프레임워크 · 제공자 · 버전 */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        {s.status !== "active" && <StatusBadge status={s.status} />}
        {s.mutating_tool_count > 0 && <MutatingBadge>변경 도구 {s.mutating_tool_count}</MutatingBadge>}
        <span>{meta}</span>
      </div>

      <EntityCardText text={s.description} />

      {/* 대상 플랫폼 */}
      <div className="flex min-w-0 items-center gap-1.5 text-xs">
        <Server className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-label="대상 플랫폼" />
        {s.target_platform ? <span className="truncate">{s.target_platform}</span> : <span >플랫폼 미지정</span>}
      </div>
    </EntityCard>
  )
}

function ServersTable() {
  const {
    serverFilters: f, setServerFilters, serverPage: page, setServerPage, pageSize, setPageSize,
    setSelectedServer, reloadKey,
  } = useMcpServers()
  const [items, setItems] = useState<McpServerSummary[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [searchInput, setSearchInput] = useState(f.search)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [view, setView] = useListView("argus.mcp.serversViewMode")

  useEffect(() => {
    let alive = true
    setLoading(true)
    fetchMcpServers({
      search: f.search || undefined,
      status: f.status === "all" ? undefined : f.status,
      provider_type: f.provider === "all" ? undefined : f.provider,
      framework: f.framework === "all" ? undefined : f.framework,
      page,
      pageSize,
    })
      .then((d) => { if (alive) { setItems(d.items); setTotal(d.total) } })
      .catch((e) => notifyError(e, "MCP 서버 목록을 불러오지 못했습니다."))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [f, page, pageSize, reloadKey])

  const setFilter = (patch: Partial<typeof f>) => {
    setServerFilters((prev) => ({ ...prev, ...patch }))
    setServerPage(1)
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          className="max-w-xs flex-1"
          placeholder="MCP 서버 검색…"
          value={searchInput}
          onValueChange={(v) => {
            setSearchInput(v)
            if (debounceRef.current) clearTimeout(debounceRef.current)
            debounceRef.current = setTimeout(() => setFilter({ search: v }), v ? 400 : 0)
          }}
        />
        <Select value={f.status} onValueChange={(v) => setFilter({ status: v })}>
          <SelectTrigger size="sm" className="w-[130px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">전체 상태</SelectItem>
            {STATUS_OPTIONS.map((s) => <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={f.provider} onValueChange={(v) => setFilter({ provider: v })}>
          <SelectTrigger size="sm" className="w-[130px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">전체 제공자</SelectItem>
            {PROVIDER_TYPES.map((s) => <SelectItem key={s} value={s}>{PROVIDER_LABEL[s]}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={f.framework} onValueChange={(v) => setFilter({ framework: v })}>
          <SelectTrigger size="sm" className="w-[140px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">전체 프레임워크</SelectItem>
            <SelectItem value="argus-forge">Argus Forge</SelectItem>
            <SelectItem value="generic">일반 MCP</SelectItem>
          </SelectContent>
        </Select>
        {/* 보기 — 표 / 카드(브라우저에 기억) */}
        <ViewToggle view={view} onChange={setView} />
      </div>

      {view === "card" ? (
        loading ? (
          <CardListMessage>불러오는 중…</CardListMessage>
        ) : items.length === 0 ? (
          <CardListMessage>등록된 MCP 서버가 없습니다. “서버 등록” 또는 “가져오기”로 추가하세요.</CardListMessage>
        ) : (
          <EntityCardGrid>
            {items.map((s) => <ServerCard key={s.id} server={s} onOpen={() => setSelectedServer(s.name)} />)}
          </EntityCardGrid>
        )
      ) : (
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>이름</TableHead>
              <TableHead>상태 <OriginBadge origin="argus" className="ml-0.5 align-middle" /></TableHead>
              <TableHead>프레임워크</TableHead>
              <TableHead>대상 플랫폼</TableHead>
              <TableHead>Registry 이름</TableHead>
              <TableHead className="text-right">도구 (변경)</TableHead>
              <TableHead className="text-right">배포</TableHead>
              <TableHead>소유자</TableHead>
              <TableHead>최근 수집</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={9} className="h-24 text-center text-muted-foreground">불러오는 중…</TableCell></TableRow>
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={9} className="h-24 text-center text-muted-foreground">
                  등록된 MCP 서버가 없습니다. “서버 등록” 또는 “가져오기”로 추가하세요.
                </TableCell>
              </TableRow>
            ) : (
              items.map((s) => (
                <TableRow key={s.id} className="cursor-pointer" onClick={() => setSelectedServer(s.name)}>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <Server className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <div className="flex flex-col">
                        <span className="font-medium">{s.display_name || s.title || s.name}</span>
                        <span className="text-xs">{s.name}</span>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell><StatusBadge status={s.status} /></TableCell>
                  <TableCell>
                    <Badge variant="outline">{FRAMEWORK_LABEL[s.framework] ?? s.framework}</Badge>
                  </TableCell>
                  <TableCell className="text-sm">{s.target_platform || "-"}</TableCell>
                  <TableCell className="font-mono text-xs">{s.registry_name || "-"}</TableCell>
                  <TableCell className="text-right text-sm">
                    {s.tool_count}
                    {s.mutating_tool_count > 0 && (
                      <span className="ml-1 text-xs text-orange-600 dark:text-orange-400">({s.mutating_tool_count})</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right text-sm">{s.deployment_count}</TableCell>
                  <TableCell className="text-sm">{s.owner_email || "-"}</TableCell>
                  <TableCell className="text-xs">{fmtDate(s.last_synced_at)}</TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      )}
      <Pagination page={page} total={total} pageSize={pageSize} setPage={setServerPage} setPageSize={setPageSize} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// 배포 목록
// ---------------------------------------------------------------------------

export function DeploymentRows({
  items, onOpen, showServer = true,
}: {
  items: McpDeploymentSummary[]
  onOpen: (name: string) => void
  showServer?: boolean
}) {
  return (
    <>
      {items.map((d) => (
        <TableRow key={d.id} className="cursor-pointer" onClick={() => onOpen(d.name)}>
          <TableCell>
            <div className="flex flex-col">
              <span className="font-medium">{d.display_name || d.name}</span>
              <span className="text-xs">{d.name}</span>
            </div>
          </TableCell>
          {showServer && <TableCell className="text-sm">{d.server_name || "-"}</TableCell>}
          <TableCell><OriginText origin={d.environment} /></TableCell>
          <TableCell><StatusBadge status={d.status} /></TableCell>
          <TableCell><HealthDot status={d.health_status} /></TableCell>
          <TableCell className="text-right text-sm">{d.exposed_tool_count}</TableCell>
          <TableCell>
            {d.enabled_mutating_groups.length === 0 ? (
              <span className="text-xs">읽기 전용</span>
            ) : (
              <div className="flex flex-wrap gap-1">
                {d.enabled_mutating_groups.map((g) => (
                  <MutatingBadge key={g}>{g}</MutatingBadge>
                ))}
              </div>
            )}
          </TableCell>
          <TableCell><RiskBadge risk={d.max_exposed_risk} /></TableCell>
          <TableCell className="text-sm">
            {d.warning_count > 0 ? (
              <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400">
                <AlertTriangle className="h-3.5 w-3.5" />{d.warning_count}
              </span>
            ) : "-"}
          </TableCell>
          <TableCell className="text-sm">
            {d.open_finding_count > 0 ? (
              <span className="inline-flex items-center gap-1">
                <span className="font-medium text-red-700 dark:text-red-400">{d.open_finding_count}</span>
                <RiskBadge risk={d.max_finding_severity} />
              </span>
            ) : d.policy_version ? (
              <span className="text-xs text-emerald-700 dark:text-emerald-400">정책 일치</span>
            ) : (
              <span className="text-xs text-muted-foreground">정책 없음</span>
            )}
          </TableCell>
          <TableCell className="text-right text-sm">{d.agent_count}</TableCell>
          <TableCell className="text-xs">{fmtDate(d.last_observed_at)}</TableCell>
        </TableRow>
      ))}
    </>
  )
}

export function DeploymentHeader({ showServer = true }: { showServer?: boolean }) {
  return (
    <TableHeader>
      <TableRow>
        <TableHead>배포</TableHead>
        {showServer && <TableHead>서버 정의</TableHead>}
        <TableHead>환경</TableHead>
        <TableHead>상태</TableHead>
        <TableHead><InfoTip tip="헬스 체크(엔드포인트 응답 확인) 결과입니다. 연속으로 실패하면 '헬스 연속 실패' finding 이 생깁니다.">헬스</InfoTip></TableHead>
        <TableHead className="text-right">노출 도구</TableHead>
        <TableHead><InfoTip tip="데이터를 바꾸는 도구 그룹 중 서버 설정에서 켜진 그룹입니다. 없으면 읽기 전용입니다.">활성 변경 그룹</InfoTip></TableHead>
        <TableHead>최대 위험도</TableHead>
        <TableHead>경고</TableHead>
        <TableHead>finding <OriginBadge origin="argus" className="ml-0.5 align-middle" /></TableHead>
        <TableHead className="text-right">에이전트</TableHead>
        <TableHead>최근 관측</TableHead>
      </TableRow>
    </TableHeader>
  )
}

/**
 * MCP 배포 카드(TODO-115) — 서버 정의 카드와 같은 틀(`EntityCard`).
 * 배지 줄: 환경 · 상태(활성이 아닐 때) · 경고 · 미해결 finding, 변경 줄: 켜진 변경 그룹(없으면 읽기 전용) · 노출 도구 최대 위험도,
 * 바닥: 노출 도구 · 연결 에이전트 수와 헬스(마우스를 올리면 최근 관측).
 */
export function DeploymentCard({ deployment: d, onOpen }: { deployment: McpDeploymentSummary; onOpen: () => void }) {
  const title = d.display_name || d.name
  const crumb = [d.department, d.server_name].filter(Boolean).join(" › ")
  return (
    <EntityCard
      crumb={crumb || null}
      title={title}
      prominent={!!d.display_name}
      identifier={d.display_name ? d.name : null}
      onOpen={onOpen}
      menuLabel={`${title} 메뉴`}
      menu={<LinkMenuItems href={`/dashboard/mcp-servers?deployment=${encodeURIComponent(d.name)}`} urn={d.urn} />}
      footer={
        <>
          <span className="tabular-nums">노출 도구 {d.exposed_tool_count} · 에이전트 {d.agent_count}</span>
          <span className="ml-auto inline-flex items-center gap-1.5" title={`최근 관측 ${fmtDate(d.last_observed_at)}`}>
            헬스 <HealthDot status={d.health_status} />
          </span>
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <OriginText origin={d.environment} className="mr-1" />
        {d.status !== "active" && <StatusBadge status={d.status} />}
        {d.warning_count > 0 && (
          <span className="inline-flex items-center gap-1 text-amber-700 dark:text-amber-400" title="보안 경고">
            <AlertTriangle className="h-3.5 w-3.5" />경고 {d.warning_count}
          </span>
        )}
        {d.open_finding_count > 0 && (
          <span className="inline-flex items-center gap-1 text-red-700 dark:text-red-400" title="정책과 다른 미해결 finding">
            finding {d.open_finding_count}
          </span>
        )}
      </div>

      <EntityCardText text={d.description} />

      {/* 변경 그룹 · 최대 위험도 */}
      <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs">
        {d.enabled_mutating_groups.length === 0 ? (
          <span>읽기 전용</span>
        ) : (
          d.enabled_mutating_groups.map((g) => <MutatingBadge key={g}>{g}</MutatingBadge>)
        )}
        {d.max_exposed_risk && (
          <span className="ml-auto inline-flex items-center gap-1" title="노출 도구의 최대 위험도">
            최대 위험 <RiskBadge risk={d.max_exposed_risk} />
          </span>
        )}
      </div>
    </EntityCard>
  )
}

function DeploymentsTable() {
  const {
    deploymentFilters: f, setDeploymentFilters, deploymentPage: page, setDeploymentPage,
    pageSize, setPageSize, setSelectedDeployment, reloadKey,
  } = useMcpServers()
  const [items, setItems] = useState<McpDeploymentSummary[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [searchInput, setSearchInput] = useState(f.search)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [view, setView] = useListView("argus.mcp.deploymentsViewMode")

  useEffect(() => {
    let alive = true
    setLoading(true)
    fetchMcpDeployments({
      search: f.search || undefined,
      environment: f.environment === "all" ? undefined : f.environment,
      status: f.status === "all" ? undefined : f.status,
      mutating_enabled: f.mutating === "all" ? undefined : f.mutating === "yes",
      page,
      pageSize,
    })
      .then((d) => { if (alive) { setItems(d.items); setTotal(d.total) } })
      .catch((e) => notifyError(e, "MCP 배포 목록을 불러오지 못했습니다."))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [f, page, pageSize, reloadKey])

  const setFilter = (patch: Partial<typeof f>) => {
    setDeploymentFilters((prev) => ({ ...prev, ...patch }))
    setDeploymentPage(1)
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          className="max-w-xs flex-1"
          placeholder="배포 검색(이름·엔드포인트)…"
          value={searchInput}
          onValueChange={(v) => {
            setSearchInput(v)
            if (debounceRef.current) clearTimeout(debounceRef.current)
            debounceRef.current = setTimeout(() => setFilter({ search: v }), v ? 400 : 0)
          }}
        />
        <Select value={f.environment} onValueChange={(v) => setFilter({ environment: v })}>
          <SelectTrigger size="sm" className="w-[120px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">전체 환경</SelectItem>
            {ENVIRONMENTS.map((e) => <SelectItem key={e} value={e}>{e}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={f.status} onValueChange={(v) => setFilter({ status: v })}>
          <SelectTrigger size="sm" className="w-[130px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">전체 상태</SelectItem>
            {STATUS_OPTIONS.map((s) => <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={f.mutating} onValueChange={(v) => setFilter({ mutating: v })}>
          <SelectTrigger size="sm" className="w-[170px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">변경 도구 전체</SelectItem>
            <SelectItem value="yes">변경 도구 활성만</SelectItem>
            <SelectItem value="no">읽기 전용만</SelectItem>
          </SelectContent>
        </Select>
        <ViewToggle view={view} onChange={setView} />
      </div>
      {view === "card" ? (
        loading ? (
          <CardListMessage>불러오는 중…</CardListMessage>
        ) : items.length === 0 ? (
          <CardListMessage>등록된 배포가 없습니다.</CardListMessage>
        ) : (
          <EntityCardGrid>
            {items.map((d) => <DeploymentCard key={d.id} deployment={d} onOpen={() => setSelectedDeployment(d.name)} />)}
          </EntityCardGrid>
        )
      ) : (
      <div className="rounded-md border">
        <Table>
          <DeploymentHeader />
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={12} className="h-24 text-center text-muted-foreground">불러오는 중…</TableCell></TableRow>
            ) : items.length === 0 ? (
              <TableRow><TableCell colSpan={12} className="h-24 text-center text-muted-foreground">등록된 배포가 없습니다.</TableCell></TableRow>
            ) : (
              <DeploymentRows items={items} onOpen={setSelectedDeployment} />
            )}
          </TableBody>
        </Table>
      </div>
      )}
      <Pagination page={page} total={total} pageSize={pageSize} setPage={setDeploymentPage} setPageSize={setPageSize} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// 미등록 연결 — 에이전트에 직접 입력된(카탈로그 미등록) MCP
// ---------------------------------------------------------------------------

/**
 * 미등록 MCP 연결 카드(TODO-119) — 서버 정의 · 배포 카드와 같은 틀(`EntityCard`). 열 상세가 없어 카드를 눌러도 이동하지 않는다.
 * 인증 방식, 주소(두 줄), 이 연결을 쓰는 에이전트(4개 + "+N"), 바닥 줄에 에이전트 수와 *서버 등록* 버튼.
 */
export function UnregisteredCard({ item: u, onRegister }: { item: McpUnregistered; onRegister?: () => void }) {
  const shown = u.agents.slice(0, 4)
  const rest = u.agents.length - shown.length
  return (
    <EntityCard
      title={u.name}
      footer={
        <>
          <span className="tabular-nums">사용 에이전트 {u.agent_count}</span>
          {onRegister && (
            <Button size="sm" variant="outline" className="ml-auto h-7" onClick={onRegister}>서버 등록</Button>
          )}
        </>
      }
    >
      <div className="text-xs">
        인증 {u.auth_method ? authMethodLabel(u.auth_method) : <span className="text-muted-foreground">미지정</span>}
      </div>

      <p className="line-clamp-2 min-h-8 break-all font-mono text-xs leading-4" title={u.url ?? undefined}>
        {u.url || <span className="font-sans text-muted-foreground">주소 없음</span>}
      </p>

      {shown.length > 0 && (
        <div className="flex min-w-0 items-center gap-1" title={u.agents.join(", ")}>
          {shown.map((a) => (
            <Badge key={a} size="sm" variant="secondary" className="block min-w-0 shrink truncate">{a}</Badge>
          ))}
          {rest > 0 && <span className="shrink-0 text-[11px] tabular-nums">+{rest}</span>}
        </div>
      )}
    </EntityCard>
  )
}

function UnregisteredTable() {
  const { reloadKey, setOpen } = useMcpServers()
  const canRegister = usePermissions().canUseFeature("mcp.register")
  const [items, setItems] = useState<McpUnregistered[]>([])
  const [loading, setLoading] = useState(true)
  const [view, setView] = useListView("argus.mcp.unregisteredViewMode")

  useEffect(() => {
    setLoading(true)
    fetchUnregisteredMcp()
      .then(setItems)
      .catch((e) => notifyError(e, "미등록 MCP 목록을 불러오지 못했습니다."))
      .finally(() => setLoading(false))
  }, [reloadKey])

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <p className="text-sm">
          AI Agent 상세에서 이름·URL 을 직접 입력한 MCP 연결 중, 카탈로그 배포와 연결되지 않은 항목입니다.
          서버 정의와 배포를 등록한 뒤 배포 상세의 “사용 에이전트” 탭에서 연결할 수 있습니다.
        </p>
        <ViewToggle view={view} onChange={setView} />
      </div>
      {view === "card" ? (
        loading ? (
          <CardListMessage>불러오는 중…</CardListMessage>
        ) : items.length === 0 ? (
          <CardListMessage>미등록 MCP 연결이 없습니다.</CardListMessage>
        ) : (
          <EntityCardGrid>
            {items.map((u) => (
              <UnregisteredCard key={`${u.name}|${u.url}`} item={u} onRegister={canRegister ? () => setOpen("add-server") : undefined} />
            ))}
          </EntityCardGrid>
        )
      ) : (
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>이름</TableHead>
              <TableHead>URL</TableHead>
              <TableHead>인증</TableHead>
              <TableHead>사용 에이전트</TableHead>
              <TableHead className="w-28" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={5} className="h-24 text-center text-muted-foreground">불러오는 중…</TableCell></TableRow>
            ) : items.length === 0 ? (
              <TableRow><TableCell colSpan={5} className="h-24 text-center text-muted-foreground">미등록 MCP 연결이 없습니다.</TableCell></TableRow>
            ) : (
              items.map((u) => (
                <TableRow key={`${u.name}|${u.url}`}>
                  <TableCell className="font-medium">{u.name}</TableCell>
                  <TableCell className="break-all font-mono text-xs">{u.url || "-"}</TableCell>
                  <TableCell>{u.auth_method ? <Badge variant="outline">{authMethodLabel(u.auth_method)}</Badge> : "-"}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {u.agents.map((a) => <Badge key={a} size="sm" variant="secondary">{a}</Badge>)}
                    </div>
                  </TableCell>
                  <TableCell>
                    {canRegister && (
                      <Button size="sm" variant="outline" onClick={() => setOpen("add-server")}>
                        서버 등록
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      )}
    </div>
  )
}
