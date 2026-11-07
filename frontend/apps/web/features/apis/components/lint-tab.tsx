"use client"

import { useCallback, useEffect, useState } from "react"
import { Loader2 } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { cn } from "@workspace/ui/lib/utils"

import { type ApiLint, type ApiSpec, fetchApiLint } from "../api"
import { TabEmpty, TabError, TabLoading, fmtDateTime } from "./apis-detail-shared"
import { errorMessage } from "@/lib/notify"

// ---------------------------------------------------------------------------
// 린팅 (스펙 품질 검사 — Spectral 스타일)
// ---------------------------------------------------------------------------

const LINT_SEVERITY: Record<string, { label: string; cls: string }> = {
  error: { label: "오류", cls: "bg-red-600 text-white" },
  warn: { label: "경고", cls: "bg-amber-500 text-white" },
  info: { label: "정보", cls: "bg-slate-400 text-white" },
}

export function LintTab({ apiName, specs }: { apiName: string; specs: ApiSpec[] }) {
  // 검사할 스펙 버전 — "current" 면 현재 버전
  const [specId, setSpecId] = useState("current")
  const [lint, setLint] = useState<ApiLint | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try { setLint(await fetchApiLint(apiName, specId === "current" ? undefined : Number(specId))) }
    catch (e) { setError(errorMessage(e) ?? "린팅 결과를 불러오지 못했습니다.") }
    finally { setLoading(false) }
  }, [apiName, specId])
  // 스펙이 없으면(수동 등록 API) 백엔드가 spec_id=null·score=0 을 돌려준다 — 점수 대신 안내를 표시.
  const noSpec = lint !== null && lint.spec_id === null

  useEffect(() => { load() }, [load])

  const scoreCls = (s: number) => (s >= 90 ? "text-emerald-600" : s >= 70 ? "text-amber-500" : "text-red-600")

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">현재 스펙의 품질을 Spectral(oas 룰셋) 스타일 규칙으로 검사해 오류·경고·정보와 품질 점수(0~100)를 보여주는 화면입니다. info·서버·태그·operationId·응답·경로 파라미터·보안 등 모범사례 위반을 찾아 개선점을 제시합니다.</p>
        {!noSpec && (
          <div className="flex shrink-0 items-center gap-2">
            {specs.length > 1 && (
              <Select value={specId} onValueChange={setSpecId}>
                <SelectTrigger className="h-8 w-[220px] text-sm" aria-label="검사할 스펙 버전"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="current">현재 버전</SelectItem>
                  {specs.filter((s) => s.is_current !== "true").map((s) => (
                    <SelectItem key={s.id} value={String(s.id)}>v{s.version} · {fmtDateTime(s.created_at)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <Button size="sm" variant="outline" disabled={loading} onClick={load}>
              {loading ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}다시 검사
            </Button>
          </div>
        )}
      </div>

      {error && <TabError message={error} onRetry={load} />}
      {!lint ? (
        !error && <TabLoading />
      ) : noSpec ? (
        <TabEmpty>린팅할 스펙이 없습니다. 수동 등록 API 는 OpenAPI 스펙이 없어 품질 검사를 하지 않습니다.</TabEmpty>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-md border px-4 py-3">
              <p className="text-xs text-muted-foreground">품질 점수</p>
              <p className={cn("mt-1 text-2xl font-bold", scoreCls(lint.score))}>{lint.score}<span className="text-sm font-normal text-muted-foreground"> / 100</span></p>
            </div>
            <div className="rounded-md border px-4 py-3"><p className="text-xs text-muted-foreground">오류</p><p className="mt-1 text-xl font-semibold text-red-600">{lint.error_count}</p></div>
            <div className="rounded-md border px-4 py-3"><p className="text-xs text-muted-foreground">경고</p><p className="mt-1 text-xl font-semibold text-amber-500">{lint.warning_count}</p></div>
            <div className="rounded-md border px-4 py-3"><p className="text-xs text-muted-foreground">정보</p><p className="mt-1 text-xl font-semibold text-slate-500">{lint.info_count}</p></div>
          </div>

          {lint.findings.length === 0 ? (
            <p className="text-sm text-emerald-600">발견된 문제가 없습니다. 스펙 품질이 양호합니다.</p>
          ) : (
            <div className="overflow-hidden rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/40"><tr>
                  <th className="w-[70px] px-3 py-2 text-left font-medium text-muted-foreground">심각도</th>
                  <th className="w-[200px] px-3 py-2 text-left font-medium text-muted-foreground">규칙</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">메시지</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">위치</th>
                </tr></thead>
                <tbody>
                  {lint.findings.map((f, i) => {
                    const sev = LINT_SEVERITY[f.severity] ?? LINT_SEVERITY.info!
                    return (
                      <tr key={i} className="border-t align-top">
                        <td className="px-3 py-2"><Badge className={cn("text-[10px]", sev.cls)}>{sev.label}</Badge></td>
                        <td className="px-3 py-2">{f.rule}</td>
                        <td className="px-3 py-2">{f.message}</td>
                        <td className="px-3 py-2 text-muted-foreground break-all">{f.location || "-"}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
