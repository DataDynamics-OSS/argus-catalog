"use client"

/**
 * MCP 통제 현황 (Phase 2 대시보드).
 *  - 환경 × 서버 매트릭스 (변경 그룹·정책·finding)
 *  - 고위험 도구가 노출된 배포 Top
 *  - finding 종류·심각도 분포, 14일 열림/해소 추이
 *  - 최근 변경 도구 호출, 정책 커버리지
 */

import { useEffect, useState } from "react"
import { AlertTriangle, CheckCircle2, Loader2, ShieldCheck } from "lucide-react"
import { notifyError } from "@/lib/notify"

import { Badge } from "@workspace/ui/components/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { cn } from "@workspace/ui/lib/utils"

import { fetchMcpControlOverview } from "../../api"
import { FINDING_KIND_LABEL, RISK_LABEL, type McpControlOverview } from "../../data/schema"
import { HealthDot, RiskBadge } from "../mcp-badges"
import { useMcpServers } from "../mcp-provider"
import { AuditTable } from "./audit-panel"
import { FindingsPanel } from "./findings-panel"

// 검증된 기본 categorical 팔레트 slot 1·2 (dataviz palette.md) — 라이트/다크 각각
const OPENED = "bg-[#2a78d6] dark:bg-[#3987e5]"
const RESOLVED = "bg-[#eb6834] dark:bg-[#d95926]"

