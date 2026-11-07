"use client"

import { useEffect, useState } from "react"
import { AlertTriangle, Box, Download, Rocket } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import { StatCard } from "@/components/stat-card"
import { ActivityTrendCard } from "@/components/model-hub/activity-trend-card"
import { DistributionCard } from "@/components/model-hub/distribution-card"
import { TopBarCard } from "@/components/model-hub/top-bar-card"
import { formatBytes, formatBytesAxis } from "@/components/model-hub/format"

import { fetchModelStats, type ModelStats } from "../api"
import { errorMessage } from "@/lib/notify"

const href = (name: string) => `/dashboard/models/${encodeURIComponent(name)}`

// 버전 상태 코드 → 라벨·색 (도넛 차트)
const VERSION_STATUS: Record<string, { label: string; color: string }> = {
  READY: { label: "준비됨", color: "#3b82f6" },
  PENDING: { label: "등록 대기", color: "#a1a1aa" },
  FAILED: { label: "실패", color: "#ef4444" },
}

/**
 * MLflow 모델 대시보드 — 요약 카드 4개, 활동 추이(기간 토글) + 버전 상태 분포,
 * 상위 모델 차트 3종(다운로드·크기·버전 수).
 */
export function ModelsDashboard() {
  const [stats, setStats] = useState<ModelStats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    setError(null)
    fetchModelStats()
      .then(setStats)
      .catch((e) => setError(errorMessage(e) ?? "통계를 불러오지 못했습니다."))
  }, [reload])

  if (error) {
    return (
      <Card><CardContent className="flex flex-col items-center gap-3 py-12 text-sm">
        <p className="text-destructive">{error}</p>
        <Button size="sm" variant="outline" onClick={() => setReload((k) => k + 1)}>다시 시도</Button>
      </CardContent></Card>
    )
  }

  if (!stats) {
    return (
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[...Array(4)].map((_, i) => <div key={i} className="h-[74px] animate-pulse rounded-xl bg-muted" />)}
        </div>
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="h-[300px] animate-pulse rounded-xl bg-muted lg:col-span-2" />
          <div className="h-[300px] animate-pulse rounded-xl bg-muted" />
        </div>
        <div className="grid gap-4 lg:grid-cols-3">
          {[...Array(3)].map((_, i) => <div key={i} className="h-[280px] animate-pulse rounded-xl bg-muted" />)}
        </div>
      </div>
    )
  }

  const attention = stats.pending_count + stats.failed_count
  const labelOf = (n: string) => n.split(".").pop() ?? n

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard icon={Box} label="모델" value={stats.total_models} hint={`버전 ${stats.total_versions}개`} />
        <StatCard
          icon={Rocket} label="운영 중" value={stats.production_models}
          hint="운영 스테이지 버전이 있는 모델"
        />
        <StatCard
          icon={Download} label="다운로드 (최근 30일)" value={stats.download_30d_total.toLocaleString()}
          hint={`누적 ${stats.total_download.toLocaleString()}회`}
        />
        <StatCard
          icon={AlertTriangle} label="주의 필요" value={attention}
          hint={attention ? `등록 대기 ${stats.pending_count} · 실패 ${stats.failed_count}` : "등록 대기·실패 버전 없음"}
          tone={attention ? "warn" : undefined}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <ActivityTrendCard
          className="lg:col-span-2"
          publishLabel="버전 등록"
          downloads={{ h24: stats.daily_download_1d, d7: stats.daily_download_7d, d30: stats.daily_download_30d }}
          publishes={{ h24: stats.daily_publish_1d, d7: stats.daily_publish_7d, d30: stats.daily_publish_30d }}
        />
        <DistributionCard
          title="버전 상태"
          empty="등록된 버전이 없습니다."
          items={stats.status_distribution.map((d) => ({
            key: d.status,
            label: VERSION_STATUS[d.status]?.label ?? d.status,
            count: d.count,
            color: VERSION_STATUS[d.status]?.color ?? "#71717a",
          }))}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <TopBarCard
          title="다운로드 상위 모델" color="#10b981" empty="아직 다운로드된 모델이 없습니다."
          items={Object.entries(stats.download_by_model).map(([n, c]) => ({
            name: n, label: labelOf(n), value: c, display: `다운로드 ${c.toLocaleString()}회`, href: href(n),
          }))}
        />
        <TopBarCard
          title="크기 상위 모델" color="#3b82f6" empty="크기 정보가 있는 모델이 없습니다." axisFormat={formatBytesAxis}
          items={stats.model_sizes.map((m) => ({
            name: m.model_name, label: labelOf(m.model_name), value: m.model_size_bytes,
            display: formatBytes(m.model_size_bytes), href: href(m.model_name),
          }))}
        />
        <TopBarCard
          title="버전이 많은 모델" color="#8b5cf6" empty="버전이 없습니다."
          items={stats.versions_per_model.map((m) => ({
            name: m.model_name, label: labelOf(m.model_name), value: m.version_count,
            display: `버전 ${m.version_count}개`, href: href(m.model_name),
          }))}
        />
      </div>
    </div>
  )
}
