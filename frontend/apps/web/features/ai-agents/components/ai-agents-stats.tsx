"use client"

import { InfoTip } from "@/components/tip"
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
  Scale,
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
import { RISK_TIER_LABEL } from "./ai-agents-skills-data"
import { encodeList } from "@/lib/text-list"

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
        tip="에이전트 정보에 '사람 승인 필요(HITL)'가 켜진 에이전트 수입니다. 중요 액션을 실행하기 전에 사람이 승인합니다."
      />
      <StatCard
        icon={ShieldAlert}
        label="승인 없는 고위험 도구"
        value={stats?.risky_tool_agents ?? "-"}
        hint="high/critical 도구를 승인 없이 쓰는 에이전트"
        tip="위험도가 높음 · 치명인 도구를 '승인 필요' 없이 등록한 에이전트 수입니다. 도구 탭에서 승인 필요를 켜거나 위험도를 다시 확인하세요."
        tone={stats?.risky_tool_agents ? "warn" : undefined}
      />
      <StatCard
        icon={Link2}
        label="미등록 MCP 연결"
        value={stats?.unregistered_mcp_count ?? "-"}
        hint="MCP Server 카탈로그에 없는 직접 입력 연결"
        tip="에이전트에 이름 · URL 로 직접 입력했고 MCP Server 카탈로그의 배포와 연결되지 않은 MCP 연결 수입니다. 배포와 연결해야 실효 도구 · 정책 번들에 반영됩니다."
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
        tip="소유자가 비어 있거나, 소유자 이메일이 사용자 목록에 없거나, 소유자 계정이 비활성인 에이전트 수입니다."
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
        tip={"정보 완성도가 100점 미만인 에이전트 수입니다. 완성도 배점: 소유자 · 부서 · 분류 25, 설명(20자 이상) 15, 호출 주소 · 기반 모델 · 도구 위험도 · 입출력 형식 · 안전장치 · 평가 기록 각 10."}
        onClick={() => {
          setListTab("agents")
          applyQuery({ incomplete: true, sort: "completeness" }, "builtin:incomplete")
        }}
      />
      <StatCard
        icon={Scale}
        label="위험 등급 미지정"
        value={stats?.unassessed_risk_count ?? "-"}
        hint={stats && stats.by_risk_tier.length > 0
          ? stats.by_risk_tier.map((r) => `${RISK_TIER_LABEL[r.name] ?? r.name} ${r.count}`).join(" · ")
          : "폐기 제외"}
        tip="위험 등급(AI 위험 분류)을 정하지 않은 에이전트 수입니다. 폐기된 에이전트는 세지 않습니다."
        onClick={() => {
          setListTab("agents")
          applyQuery({ risk_tier: "none" }, "builtin:risk-tier-none")
        }}
      />
      <StatCard
        icon={ShieldAlert}
        label="고위험 영향 평가 없음"
        value={stats?.high_risk_unassessed_count ?? "-"}
        hint="고위험·금지 등급인데 영향 평가 기록이 없는 에이전트"
        tip="위험 등급이 고위험 · 금지인데 영향 평가 일자가 비어 있는 에이전트 수입니다(폐기 제외)."
        tone={stats?.high_risk_unassessed_count ? "warn" : undefined}
        onClick={() => {
          setListTab("agents")
          applyQuery({ risk_tier: "high,prohibited" }, "builtin:risk-tier-high")
        }}
      />
      <div className="col-span-2 flex items-center md:col-span-4 xl:col-span-3">
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
              <TableHead className="text-right"><InfoTip tip="부서에 속한 에이전트의 정보 완성도 평균입니다. 정보 완성도(100점) — 소유자 · 부서 · 분류 25, 설명(20자 이상) 15, 호출 주소 · 기반 모델 · 도구 위험도 · 입출력 형식 · 안전장치 · 평가 기록 각 10.">평균 완성도</InfoTip></TableHead>
              <TableHead className="text-right">필수 항목 누락</TableHead>
              <TableHead className="text-right">책임자 확인 필요</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {stats.by_department.map((d) => (
              <TableRow
                key={d.department ?? ""}
                className={d.department ? "cursor-pointer" : undefined}
                onClick={() => d.department && applyQuery({ department: encodeList([d.department]) ?? undefined }, null)}
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
