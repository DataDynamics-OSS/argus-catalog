"use client"

/**
 * 서버 정의 — capability 버전 이력 (Phase 2).
 * 수집·manifest 로 도구 정의(설명·입력 스키마·annotations·그룹)가 바뀔 때마다 한 행이 생긴다.
 * 설명만 슬쩍 바뀐 도구(tool poisoning 류)도 "변경된 도구" 로 드러난다.
 */

import { Fragment, useEffect, useState } from "react"
import { Loader2 } from "lucide-react"
import { notifyError } from "@/lib/notify"

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"

import { fetchMcpServerVersion, fetchMcpServerVersions } from "../../api"
import type { McpServerVersion, McpServerVersionDetail } from "../../data/schema"
import { OriginBadge, fmtDate } from "../mcp-badges"
import { DiffView } from "../mcp-deployment-detail"
import { JsonBlock } from "../mcp-server-detail"

function count(diff: Record<string, unknown> | null, key: string): number {
  const tools = (diff?.tools ?? {}) as Record<string, unknown>
  return Array.isArray(tools[key]) ? (tools[key] as unknown[]).length : 0
}

export function VersionsPanel({ name, refreshKey }: { name: string; refreshKey?: string | null }) {
  const [rows, setRows] = useState<McpServerVersion[] | null>(null)
  const [openId, setOpenId] = useState<number | null>(null)
  const [detail, setDetail] = useState<McpServerVersionDetail | null>(null)

  useEffect(() => {
    fetchMcpServerVersions(name).then(setRows).catch((e) => notifyError(e, "서버 버전 목록을 불러오지 못했습니다."))
  }, [name, refreshKey])

  useEffect(() => {
    if (openId == null) {
      setDetail(null)
      return
    }
    fetchMcpServerVersion(name, openId).then(setDetail).catch(() => setDetail(null))
  }, [name, openId])

  return (
    <div className="space-y-2">
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <OriginBadge origin="argus" /> 수집·manifest 가져오기로 도구 구성이 바뀔 때 기록합니다. 행을 누르면 변경 내역과 스냅숏을 봅니다.
      </p>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[160px]">기록 시각</TableHead>
              <TableHead>보고 버전</TableHead>
              <TableHead>도구</TableHead>
              <TableHead>변경</TableHead>
              <TableHead>출처</TableHead>
              <TableHead>해시</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {!rows ? (
              <TableRow><TableCell colSpan={6} className="h-14 text-center"><Loader2 className="mx-auto h-4 w-4 animate-spin" /></TableCell></TableRow>
            ) : rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="h-14 text-center text-muted-foreground">
                  아직 기록된 버전이 없습니다. 배포를 수집하거나 manifest 를 가져오면 생깁니다.
                </TableCell>
              </TableRow>
            ) : rows.map((v) => (
              <Fragment key={v.id}>
                <TableRow className="cursor-pointer" onClick={() => setOpenId(openId === v.id ? null : v.id)}>
                  <TableCell className="text-xs">{fmtDate(v.created_at)}</TableCell>
                  <TableCell className="font-mono text-xs">{v.reported_version ?? "-"}</TableCell>
                  <TableCell className="text-xs">{v.tool_count}</TableCell>
                  <TableCell className="text-xs">
                    {(v.diff as { initial?: boolean } | null)?.initial ? "최초" : (
                      <span className="space-x-2">
                        {count(v.diff, "added") > 0 && <span className="text-emerald-700 dark:text-emerald-400">+{count(v.diff, "added")}</span>}
                        {count(v.diff, "removed") > 0 && <span className="text-red-700 dark:text-red-400">-{count(v.diff, "removed")}</span>}
                        {count(v.diff, "changed") > 0 && <span className="text-amber-700 dark:text-amber-400">~{count(v.diff, "changed")}</span>}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs">{v.source ?? "-"}{v.deployment_name ? ` · ${v.deployment_name}` : ""}</TableCell>
                  <TableCell className="font-mono text-[11px] text-muted-foreground">{v.capability_hash.slice(0, 12)}</TableCell>
                </TableRow>
                {openId === v.id && (
                  <TableRow>
                    <TableCell colSpan={6} className="space-y-2 bg-muted/30">
                      {v.diff && <DiffView diff={v.diff} />}
                      {detail?.id === v.id && <JsonBlock value={detail.snapshot} />}
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