export function ControlOverview() {
  const { setSelectedDeployment, reloadKey } = useMcpServers()
  const [data, setData] = useState<McpControlOverview | null>(null)
  const [openId, setOpenId] = useState<number | null>(null)

  useEffect(() => {
    fetchMcpControlOverview().then(setData).catch((e) => notifyError(e, "MCP 통제 현황을 불러오지 못했습니다."))
  }, [reloadKey])

  if (!data) return <div className="flex h-40 items-center justify-center"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>

  const c = data.coverage
  const pct = (n: number) => (c.deployments ? Math.round((n / c.deployments) * 100) : 0)
  const totalOpen = data.findings_by_severity.reduce((a, b) => a + b.count, 0)

  return (
    <div className="flex flex-col gap-4">
      {/* 커버리지 */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile label="미해소 finding" value={totalOpen} tone={totalOpen ? "warn" : undefined}
          hint={data.findings_by_severity.map((s) => `${RISK_LABEL[s.name] ?? s.name} ${s.count}`).join(" · ") || "없음"} />
        <Tile label="승인 정책 설정" value={`${c.with_policy} / ${c.deployments}`} hint={`${pct(c.with_policy)}% 배포`} />
        <Tile label="감사 수집 연결" value={`${c.with_ingest_token} / ${c.deployments}`} hint="수집 토큰 발급 배포" />
        <Tile label="정책 강제(enforce)" value={`${c.enforce} / ${c.deployments}`}
          tone={c.enforce_applied < c.enforce ? "warn" : undefined}
          hint={c.enforce ? `최신 번들 적용 ${c.enforce_applied} / ${c.enforce}` : "강제 모드 배포 없음"} />
      </div>

      {/* 매트릭스 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">환경 × 서버</CardTitle>
          <p className="text-xs text-muted-foreground">
            각 칸은 배포입니다. 주황 배지 = 켜진 변경 그룹, 빨간 숫자 = 미해소 finding, 점선 = 승인 정책 없음.
          </p>
        </CardHeader>
        <CardContent>
          {data.matrix.length === 0 ? (
            <p className="text-sm text-muted-foreground">배포가 없습니다.</p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[200px]">서버</TableHead>
                    {data.environments.map((e) => <TableHead key={e}>{e}</TableHead>)}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.matrix.map((row) => (
                    <TableRow key={row.server}>
                      <TableCell>
                        <div className="font-medium">{row.server_display_name || row.server}</div>
                        <div className="text-[11px] text-muted-foreground">{row.target_platform ?? ""}</div>
                      </TableCell>
                      {data.environments.map((env) => (
                        <TableCell key={env} className="align-top">
                          <div className="flex flex-col gap-1.5">
                            {(row.cells[env] ?? []).map((cell) => (
                              <button
                                type="button"
                                key={cell.deployment}
                                onClick={() => setSelectedDeployment(cell.deployment)}
                                className={cn(
                                  "rounded-md border p-1.5 text-left text-xs hover:bg-muted/60",
                                  !cell.policy_set && "border-dashed",
                                  cell.open_findings > 0 && "border-red-300 dark:border-red-800",
                                )}
                              >
                                <div className="flex items-center gap-1.5">
                                  <HealthDot status={cell.health_status} withLabel={false} />
                                  <span className="font-mono">{cell.deployment}</span>
                                  {cell.open_findings > 0 && (
                                    <span className="ml-auto inline-flex items-center gap-0.5 font-medium text-red-700 dark:text-red-400">
                                      <AlertTriangle className="h-3 w-3" />{cell.open_findings}
                                    </span>
                                  )}
                                </div>
                                {cell.enabled_mutating_groups.length > 0 && (
                                  <div className="mt-1 flex flex-wrap gap-0.5">
                                    {cell.enabled_mutating_groups.map((g) => (
                                      <Badge key={g} variant="outline" className="h-4 border-orange-300 px-1 text-[10px] text-orange-700 dark:text-orange-300">{g}</Badge>
                                    ))}
                                  </div>
                                )}
                              </button>
                            ))}
                          </div>
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        {/* 위험 배포 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">주의할 배포</CardTitle>
            <p className="text-xs text-muted-foreground">고위험(높음·치명) 도구가 노출됐거나 finding 이 있는 배포 — PROD 우선</p>
          </CardHeader>
          <CardContent>
            {data.risky.length === 0 ? (
              <p className="flex items-center gap-1 text-sm text-muted-foreground"><CheckCircle2 className="h-4 w-4 text-emerald-600" /> 없습니다.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>배포</TableHead>
                    <TableHead className="text-right">고위험 노출</TableHead>
                    <TableHead>최대 위험</TableHead>
                    <TableHead className="text-right">finding</TableHead>
                    <TableHead>정책</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.risky.map((r) => (
                    <TableRow key={r.deployment} className="cursor-pointer" onClick={() => setSelectedDeployment(r.deployment)}>
                      <TableCell>
                        <span className="font-mono text-xs">{r.deployment}</span>
                        <div className="text-[11px] text-muted-foreground">{r.environment} · {r.server_name}</div>
                      </TableCell>
                      <TableCell className="text-right text-sm">{r.high_risk_exposed} / {r.exposed_tool_count}</TableCell>
                      <TableCell><RiskBadge risk={r.max_exposed_risk} /></TableCell>
                      <TableCell className="text-right text-sm">{r.open_findings || "-"}</TableCell>
                      <TableCell>
                        {r.policy_set
                          ? <ShieldCheck className="h-4 w-4 text-emerald-600" aria-label="승인 정책 있음" />
                          : <span className="text-xs text-muted-foreground">없음</span>}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* 분포 + 추이 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">finding 분포 · 추이</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1">
              {data.findings_by_kind.length === 0 ? (
                <p className="text-xs text-muted-foreground">미해소 finding 이 없습니다.</p>
              ) : data.findings_by_kind.map((k) => {
                const max = data.findings_by_kind[0]?.count || 1
                return (
                  <div key={k.name} className="grid grid-cols-[170px_1fr_32px] items-center gap-2 text-xs" title={`${FINDING_KIND_LABEL[k.name] ?? k.name}: ${k.count}건`}>
                    <span className="truncate text-muted-foreground">{FINDING_KIND_LABEL[k.name] ?? k.name}</span>
                    <div className="h-2.5 rounded-r bg-muted">
                      <div className={cn("h-2.5 rounded-r", OPENED)} style={{ width: `${(k.count / max) * 100}%` }} />
                    </div>
                    <span className="text-right tabular-nums">{k.count}</span>
                  </div>
                )
              })}
            </div>
            <TrendChart trend={data.trend} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle className="text-sm">미해소 finding</CardTitle></CardHeader>
        <CardContent><FindingsPanel /></CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">최근 변경 도구 호출</CardTitle>
          <p className="text-xs text-muted-foreground">감사 이벤트 수집이 연결된 배포의 최근 20건</p>
        </CardHeader>
        <CardContent>
          <AuditTable rows={data.recent_calls} openId={openId} setOpenId={setOpenId} showDeployment />
        </CardContent>
      </Card>
    </div>
  )
}

function Tile({ label, value, hint, tone }: { label: string; value: number | string; hint?: string; tone?: "warn" }) {
  return (
    <Card>
      <CardContent className="pt-5">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className={cn("mt-1 text-2xl font-semibold tabular-nums", tone === "warn" && "text-red-700 dark:text-red-400")}>{value}</div>
        {hint && <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{hint}</div>}
      </CardContent>
    </Card>
  )
}

/** 14일 열림/해소 — 일별 묶음 막대 (단일 y축, 0 기준선). */
function TrendChart({ trend }: { trend: McpControlOverview["trend"] }) {
  const max = Math.max(1, ...trend.map((t) => Math.max(t.opened, t.resolved)))
  const H = 72
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-muted-foreground">최근 {trend.length}일</span>
        <span className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1"><span className={cn("inline-block h-2 w-2 rounded-sm", OPENED)} />열림</span>
          <span className="inline-flex items-center gap-1"><span className={cn("inline-block h-2 w-2 rounded-sm", RESOLVED)} />해소</span>
        </span>
      </div>
      <div className="flex items-end gap-1 border-b border-border" style={{ height: H }} role="img"
        aria-label={trend.map((t) => `${t.date} 열림 ${t.opened} 해소 ${t.resolved}`).join(", ")}>
        {trend.map((t) => (
          <div key={t.date} className="group flex h-full flex-1 items-end justify-center gap-[2px] hover:bg-muted/50"
            title={`${t.date}\n열림 ${t.opened} · 해소 ${t.resolved}`}>
            <div className={cn("w-1/3 rounded-t-[4px]", OPENED)} style={{ height: `${(t.opened / max) * 100}%` }} />
            <div className={cn("w-1/3 rounded-t-[4px]", RESOLVED)} style={{ height: `${(t.resolved / max) * 100}%` }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
        <span>{trend[0]?.date.slice(5)}</span>
        <span>최대 {max}</span>
        <span>{trend[trend.length - 1]?.date.slice(5)}</span>
      </div>
    </div>
  )
}
