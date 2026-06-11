"use client"

import { useCallback, useEffect, useState } from "react"
import {
  BookOpen, Database, Server, Tags, Users, RefreshCw,
} from "lucide-react"
import Link from "next/link"

import { Badge } from "@workspace/ui/components/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { DashboardHeader } from "@/components/dashboard-header"
import { Highchart } from "@/components/charts/highchart"

type CatalogStats = {
  total_datasets: number
  total_datasources: number
  total_tags: number
  total_glossary_terms: number
  total_owners: number
  synced_datasets: number
  datasets_by_datasource: { datasource: string; count: number }[]
  datasets_by_origin: { origin: string; count: number }[]
  datasets_by_datasource_type: { type: string; count: number }[]
  schema_fields_by_datasource: { datasource: string; count: number }[]
  top_tagged_datasets: { name: string; count: number }[]
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

function shortName(name: string): string {
  return name.length > 18 ? name.slice(0, 16) + "..." : name
}

function shortDate(dateStr: string): string {
  const parts = dateStr.split("-")
  return `${parts[1]}/${parts[2]}`
}

const DATASOURCE_COLORS = [
  "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
  "#ec4899", "#06b6d4", "#f97316", "#14b8a6", "#6366f1",
]

const ORIGIN_COLORS: Record<string, string> = {
  PROD: "#3b82f6",
  DEV: "#f59e0b",
  STAGING: "#10b981",
}

// MLflow 모델 / OCI 모델 허브 대시보드와 동일한 Highcharts 공통 옵션.
// 폰트는 페이지의 Pretendard 를 상속하고, 범례·타이틀은 카드 헤더가 대체.
const COMMON_CHART_OPTS = {
  chart: {
    style: { fontFamily: "var(--font-pretendard), system-ui, sans-serif" },
    spacing: [8, 8, 8, 8] as [number, number, number, number],
  },
  title: { text: undefined as string | undefined },
  legend: { enabled: false },
} as const

export default function DashboardPage() {
  const [stats, setStats] = useState<CatalogStats | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchStats = useCallback(async () => {
    try {
      setLoading(true)
      const res = await fetch("/api/v1/catalog/stats")
      if (res.ok) setStats(await res.json())
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchStats() }, [fetchStats])

  if (loading || !stats) {
    return (
      <>
        <DashboardHeader title="데이터 카탈로그" />
        <div className="flex flex-1 flex-col gap-4 p-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
            {[...Array(6)].map((_, i) => (
              <Card key={i}><CardContent className="pt-6"><div className="h-16 animate-pulse bg-muted rounded" /></CardContent></Card>
            ))}
          </div>
        </div>
      </>
    )
  }

  const datasourceBarData = stats.datasets_by_datasource.slice(0, 5).map((d) => ({
    name: shortName(d.datasource), fullName: d.datasource, count: d.count,
  }))

  // Show top 5 datasource types, group the rest as "Others"
  const datasourceTypeRaw = stats.datasets_by_datasource_type.filter((d) => d.count > 0)
  const datasourceTypeData = datasourceTypeRaw.length <= 6
    ? datasourceTypeRaw
    : [
        ...datasourceTypeRaw.slice(0, 5),
        { type: "기타", count: datasourceTypeRaw.slice(5).reduce((s, d) => s + d.count, 0) },
      ]
  const originData = stats.datasets_by_origin.filter((d) => d.count > 0)

  const schemaData = stats.schema_fields_by_datasource.map((d) => ({
    name: shortName(d.datasource), fullName: d.datasource, count: d.count,
  }))

  const tagData = stats.top_tagged_datasets.map((d) => ({
    name: shortName(d.name), fullName: d.name, count: d.count,
  }))

  const ds1dData = stats.daily_datasets_1d.map((d) => ({ date: d.date, fullDate: d.date, count: d.count }))
  const ds7dData = stats.daily_datasets_7d.map((d) => ({ date: shortDate(d.date), fullDate: d.date, count: d.count }))
  const ds30dData = stats.daily_datasets_30d.map((d) => ({ date: shortDate(d.date), fullDate: d.date, count: d.count }))

  return (
    <>
      <DashboardHeader title="데이터 카탈로그" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        {/* Summary Cards */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
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
              <p className="text-xs text-muted-foreground">
                전체 {stats.total_datasets}개 중
              </p>
            </CardContent>
          </Card>
        </div>

        {/* Charts Row 1: Distribution */}
        <div className="grid gap-4 lg:grid-cols-3">
          {/* Donut: Datasource Type */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">데이터 소스 유형 분포</CardTitle>
            </CardHeader>
            <CardContent>
              {datasourceTypeData.length > 0 ? (
                <div className="flex items-center gap-4">
                  <Highchart
                    height={160}
                    className="w-[160px] shrink-0"
                    options={{
                      ...COMMON_CHART_OPTS,
                      chart: { ...COMMON_CHART_OPTS.chart, type: "pie" },
                      tooltip: { pointFormat: "{point.y}" },
                      plotOptions: {
                        pie: { innerSize: "55%", dataLabels: { enabled: false }, borderWidth: 2 },
                      },
                      series: [{
                        type: "pie",
                        data: datasourceTypeData.map((s, i) => ({
                          name: s.type,
                          y: s.count,
                          color: DATASOURCE_COLORS[i % DATASOURCE_COLORS.length],
                        })),
                      }],
                    }}
                  />
                  <div className="space-y-1.5">
                    {datasourceTypeData.map((s, i) => (
                      <div key={s.type} className="flex items-center gap-2 text-sm">
                        <span className="h-3 w-3 rounded-full shrink-0" style={{ backgroundColor: DATASOURCE_COLORS[i % DATASOURCE_COLORS.length] }} />
                        <span className="text-muted-foreground truncate">{s.type}</span>
                        <span className="font-medium ml-auto">{s.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground text-center py-8">데이터 없음</p>
              )}
            </CardContent>
          </Card>

          {/* Donut: Origin */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">환경별 분포</CardTitle>
            </CardHeader>
            <CardContent>
              {originData.length > 0 ? (
                <div className="flex items-center gap-4">
                  <Highchart
                    height={160}
                    className="w-[160px] shrink-0"
                    options={{
                      ...COMMON_CHART_OPTS,
                      chart: { ...COMMON_CHART_OPTS.chart, type: "pie" },
                      tooltip: { pointFormat: "{point.y}" },
                      plotOptions: {
                        pie: { innerSize: "55%", dataLabels: { enabled: false }, borderWidth: 2 },
                      },
                      series: [{
                        type: "pie",
                        data: originData.map((s) => ({
                          name: s.origin,
                          y: s.count,
                          color: ORIGIN_COLORS[s.origin] || "#a1a1aa",
                        })),
                      }],
                    }}
                  />
                  <div className="space-y-2">
                    {originData.map((s) => (
                      <div key={s.origin} className="flex items-center gap-2 text-sm">
                        <span className="h-3 w-3 rounded-full shrink-0" style={{ backgroundColor: ORIGIN_COLORS[s.origin] || "#a1a1aa" }} />
                        <span className="text-muted-foreground">{s.origin}</span>
                        <span className="font-medium ml-auto">{s.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground text-center py-8">데이터 없음</p>
              )}
            </CardContent>
          </Card>

          {/* Bar: Top Datasources */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">데이터셋 수 상위 데이터 소스 (상위 5위)</CardTitle>
            </CardHeader>
            <CardContent>
              {datasourceBarData.length > 0 ? (
                <Highchart
                  height={180}
                  options={{
                    ...COMMON_CHART_OPTS,
                    chart: { ...COMMON_CHART_OPTS.chart, type: "bar" },
                    xAxis: { categories: datasourceBarData.map((d) => d.name), labels: { style: { fontSize: "11px" } } },
                    yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                    tooltip: {
                      formatter() {
                        const full = datasourceBarData[this.index!]?.fullName ?? this.x
                        return `<b>${full}</b><br/>${this.y}`
                      },
                    },
                    series: [{ type: "bar", data: datasourceBarData.map((d) => d.count), color: "#3b82f6" }],
                  }}
                />
              ) : (
                <p className="text-sm text-muted-foreground text-center py-8">데이터 없음</p>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Charts Row 2: Dataset Growth Trends */}
        <div className="grid gap-4 lg:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">시간별 신규 데이터셋 (최근 24시간)</CardTitle>
            </CardHeader>
            <CardContent>
              {ds1dData.length > 0 ? (
                <Highchart
                  height={180}
                  options={{
                    ...COMMON_CHART_OPTS,
                    chart: { ...COMMON_CHART_OPTS.chart, type: "column" },
                    xAxis: { categories: ds1dData.map((d) => d.date), labels: { style: { fontSize: "10px" } } },
                    yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                    tooltip: { pointFormat: "신규 데이터셋: <b>{point.y}</b>" },
                    series: [{ type: "column", data: ds1dData.map((d) => d.count), color: "#10b981" }],
                  }}
                />
              ) : (
                <p className="text-sm text-muted-foreground text-center py-8">아직 데이터가 없습니다</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">일별 신규 데이터셋 (최근 7일)</CardTitle>
            </CardHeader>
            <CardContent>
              {ds7dData.length > 0 ? (
                <Highchart
                  height={180}
                  options={{
                    ...COMMON_CHART_OPTS,
                    chart: { ...COMMON_CHART_OPTS.chart, type: "spline" },
                    xAxis: { categories: ds7dData.map((d) => d.date), labels: { style: { fontSize: "10px" } } },
                    yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                    tooltip: {
                      formatter() {
                        const full = ds7dData[this.index!]?.fullDate ?? this.x
                        return `<b>${full}</b><br/>신규 데이터셋: ${this.y}`
                      },
                    },
                    series: [{ type: "spline", data: ds7dData.map((d) => d.count), color: "#f59e0b" }],
                  }}
                />
              ) : (
                <p className="text-sm text-muted-foreground text-center py-8">아직 데이터가 없습니다</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">일별 신규 데이터셋 (최근 30일)</CardTitle>
            </CardHeader>
            <CardContent>
              {ds30dData.length > 0 ? (
                <Highchart
                  height={180}
                  options={{
                    ...COMMON_CHART_OPTS,
                    chart: { ...COMMON_CHART_OPTS.chart, type: "spline" },
                    xAxis: { categories: ds30dData.map((d) => d.date), labels: { style: { fontSize: "10px" } } },
                    yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                    tooltip: {
                      formatter() {
                        const full = ds30dData[this.index!]?.fullDate ?? this.x
                        return `<b>${full}</b><br/>신규 데이터셋: ${this.y}`
                      },
                    },
                    series: [{ type: "spline", data: ds30dData.map((d) => d.count), color: "#3b82f6" }],
                  }}
                />
              ) : (
                <p className="text-sm text-muted-foreground text-center py-8">아직 데이터가 없습니다</p>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Charts Row 3: Schema & Tags */}
        <div className="grid gap-4 lg:grid-cols-2">
          {/* Bar: Schema Fields by Datasource */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">데이터 소스별 스키마 필드 수</CardTitle>
            </CardHeader>
            <CardContent>
              {schemaData.length > 0 ? (
                <Highchart
                  height={180}
                  options={{
                    ...COMMON_CHART_OPTS,
                    chart: { ...COMMON_CHART_OPTS.chart, type: "bar" },
                    xAxis: { categories: schemaData.map((d) => d.name), labels: { style: { fontSize: "11px" } } },
                    yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                    tooltip: {
                      formatter() {
                        const full = schemaData[this.index!]?.fullName ?? this.x
                        return `<b>${full}</b><br/>${this.y}`
                      },
                    },
                    series: [{ type: "bar", data: schemaData.map((d) => d.count), color: "#8b5cf6" }],
                  }}
                />
              ) : (
                <p className="text-sm text-muted-foreground text-center py-8">스키마 데이터가 없습니다</p>
              )}
            </CardContent>
          </Card>

          {/* Bar: Top Tagged Datasets */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">태그 수 상위 데이터셋</CardTitle>
            </CardHeader>
            <CardContent>
              {tagData.length > 0 ? (
                <Highchart
                  height={180}
                  options={{
                    ...COMMON_CHART_OPTS,
                    chart: { ...COMMON_CHART_OPTS.chart, type: "bar" },
                    xAxis: { categories: tagData.map((d) => d.name), labels: { style: { fontSize: "11px" } } },
                    yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                    tooltip: {
                      formatter() {
                        const full = tagData[this.index!]?.fullName ?? this.x
                        return `<b>${full}</b><br/>태그 ${this.y}개`
                      },
                    },
                    series: [{ type: "bar", data: tagData.map((d) => d.count), color: "#ec4899" }],
                  }}
                />
              ) : (
                <p className="text-sm text-muted-foreground text-center py-8">태그된 데이터셋이 없습니다</p>
              )}
            </CardContent>
          </Card>
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
                      <th className="px-3 py-2 text-center font-medium w-20">환경</th>
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
                          <Link
                            href={`/dashboard/datasets/${ds.id}`}
                            className="font-medium hover:underline"
                          >
                            {ds.name}
                          </Link>
                          {ds.summary && (
                            <p className="text-xs text-muted-foreground truncate max-w-[400px]">{ds.summary}</p>
                          )}
                        </td>
                        <td className="px-3 py-2 text-center text-muted-foreground">{ds.datasource_name}</td>
                        <td className="px-3 py-2 text-center">
                          <Badge variant="outline" className="text-xs">{ds.origin}</Badge>
                        </td>
                        <td className="px-3 py-2 text-center text-muted-foreground">{ds.tag_count}</td>
                        <td className="px-3 py-2 text-center text-muted-foreground">{ds.owner_count}</td>
                        <td className="px-3 py-2 text-center text-muted-foreground">{ds.schema_field_count}</td>
                        <td className="px-3 py-2 text-center text-muted-foreground">
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
