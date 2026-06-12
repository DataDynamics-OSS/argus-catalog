"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Database,
  Globe,
  Info,
  Loader2,
  RefreshCw,
  Search,
} from "lucide-react"
import Link from "next/link"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { GridPagination } from "@/components/grid-pagination"

import {
  browseInstanceDatasets,
  fetchStats,
  type FederatedBrowseDataset,
  type FederatedBrowseResponse,
  type FederationStatsInstance,
} from "./api"
import { federatedDetailHref } from "./search-panel"

/** 우측 데이터셋 그리드 기본 페이지 크기(선택 가능). */
const PAGE_SIZE = 50

/** 출처(환경) 배지 색 — 로컬 데이터셋 카드와 동일 규칙. */
function originBg(origin: string): string {
  if (origin === "PROD") return "bg-emerald-600"
  if (origin === "STAGING") return "bg-orange-500"
  return "bg-sky-500"
}

/** ISO 문자열을 YYYY-MM-DD 로. 빈 값/파싱 실패 시 빈 문자열. */
function formatYmd(value: string | null): string {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/**
 * 표시 이름 — 논리명(display_name)이 있으면 그대로(예: 배우), 없으면 물리명에서
 * path 를 뗀 마지막 토막(테이블명, 예: sakila.film → film). 분류 체계 트리에서
 * 폴더(스키마/DB)가 이미 경로로 드러나므로 잎은 path 를 뗀다.
 */
function leafName(d: FederatedBrowseDataset): string {
  if (d.display_name && d.display_name.trim()) return d.display_name
  const src = d.qualified_name || d.name || ""
  const segs = src
    .split(".")
    .map((s) => s.trim())
    .filter(Boolean)
  return segs.length > 0 ? segs[segs.length - 1]! : d.name || ""
}

/**
 * 미러 데이터셋 그리드 — sidebar 데이터셋 목록의 테이블(그리드) 뷰와 동일한 표현.
 * 이름을 누르면 전체화면 상세로 이동한다(별도 상세/리니지 버튼 없음).
 */
function FederatedDatasetTable({
  datasets,
}: {
  datasets: FederatedBrowseDataset[]
}) {
  return (
    <div className="overflow-hidden rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>이름</TableHead>
            <TableHead className="w-[120px] text-center">환경</TableHead>
            <TableHead className="w-[70px] text-center">필드</TableHead>
            <TableHead className="w-[110px] text-center">등록일</TableHead>
            <TableHead className="w-[110px] text-center">동기화일</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {datasets.map((d) => (
            <TableRow key={d.federated_urn}>
              <TableCell>
                <div className="min-w-0">
                  <Link
                    href={federatedDetailHref(d.federated_urn)}
                    className="text-sm font-medium hover:underline"
                    title={d.name}
                  >
                    {leafName(d)}
                  </Link>
                  {d.summary && (
                    <p
                      className="max-w-[400px] truncate text-xs text-muted-foreground"
                      title={d.summary}
                    >
                      {d.summary}
                    </p>
                  )}
                </div>
              </TableCell>
              <TableCell className="text-center">
                {d.origin && (
                  <Badge className={`text-xs text-white ${originBg(d.origin)}`}>
                    {d.origin}
                  </Badge>
                )}
              </TableCell>
              <TableCell className="text-center text-sm">
                {d.field_count}
              </TableCell>
              <TableCell className="text-center text-sm">
                {formatYmd(d.remote_created_at)}
              </TableCell>
              <TableCell className="text-center text-sm">
                {formatYmd(d.harvested_at)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

/** 우측 그리드가 표시할 현재 선택(좌측 트리에서 데이터소스 클릭). */
type Selection = {
  key: string
  breadcrumb: string[]
  datasets: FederatedBrowseDataset[]
}

/** 인스턴스가 둘러볼 미러(HARVEST 복제본)를 가질 수 있는지. */
function hasMirror(i: FederationStatsInstance): boolean {
  return i.mode === "HARVEST" || i.mode === "HYBRID" || i.mirror_datasets > 0
}

type InstState = {
  loading: boolean
  error: string | null
  resp: FederatedBrowseResponse | null
}

export function FederationBrowsePanel() {
  const [instances, setInstances] = useState<FederationStatsInstance[] | null>(
    null
  )
  const [statsError, setStatsError] = useState<string | null>(null)
  const [statsLoading, setStatsLoading] = useState(false)

  const [query, setQuery] = useState("")
  const [appliedQ, setAppliedQ] = useState("")

  const [expandedInst, setExpandedInst] = useState<Set<number>>(new Set())
  const [data, setData] = useState<Record<number, InstState>>({})

  // 우측 그리드에 표시할 현재 선택(좌측 트리에서 데이터소스/폴더 클릭).
  const [selection, setSelection] = useState<Selection | null>(null)
  // 우측 그리드 페이지(0-base) + 페이지당 행 수 — 선택이 바뀌면 0 으로 복귀.
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(PAGE_SIZE)

  // 선택과 동시에 페이지를 0 으로 초기화하는 래퍼.
  const selectNode = useCallback((sel: Selection) => {
    setSelection(sel)
    setPage(0)
  }, [])

  const loadStats = useCallback(async () => {
    setStatsLoading(true)
    setStatsError(null)
    try {
      const s = await fetchStats()
      setInstances(s.instances)
    } catch (e) {
      setStatsError(e instanceof Error ? e.message : String(e))
      setInstances([])
    } finally {
      setStatsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStats()
  }, [loadStats])

  const mirrorInstances = useMemo(
    () => (instances ?? []).filter(hasMirror),
    [instances]
  )

  const loadInstance = useCallback(
    async (id: number, q: string): Promise<FederatedBrowseResponse | null> => {
      setData((d) => ({
        ...d,
        [id]: { loading: true, error: null, resp: d[id]?.resp ?? null },
      }))
      try {
        const resp = await browseInstanceDatasets(id, q || undefined)
        setData((d) => ({ ...d, [id]: { loading: false, error: null, resp } }))
        return resp
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        setData((d) => ({
          ...d,
          [id]: { loading: false, error: msg, resp: null },
        }))
        return null
      }
    },
    []
  )

  const toggleInstance = useCallback(
    (id: number) => {
      setExpandedInst((prev) => {
        const next = new Set(prev)
        if (next.has(id)) {
          next.delete(id)
        } else {
          next.add(id)
          if (!data[id]?.resp && !data[id]?.loading) loadInstance(id, appliedQ)
        }
        return next
      })
    },
    [data, appliedQ, loadInstance]
  )

  // 필터 적용: 비어있으면 lazy 모드로 복귀, 값이 있으면 미러 인스턴스를 모두 즉시
  // 조회하고 결과가 있는 인스턴스를 자동 펼친다.
  const applyFilter = useCallback(async () => {
    const q = query.trim()
    setAppliedQ(q)
    setData({})
    setSelection(null)
    setPage(0)
    if (!q) {
      setExpandedInst(new Set())
      return
    }
    const targets = mirrorInstances
    const results = await Promise.all(targets.map((i) => loadInstance(i.id, q)))
    const ei = new Set<number>()
    targets.forEach((inst, idx) => {
      const r = results[idx]
      if (r && r.total_datasets > 0) ei.add(inst.id)
    })
    setExpandedInst(ei)
  }, [query, mirrorInstances, loadInstance])

  const refresh = useCallback(() => {
    setData({})
    setExpandedInst(new Set())
    setSelection(null)
    setPage(0)
    loadStats()
  }, [loadStats])

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex items-center gap-2">
        <div className="relative max-w-xl flex-1">
          <Search className="absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            placeholder="데이터셋 이름/설명으로 트리 좁히기..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyFilter()}
          />
        </div>
        <Button variant="outline" onClick={applyFilter}>
          필터
        </Button>
        <Button variant="ghost" size="icon" onClick={refresh} title="새로고침">
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>

      {statsError && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" /> {statsError}
        </div>
      )}

      {statsLoading && !instances && (
        <div className="flex items-center gap-2 py-8 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> 인스턴스 불러오는 중...
        </div>
      )}

      {instances && mirrorInstances.length === 0 && (
        <p className="py-8 text-center text-sm text-muted-foreground">
          미러(HARVEST) 데이터셋을 가진 인스턴스가 없습니다. 인스턴스 관리에서
          HARVEST/HYBRID 모드로 등록하고 가져오기를 실행하세요.
        </p>
      )}

      {/* 좌: 인스턴스 → 데이터소스 트리 / 우: 선택 데이터소스의 데이터셋 그리드 */}
      {mirrorInstances.length > 0 && (
        <div className="flex min-h-0 flex-1 gap-3">
          {/* LEFT — 트리 */}
          <div className="w-80 shrink-0 overflow-auto rounded-md border">
            {mirrorInstances.map((inst) => {
              const open = expandedInst.has(inst.id)
              const st = data[inst.id]
              return (
                <div key={inst.id} className="border-b last:border-b-0">
                  {/* 인스턴스 노드 — 펼치기 전용 */}
                  <button
                    type="button"
                    onClick={() => toggleInstance(inst.id)}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/50"
                  >
                    {open ? (
                      <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                    )}
                    <Globe className="h-4 w-4 shrink-0 text-blue-600" />
                    <span className="truncate font-medium">{inst.name}</span>
                    <span className="ml-auto text-xs text-muted-foreground">
                      {inst.mirror_datasets.toLocaleString()}
                    </span>
                  </button>

                  {open && (
                    <div className="border-t">
                      {st?.loading && (
                        <div className="flex items-center gap-2 px-3 py-2 text-sm text-muted-foreground">
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />{" "}
                          불러오는 중...
                        </div>
                      )}
                      {st?.error && (
                        <div className="flex items-center gap-2 px-3 py-2 text-sm text-destructive">
                          <AlertTriangle className="h-3.5 w-3.5" /> {st.error}
                        </div>
                      )}
                      {st?.resp && st.resp.datasources.length === 0 && (
                        <p className="px-3 py-2 text-sm text-muted-foreground">
                          {appliedQ
                            ? "필터에 맞는 데이터셋이 없습니다."
                            : "미러된 데이터셋이 없습니다."}
                        </p>
                      )}
                      {st?.resp?.truncated && (
                        <p className="flex items-center gap-1 px-3 py-1 text-xs text-amber-600">
                          <Info className="h-3 w-3 shrink-0" /> 일부만 표시(전체{" "}
                          {st.resp.total_datasets.toLocaleString()}개). 필터로
                          좁혀 보세요.
                        </p>
                      )}

                      {/* 데이터소스 노드 — 클릭하면 우측에 그 데이터셋 목록(평면) 표시 */}
                      {st?.resp?.datasources.map((ds) => {
                        const dsKey = `${inst.id}:${ds.datasource_name}`
                        const selected = selection?.key === dsKey
                        return (
                          <div
                            key={dsKey}
                            role="button"
                            tabIndex={0}
                            onClick={() =>
                              selectNode({
                                key: dsKey,
                                breadcrumb: [inst.name, ds.datasource_name],
                                datasets: ds.datasets,
                              })
                            }
                            className={`flex w-full cursor-pointer items-center gap-1.5 py-1.5 pr-3 pl-8 text-left hover:bg-muted/50 ${
                              selected ? "bg-muted" : ""
                            }`}
                          >
                            <Database className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                            <span className="truncate text-sm">
                              {ds.datasource_name}
                            </span>
                            <span className="ml-auto text-xs text-muted-foreground">
                              {ds.dataset_count}
                            </span>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {/* RIGHT — 선택 노드의 데이터셋 그리드 */}
          <div className="min-w-0 flex-1 overflow-auto rounded-md border">
            {selection ? (
              (() => {
                const total = selection.datasets.length
                const pageCount = Math.max(1, Math.ceil(total / pageSize))
                // 데이터 변동/페이지 크기 변경에 대비해 현재 페이지를 범위 내로 보정
                const safePage = Math.min(page, pageCount - 1)
                const start = safePage * pageSize
                const pageItems = selection.datasets.slice(start, start + pageSize)
                return (
                  <div className="flex min-h-0 flex-col">
                    <div className="flex items-center gap-2 border-b px-3 py-2">
                      <span className="truncate text-sm text-muted-foreground">
                        {selection.breadcrumb.join("  ›  ")}
                      </span>
                      <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                        {total.toLocaleString()}개
                      </span>
                    </div>
                    <div className="p-3">
                      {total > 0 ? (
                        <>
                          <FederatedDatasetTable datasets={pageItems} />
                          {/* 페이저 — sidebar 데이터셋 목록과 동일한 UI(항상 표시) */}
                          <GridPagination
                            page={safePage}
                            pageCount={pageCount}
                            pageSize={pageSize}
                            onPage={setPage}
                            onPageSize={(s) => {
                              setPageSize(s)
                              setPage(0)
                            }}
                          />
                        </>
                      ) : (
                        <p className="py-8 text-center text-sm text-muted-foreground">
                          이 노드에 직접 속한 데이터셋이 없습니다. 하위 폴더를
                          선택해 보세요.
                        </p>
                      )}
                    </div>
                  </div>
                )
              })()
            ) : (
              <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
                왼쪽 트리에서 데이터소스나 폴더를 선택하면 데이터셋이 표시됩니다.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
