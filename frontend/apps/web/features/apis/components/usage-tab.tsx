"use client"

import { useEffect, useState } from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { Loader2, RefreshCcw } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { cn } from "@workspace/ui/lib/utils"

import { type ApiDetail, type ApiUsage, fetchApiUsage } from "../api"
import { MethodBadge, TabEmpty, TabError, TabLoading, fmtDateTime } from "./apis-detail-shared"
import { errorMessage } from "@/lib/notify"

// ---------------------------------------------------------------------------
// 관측 탭 — 이 화면의 호출(Try-it) 기록 집계
// ---------------------------------------------------------------------------

const DAY_OPTIONS = ["7", "30", "90"] as const
const ALL = "__all__"
// 상태(성공/오류) 색 — 상태 의미로만 사용하고 항상 범례·숫자와 함께 표시
const OK_BAR = "bg-emerald-500"
const ERR_BAR = "bg-red-500"

/** 상태 코드 묶음 — 0 은 응답을 받지 못한 경우(네트워크 오류·시간 초과) */
function statusGroup(code: string): { key: string; label: string; dot: string } {
  if (code === "0") return { key: "net", label: "네트워크 오류", dot: "bg-slate-500" }
  if (/^[23]/.test(code)) return { key: "ok", label: "성공(2xx·3xx)", dot: "bg-emerald-500" }
  if (code.startsWith("4")) return { key: "4xx", label: "클라이언트 오류(4xx)", dot: "bg-amber-500" }
  return { key: "5xx", label: "서버 오류(5xx)", dot: "bg-red-500" }
}

