"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import {
  AlertTriangle,
  GitBranch,
  Globe,
  HardDrive,
  Info,
  Loader2,
  Search,
} from "lucide-react"
import { useRouter } from "next/navigation"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"

import {
  datasetLineage,
  federatedSearch,
  type FederatedLineageGraph,
  type FederatedSearchHit,
} from "./api"
import { errorMessage } from "@/lib/notify"
import { markdownToPlain } from "@/features/search/excerpt"

/** 페더레이션 데이터셋 상세 전체화면 경로. */
export function federatedDetailHref(federatedUrn: string): string {
  return `/dashboard/federation/datasets?urn=${encodeURIComponent(federatedUrn)}`
}

/** 출처 배지 — 로컬이면 회색, peer 면 인스턴스명. */
export function SourceBadge({
  instanceKey,
  instanceName,
}: {
  instanceKey: string | null
  instanceName?: string | null
}) {
  if (!instanceKey) {
    return (
      <Badge variant="secondary" className="gap-1">
        <HardDrive className="h-3 w-3" /> 로컬
      </Badge>
    )
  }
  return (
    <Badge
      variant="outline"
      className="gap-1 border-blue-300 text-blue-700 dark:text-blue-300"
    >
      <Globe className="h-3 w-3" /> {instanceName || instanceKey}
    </Badge>
  )
}

/** federated URN 으로 정규화 — LIVE peer hit 은 plain urn 이라 prefix 를 붙인다. */
function toFederatedUrn(hit: FederatedSearchHit): string {
  if (hit.urn.includes("::")) return hit.urn
  if (hit.source_instance_key) return `${hit.source_instance_key}::${hit.urn}`
  return hit.urn
}

