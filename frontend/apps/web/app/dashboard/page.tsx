"use client"

import { datasourceTypeLabel } from "@/features/datasources/datasource-configs"
import { useCallback, useEffect, useState } from "react"
import {
  Activity, BookOpen, Database, Server, Tags, Users, RefreshCw,
} from "lucide-react"
import Link from "next/link"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { DashboardHeader } from "@/components/dashboard-header"
import { authFetch } from "@/features/auth/auth-fetch"
import { fetchSyncRunSummary, type SyncRunSummary } from "@/features/datasources/api"
import { datasourceDatasetsHref, syncRunsHref } from "@/features/datasources/links"
import { usePermissions } from "@/features/permissions/use-permissions"
import { OTHER, SERIES, seriesColor } from "@/components/charts/chart-style"
import { TrendBarChart } from "@/components/charts/trend-bar-chart"
import { DistributionCard, type DistributionItem } from "@/components/model-hub/distribution-card"
import { SegmentedToggle } from "@/components/model-hub/segmented-toggle"
import { TopBarCard, type TopBarItem } from "@/components/model-hub/top-bar-card"
import { datasetHref, datasetListHref } from "@/features/datasets/links"
import { originLabel, originMeta, originRank } from "@/features/datasets/origin"
import { OriginText } from "@/features/datasets/components/origin-text"
import { interactiveCardClass } from "@/components/card-hover"
import { fillDaily, fillHourly } from "@/features/dashboard/trend"

type CatalogStats = {
  total_datasets: number
  total_datasources: number
  total_tags: number
  total_glossary_terms: number
  total_owners: number
  synced_datasets: number
  datasets_by_datasource: { id?: number; key?: string; datasource: string; count: number }[]
  datasets_by_origin: { origin: string; count: number }[]
  datasets_by_datasource_type: { type: string; count: number }[]
  schema_fields_by_datasource: { id?: number; key?: string; datasource: string; count: number }[]
  top_tagged_datasets: { id?: number; name: string; count: number }[]
  daily_datasets_1d: { date: string; count: number }[]
  daily_datasets_7d: { date: string; count: number }[]
  daily_datasets_30d: { date: string; count: number }[]
  recent_datasets: {
    id: number
    name: string
    datasource_name: string
    datasource_type: string
    summary: string | null
    description: string | null
    origin: string
    status: string
    tag_count: number
    owner_count: number
    schema_field_count: number
    updated_at: string
  }[]
}

function shortDate(dateStr: string): string {
  const parts = dateStr.split("-")
  return `${parts[1]}/${parts[2]}`
}

