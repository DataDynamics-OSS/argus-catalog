"use client"

import { useEffect, useMemo, useState } from "react"
import { Download, Monitor, TrendingUp } from "lucide-react"

import { CommentSection } from "@/components/comments"
import { StatCard } from "@/components/stat-card"
import { clientKind, formatDateTime, timeAgo } from "@/components/model-hub/format"

import { fetchModelDownloadStats, type ModelDownloadStats } from "../../api"

const TYPE_LABEL: Record<string, string> = { load: "모델 로드", download: "파일 내려받기", pull: "파일 내려받기 (CLI)" }

/** 활동 탭 — 사용 현황(누적·30일·주 클라이언트) + 최근 사용 기록 + 댓글. */
export function ActivityTab({ modelName, canEdit }: { modelName: string; canEdit: boolean }) {
  const [stats, setStats] = useState<ModelDownloadStats | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    fetchModelDownloadStats(modelName).then(setStats).catch(() => setError(true))
  }, [modelName])

  const last30 = useMemo(() => stats?.daily_download.reduce((s, d) => s + d.count, 0) ?? 0, [stats])
  const topClient = useMemo(() => {
    if (!stats?.recent_logs.length) return null
    const counts = new Map<string, number>()
    for (const l of stats.recent_logs) {
      const k = clientKind(l.user_agent)
      counts.set(k, (counts.get(k) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]!
  }, [stats])

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard icon={Download} label="사용 (누적)" value={stats ? stats.total_download.toLocaleString() : "—"} hint="모델 로드·파일 내려받기" />
        <StatCard icon={TrendingUp} label="최근 30일" value={stats ? last30.toLocaleString() : "—"} />
        <StatCard
          icon={Monitor} label="주 클라이언트"
          value={topClient ? topClient[0] : "—"}
          hint={topClient ? `최근 기록 ${stats!.recent_logs.length}건 중 ${topClient[1]}건` : "기록 없음"}
        />
      </div>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">최근 사용 기록</h3>
        {error ? (
          <p className="text-sm text-destructive">사용 기록을 불러오지 못했습니다.</p>
        ) : !stats ? (
          <p className="text-sm text-muted-foreground">불러오는 중…</p>
        ) : stats.recent_logs.length === 0 ? (
          <p className="rounded-lg border border-dashed py-8 text-center text-sm text-muted-foreground">아직 이 모델을 로드하거나 내려받은 기록이 없습니다.</p>
        ) : (
          <div className="max-h-[360px] overflow-auto rounded-lg border">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-muted/60">
                <tr>
                  <th className="w-64 px-3 py-2 text-left font-medium">시간</th>
                  <th className="w-16 px-3 py-2 text-left font-medium">버전</th>
                  <th className="w-28 px-3 py-2 text-left font-medium">방식</th>
                  <th className="px-3 py-2 text-left font-medium">클라이언트</th>
                  {canEdit && <th className="w-36 px-3 py-2 text-left font-medium">IP</th>}
                </tr>
              </thead>
              <tbody className="divide-y">
                {stats.recent_logs.map((l, i) => (
                  <tr key={i}>
                    <td className="whitespace-nowrap px-3 py-1.5" title={formatDateTime(l.downloaded_at, true)}>
                      {formatDateTime(l.downloaded_at)} <span className="text-xs text-muted-foreground">({timeAgo(l.downloaded_at)})</span>
                    </td>
                    <td className="px-3 py-1.5">v{l.version}</td>
                    <td className="px-3 py-1.5">{TYPE_LABEL[l.download_type] ?? l.download_type}</td>
                    <td className="whitespace-nowrap px-3 py-1.5" title={l.user_agent ?? ""}>{clientKind(l.user_agent)}</td>
                    {canEdit && <td className="px-3 py-1.5 font-mono text-xs">{l.client_ip || "—"}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">댓글</h3>
        <CommentSection entityType="model" entityId={modelName} />
      </section>
    </div>
  )
}
