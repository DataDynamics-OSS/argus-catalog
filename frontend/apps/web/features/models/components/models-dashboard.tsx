"use client"

import { useCallback, useEffect, useState } from "react"
import { Box, GitBranch, CheckCircle2, AlertTriangle, Activity } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Highchart } from "@/components/charts/highchart"
import { fetchModelStats, type ModelStats } from "../api"

// 모든 Highcharts 차트의 공통 옵션 (폰트·여백·범례)
const COMMON_CHART_OPTS = {
  chart: {
    style: { fontFamily: "var(--font-pretendard), system-ui, sans-serif" },
    spacing: [8, 8, 8, 8] as [number, number, number, number],
  },
  title: { text: undefined as string | undefined },
  legend: { enabled: false },
} as const

/** Format bytes to human-readable string (tooltip 용 — "1.5 KB"). */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  if (bytes < 1024 ** 4) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  return `${(bytes / 1024 ** 4).toFixed(1)} TB`
}

/** 축 라벨용 약식 포맷 — 소수점 없이 "1K / 5M / 2G / 3T" 식. */
function formatSizeAxis(bytes: number): string {
  if (bytes < 1024) return `${Math.round(bytes)}B`
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)}K`
  if (bytes < 1024 ** 3) return `${Math.round(bytes / 1024 ** 2)}M`
  if (bytes < 1024 ** 4) return `${Math.round(bytes / 1024 ** 3)}G`
  return `${Math.round(bytes / 1024 ** 4)}T`
}

/** Shorten model name for chart labels. */
function shortName(name: string): string {
  const parts = name.split(".")
  return parts[parts.length - 1] ?? name
}

/** Format date label for line chart. */
function shortDate(dateStr: string): string {
  const parts = dateStr.split("-")
  return `${parts[1]}/${parts[2]}`
}

const STATUS_COLORS: Record<string, string> = {
  READY: "#3b82f6",
  PENDING: "#a1a1aa",
  FAILED: "#ef4444",
}

// 버전 상태 코드 → 화면 표시용 한글 라벨
const STATUS_LABELS: Record<string, string> = {
  READY: "준비됨",
  PENDING: "대기",
  FAILED: "실패",
}

export function ModelsDashboard() {
  const [stats, setStats] = useState<ModelStats | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      setStats(await fetchModelStats())
    } catch (err) {
      console.error("Failed to fetch model stats:", err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  if (loading || !stats) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {[...Array(5)].map((_, i) => (
          <Card key={i}>
            <CardContent className="pt-6">
              <div className="h-16 animate-pulse bg-muted rounded" />
            </CardContent>
          </Card>
        ))}
      </div>
    )
  }

  const sizeData = stats.model_sizes.slice(0, 5).map((m) => ({
    name: shortName(m.model_name),
    fullName: m.model_name,
    size: m.model_size_bytes,
    sizeLabel: formatSize(m.model_size_bytes),
  }))

  const versionData = stats.versions_per_model.slice(0, 5).map((m) => ({
    name: shortName(m.model_name),
    fullName: m.model_name,
    versions: m.version_count,
  }))

  const pieData = stats.status_distribution.filter((s) => s.count > 0)

  const daily1dData = stats.daily_download_1d.map((d) => ({
    date: d.date,
    fullDate: d.date,
    count: d.count,
  }))

  const daily7dData = stats.daily_download_7d.map((d) => ({
    date: shortDate(d.date),
    fullDate: d.date,
    count: d.count,
  }))

  const daily30dData = stats.daily_download_30d.map((d) => ({
    date: shortDate(d.date),
    fullDate: d.date,
    count: d.count,
  }))

  const pub1dData = stats.daily_publish_1d.map((d) => ({
    date: d.date,
    fullDate: d.date,
    count: d.count,
  }))

  const pub7dData = stats.daily_publish_7d.map((d) => ({
    date: shortDate(d.date),
    fullDate: d.date,
    count: d.count,
  }))

  const pub30dData = stats.daily_publish_30d.map((d) => ({
    date: shortDate(d.date),
    fullDate: d.date,
    count: d.count,
  }))

  return (
    <div className="space-y-4">
      {/* Summary Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">전체 모델</CardTitle>
            <Box className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats.total_models}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">전체 버전</CardTitle>
            <GitBranch className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats.total_versions}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">준비됨</CardTitle>
            <CheckCircle2 className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-blue-500">{stats.ready_models}</div>
            <p className="text-xs text-muted-foreground mt-1">
              버전 {stats.ready_versions}개
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">대기 / 실패</CardTitle>
            <AlertTriangle className="h-4 w-4 text-orange-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              <span className="text-zinc-400">{stats.pending_count}</span>
              <span className="text-muted-foreground mx-1">/</span>
              <span className="text-red-500">{stats.failed_count}</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">전체 다운로드</CardTitle>
            <Activity className="h-4 w-4 text-emerald-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-emerald-500">{stats.total_download}</div>
          </CardContent>
        </Card>
      </div>

      {/* Charts Row 1 */}
      <div className="grid gap-4 lg:grid-cols-3">
        {/* Pie: Version Status */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">버전 상태</CardTitle>
          </CardHeader>
          <CardContent>
            {pieData.length > 0 ? (
              <div className="flex items-center gap-4">
                <Highchart
                  height={140}
                  className="w-[140px]"
                  options={{
                    ...COMMON_CHART_OPTS,
                    chart: { ...COMMON_CHART_OPTS.chart, type: "pie" },
                    tooltip: { pointFormat: "<b>{point.y}</b>" },
                    plotOptions: {
                      pie: { innerSize: "58%", dataLabels: { enabled: false }, borderWidth: 2 },
                    },
                    series: [{
                      type: "pie",
                      data: pieData.map((s) => ({
                        name: STATUS_LABELS[s.status] ?? s.status,
                        y: s.count,
                        color: STATUS_COLORS[s.status] || "#a1a1aa",
                      })),
                    }],
                  }}
                />
                <div className="space-y-2">
                  {stats.status_distribution.map((s) => (
                    <div key={s.status} className="flex items-center gap-2 text-sm">
                      <span
                        className="h-3 w-3 rounded-full"
                        style={{ backgroundColor: STATUS_COLORS[s.status] || "#a1a1aa" }}
                      />
                      <span className="text-muted-foreground">{STATUS_LABELS[s.status] ?? s.status}</span>
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

        {/* Bar: Model Size */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">모델 크기 (상위 5개)</CardTitle>
          </CardHeader>
          <CardContent>
            {sizeData.length > 0 ? (
              <Highchart
                height={160}
                options={{
                  ...COMMON_CHART_OPTS,
                  chart: { ...COMMON_CHART_OPTS.chart, type: "bar" },
                  xAxis: { categories: sizeData.map((d) => d.name), labels: { style: { fontSize: "11px" } } },
                  yAxis: {
                    title: { text: undefined },
                    labels: { style: { fontSize: "11px" }, formatter() { return formatSizeAxis(this.value as number) } },
                  },
                  tooltip: {
                    formatter() {
                      const full = sizeData[this.index!]?.fullName ?? this.x
                      return `<b>${full}</b><br/>${formatSize(this.y as number)}`
                    },
                  },
                  series: [{ type: "bar", data: sizeData.map((d) => d.size), color: "#3b82f6" }],
                }}
              />
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8">데이터 없음</p>
            )}
          </CardContent>
        </Card>

        {/* Bar: Versions per Model */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">모델별 버전 수 (상위 5개)</CardTitle>
          </CardHeader>
          <CardContent>
            {versionData.length > 0 ? (
              <Highchart
                height={160}
                options={{
                  ...COMMON_CHART_OPTS,
                  chart: { ...COMMON_CHART_OPTS.chart, type: "bar" },
                  xAxis: { categories: versionData.map((d) => d.name), labels: { style: { fontSize: "11px" } } },
                  yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                  tooltip: {
                    formatter() {
                      const full = versionData[this.index!]?.fullName ?? this.x
                      return `<b>${full}</b><br/>버전: ${this.y}`
                    },
                  },
                  series: [{ type: "bar", data: versionData.map((d) => d.versions), color: "#8b5cf6" }],
                }}
              />
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8">데이터 없음</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Charts Row 2: Download Trends */}
      <div className="grid gap-4 lg:grid-cols-3">
        {/* Hourly Download (1 day) */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">시간별 다운로드 (24시간)</CardTitle>
          </CardHeader>
          <CardContent>
            {daily1dData.length > 0 ? (
              <Highchart
                height={160}
                options={{
                  ...COMMON_CHART_OPTS,
                  chart: { ...COMMON_CHART_OPTS.chart, type: "column" },
                  xAxis: { categories: daily1dData.map((d) => d.date), labels: { style: { fontSize: "10px" } } },
                  yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                  tooltip: { pointFormat: "다운로드 수: <b>{point.y}</b>" },
                  series: [{ type: "column", data: daily1dData.map((d) => d.count), color: "#10b981" }],
                }}
              />
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8">아직 다운로드 데이터가 없습니다</p>
            )}
          </CardContent>
        </Card>

        {/* Daily Download (7 days) */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">일별 다운로드 (7일)</CardTitle>
          </CardHeader>
          <CardContent>
            {daily7dData.length > 0 ? (
              <Highchart
                height={160}
                options={{
                  ...COMMON_CHART_OPTS,
                  chart: { ...COMMON_CHART_OPTS.chart, type: "line" },
                  xAxis: { categories: daily7dData.map((d) => d.date), labels: { style: { fontSize: "10px" } } },
                  yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                  tooltip: {
                    formatter() {
                      const full = daily7dData[this.index!]?.fullDate ?? this.x
                      return `<b>${full}</b><br/>다운로드 수: ${this.y}`
                    },
                  },
                  series: [{ type: "line", data: daily7dData.map((d) => d.count), color: "#f59e0b" }],
                }}
              />
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8">아직 다운로드 데이터가 없습니다</p>
            )}
          </CardContent>
        </Card>

        {/* Daily Download (30 days) */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">일별 다운로드 (30일)</CardTitle>
          </CardHeader>
          <CardContent>
            {daily30dData.length > 0 ? (
              <Highchart
                height={160}
                options={{
                  ...COMMON_CHART_OPTS,
                  chart: { ...COMMON_CHART_OPTS.chart, type: "line" },
                  xAxis: { categories: daily30dData.map((d) => d.date), labels: { style: { fontSize: "10px" } } },
                  yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                  tooltip: {
                    formatter() {
                      const full = daily30dData[this.index!]?.fullDate ?? this.x
                      return `<b>${full}</b><br/>다운로드 수: ${this.y}`
                    },
                  },
                  series: [{ type: "line", data: daily30dData.map((d) => d.count), color: "#3b82f6" }],
                }}
              />
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8">아직 다운로드 데이터가 없습니다</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Charts Row 3: Publish Trends */}
      <div className="grid gap-4 lg:grid-cols-3">
        {/* Hourly Publish (1 day) */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">시간별 게시 (24시간)</CardTitle>
          </CardHeader>
          <CardContent>
            {pub1dData.length > 0 ? (
              <Highchart
                height={160}
                options={{
                  ...COMMON_CHART_OPTS,
                  chart: { ...COMMON_CHART_OPTS.chart, type: "column" },
                  xAxis: { categories: pub1dData.map((d) => d.date), labels: { style: { fontSize: "10px" } } },
                  yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                  tooltip: { pointFormat: "게시 수: <b>{point.y}</b>" },
                  series: [{ type: "column", data: pub1dData.map((d) => d.count), color: "#a855f7" }],
                }}
              />
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8">아직 게시 데이터가 없습니다</p>
            )}
          </CardContent>
        </Card>

        {/* Daily Publish (7 days) */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">일별 게시 (7일)</CardTitle>
          </CardHeader>
          <CardContent>
            {pub7dData.length > 0 ? (
              <Highchart
                height={160}
                options={{
                  ...COMMON_CHART_OPTS,
                  chart: { ...COMMON_CHART_OPTS.chart, type: "line" },
                  xAxis: { categories: pub7dData.map((d) => d.date), labels: { style: { fontSize: "10px" } } },
                  yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                  tooltip: {
                    formatter() {
                      const full = pub7dData[this.index!]?.fullDate ?? this.x
                      return `<b>${full}</b><br/>게시 수: ${this.y}`
                    },
                  },
                  series: [{ type: "line", data: pub7dData.map((d) => d.count), color: "#d946ef" }],
                }}
              />
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8">아직 게시 데이터가 없습니다</p>
            )}
          </CardContent>
        </Card>

        {/* Daily Publish (30 days) */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">일별 게시 (30일)</CardTitle>
          </CardHeader>
          <CardContent>
            {pub30dData.length > 0 ? (
              <Highchart
                height={160}
                options={{
                  ...COMMON_CHART_OPTS,
                  chart: { ...COMMON_CHART_OPTS.chart, type: "line" },
                  xAxis: { categories: pub30dData.map((d) => d.date), labels: { style: { fontSize: "10px" } } },
                  yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                  tooltip: {
                    formatter() {
                      const full = pub30dData[this.index!]?.fullDate ?? this.x
                      return `<b>${full}</b><br/>게시 수: ${this.y}`
                    },
                  },
                  series: [{ type: "line", data: pub30dData.map((d) => d.count), color: "#8b5cf6" }],
                }}
              />
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8">아직 게시 데이터가 없습니다</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