const localIso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`

/** 기간 내 모든 날짜(오늘 포함 N일, 사용자 시간대) — 호출 없는 날은 0 */
function fillDays(days: number, daily: ApiUsage["daily"]) {
  const by = new Map(daily.map((d) => [d.date, d]))
  const out: { date: string; count: number; errors: number }[] = []
  const today = new Date()
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i)
    const iso = localIso(d)
    out.push({ date: iso, count: by.get(iso)?.count ?? 0, errors: by.get(iso)?.errors ?? 0 })
  }
  return out
}

// 일별 추이 — 성공/오류 누적 막대. 막대에 올리면 그날 수치를 위에 표시.
function DailyChart({ days, daily }: { days: number; daily: ApiUsage["daily"] }) {
  const data = fillDays(days, daily)
  const max = Math.max(1, ...data.map((d) => d.count))
  const [hover, setHover] = useState<number | null>(null)
  const h = hover !== null ? data[hover] : null
  const H = 120
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-3 text-muted-foreground">
          <span className="inline-flex items-center gap-1"><span className={cn("h-2.5 w-2.5 rounded-sm", OK_BAR)} /> 성공</span>
          <span className="inline-flex items-center gap-1"><span className={cn("h-2.5 w-2.5 rounded-sm", ERR_BAR)} /> 오류</span>
        </div>
        <span className="min-h-4 text-foreground" aria-live="polite">
          {h ? <>{h.date} · 호출 <b>{h.count}</b>건 · 오류 <b>{h.errors}</b>건</> : <span className="text-muted-foreground">막대에 마우스를 올리면 날짜별 수치가 표시됩니다.</span>}
        </span>
      </div>
      <div className="relative" style={{ height: H }} onMouseLeave={() => setHover(null)}>
        {/* 기준선·최댓값 눈금 */}
        <div className="absolute inset-x-0 bottom-0 border-t border-border" />
        <div className="absolute inset-x-0 top-0 border-t border-dashed border-border/60" />
        <span className="absolute top-0 left-0 -translate-y-full pb-0.5 text-[10px] text-muted-foreground">{max}</span>
        <div className="absolute inset-0 flex items-end gap-px">
          {data.map((d, i) => {
            const okH = ((d.count - d.errors) / max) * H
            const errH = (d.errors / max) * H
            return (
              <div
                key={d.date}
                className="flex h-full min-w-0 flex-1 cursor-default flex-col justify-end px-px"
                onMouseEnter={() => setHover(i)}
                aria-label={`${d.date} 호출 ${d.count}건, 오류 ${d.errors}건`}
                role="img"
              >
                <div className={cn("flex flex-col justify-end", hover === i && "opacity-80")}>
                  {/* 누적 순서: 오류(위) · 성공(아래). 세그먼트 사이 2px 간격, 위 끝만 둥글게 */}
                  {errH > 0 && <div className={cn("rounded-t-[3px]", ERR_BAR)} style={{ height: Math.max(2, errH) }} />}
                  {errH > 0 && okH > 0 && <div className="h-0.5" />}
                  {okH > 0 && <div className={cn(errH > 0 ? "" : "rounded-t-[3px]", OK_BAR)} style={{ height: Math.max(2, okH) }} />}
                </div>
              </div>
            )
          })}
        </div>
      </div>
      <div className="flex justify-between text-[10px] text-muted-foreground">
        <span>{data[0]?.date}</span>
        <span>{data[data.length - 1]?.date}</span>
      </div>
    </div>
  )
}

export function UsageTab({ api, onOpenEndpoint }: {
  api: ApiDetail
  /** 인기 엔드포인트 클릭 → 엔드포인트 탭의 그 행으로 이동 */
  onOpenEndpoint?: (method: string, path: string) => void
}) {
  const apiName = api.name
  // 기간은 주소(?days=)에 유지 — 탭을 오가거나 새로고침해도 같은 기간
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const daysParam = searchParams.get("days") ?? "30"
  const days = (DAY_OPTIONS as readonly string[]).includes(daysParam) ? daysParam : "30"
  const setDays = (v: string) => {
    const sp = new URLSearchParams(window.location.search)
    if (v === "30") sp.delete("days")
    else sp.set("days", v)
    const qs = sp.toString()
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false })
  }
  // 전체 호출(다른 사람의 호출 URL·호출자 포함)은 API 생성자 본인·관리자만 — 서버도 같은 기준(mine=false 는 403)
  const canSeeAll = api.permissions?.can_edit ?? false
  const [mineChoice, setMine] = useState(false)
  const mine = !canSeeAll || mineChoice
  const [endpoint, setEndpoint] = useState(ALL)
  const [reloadSeq, setReloadSeq] = useState(0)

  const key = `${days}|${mine}|${endpoint}|${reloadSeq}`
  const [result, setResult] = useState<{ key: string; data?: ApiUsage; error?: string } | null>(null)
  useEffect(() => {
    let active = true
    const k = `${days}|${mine}|${endpoint}|${reloadSeq}`
    const ep = endpoint === ALL ? null : (() => { const i = endpoint.indexOf(" "); return { method: endpoint.slice(0, i), path: endpoint.slice(i + 1) } })()
    fetchApiUsage(apiName, Number(days), { mine, endpoint: ep })
      .then((data) => { if (active) setResult({ key: k, data }) })
      .catch((e) => { if (active) setResult({ key: k, error: errorMessage(e) ?? "사용량을 불러오지 못했습니다." }) })
    return () => { active = false }
  }, [apiName, days, mine, endpoint, reloadSeq])
  const loading = result?.key !== key
  // 새 결과를 기다리는 동안 이전 결과를 흐리게 유지
  const usage = result?.data ?? null
  const error = !loading ? result?.error ?? null : null

  const epKeys = new Set(api.endpoints.map((e) => `${e.method} ${e.path}`))
  const splitEp = (s: string) => { const i = s.indexOf(" "); return { method: s.slice(0, i), path: s.slice(i + 1) } }

  const stat = (label: string, value: string, tone?: string) => (
    <div className="rounded-md border px-4 py-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn("mt-1 text-xl font-semibold", tone)}>{value}</p>
    </div>
  )

  // 상태 코드 묶음 합계
  const groups = new Map<string, { label: string; dot: string; count: number; codes: string[] }>()
  for (const s of usage?.by_status ?? []) {
    const g = statusGroup(s.status)
    const cur = groups.get(g.key) ?? { label: g.label, dot: g.dot, count: 0, codes: [] }
    cur.count += s.count
    cur.codes.push(s.status === "0" ? "응답 없음" : `${s.status}×${s.count}`)
    groups.set(g.key, cur)
  }
  const groupOrder = ["ok", "4xx", "5xx", "net"].filter((k) => groups.has(k))

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        이 카탈로그의 엔드포인트 탭에서 실행한 호출(Try-it)을 집계한 화면입니다 — 실제 운영 트래픽은 포함되지 않습니다. 호출 수·성공률·지연(평균/p95)·일별 추이·상태 코드·인기 엔드포인트·호출자별 사용량을 보여 줍니다.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <Select value={days} onValueChange={setDays}>
          <SelectTrigger className="h-8 w-[110px] text-sm" aria-label="기간"><SelectValue /></SelectTrigger>
          <SelectContent>{DAY_OPTIONS.map((d) => <SelectItem key={d} value={d}>최근 {d}일</SelectItem>)}</SelectContent>
        </Select>
        <Select value={endpoint} onValueChange={setEndpoint}>
          <SelectTrigger className="h-8 w-[280px] text-sm" aria-label="엔드포인트"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>모든 엔드포인트</SelectItem>
            {api.endpoints.map((e) => <SelectItem key={e.id} value={`${e.method} ${e.path}`}>{e.method} {e.path}</SelectItem>)}
          </SelectContent>
        </Select>
        <Button
          size="sm" variant={mine ? "secondary" : "outline"} className="h-8" aria-pressed={mine}
          disabled={!canSeeAll}
          title={canSeeAll ? undefined : "전체 호출은 API 소유자·관리자만 볼 수 있습니다."}
          onClick={() => setMine((v) => !v)}
        >
          내 호출만
        </Button>
        <Button size="sm" variant="outline" className="ml-auto h-8" disabled={loading} onClick={() => setReloadSeq((n) => n + 1)}>
          {loading ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <RefreshCcw className="mr-1 h-4 w-4" />}새로고침
        </Button>
      </div>
      {!canSeeAll && (
        <p className="text-xs text-muted-foreground">
          내 호출만 표시합니다 — 다른 사람의 호출(URL·호출자)이 포함된 전체 호출은 API 소유자·관리자만 볼 수 있습니다.
        </p>
      )}

      {error ? (
        <TabError message={error} onRetry={() => setReloadSeq((n) => n + 1)} />
      ) : !usage ? (
        <TabLoading />
      ) : (
        <div className={cn("space-y-4 transition-opacity", loading && "opacity-60")}>
          {usage.total_calls === 0 ? (
            <TabEmpty>
              {mine || endpoint !== ALL ? "조건에 맞는 호출 기록이 없습니다." : "기간 내 호출 기록이 없습니다. 엔드포인트 탭에서 행을 펼쳐 호출(Try-it)하면 사용량이 집계됩니다."}
            </TabEmpty>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                {stat("총 호출", usage.total_calls.toLocaleString())}
                {stat("성공률", `${usage.success_rate}%`)}
                {stat("오류 호출", usage.error_calls.toLocaleString(), usage.error_calls > 0 ? "text-red-600" : undefined)}
                {stat("평균 지연", `${usage.avg_latency_ms} ms`)}
                {stat("p95 지연", `${usage.p95_latency_ms} ms`)}
              </div>

              <Card><CardContent className="pt-4">
                <p className="mb-3 text-sm font-medium">일별 호출 추이</p>
                <DailyChart days={Number(days)} daily={usage.daily} />
              </CardContent></Card>

              <Card><CardContent className="pt-4">
                <p className="mb-2 text-sm font-medium">상태 코드 분포</p>
                <div className="flex flex-wrap gap-x-6 gap-y-2">
                  {groupOrder.map((k) => {
                    const g = groups.get(k)!
                    return (
                      <div key={k} className="text-sm" title={g.codes.join(", ")}>
                        <span className="inline-flex items-center gap-1.5">
                          <span className={cn("h-2.5 w-2.5 rounded-full", g.dot)} />
                          {g.label} <b>{g.count.toLocaleString()}</b>건
                          <span className="text-xs text-muted-foreground">({Math.round((g.count / usage.total_calls) * 100)}%)</span>
                        </span>
                        <p className="ml-4 text-xs text-muted-foreground">{g.codes.join(" · ")}</p>
                      </div>
                    )
                  })}
                </div>
              </CardContent></Card>

              <div className="grid gap-4 lg:grid-cols-2">
                <Card><CardContent className="pt-4">
                  <p className="mb-2 text-sm font-medium">인기 엔드포인트</p>
                  <div className="overflow-hidden rounded-md border">
                    <table className="w-full text-sm">
                      <thead className="bg-muted/40"><tr>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">엔드포인트</th>
                        <th className="w-[70px] px-3 py-2 text-right font-medium text-muted-foreground">호출</th>
                        <th className="w-[90px] px-3 py-2 text-right font-medium text-muted-foreground">평균 지연</th>
                      </tr></thead>
                      <tbody>
                        {usage.top_endpoints.map((e) => {
                          const { method, path } = splitEp(e.endpoint)
                          const linkable = !!onOpenEndpoint && epKeys.has(e.endpoint)
                          return (
                            <tr key={e.endpoint} className="border-t">
                              <td className="px-3 py-2">
                                {linkable ? (
                                  <button type="button" className="flex items-center gap-2 text-left hover:underline" title="엔드포인트 탭에서 열기" onClick={() => onOpenEndpoint!(method, path)}>
                                    <MethodBadge method={method} /><span className="break-all">{path}</span>
                                  </button>
                                ) : (
                                  <span className="break-all" title="현재 엔드포인트 목록에 없는 호출(경로 미지정·스펙 변경 등)">{e.endpoint}</span>
                                )}
                              </td>
                              <td className="px-3 py-2 text-right">{e.count}</td>
                              <td className="px-3 py-2 text-right text-muted-foreground">{e.avg_latency_ms} ms</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </CardContent></Card>

                <Card><CardContent className="pt-4">
                  <p className="mb-2 text-sm font-medium">호출자별 사용량</p>
                  <div className="overflow-hidden rounded-md border">
                    <table className="w-full text-sm">
                      <thead className="bg-muted/40"><tr>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">호출자</th>
                        <th className="w-[90px] px-3 py-2 text-right font-medium text-muted-foreground">호출</th>
                      </tr></thead>
                      <tbody>
                        {usage.top_callers.map((c) => (
                          <tr key={c.name} className="border-t">
                            <td className="px-3 py-2">{c.name}</td>
                            <td className="px-3 py-2 text-right">{c.count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent></Card>
              </div>

              <Card><CardContent className="pt-4">
                <p className="mb-2 text-sm font-medium">최근 호출 <span className="text-xs font-normal text-muted-foreground">(최대 20건 · 쿼리의 키·토큰 값은 *** 로 가려 저장)</span></p>
                <div className="overflow-hidden rounded-md border">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/40"><tr>
                      <th className="w-[70px] px-3 py-2 text-left font-medium text-muted-foreground">메서드</th>
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">URL</th>
                      <th className="w-[110px] px-3 py-2 text-right font-medium text-muted-foreground">상태</th>
                      <th className="w-[80px] px-3 py-2 text-right font-medium text-muted-foreground">지연</th>
                      <th className="w-[120px] px-3 py-2 text-left font-medium text-muted-foreground">호출자</th>
                      <th className="w-[160px] px-3 py-2 text-left font-medium text-muted-foreground">시각</th>
                    </tr></thead>
                    <tbody>
                      {usage.recent.map((r) => (
                        <tr key={r.id} className="border-t align-top">
                          <td className="px-3 py-2">{r.method}</td>
                          <td className="px-3 py-2 break-all">{r.url}</td>
                          <td className={cn("px-3 py-2 text-right", r.ok ? "text-emerald-600" : "text-red-600")} title={r.error || undefined}>
                            {r.status_code ? r.status_code : <span className="cursor-help underline decoration-dotted">네트워크 오류</span>}
                          </td>
                          <td className="px-3 py-2 text-right text-muted-foreground">{r.latency_ms} ms</td>
                          <td className="px-3 py-2 break-all text-muted-foreground">{r.called_by || "-"}</td>
                          <td className="px-3 py-2 text-muted-foreground">{fmtDateTime(r.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent></Card>
            </>
          )}
        </div>
      )}
    </div>
  )
}
