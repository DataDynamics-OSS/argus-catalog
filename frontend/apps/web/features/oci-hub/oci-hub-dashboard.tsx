"use client"

import { useCallback, useEffect, useState } from "react"
import { Box, Download, GitBranch, Globe, Upload, Activity } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Highchart } from "@/components/charts/highchart"
import { fetchOciHubStats, type OciHubStats } from "./api"

function formatSize(bytes: number): string {
  if (!bytes) return "-"
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
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

function shortName(name: string): string {
  return name.length > 20 ? name.slice(0, 18) + "..." : name
}

function shortDate(dateStr: string): string {
  const parts = dateStr.split("-")
  return `${parts[1]}/${parts[2]}`
}

const SOURCE_COLORS: Record<string, string> = {
  huggingface: "#3b82f6",
  my: "#f97316",
  file: "#10b981",
  local: "#10b981",
  unknown: "#a1a1aa",
}

// 출처 코드 → 화면 표시용 라벨 (legend / tooltip / 상세 페이지 공용).
// "HuggingFace" 는 고유명사라 그대로, 나머지는 한글.
//   - "my"     : 사용자가 SDK push / 콘솔로 직접 등록한 자체 모델
//   - "local"  : 에어갭 환경에서 import-local 로 등록된 모델
//   - "file"   : 파일 업로드로 등록된 모델
export const SOURCE_LABELS: Record<string, string> = {
  huggingface: "HuggingFace",
  my: "내 모델",
  file: "파일",
  local: "로컬",
  unknown: "기타",
}

// 모든 Highcharts 차트에 공통으로 적용할 폰트·여백 등의 기본 옵션
const COMMON_CHART_OPTS = {
  chart: {
    style: { fontFamily: "var(--font-pretendard), system-ui, sans-serif" },
    spacing: [8, 8, 8, 8] as [number, number, number, number],
  },
  title: { text: undefined as string | undefined },
  legend: { enabled: false },
} as const

export function OciHubDashboard() {
  const [stats, setStats] = useState<OciHubStats | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      setStats(await fetchOciHubStats())
    } catch (err) {
      console.error("Failed to fetch OCI Hub stats:", err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  if (loading || !stats) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
        {[...Array(6)].map((_, i) => (
          <Card key={i}><CardContent className="pt-6"><div className="h-16 animate-pulse bg-muted rounded" /></CardContent></Card>
        ))}
      </div>
    )
  }

  const pieData = stats.source_distribution.filter((s) => s.count > 0)
  const sizeData = stats.model_sizes.slice(0, 5).map((m) => ({ name: shortName(m.model_name), fullName: m.model_name, size: m.total_size }))
  const dlData = stats.top_downloads.slice(0, 5).map((m) => ({ name: shortName(m.model_name), fullName: m.model_name, downloads: m.download_count }))

  const dl1dData = stats.download_1d.map((d) => ({ date: d.date, fullDate: d.date, count: d.count }))
  const dl7dData = stats.download_7d.map((d) => ({ date: shortDate(d.date), fullDate: d.date, count: d.count }))
  const dl30dData = stats.download_30d.map((d) => ({ date: shortDate(d.date), fullDate: d.date, count: d.count }))

  const pub1dData = stats.publish_1d.map((d) => ({ date: d.date, fullDate: d.date, count: d.count }))
  const pub7dData = stats.publish_7d.map((d) => ({ date: shortDate(d.date), fullDate: d.date, count: d.count }))
  const pub30dData = stats.publish_30d.map((d) => ({ date: shortDate(d.date), fullDate: d.date, count: d.count }))

  return (
    <div className="space-y-4">
      {/* Summary Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
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
            <CardTitle className="text-sm font-medium">HuggingFace</CardTitle>
            <Globe className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-blue-500">{stats.hf_count}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">내 모델</CardTitle>
            <Upload className="h-4 w-4 text-orange-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-orange-500">{stats.my_count}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">전체 다운로드</CardTitle>
            <Download className="h-4 w-4 text-emerald-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-emerald-500">{stats.total_download}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">전체 게시</CardTitle>
            <Activity className="h-4 w-4 text-purple-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-purple-500">{stats.total_publish}</div>
          </CardContent>
        </Card>
      </div>

      {/* Charts Row 1: Source / Size / Downloads */}
      <div className="grid gap-4 lg:grid-cols-3">
        {/* Donut: Source Distribution */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">출처별 분포</CardTitle>
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
                      pie: {
                        innerSize: "58%",
                        dataLabels: { enabled: false },
                        borderWidth: 2,
                      },
                    },
                    series: [{
                      type: "pie",
                      data: pieData.map((s) => ({
                        name: SOURCE_LABELS[s.source] ?? s.source,
                        y: s.count,
                        color: SOURCE_COLORS[s.source] || "#a1a1aa",
                      })),
                    }],
                  }}
                />
                <div className="space-y-2">
                  {pieData.map((s) => (
                    <div key={s.source} className="flex items-center gap-2 text-sm">
                      <span className="h-3 w-3 rounded-full" style={{ backgroundColor: SOURCE_COLORS[s.source] || "#a1a1aa" }} />
                      <span className="text-muted-foreground">{SOURCE_LABELS[s.source] ?? s.source}</span>
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

        {/* Bar: Model Size Top 10 */}
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
                  xAxis: {
                    categories: sizeData.map((d) => d.name),
                    labels: { style: { fontSize: "11px" } },
                  },
                  yAxis: {
                    title: { text: undefined },
                    labels: {
                      style: { fontSize: "11px" },
                      formatter() { return formatSizeAxis(this.value as number) },
                    },
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

        {/* Bar: Downloads Top 10 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">다운로드 (상위 5개)</CardTitle>
          </CardHeader>
          <CardContent>
            {dlData.length > 0 ? (
              <Highchart
                height={160}
                options={{
                  ...COMMON_CHART_OPTS,
                  chart: { ...COMMON_CHART_OPTS.chart, type: "bar" },
                  xAxis: {
                    categories: dlData.map((d) => d.name),
                    labels: { style: { fontSize: "11px" } },
                  },
                  yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                  tooltip: {
                    formatter() {
                      const full = dlData[this.index!]?.fullName ?? this.x
                      return `<b>${full}</b><br/>다운로드 수: ${this.y}`
                    },
                  },
                  series: [{ type: "bar", data: dlData.map((d) => d.downloads), color: "#10b981" }],
                }}
              />
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8">아직 다운로드 기록이 없습니다</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Charts Row 2: Download Trends */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">시간별 다운로드 (24시간)</CardTitle>
          </CardHeader>
          <CardContent>
            {dl1dData.length > 0 ? (
              <Highchart
                height={160}
                options={{
                  ...COMMON_CHART_OPTS,
                  chart: { ...COMMON_CHART_OPTS.chart, type: "column" },
                  xAxis: { categories: dl1dData.map((d) => d.date), labels: { style: { fontSize: "10px" } } },
                  yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                  tooltip: { pointFormat: "다운로드 수: <b>{point.y}</b>" },
                  series: [{ type: "column", data: dl1dData.map((d) => d.count), color: "#10b981" }],
                }}
              />
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8">아직 다운로드 데이터가 없습니다</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">일별 다운로드 (7일)</CardTitle>
          </CardHeader>
          <CardContent>
            {dl7dData.length > 0 ? (
              <Highchart
                height={160}
                options={{
                  ...COMMON_CHART_OPTS,
                  chart: { ...COMMON_CHART_OPTS.chart, type: "line" },
                  xAxis: { categories: dl7dData.map((d) => d.date), labels: { style: { fontSize: "10px" } } },
                  yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                  tooltip: {
                    formatter() {
                      const full = dl7dData[this.index!]?.fullDate ?? this.x
                      return `<b>${full}</b><br/>다운로드 수: ${this.y}`
                    },
                  },
                  series: [{ type: "line", data: dl7dData.map((d) => d.count), color: "#f59e0b" }],
                }}
              />
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8">아직 다운로드 데이터가 없습니다</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">일별 다운로드 (30일)</CardTitle>
          </CardHeader>
          <CardContent>
            {dl30dData.length > 0 ? (
              <Highchart
                height={160}
                options={{
                  ...COMMON_CHART_OPTS,
                  chart: { ...COMMON_CHART_OPTS.chart, type: "line" },
                  xAxis: { categories: dl30dData.map((d) => d.date), labels: { style: { fontSize: "10px" } } },
                  yAxis: { title: { text: undefined }, allowDecimals: false, labels: { style: { fontSize: "11px" } } },
                  tooltip: {
                    formatter() {
                      const full = dl30dData[this.index!]?.fullDate ?? this.x
                      return `<b>${full}</b><br/>다운로드 수: ${this.y}`
                    },
                  },
                  series: [{ type: "line", data: dl30dData.map((d) => d.count), color: "#3b82f6" }],
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
