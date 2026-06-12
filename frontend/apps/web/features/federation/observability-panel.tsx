"use client"

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
        <div className="text-muted-foreground">{icon}</div>
        <div>
          <div className="text-2xl leading-none font-semibold">{value}</div>
          <div className="mt-1 text-xs text-muted-foreground">{label}</div>
        </div>
      </CardContent>
    </Card>
  )
}

function SyncBadge({ status }: { status: string | null }) {
  if (!status) return <Badge variant="outline">미수집</Badge>
  if (status === "SUCCESS")
    return (
      <Badge variant="default" className="gap-1">
        <CheckCircle2 className="h-3 w-3" /> 성공
      </Badge>
    )
  if (status === "FAILED")
    return (
      <Badge variant="secondary" className="gap-1 text-destructive">
        <XCircle className="h-3 w-3" /> 실패
      </Badge>
    )
  return <Badge variant="outline">{status}</Badge>
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
      setError(e instanceof Error ? e.message : String(e))
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
        <Loader2 className="h-4 w-4 animate-spin" /> 불러오는 중...
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
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
                  <TableHead>미러(DS/리니지)</TableHead>
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
                        <div className="font-mono text-xs text-muted-foreground">
                          {inst.instance_key}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{inst.mode}</Badge>
                        {inst.status !== "ACTIVE" && (
                          <Badge variant="secondary" className="ml-1">
                            {inst.status}
                          </Badge>
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
                            <span className="text-xs text-muted-foreground">
                              {new Date(
                                inst.last_sync_started_at
                              ).toLocaleString()}
                            </span>
                          )}
                        </div>
                        {inst.last_error && (
                          <div className="mt-0.5 flex items-center gap-1 text-xs text-destructive">
                            <AlertTriangle className="h-3 w-3" />{" "}
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
                          <span className="text-sm text-muted-foreground">
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