// 24시간 추이 버킷은 UTC ISO 시각으로 온다 — 브라우저 로컬 시각으로 표시
const pad2 = (n: number) => String(n).padStart(2, "0")
function localHour(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}
function localDateHour(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : `${pad2(d.getMonth() + 1)}/${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

type Period = "h24" | "d7" | "d30"
const PERIOD_LABELS: Record<Period, string> = { h24: "24시간", d7: "7일", d30: "30일" }

/** 신규 데이터셋 — 24시간 · 7일 · 30일을 토글로 바꾸는 막대 하나(모델 허브의 활동 추이와 같은 모양). */
function NewDatasetsCard({ stats, className }: { stats: CatalogStats; className?: string }) {
  const [period, setPeriod] = useState<Period>("d7")
  // API 는 데이터가 있는 버킷만 준다 — 빈 시간·날짜를 0 으로 채워 시간축을 연속으로 그린다
  const now = new Date()
  const points = period === "h24"
    ? fillHourly(stats.daily_datasets_1d, now).map((d) => ({ label: localHour(d.date), full: localDateHour(d.date), count: d.count }))
    : fillDaily(period === "d7" ? stats.daily_datasets_7d : stats.daily_datasets_30d, period === "d7" ? 7 : 30, now)
        .map((d) => ({ label: shortDate(d.date), full: d.date, count: d.count }))
  const total = points.reduce((s, p) => s + p.count, 0)

  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-2">
        <div>
          <CardTitle className="text-sm font-medium">신규 데이터셋</CardTitle>
          <p className="mt-0.5 text-xs">최근 {PERIOD_LABELS[period]} · {total.toLocaleString()}개</p>
        </div>
        <SegmentedToggle
          value={period}
          onChange={setPeriod}
          options={(Object.keys(PERIOD_LABELS) as Period[]).map((p) => ({ value: p, label: PERIOD_LABELS[p] }))}
        />
      </CardHeader>
      <CardContent>
        {total === 0 ? (
          <p className="flex h-[200px] items-center justify-center text-sm text-muted-foreground">
            최근 {PERIOD_LABELS[period]} 동안 새로 등록된 데이터셋이 없습니다.
          </p>
        ) : (
          <TrendBarChart
            labels={points.map((p) => p.label)}
            fullLabels={points.map((p) => p.full)}
            series={[{ key: "count", name: "신규 데이터셋", color: SERIES[0], data: points.map((p) => p.count), unit: "개" }]}
          />
        )}
      </CardContent>
    </Card>
  )
}

export default function DashboardPage() {
  const [stats, setStats] = useState<CatalogStats | null>(null)
  // 수집 실패 카드 — 통계와 따로 불러온다(실패해도 대시보드는 그대로)
  const [syncSummary, setSyncSummary] = useState<SyncRunSummary | null>(null)
  const { canUseFeature, isMenuAllowed } = usePermissions()
  // 데이터셋 메뉴를 막은 역할에게는 데이터셋 화면으로 가는 링크 대신 글자만 보인다(DEF-158)
  const datasetsOpen = isMenuAllowed("datasets")
  useEffect(() => {
    fetchSyncRunSummary().then(setSyncSummary).catch(() => setSyncSummary(null))
  }, [])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchStats = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      // 7일·30일 추이를 브라우저 시간대 날짜로 묶도록 IANA 시간대를 함께 보낸다
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
      const res = await authFetch(`/api/v1/catalog/stats${tz ? `?tz=${encodeURIComponent(tz)}` : ""}`)
      if (res.ok) setStats(await res.json())
      else setError(`통계를 불러오지 못했습니다 (HTTP ${res.status})`)
    } catch {
      setError("통계를 불러오지 못했습니다 (네트워크 오류)")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchStats() }, [fetchStats])

  // 조회 실패 시 스켈레톤을 계속 보여주지 않고 오류 문구 + 다시 시도
  if (!loading && (error || !stats)) {
    return (
      <>
        <DashboardHeader title="데이터 카탈로그" />
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-4">
          <p className="text-sm text-destructive">{error ?? "통계를 불러오지 못했습니다."}</p>
          <Button variant="outline" size="sm" onClick={() => fetchStats()}>
            다시 시도
          </Button>
        </div>
      </>
    )
  }

  if (loading || !stats) {
    return (
      <>
        <DashboardHeader title="데이터 카탈로그" />
        <div className="flex flex-1 flex-col gap-4 p-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-7">
            {[...Array(7)].map((_, i) => (
              <Card key={i}><CardContent className="pt-6"><div className="h-16 animate-pulse bg-muted rounded" /></CardContent></Card>
            ))}
          </div>
        </div>
      </>
    )
  }

  // 데이터 소스 유형 — 상위 5개 + 나머지는 "기타"(회색). 색은 순위가 아니라 순서 고정 팔레트
  const typeRaw = stats.datasets_by_datasource_type.filter((d) => d.count > 0)
  const typeItems: DistributionItem[] = [
    ...typeRaw.slice(0, typeRaw.length <= 6 ? 6 : 5).map((d, i) => ({
      key: d.type, label: datasourceTypeLabel(d.type), count: d.count, color: seriesColor(i),
    })),
    ...(typeRaw.length > 6
      ? [{ key: "__other__", label: "기타", count: typeRaw.slice(5).reduce((s, d) => s + d.count, 0), color: OTHER }]
      : []),
  ]
  // 환경 — 값마다 색 고정(필터로 하나가 빠져도 나머지 색이 바뀌지 않음), 누르면 그 환경으로 거른 목록
  const originItems: DistributionItem[] = [...stats.datasets_by_origin]
    .sort((a, b) => originRank(a.origin) - originRank(b.origin))
    .map((d) => ({
      key: d.origin, label: originLabel(d.origin), count: d.count,
      color: originMeta(d.origin).color, href: datasetsOpen ? datasetListHref({ origin: [d.origin] }) : undefined,
    }))

  const datasourceItems: TopBarItem[] = stats.datasets_by_datasource.map((d) => ({
    name: d.datasource, value: d.count, display: `${d.count.toLocaleString()}개`,
    href: d.key && datasetsOpen ? datasourceDatasetsHref(d.key) : undefined,
  }))
  const schemaItems: TopBarItem[] = stats.schema_fields_by_datasource.map((d) => ({
    name: d.datasource, value: d.count, display: `${d.count.toLocaleString()}개`,
    href: d.key && datasetsOpen ? datasourceDatasetsHref(d.key) : undefined,
  }))
  const tagItems: TopBarItem[] = stats.top_tagged_datasets.map((d) => ({
    name: d.name, value: d.count, display: `태그 ${d.count}개`,
    href: d.id != null && datasetsOpen ? datasetHref(d.id) : undefined,
  }))

  return (
    <>
      <DashboardHeader title="데이터 카탈로그" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        {/* Summary Cards */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-7">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">데이터셋</CardTitle>
              <Database className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{stats.total_datasets}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">데이터 소스</CardTitle>
              <Server className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{stats.total_datasources}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">태그</CardTitle>
              <Tags className="h-4 w-4 text-blue-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-blue-500">{stats.total_tags}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">용어집</CardTitle>
              <BookOpen className="h-4 w-4 text-purple-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-purple-500">{stats.total_glossary_terms}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">소유자</CardTitle>
              <Users className="h-4 w-4 text-orange-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-orange-500">{stats.total_owners}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">동기화 완료</CardTitle>
              <RefreshCw className="h-4 w-4 text-emerald-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-emerald-500">{stats.synced_datasets}</div>
              <p className="text-xs">
                전체 {stats.total_datasets}개 중
              </p>
            </CardContent>
          </Card>

          {/* 메타데이터 수집 화면을 볼 수 있는 사람(관리자·수집 권한)만 눌러서 이동(TODO-074) */}
          <SyncFailureCard summary={syncSummary} clickable={canUseFeature("datasources.sync")} />
        </div>

        {/* 차트 1줄: 구성 — 데이터 소스 유형 · 환경 · 데이터셋이 많은 데이터 소스 */}
        <div className="grid gap-4 lg:grid-cols-3">
          <DistributionCard title="데이터 소스 유형 분포" items={typeItems} empty="데이터 없음" />
          <DistributionCard title="환경별 분포" items={originItems} empty="데이터 없음" />
          <TopBarCard title="데이터셋 수 상위 데이터 소스" items={datasourceItems} empty="데이터 없음" />
        </div>

        {/* 차트 2줄: 신규 데이터셋 추이(기간 토글) · 스키마 필드 · 태그 */}
        <div className="grid gap-4 lg:grid-cols-4">
          <NewDatasetsCard stats={stats} className="lg:col-span-2" />
          <TopBarCard title="데이터 소스별 스키마 필드 수" items={schemaItems} empty="스키마 데이터가 없습니다" />
          <TopBarCard title="태그 수 상위 데이터셋" items={tagItems} empty="태그된 데이터셋이 없습니다" />
        </div>

        {/* Recent Datasets Table */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">최근 데이터셋</CardTitle>
          </CardHeader>
          <CardContent>
            {stats.recent_datasets.length > 0 ? (
              <div className="border rounded-md overflow-auto">
                <table className="w-full text-sm">
                  <thead className="bg-muted/60">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">이름</th>
                      <th className="px-3 py-2 text-center font-medium w-36">데이터 소스</th>
                      <th className="px-3 py-2 text-center font-medium w-28">환경</th>
                      <th className="px-3 py-2 text-center font-medium w-16">태그</th>
                      <th className="px-3 py-2 text-center font-medium w-16">소유자</th>
                      <th className="px-3 py-2 text-center font-medium w-16">필드</th>
                      <th className="px-3 py-2 text-center font-medium w-28">수정일</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {stats.recent_datasets.map((ds) => (
                      <tr key={ds.id} className="hover:bg-muted/30">
                        <td className="px-3 py-2">
                          {datasetsOpen ? (
                            <Link href={datasetHref(ds.id)} className="font-medium hover:underline">
                              {ds.name}
                            </Link>
                          ) : (
                            <span className="font-medium">{ds.name}</span>
                          )}
                          {ds.summary && (
                            <p className="text-xs truncate max-w-[400px]">{ds.summary}</p>
                          )}
                        </td>
                        <td className="px-3 py-2 text-center">{ds.datasource_name}</td>
                        <td className="px-3 py-2 text-center whitespace-nowrap">
                          <OriginText origin={ds.origin} />
                        </td>
                        <td className="px-3 py-2 text-center">{ds.tag_count}</td>
                        <td className="px-3 py-2 text-center">{ds.owner_count}</td>
                        <td className="px-3 py-2 text-center">{ds.schema_field_count}</td>
                        <td className="px-3 py-2 text-center">
                          {(() => {
                            const d = new Date(ds.updated_at)
                            const pad = (n: number) => String(n).padStart(2, "0")
                            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
                          })()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8">아직 데이터셋이 없습니다</p>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  )
}


// 최근 24시간 수집 실패 — 관리자는 눌러서 메타데이터 수집 화면(실패 필터)으로 간다(설계 §7.5)
function SyncFailureCard({ summary, clickable }: { summary: SyncRunSummary | null; clickable: boolean }) {
  const failed = summary?.failed_24h ?? null
  const body = (
    <Card className={clickable ? `h-full ${interactiveCardClass}` : "h-full"}>
      <CardHeader className="flex flex-row items-center justify-between gap-1.5 pb-2">
        {/* 카드 7개가 한 줄일 때(1440 폭) 제목이 두 줄로 꺾여 숫자가 밀리지 않게 — 기간은 작은 글씨로 붙이고 한 줄 고정(DEF-180) */}
        <CardTitle className="min-w-0 truncate text-sm font-medium">
          수집 실패 <span className="text-xs font-normal text-muted-foreground">24시간</span>
        </CardTitle>
        <Activity className={`h-4 w-4 shrink-0 ${failed ? "text-destructive" : "text-muted-foreground"}`} />
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-bold ${failed ? "text-destructive" : ""}`}>{failed ?? "-"}</div>
        <p className="text-xs text-muted-foreground">
          {summary ? `성공 ${summary.succeeded_24h} · 진행 중 ${summary.active}` : "수집 이력 없음"}
        </p>
      </CardContent>
    </Card>
  )
  return clickable ? <Link href={syncRunsHref({ status: "PROBLEM", period: "24h" })}>{body}</Link> : body
}
