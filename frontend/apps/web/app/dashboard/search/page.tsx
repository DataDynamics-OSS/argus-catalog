"use client"

import { useCallback, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { BookOpen, Bot, Database, Loader2, Plug, Search, Webhook } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { DashboardHeader } from "@/components/dashboard-header"
import {
  unifiedSearch,
  type EntitySearchResult,
  type SemanticSearchResult,
} from "@/features/datasets/api"
import { datasetExcerpt } from "@/features/search/excerpt"
import { errorMessage } from "@/lib/notify"

function scoreColor(score: number): string {
  if (score >= 0.8) return "text-emerald-600"
  if (score >= 0.6) return "text-blue-600"
  if (score >= 0.4) return "text-amber-600"
  return "text-muted-foreground"
}

function scoreBg(score: number): string {
  if (score >= 0.8) return "bg-emerald-50 border-emerald-200"
  if (score >= 0.6) return "bg-blue-50 border-blue-200"
  if (score >= 0.4) return "bg-amber-50 border-amber-200"
  return "bg-muted/50 border-muted"
}

function MatchBadge({ type }: { type: string }) {
  if (type === "hybrid") return <Badge variant="outline" className="text-[10px] px-1 py-0 text-purple-600 border-purple-200">hybrid</Badge>
  if (type === "semantic") return <Badge variant="outline" className="text-[10px] px-1 py-0 text-blue-600 border-blue-200">semantic</Badge>
  return <Badge variant="outline" className="text-[10px] px-1 py-0">keyword</Badge>
}

function ScorePill({ score }: { score: number }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${scoreBg(score)} ${scoreColor(score)}`}>
      {(score * 100).toFixed(0)}%
    </span>
  )
}

/** 비-데이터셋 엔티티(용어집/AI Agent/API) 결과 섹션. */
function EntitySection({
  title,
  icon: Icon,
  items,
  onClickItem,
}: {
  title: string
  icon: typeof Database
  items: EntitySearchResult[]
  onClickItem: (item: EntitySearchResult) => void
}) {
  if (items.length === 0) return null
  return (
    <div className="space-y-2">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
        <Icon className="h-4 w-4" />
        {title}
        <span className="text-xs font-normal">({items.length})</span>
      </h3>
      <div className="overflow-hidden rounded-md border">
        <table className="w-full text-sm">
          <tbody className="divide-y">
            {items.map((item) => (
              <tr
                key={`${item.entity_type}-${item.id}`}
                className="hover:bg-muted/30 cursor-pointer"
                onClick={() => onClickItem(item)}
              >
                <td className="px-3 py-2.5">
                  <div className="font-medium">{item.display_name || item.name}</div>
                  {item.description && (
                    <p className="text-xs text-muted-foreground truncate max-w-[500px]">
                      {item.description}
                    </p>
                  )}
                </td>
                <td className="px-3 py-2.5 text-muted-foreground w-44">
                  <div className="flex items-center justify-end gap-1.5">
                    {item.extra.category && (
                      <Badge variant="outline" className="text-xs">{item.extra.category}</Badge>
                    )}
                    {item.extra.protocol && (
                      <Badge variant="outline" className="text-xs">{item.extra.protocol}</Badge>
                    )}
                    {item.extra.platform && (
                      <Badge variant="outline" className="text-xs">{item.extra.platform}</Badge>
                    )}
                    {item.extra.status && (
                      <Badge variant="outline" className="text-xs">{item.extra.status}</Badge>
                    )}
                  </div>
                </td>
                <td className="px-3 py-2.5 text-center w-20"><MatchBadge type={item.match_type} /></td>
                <td className="px-3 py-2.5 text-center w-20"><ScorePill score={item.score} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function SearchPage() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const q = searchParams.get("q") ?? ""

  const [datasets, setDatasets] = useState<SemanticSearchResult[]>([])
  const [glossaryTerms, setGlossaryTerms] = useState<EntitySearchResult[]>([])
  const [aiAgents, setAiAgents] = useState<EntitySearchResult[]>([])
  const [apis, setApis] = useState<EntitySearchResult[]>([])
  const [mcpServers, setMcpServers] = useState<EntitySearchResult[]>([])
  const [total, setTotal] = useState(0)
  const [isLoading, setIsLoading] = useState(false)
  const [provider, setProvider] = useState<string | null>(null)
  const [model, setModel] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const doSearch = useCallback(async (searchQuery: string) => {
    if (!searchQuery.trim()) {
      setDatasets([])
      setGlossaryTerms([])
      setAiAgents([])
      setApis([])
      setMcpServers([])
      setTotal(0)
      return
    }
    setIsLoading(true)
    setError(null)
    try {
      const resp = await unifiedSearch(searchQuery.trim(), 50, 5)
      setDatasets(resp.datasets)
      setGlossaryTerms(resp.glossary_terms)
      setAiAgents(resp.ai_agents)
      setApis(resp.apis)
      setMcpServers(resp.mcp_servers ?? [])
      setTotal(resp.total)
      setProvider(resp.provider)
      setModel(resp.model)
    } catch (e) {
      // 실패를 "결과 없음"으로 보이지 않게 오류 안내를 따로 띄운다.
      setError(errorMessage(e) ?? "검색하지 못했습니다.")
      setDatasets([])
      setGlossaryTerms([])
      setAiAgents([])
      setApis([])
      setMcpServers([])
      setTotal(0)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (q) {
      doSearch(q)
    } else {
      // No query (e.g. browser back) — go back to previous page
      router.back()
    }
  }, [q, doSearch, router])

  const hasResults = total > 0

  return (
    <>
      <DashboardHeader title="검색 결과" searchQuery={q} />
      <div className="flex flex-1 flex-col gap-4 p-4">
        {/* Search meta */}
        {!isLoading && q && !error && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>&ldquo;{q}&rdquo; 검색 결과 {total}건</span>
            {provider ? (
              <Badge variant="outline" className="text-[10px] px-1.5 py-0">{provider}/{model}</Badge>
            ) : (
              <Badge variant="outline" className="text-[10px] px-1.5 py-0">키워드 검색만 (임베딩 비활성)</Badge>
            )}
          </div>
        )}

        {/* Loading */}
        {isLoading && (
          <div className="flex items-center justify-center py-16 gap-2 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="text-sm">검색 중...</span>
          </div>
        )}

        {/* Error */}
        {!isLoading && error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {/* No results */}
        {!isLoading && q && !error && !hasResults && (
          <div className="flex flex-col items-center justify-center py-16 gap-2">
            <Search className="h-10 w-10 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              &ldquo;{q}&rdquo; 와 일치하는 결과가 없습니다.
            </p>
          </div>
        )}

        {/* Dataset results */}
        {!isLoading && datasets.length > 0 && (
          <div className="space-y-2">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
              <Database className="h-4 w-4" />
              데이터셋
              <span className="text-xs font-normal">({datasets.length})</span>
            </h3>
            <div className="overflow-hidden rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/60">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">데이터셋</th>
                    <th className="px-3 py-2 text-left font-medium w-36">데이터 소스</th>
                    <th className="px-3 py-2 text-center font-medium w-20">환경</th>
                    <th className="px-3 py-2 text-center font-medium w-16">필드</th>
                    <th className="px-3 py-2 text-center font-medium w-16">태그</th>
                    <th className="px-3 py-2 text-center font-medium w-20">매칭</th>
                    <th className="px-3 py-2 text-center font-medium w-20">점수</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {datasets.map((r) => (
                    <tr
                      key={r.dataset.id}
                      className="hover:bg-muted/30 cursor-pointer"
                      onClick={() => router.push(`/dashboard/datasets/${r.dataset.id}`)}
                    >
                      <td className="px-3 py-2.5">
                        {/* 물리명 + 논리명(display_name). 둘째 줄은 목록 화면처럼 요약 → 없으면 설명 평문 */}
                        <div className="flex items-baseline gap-2">
                          <span className="font-medium">{r.dataset.name}</span>
                          {r.dataset.display_name && (
                            <span className="text-xs text-muted-foreground">{r.dataset.display_name}</span>
                          )}
                        </div>
                        {datasetExcerpt(r.dataset) && (
                          <p className="text-xs text-muted-foreground truncate max-w-[500px]">
                            {datasetExcerpt(r.dataset)}
                          </p>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <Database className="h-3 w-3" />
                          {r.dataset.datasource_name}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-center">
                        <Badge variant="outline" className="text-xs">{r.dataset.origin}</Badge>
                      </td>
                      <td className="px-3 py-2.5 text-center text-muted-foreground">{r.dataset.schema_field_count}</td>
                      <td className="px-3 py-2.5 text-center text-muted-foreground">{r.dataset.tag_count}</td>
                      <td className="px-3 py-2.5 text-center"><MatchBadge type={r.match_type} /></td>
                      <td className="px-3 py-2.5 text-center"><ScorePill score={r.score} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Entity results — 용어집 / AI Agent / API / MCP 서버 */}
        {!isLoading && (
          <>
            <EntitySection
              title="용어집"
              icon={BookOpen}
              items={glossaryTerms}
              onClickItem={(item) => router.push(`/dashboard/glossary?term=${item.id}`)}
            />
            <EntitySection
              title="AI Agent"
              icon={Bot}
              items={aiAgents}
              onClickItem={(item) => router.push(`/dashboard/ai-agents?agent=${encodeURIComponent(item.name)}`)}
            />
            <EntitySection
              title="API"
              icon={Webhook}
              items={apis}
              onClickItem={(item) => router.push(`/dashboard/apis?api=${encodeURIComponent(item.name)}`)}
            />
            <EntitySection
              title="MCP 서버"
              icon={Plug}
              items={mcpServers}
              onClickItem={(item) => router.push(`/dashboard/mcp-servers?server=${encodeURIComponent(item.name)}`)}
            />
          </>
        )}
      </div>
    </>
  )
}