export function FederationSearchPanel({
  initialQuery = "",
  onQueryCommit,
}: {
  /** URL(?q=)에서 복원한 검색어 — 있으면 마운트 시 자동 검색 */
  initialQuery?: string
  /** 검색 실행 시 호출 — 상위가 URL 에 검색어를 보관 */
  onQueryCommit?: (q: string) => void
} = {}) {
  const router = useRouter()
  const [query, setQuery] = useState(initialQuery)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [results, setResults] = useState<FederatedSearchHit[]>([])
  const [meta, setMeta] = useState<{
    queried: number
    failed: string[]
  } | null>(null)

  // 리니지 다이얼로그
  const [lineageOpen, setLineageOpen] = useState(false)
  const [lineage, setLineage] = useState<FederatedLineageGraph | null>(null)
  const [lineageLoading, setLineageLoading] = useState(false)
  const [lineageError, setLineageError] = useState<string | null>(null)
  const [lineageRoot, setLineageRoot] = useState<string>("")

  // 요청 세대 — 늦게 도착한 이전 요청의 응답이 최신 결과를 덮어쓰지 않도록
  const searchReq = useRef(0)
  const lineageReq = useRef(0)
  // 마지막으로 실행한 검색어 — URL 의 q 가 바뀐 경우(뒤로/앞으로)에만 재검색
  const lastRun = useRef<string | null>(null)

  const execSearch = useCallback(
    async (raw: string) => {
      const q = raw.trim()
      if (!q) return
      const req = ++searchReq.current
      lastRun.current = q
      onQueryCommit?.(q)
      setLoading(true)
      setError(null)
      try {
        const resp = await federatedSearch(q, { limit: 30 })
        if (req !== searchReq.current) return
        setResults(resp.items)
        setMeta({
          queried: resp.instances_queried,
          failed: resp.instances_failed,
        })
      } catch (e) {
        if (req !== searchReq.current) return
        setError(errorMessage(e) ?? String(e))
        setResults([])
        setMeta(null)
      } finally {
        if (req === searchReq.current) setLoading(false)
      }
    },
    [onQueryCommit]
  )

  const runSearch = useCallback(() => {
    if (loading) return
    void execSearch(query)
  }, [execSearch, loading, query])

  // URL 에서 온 검색어로 자동 검색(마운트·뒤로 가기). 직접 실행한 검색어와 같으면 생략.
  useEffect(() => {
    const q = initialQuery.trim()
    if (!q || q === lastRun.current) return
    setQuery(q)
    void execSearch(q)
    // execSearch 는 onQueryCommit(매 렌더 새 함수)에 의존 — q 변화에만 반응한다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuery])

  const openDetail = useCallback(
    (hit: FederatedSearchHit) => {
      router.push(federatedDetailHref(toFederatedUrn(hit)))
    },
    [router]
  )

  // 다이얼로그를 즉시 열고 내부에 로딩·오류를 표시한다.
  const openLineage = useCallback(async (hit: FederatedSearchHit) => {
    const req = ++lineageReq.current
    setLineageRoot(hit.name)
    setLineage(null)
    setLineageError(null)
    setLineageLoading(true)
    setLineageOpen(true)
    try {
      const g = await datasetLineage(toFederatedUrn(hit), 2)
      if (req === lineageReq.current) setLineage(g)
    } catch (e) {
      if (req === lineageReq.current)
        setLineageError(errorMessage(e) ?? String(e))
    } finally {
      if (req === lineageReq.current) setLineageLoading(false)
    }
  }, [])

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex items-center gap-2">
        <div className="relative max-w-xl flex-1">
          <Search className="absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            placeholder="모든 팀 카탈로그를 한 번에 검색..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
          />
        </div>
        <Button onClick={runSearch} disabled={loading}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "검색"}
        </Button>
      </div>

      {meta && (
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <span className="flex items-center gap-1">
            <Info className="h-3.5 w-3.5" /> {results.length}건 · {meta.queried}
            개 인스턴스 조회
          </span>
          {meta.failed.length > 0 && (
            <span className="flex items-center gap-1 text-amber-600">
              <AlertTriangle className="h-3.5 w-3.5" /> 실패:{" "}
              {meta.failed.join(", ")}
            </span>
          )}
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" /> {error}
        </div>
      )}

      <div className="flex flex-col gap-2 overflow-auto">
        {results.map((hit) => (
          <Card
            key={`${hit.source_instance_key ?? "local"}:${hit.urn}`}
            className="py-0"
          >
            <CardContent className="flex items-start gap-3 p-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <SourceBadge
                    instanceKey={hit.source_instance_key}
                    instanceName={hit.source_instance_name}
                  />
                  <span className="truncate font-medium">{hit.name}</span>
                  <Badge variant="secondary" className="text-xs">
                    {hit.match_type}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {(hit.score * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="mt-1 truncate text-xs text-muted-foreground">
                  {hit.datasource_name && <span>{hit.datasource_name}</span>}
                  {hit.datasource_type && <span> · {hit.datasource_type}</span>}
                  {hit.origin && <span> · {hit.origin}</span>}
                </div>
                {/* 설명은 마크다운(## 개요, **굵게**) — 기호가 보이지 않게 평문으로 */}
                {hit.description && (
                  <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                    {markdownToPlain(hit.description)}
                  </p>
                )}
                <div className="mt-1 truncate font-mono text-[11px] text-muted-foreground">
                  {hit.urn}
                </div>
              </div>
              <div className="flex shrink-0 flex-col gap-1">
                {hit.source_instance_key && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => openDetail(hit)}
                  >
                    상세
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => openLineage(hit)}
                >
                  <GitBranch className="mr-1 h-3.5 w-3.5" /> 리니지
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
        {!loading && results.length === 0 && meta && (
          <p className="py-8 text-center text-sm text-muted-foreground">
            검색 결과가 없습니다.
          </p>
        )}
      </div>

      {/* 리니지 */}
      <Dialog
        open={lineageOpen}
        onOpenChange={(o) => {
          setLineageOpen(o)
          if (!o) lineageReq.current++ // 닫은 뒤 도착한 응답 무시
        }}
      >
        <DialogContent className="max-h-[80vh] max-w-3xl overflow-auto">
          <DialogHeader>
            <DialogTitle>Cross-instance 리니지: {lineageRoot}</DialogTitle>
            <DialogDescription>
              로컬·미러 리니지를 URN 매칭으로 합친 데이터 흐름입니다.
            </DialogDescription>
          </DialogHeader>
          {lineageLoading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> 리니지를 불러오는 중...
            </div>
          ) : lineageError ? (
            <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              <AlertTriangle className="h-4 w-4 shrink-0" /> {lineageError}
            </div>
          ) : (
            lineage && <LineageBody graph={lineage} />
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}

export function LineageBody({ graph }: { graph: FederatedLineageGraph }) {
  const nodeByUrn = new Map(graph.nodes.map((n) => [n.urn, n]))
  const label = (urn: string) => {
    const n = nodeByUrn.get(urn)
    return n?.name || urn
  }
  if (graph.edges.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        연결된 리니지가 없습니다.
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-2">
      {graph.edges.map((e, i) => {
        const src = nodeByUrn.get(e.source_urn)
        const tgt = nodeByUrn.get(e.target_urn)
        return (
          <div
            key={i}
            className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm"
          >
            <SourceBadge
              instanceKey={src?.source_instance_key ?? null}
              instanceName={src?.source_instance_name}
            />
            <span
              className={
                src?.unresolved ? "text-muted-foreground italic" : "font-medium"
              }
            >
              {label(e.source_urn)}
            </span>
            <span className="text-xs text-muted-foreground">
              —{e.relation_type}→
            </span>
            <SourceBadge
              instanceKey={tgt?.source_instance_key ?? null}
              instanceName={tgt?.source_instance_name}
            />
            <span
              className={
                tgt?.unresolved ? "text-muted-foreground italic" : "font-medium"
              }
            >
              {label(e.target_urn)}
            </span>
            {e.reported_by && (
              <Badge variant="outline" className="ml-auto text-[10px]">
                보고: {e.reported_by}
              </Badge>
            )}
          </div>
        )
      })}
    </div>
  )
}
