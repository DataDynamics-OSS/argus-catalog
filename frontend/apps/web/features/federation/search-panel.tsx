"use client"

import { useCallback, useState } from "react"
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

export function FederationSearchPanel() {
  const router = useRouter()
  const [query, setQuery] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [results, setResults] = useState<FederatedSearchHit[]>([])
  const [meta, setMeta] = useState<{
    queried: number
    failed: string[]
  } | null>(null)

  // 리니지 다이얼로그
  const [lineage, setLineage] = useState<FederatedLineageGraph | null>(null)
  const [lineageRoot, setLineageRoot] = useState<string>("")

  const runSearch = useCallback(async () => {
    const q = query.trim()
    if (!q) return
    setLoading(true)
    setError(null)
    try {
      const resp = await federatedSearch(q, { limit: 30 })
      setResults(resp.items)
      setMeta({
        queried: resp.instances_queried,
        failed: resp.instances_failed,
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setResults([])
      setMeta(null)
    } finally {
      setLoading(false)
    }
  }, [query])

  const openDetail = useCallback(
    (hit: FederatedSearchHit) => {
      router.push(federatedDetailHref(toFederatedUrn(hit)))
    },
    [router]
  )

  const openLineage = useCallback(async (hit: FederatedSearchHit) => {
    setLineageRoot(hit.name)
    setLineage(null)
    try {
      const g = await datasetLineage(toFederatedUrn(hit), 2)
      setLineage(g)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
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
          <Card key={`${hit.source_instance_key ?? "local"}:${hit.urn}`}>
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
                {hit.description && (
                  <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                    {hit.description}
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
        open={lineage !== null}
        onOpenChange={(o) => !o && setLineage(null)}
      >
        <DialogContent className="max-h-[80vh] max-w-3xl overflow-auto">
          <DialogHeader>
            <DialogTitle>Cross-instance 리니지: {lineageRoot}</DialogTitle>
            <DialogDescription>
              로컬·미러 리니지를 URN 매칭으로 합친 데이터 흐름입니다.
            </DialogDescription>
          </DialogHeader>
          {lineage && <LineageBody graph={lineage} />}
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
