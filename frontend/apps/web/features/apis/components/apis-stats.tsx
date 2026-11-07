"use client"

import { AlertTriangle, CheckCircle2, Network, Route, ShieldCheck, UserX } from "lucide-react"

import { Card, CardContent } from "@workspace/ui/components/card"
import { cn } from "@workspace/ui/lib/utils"

import { useApis } from "./apis-provider"
import { hasActiveFilters } from "./apis-url-state"

// 목록 상단 지표 카드 — MCP Server 카탈로그 목록과 같은 형식(아이콘 + 라벨 + 값 + 보조 설명, 주의 항목은 amber).
// onClick 이 있으면 누를 때 그 조건으로 목록을 거른다(같은 카드를 다시 누르면 해제). active: 현재 적용 중.
function StatCard({ icon: Icon, label, value, hint, tone, onClick, active }: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: number | string
  hint?: string
  tone?: "warn"
  onClick?: () => void
  active?: boolean
}) {
  const body = (
    <CardContent className="flex items-center gap-3 px-4">
      <Icon className={tone === "warn" ? "h-5 w-5 text-amber-500" : "h-5 w-5 text-muted-foreground"} />
      <div className="min-w-0 text-left">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="text-lg font-semibold leading-tight">{value}</div>
        {hint && <div className="truncate text-[11px] text-muted-foreground">{hint}</div>}
      </div>
    </CardContent>
  )
  if (!onClick) return <Card className="py-3">{body}</Card>
  return (
    <Card
      role="button"
      tabIndex={0}
      aria-pressed={active}
      title={active ? "다시 누르면 필터 해제" : `${label} 기준으로 목록 보기`}
      onClick={onClick}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick() } }}
      className={cn(
        "cursor-pointer py-3 outline-none transition-shadow hover:shadow-md hover:ring-primary/40 focus-visible:ring-2 focus-visible:ring-primary",
        active && "ring-2 ring-primary",
      )}
    >
      {body}
    </Card>
  )
}

export function ApisStats() {
  const { stats, filters, setFilters, resetFilters } = useApis()
  const publishedPct = stats && stats.total_apis > 0 ? Math.round((stats.published_apis / stats.total_apis) * 100) : null

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <StatCard
        icon={Network}
        label="API"
        value={stats?.total_apis ?? "-"}
        hint={hasActiveFilters(filters) ? "누르면 전체 보기" : undefined}
        onClick={resetFilters}
      />
      <StatCard
        icon={CheckCircle2}
        label="게시"
        value={stats?.published_apis ?? "-"}
        hint={publishedPct !== null ? `전체의 ${publishedPct}%` : undefined}
        active={filters.status === "published"}
        onClick={() => setFilters({ status: filters.status === "published" ? "" : "published" })}
      />
      <StatCard icon={Route} label="엔드포인트" value={stats?.total_endpoints ?? "-"} />
      <StatCard
        icon={ShieldCheck}
        label="인증됨"
        value={stats?.certified_apis ?? "-"}
        hint="인증 상태 CERTIFIED"
        active={filters.certification === "CERTIFIED"}
        onClick={() => setFilters({ certification: filters.certification === "CERTIFIED" ? "" : "CERTIFIED" })}
      />
      <StatCard
        icon={AlertTriangle}
        label="미확인 Breaking 변경"
        value={stats?.open_alert_apis ?? "-"}
        hint="확인 안 된 변경 알림이 있는 API"
        tone={stats?.open_alert_apis ? "warn" : undefined}
        active={filters.alert}
        onClick={() => setFilters({ alert: !filters.alert })}
      />
      <StatCard
        icon={UserX}
        label="소유자 미지정"
        value={stats?.unowned_apis ?? "-"}
        hint="책임자 지정 필요"
        tone={stats?.unowned_apis ? "warn" : undefined}
        active={filters.owner === "unowned"}
        onClick={() => setFilters({ owner: filters.owner === "unowned" ? "" : "unowned" })}
      />
    </div>
  )
}
