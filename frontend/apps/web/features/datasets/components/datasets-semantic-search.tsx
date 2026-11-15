"use client"

import { Tip } from "@/components/tip"
import { useCallback, useState } from "react"
import { useRouter } from "next/navigation"
import { Brain, Loader2, Search, X } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Tooltip, TooltipContent, TooltipTrigger,
} from "@workspace/ui/components/tooltip"

import { hybridSearch, type SemanticSearchResult, type SemanticSearchResponse } from "../api"
import { errorMessage } from "@/lib/notify"
import { MatchTypeBadge } from "@/components/match-type-badge"
import { OriginText } from "./origin-text"
import { SearchInput } from "@/components/search-input"

export function DatasetsSmartSearch() {
  const router = useRouter()
  const [query, setQuery] = useState("")
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<SemanticSearchResult[] | null>(null)
  const [meta, setMeta] = useState<{ provider: string | null; model: string | null; total: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleSearch = useCallback(async () => {
    const q = query.trim()
    if (!q) return
    setLoading(true)
    setError(null)
    try {
      const resp: SemanticSearchResponse = await hybridSearch(q, 20, 0.3, 0.7)
      setResults(resp.items)
      setMeta({ provider: resp.provider, model: resp.model, total: resp.total })
    } catch (e) {
      setError(errorMessage(e) ?? "Search failed")
      setResults(null)
    } finally {
      setLoading(false)
    }
  }, [query])

  const handleClear = () => {
    setQuery("")
    setResults(null)
    setMeta(null)
    setError(null)
  }

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

  return (
    <div className="space-y-3">
      {/* Search bar */}
      <div className="flex items-center gap-2">
        <SearchInput
          className="flex-1"
          placeholder="이름·설명·의미로 데이터셋 검색…"
          value={query}
          onValueChange={setQuery}
          onSearch={handleSearch}
        />
        <Tip label="검색"><Button aria-label="검색" size="sm" className="h-9" onClick={handleSearch} disabled={loading || !query.trim()}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
        </Button></Tip>
        {results && (
          <Button variant="ghost" size="sm" className="h-9" onClick={handleClear}>
            <X className="h-4 w-4 mr-1" />지우기
          </Button>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex items-center">
              <Brain className={`h-4 w-4 ${meta?.provider ? "text-purple-500" : "text-muted-foreground/40"}`} />
            </div>
          </TooltipTrigger>
          <TooltipContent>
            {meta?.provider
              ? `Semantic search active (${meta.provider}/${meta.model})`
              : "Semantic search disabled — keyword only"
            }
          </TooltipContent>
        </Tooltip>
      </div>

      {/* Error */}
      {error && (
        <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
          {error}
        </div>
      )}

      {/* Results */}
      {results && (
        <div className="space-y-2">
          <div className="flex items-center gap-3 text-xs">
            <span>{meta?.total ?? 0} results</span>
            {meta?.provider && (
              <Badge size="sm" variant="outline">{meta.provider}/{meta.model}</Badge>
            )}
            {!meta?.provider && (
              <Badge size="sm" variant="outline">keyword only</Badge>
            )}
          </div>

          {results.length > 0 ? (
            <div className="border rounded-md overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted/60">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">Dataset</th>
                    <th className="px-3 py-2 text-left font-medium w-32">Datasource</th>
                    <th className="px-3 py-2 text-center font-medium w-20">Origin</th>
                    <th className="px-3 py-2 text-center font-medium w-16">Tags</th>
                    <th className="px-3 py-2 text-center font-medium w-20">Match</th>
                    <th className="px-3 py-2 text-center font-medium w-20">Score</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {results.map((r) => (
                    <tr
                      key={r.dataset.id}
                      className="hover:bg-muted/30 cursor-pointer"
                      onClick={() => router.push(`/dashboard/datasets/${r.dataset.id}`)}
                    >
                      <td className="px-3 py-2">
                        <div className="font-medium text-sm">{r.dataset.name}</div>
                        {r.dataset.description && (
                          <p className="text-xs truncate max-w-[500px]">
                            {r.dataset.description}
                          </p>
                        )}
                      </td>
                      <td className="px-3 py-2 text-sm">{r.dataset.datasource_name}</td>
                      <td className="px-3 py-2 text-center">
                        <OriginText origin={r.dataset.origin} />
                      </td>
                      <td className="px-3 py-2 text-center">{r.dataset.tag_count}</td>
                      <td className="px-3 py-2 text-center"><MatchTypeBadge type={r.match_type} /></td>
                      <td className="px-3 py-2 text-center">
                        <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${scoreBg(r.score)} ${scoreColor(r.score)}`}>
                          {(r.score * 100).toFixed(0)}%
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-center py-8 text-sm">
              No matching datasets found.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
