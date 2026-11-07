"use client"

/**
 * finding 목록 — 배포 상세 탭(``name`` 지정) 또는 통제 현황의 전체 목록(``name`` 없음).
 * 조건이 사라지면 자동 해소되므로 사용자는 "확인(acknowledge)" 만 한다.
 */

import { Fragment, useCallback, useEffect, useState } from "react"
import { BellRing, CheckCircle2, Loader2, RefreshCw, RotateCcw } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"
import { usePrompt } from "@/components/confirm-provider"

import { Button } from "@workspace/ui/components/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"

import { evaluateMcpFindings, fetchAllMcpFindings, fetchMcpFindings, updateMcpFinding } from "../../api"
import { FINDING_KIND_LABEL, RISK_OPTIONS, type McpFinding } from "../../data/schema"
import { FindingStatusBadge, RiskBadge, fmtDate } from "../mcp-badges"
import { useMcpServers } from "../mcp-provider"
import { JsonBlock } from "../mcp-server-detail"

const ALL = "__all__"

export function FindingsPanel({ name, onChanged, canEdit = true }: {
  name?: string
  onChanged?: () => Promise<void>
  /** 배포 화면에서 등록자·관리자가 아니면 다시 계산·확인 버튼을 숨긴다(서버 _guard_deployment) */
  canEdit?: boolean
}) {
  const askInput = usePrompt()
  const { setSelectedDeployment } = useMcpServers()
  const [rows, setRows] = useState<McpFinding[] | null>(null)
  const [total, setTotal] = useState(0)
  const [status, setStatus] = useState("unresolved")
  const [severity, setSeverity] = useState(ALL)
  const [kind, setKind] = useState(ALL)
  const [openId, setOpenId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    const p = { status: status === ALL ? undefined : status, severity: severity === ALL ? undefined : severity,
      kind: kind === ALL ? undefined : kind, pageSize: name ? 200 : 100 }
    try {
      const r = name ? await fetchMcpFindings(name, p) : await fetchAllMcpFindings({ ...p, status: p.status ?? "all" })
      setRows(r.items)
      setTotal(r.total)
    } catch (e) {
      notifyError(e, "finding 목록을 불러오지 못했습니다.")
    }
  }, [name, status, severity, kind])

  useEffect(() => {
    load()
  }, [load])

  const ack = async (f: McpFinding, next: "open" | "acknowledged") => {
    let note: string | null = null
    if (next === "acknowledged") {
      const input = await askInput({
        title: "finding 확인",
        label: "확인 메모 (선택)",
        multiline: true,
        confirmText: "확인 처리",
      })
      if (input === null) return // 취소
      note = input.trim() || null
    }
    try {
      await updateMcpFinding(f.deployment_name ?? name ?? "", f.id, { status: next, note })
      await load()
      await onChanged?.()
    } catch (e) {
      notifyError(e, "finding 상태를 변경하지 못했습니다.")
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger size="sm" className="w-[140px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="unresolved">미해소 전체</SelectItem>
            <SelectItem value="open">열림</SelectItem>
            <SelectItem value="acknowledged">확인됨</SelectItem>
            <SelectItem value="resolved">해소</SelectItem>
            <SelectItem value={ALL}>전체</SelectItem>
          </SelectContent>
        </Select>
        <Select value={severity} onValueChange={setSeverity}>
          <SelectTrigger size="sm" className="w-[120px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>심각도 전체</SelectItem>
            {RISK_OPTIONS.map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={kind} onValueChange={setKind}>
          <SelectTrigger size="sm" className="w-[200px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>종류 전체</SelectItem>
            {Object.entries(FINDING_KIND_LABEL).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}
          </SelectContent>
        </Select>
        <span className="text-xs text-muted-foreground">{total}건</span>
        {name && canEdit && (
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                await evaluateMcpFindings(name)
                await load()
                await onChanged?.()
                toast.success("finding 을 다시 계산했습니다.")
              } catch (e) {
                notifyError(e, "finding 을 다시 계산하지 못했습니다.")
              } finally {
                setBusy(false)
              }
            }}
          >
            {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1 h-4 w-4" />} 다시 계산
          </Button>
        )}
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[80px]">심각도</TableHead>
              {!name && <TableHead>배포</TableHead>}
              <TableHead>종류</TableHead>
              <TableHead>내용</TableHead>
              <TableHead className="w-[90px]">상태</TableHead>
              <TableHead className="w-[150px]">최근 확인</TableHead>
              <TableHead className="w-[90px]" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {!rows ? (
              <TableRow><TableCell colSpan={7} className="h-14 text-center"><Loader2 className="mx-auto h-4 w-4 animate-spin" /></TableCell></TableRow>
            ) : rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="h-14 text-center text-muted-foreground">
                  <CheckCircle2 className="mr-1 inline h-4 w-4 text-emerald-600" /> 해당하는 finding 이 없습니다.
                </TableCell>
              </TableRow>
            ) : rows.map((f) => (
              <Fragment key={f.id}>
                <TableRow className="cursor-pointer" onClick={() => setOpenId(openId === f.id ? null : f.id)}>
                  <TableCell><RiskBadge risk={f.severity} /></TableCell>
                  {!name && (
                    <TableCell>
                      <button
                        type="button"
                        className="text-left text-primary hover:underline"
                        onClick={(e) => { e.stopPropagation(); setSelectedDeployment(f.deployment_name) }}
                      >
                        <span className="font-mono text-xs">{f.deployment_name}</span>
                      </button>
                      <div className="text-[11px] text-muted-foreground">{f.environment} · {f.server_name}</div>
                    </TableCell>
                  )}
                  <TableCell className="text-xs">{FINDING_KIND_LABEL[f.kind] ?? f.kind}</TableCell>
                  <TableCell className="text-sm">
                    {f.title}
                    {f.alert_id && <BellRing className="ml-1 inline h-3.5 w-3.5 text-amber-600" aria-label="알림 발행됨" />}
                  </TableCell>
                  <TableCell><FindingStatusBadge status={f.status} /></TableCell>
                  <TableCell className="text-xs text-muted-foreground">{fmtDate(f.last_seen_at)}</TableCell>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    {canEdit && f.status === "open" && (
                      <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => ack(f, "acknowledged")}>확인</Button>
                    )}
                    {canEdit && f.status === "acknowledged" && (
                      <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => ack(f, "open")}>
                        <RotateCcw className="mr-1 h-3 w-3" /> 열기
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
                {openId === f.id && (
                  <TableRow>
                    <TableCell colSpan={7} className="bg-muted/30">
                      <div className="grid gap-2 text-xs sm:grid-cols-3">
                        <div>최초 발견: {fmtDate(f.first_seen_at)}</div>
                        <div>해소: {fmtDate(f.resolved_at)}</div>
                        <div>확인: {f.acknowledged_by ? `${f.acknowledged_by} (${fmtDate(f.acknowledged_at)})` : "-"}</div>
                      </div>
                      {f.ack_note && <div className="mt-1 text-xs">메모: {f.ack_note}</div>}
                      {f.detail && <div className="mt-2"><JsonBlock value={f.detail} /></div>}
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
