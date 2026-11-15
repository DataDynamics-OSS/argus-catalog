"use client"

import { InfoTip } from "@/components/tip"
import { useCallback, useEffect, useState } from "react"
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  GitBranch,
  Loader2,
  RefreshCw,
  ShieldAlert,
  XCircle,
} from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { StatusBadge } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"

import { fetchStats, type FederationStats } from "./api"
import { instanceStatus, modeLabel, syncStatus } from "./status"
import { errorMessage } from "@/lib/notify"

function StatCard({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode
  label: string
  value: number | string
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-4">
        <div >{icon}</div>
        <div>
          <div className="text-2xl leading-none font-semibold">{value}</div>
          <div className="mt-1 text-xs">{label}</div>
        </div>
      </CardContent>
    </Card>
  )
}

function SyncBadge({ status }: { status: string | null }) {
  if (!status) return <StatusBadge tone="neutral">미수집</StatusBadge>
  const icon = status === "SUCCESS" ? CheckCircle2 : status === "FAILED" ? XCircle : undefined
  return <StatusBadge {...syncStatus(status)} icon={icon} />
}

export function FederationObservabilityPanel() {
  const [stats, setStats] = useState<FederationStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      setStats(await fetchStats())
      setError(null)
    } catch (e) {
      setError(errorMessage(e) ?? String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  if (loading && !stats) {
    return (
      <div className="flex items-center gap-2 py-8 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> 불러오는 중…
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm">
          peer 동기화 상태·미러 규모·circuit breaker 를 한눈에 봅니다.
        </p>
        <Button variant="outline" size="sm" onClick={reload} disabled={loading}>
          <RefreshCw
            className={`mr-1 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
          />{" "}
          새로고침
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      {stats && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard
              icon={<Database className="h-5 w-5" />}
              label="인스턴스 (활성)"
              value={`${stats.total_instances} (${stats.active_instances})`}
            />
            <StatCard
              icon={<Database className="h-5 w-5" />}
              label="미러 데이터셋"
              value={stats.total_mirror_datasets}
            />
            <StatCard
              icon={<GitBranch className="h-5 w-5" />}
              label="미러 리니지 엣지"
              value={stats.total_mirror_lineage}
            />
            <StatCard
              icon={<ShieldAlert className="h-5 w-5" />}
              label="회로 열림(breaker)"
              value={stats.instances.filter((i) => i.breaker_open).length}
            />
          </div>

          <div className="overflow-auto rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>인스턴스</TableHead>
                  <TableHead>모드</TableHead>
                  <TableHead><InfoTip tip="이 인스턴스에서 가져와 로컬에 둔 데이터셋 수 / 리니지 수입니다. 실시간 모드 인스턴스는 미러가 없습니다.">미러(DS/리니지)</InfoTip></TableHead>
                  <TableHead>최근 동기화</TableHead>
                  <TableHead>Breaker</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {stats.instances.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={5}
                      className="py-8 text-center text-muted-foreground"
                    >
                      등록된 인스턴스가 없습니다.
                    </TableCell>
                  </TableRow>
                ) : (
                  stats.instances.map((inst) => (
                    <TableRow key={inst.id}>
                      <TableCell>
                        <div className="font-medium">{inst.name}</div>
                        <div className="font-mono text-xs">
                          {inst.instance_key}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" title={inst.mode}>{modeLabel(inst.mode)}</Badge>
                        {inst.status !== "ACTIVE" && (
                          <StatusBadge {...instanceStatus(inst.status)} className="ml-1" />
                        )}
                      </TableCell>
                      <TableCell className="text-sm">
                        {inst.mode === "LIVE" ? (
                          <span className="text-muted-foreground">—</span>
                        ) : (
                          <>
                            {inst.mirror_datasets} / {inst.mirror_lineage}
                          </>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <SyncBadge status={inst.last_sync_status} />
                          {inst.last_sync_started_at && (
                            <span className="text-xs">
                              {new Date(
                                inst.last_sync_started_at
                              ).toLocaleString()}
                            </span>
                          )}
                        </div>
                        {inst.last_error && (
                          <div className="mt-0.5 flex items-start gap-1 text-xs break-all whitespace-normal text-destructive">
                            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{" "}
                            {inst.last_error}
                          </div>
                        )}
                      </TableCell>
                      <TableCell>
                        {inst.breaker_open ? (
                          <Badge
                            variant="secondary"
                            className="gap-1 text-destructive"
                          >
                            <ShieldAlert className="h-3 w-3" /> 열림 (
                            {inst.breaker_failures})
                          </Badge>
                        ) : (
                          <span className="text-sm">
                            정상
                          </span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </>
      )}
    </div>
  )
}
