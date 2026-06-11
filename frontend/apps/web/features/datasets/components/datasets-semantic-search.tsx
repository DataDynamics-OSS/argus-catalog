"use client"

import { useCallback, useState } from "react"
import { useRouter } from "next/navigation"
import { Brain, Loader2, Search, X } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import {
  Tooltip, TooltipContent, TooltipTrigger,
} from "@workspace/ui/components/tooltip"

import { hybridSearch, type SemanticSearchResult, type SemanticSearchResponse } from "../api"

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
      setError(e instanceof Error ? e.message : "Search failed")
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

  function matchBadge(type: string) {
    if (type === "hybrid") return <Badge variant="outline" className="text-[10px] px-1 py-0 text-purple-600 border-purple-200">hybrid</Badge>
    if (type === "semantic") return <Badge variant="outline" className="text-[10px] px-1 py-0 text-blue-600 border-blue-200">semantic</Badge>
    return <Badge variant="outline" className="text-[10px] px-1 py-0">keyword</Badge>
  }

  return (
    <div className="space-y-3">
      {/* Search bar */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search datasets by name, description, or meaning..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            className="pl-8 h-9"
          />
        </div>
        <Button size="sm" className="h-9" onClick={handleSearch} disabled={loading || !query.trim()}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
        </Button>
        {results && (
          <Button variant="ghost" size="sm" className="h-9" onClick={handleClear}>
            <X className="h-4 w-4 mr-1" />Clear
          </Button>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex items-center text-muted-foreground">
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
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span>{meta?.total ?? 0} results</span>
            {meta?.provider && (
              <Badge variant="outline" className="text-[10px] px-1.5 py-0">{meta.provider}/{meta.model}</Badge>
            )}
            {!meta?.provider && (
              <Badge variant="outline" className="text-[10px] px-1.5 py-0">keyword only</Badge>
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
                          <p className="text-xs text-muted-foreground truncate max-w-[500px]">
                            {r.dataset.description}
                          </p>
                        )}
                      </td>
                      <td className="px-3 py-2 text-muted-foreground text-sm">{r.dataset.datasource_name}</td>
                      <td className="px-3 py-2 text-center">
                        <Badge variant="outline" className="text-xs">{r.dataset.origin}</Badge>
                      </td>
                      <td className="px-3 py-2 text-center text-muted-foreground">{r.dataset.tag_count}</td>
                      <td className="px-3 py-2 text-center">{matchBadge(r.match_type)}</td>
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
            <div className="text-center py-8 text-sm text-muted-foreground">
              No matching datasets found.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
