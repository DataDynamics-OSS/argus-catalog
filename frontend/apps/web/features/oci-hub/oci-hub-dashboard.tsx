"use client"

import { useEffect, useState } from "react"
import { Box, Download, HardDrive, ShieldCheck } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import { StatCard } from "@/components/stat-card"
import { ActivityTrendCard } from "@/components/model-hub/activity-trend-card"
import { DistributionCard } from "@/components/model-hub/distribution-card"
import { TopBarCard } from "@/components/model-hub/top-bar-card"
import { formatBytes, formatBytesAxis } from "@/components/model-hub/format"

import { fetchOciHubStats, OCI_STATUS_LABELS, type OciHubStats } from "./api"
import { errorMessage } from "@/lib/notify"

// 출처 코드 → 화면 표시용 라벨 (배지·상세·대시보드 공용). "HuggingFace" 는 고유명사라 그대로.
export const SOURCE_LABELS: Record<string, string> = {
  huggingface: "HuggingFace",
  my: "직접 등록",
  file: "파일",
  local: "로컬",
  mlflow: "MLflow 변환",
  unknown: "기타",
}

const SOURCE_COLORS: Record<string, string> = {
  huggingface: "#3b82f6", my: "#f97316", file: "#10b981", local: "#14b8a6", mlflow: "#8b5cf6", unknown: "#a1a1aa",
}

// 모델 상태 — 수명 주기 순서대로 범례에 보이도록 순서를 고정한다 (상태 배지와 같은 색 계열).
const MODEL_STATUS_ORDER = ["draft", "review", "approved", "production", "deprecated", "archived"]
const MODEL_STATUS_COLORS: Record<string, string> = {
  draft: "#a1a1aa", review: "#f59e0b", approved: "#3b82f6", production: "#16a34a", deprecated: "#52525b", archived: "#27272a",
}

const href = (name: string) => `/dashboard/oci-hub/${encodeURIComponent(name)}`

/**
 * OCI 모델 허브 대시보드 — 요약 카드 4개, 활동 추이(기간 토글) + 출처 분포,
 * 상태 분포 + 상위 모델 차트 2종(다운로드·크기).
 */
export function OciHubDashboard() {
  const [stats, setStats] = useState<OciHubStats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    setError(null)
    fetchOciHubStats()
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

  const sc = stats.status_counts ?? {}
  const released = (sc.approved ?? 0) + (sc.production ?? 0)
  const inReview = (sc.draft ?? 0) + (sc.review ?? 0)

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          icon={Box} label="모델" value={stats.total_models}
          hint={stats.source_distribution.map((d) => `${SOURCE_LABELS[d.source] ?? d.source} ${d.count}`).join(" · ") || "출처 정보 없음"}
        />
        <StatCard
          icon={ShieldCheck} label="승인·운영" value={released}
          hint={inReview ? `초안·검토 중 ${inReview}개` : "검토 대기 없음"}
        />
        <StatCard
          icon={Download} label="다운로드 (누적)" value={stats.total_downloads.toLocaleString()}
          hint={`최근 30일 ${stats.download_30d_total.toLocaleString()}회`}
        />
        <StatCard
          icon={HardDrive} label="저장 용량" value={formatBytes(stats.total_storage)}
          hint={`준비된 버전 ${stats.total_versions}개`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <ActivityTrendCard
          className="lg:col-span-2"
          publishLabel="버전 게시"
          downloads={{ h24: stats.download_1d, d7: stats.download_7d, d30: stats.download_30d }}
          publishes={{ h24: stats.publish_1d, d7: stats.publish_7d, d30: stats.publish_30d }}
        />
        <DistributionCard
          title="출처별 모델"
          empty="등록된 모델이 없습니다."
          items={stats.source_distribution.map((d) => ({
            key: d.source,
            label: SOURCE_LABELS[d.source] ?? d.source,
            count: d.count,
            color: SOURCE_COLORS[d.source] ?? "#71717a",
          }))}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <DistributionCard
          title="상태별 모델"
          empty="등록된 모델이 없습니다."
          items={[
            ...MODEL_STATUS_ORDER.filter((k) => k in sc),
            ...Object.keys(sc).filter((k) => !MODEL_STATUS_ORDER.includes(k)),
          ].map((k) => ({
            key: k, label: OCI_STATUS_LABELS[k] ?? k, count: sc[k] ?? 0, color: MODEL_STATUS_COLORS[k] ?? "#71717a",
          }))}
        />
        <TopBarCard
          title="다운로드 상위 모델" color="#10b981" empty="아직 다운로드된 모델이 없습니다."
          items={stats.top_downloads.map((m) => ({
            name: m.model_name, value: m.download_count,
            display: `다운로드 ${m.download_count.toLocaleString()}회`, href: href(m.model_name),
          }))}
        />
        <TopBarCard
          title="크기 상위 모델" color="#3b82f6" empty="저장된 파일이 있는 모델이 없습니다." axisFormat={formatBytesAxis}
          items={stats.model_sizes.map((m) => ({
            name: m.model_name, value: m.total_size,
            display: formatBytes(m.total_size), href: href(m.model_name),
          }))}
        />
      </div>
    </div>
  )
}
