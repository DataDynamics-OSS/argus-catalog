"use client"

import { useEffect, useState } from "react"
import {
  Activity,
  AlertTriangle,
  Bot,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Link2,
  Radar,
  ShieldAlert,
  UserCheck,
  UserX,
} from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"

import { StatCard } from "@/components/stat-card"
import { fetchAIAgentStats, type AIAgentStats } from "../api"
import { useAIAgents } from "./ai-agents-provider"

/** AI Agent 목록 상단 지표 (MCP Server 목록과 같은 카드 형식). `reloadKey` 가 바뀌면 다시 조회. */
export function AIAgentsStats({ reloadKey }: { reloadKey: string | number }) {
  const [stats, setStats] = useState<AIAgentStats | null>(null)
  const [showDept, setShowDept] = useState(false)
  const { applyQuery, setListTab } = useAIAgents()

  useEffect(() => {
    fetchAIAgentStats().then(setStats).catch(() => setStats(null))
  }, [reloadKey])

  const errRate =
    stats && stats.invocations_24h > 0 ? (stats.errors_24h / stats.invocations_24h) * 100 : null

  return (
    <div className="flex flex-col gap-3">
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
      <StatCard
        icon={Bot}
        label="에이전트"
        value={stats?.total_agents ?? "-"}
        hint={stats ? `활성 ${stats.active_agents} · 멀티 에이전트 ${stats.multi_agent_count}` : undefined}
      />
      <StatCard
        icon={Activity}
        label="호출 (24시간)"
        value={stats?.invocations_24h ?? "-"}
        hint={stats ? `누적 ${stats.total_invocations.toLocaleString()}회` : undefined}
      />
      <StatCard
        icon={AlertTriangle}
        label="오류율 (24시간)"
        value={errRate == null ? "-" : `${errRate.toFixed(1)}%`}
        hint={stats ? `실패 ${stats.errors_24h}건` : undefined}
        tone={errRate != null && errRate >= 5 ? "warn" : undefined}
      />
      <StatCard
        icon={UserCheck}
        label="사람 승인(HITL) 필요"
        value={stats?.hitl_required_count ?? "-"}
        hint="중요 액션에 사람 승인이 필요한 에이전트"
      />
      <StatCard
        icon={ShieldAlert}
        label="승인 없는 고위험 도구"
        value={stats?.risky_tool_agents ?? "-"}
        hint="high/critical 도구를 승인 없이 쓰는 에이전트"
        tone={stats?.risky_tool_agents ? "warn" : undefined}
      />
      <StatCard
        icon={Link2}
        label="미등록 MCP 연결"
        value={stats?.unregistered_mcp_count ?? "-"}
        hint="MCP Server 카탈로그에 없는 직접 입력 연결"
        tone={stats?.unregistered_mcp_count ? "warn" : undefined}
      />
      <StatCard
        icon={Radar}
        label="미등록 에이전트"
        value={stats?.unregistered_agent_count ?? "-"}
        hint="실행 기록에 나왔지만 카탈로그에 없는 에이전트"
        tone={stats?.unregistered_agent_count ? "warn" : undefined}
        onClick={() => setListTab("discoveries")}
      />
    </div>

    {/* 분류와 책임 — 눌러서 해당 조건으로 목록 보기 */}
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
      <StatCard
        icon={UserX}
        label="책임자 확인 필요"
        value={stats?.owner_attention_count ?? "-"}
        hint="소유자 없음·사용자 목록에 없음·비활성 계정"
        tone={stats?.owner_attention_count ? "warn" : undefined}
        onClick={() => {
          setListTab("agents")
          applyQuery({ owner_state: "attention" }, "builtin:owner-attention")
        }}
      />
      <StatCard
        icon={ClipboardList}
        label="정보 부족"
        value={stats?.incomplete_count ?? "-"}
        hint="정보 완성도 100점 미만"
        onClick={() => {
          setListTab("agents")
          applyQuery({ incomplete: true, sort: "completeness" }, "builtin:incomplete")
        }}
      />
      <div className="col-span-2 flex items-center md:col-span-2 xl:col-span-5">
        <Button variant="ghost" size="sm" className="text-xs" onClick={() => setShowDept((v) => !v)}
          disabled={!stats?.by_department.length}>
          {showDept ? <ChevronDown className="mr-1 h-4 w-4" /> : <ChevronRight className="mr-1 h-4 w-4" />}
          부서별 정보 품질
        </Button>
      </div>
    </div>
    {showDept && stats && (
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>부서</TableHead>
              <TableHead className="text-right">에이전트</TableHead>
              <TableHead className="text-right">평균 완성도</TableHead>
              <TableHead className="text-right">필수 항목 누락</TableHead>
              <TableHead className="text-right">책임자 확인 필요</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {stats.by_department.map((d) => (
              <TableRow
                key={d.department ?? ""}
                className={d.department ? "cursor-pointer" : undefined}
                onClick={() => d.department && applyQuery({ department: d.department }, null)}
              >
                <TableCell className="text-sm">{d.department ?? <span className="text-muted-foreground">(부서 없음)</span>}</TableCell>
                <TableCell className="text-right text-sm tabular-nums">{d.agents}</TableCell>
                <TableCell className="text-right text-sm tabular-nums">{d.avg_completeness}</TableCell>
                <TableCell className="text-right text-sm tabular-nums">{d.missing_required || "-"}</TableCell>
                <TableCell className="text-right text-sm tabular-nums">{d.owner_attention || "-"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    )}
    </div>
  )
}
