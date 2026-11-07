"use client"

/**
 * 배포 상세 — 감사 이벤트 탭 (Phase 2).
 *
 * 서버의 변경 도구 호출 감사 로그(Forge Audit JSONL)를 받아 승인 정책으로 판정한 결과를 보여준다.
 * 수집 경로: 서버 쪽 shipper 가 서비스 토큰으로 POST, 또는 화면에서 JSONL 업로드.
 * 서비스 토큰은 enforce 모드의 정책 번들 pull 에도 쓴다(Phase 3).
 */

import { Fragment, useCallback, useEffect, useState } from "react"
import { Copy, KeyRound, Loader2, RefreshCw, Trash2, Upload } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"
import { useConfirm } from "@/components/confirm-provider"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
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
import { Textarea } from "@workspace/ui/components/textarea"

import {
  fetchMcpAuditEvents,
  issueMcpIngestToken,
  revokeMcpIngestToken,
  uploadMcpAuditEvents,
} from "../../api"
import type { McpAuditEvent, McpAuditIngestResult, McpDeploymentDetail } from "../../data/schema"
import { DecisionBadge, OriginBadge, RiskBadge, fmtDate } from "../mcp-badges"
import { JsonBlock } from "../mcp-server-detail"

const ALL = "__all__"
const PAGE = 50

export function AuditPanel({ dep, onChanged, canEdit = true }: {
  dep: McpDeploymentDetail
  onChanged: () => Promise<void>
  /** 배포 등록자·관리자만 JSONL 업로드·서비스 토큰 발급/폐기(서버 _guard_deployment) */
  canEdit?: boolean
}) {
  const [rows, setRows] = useState<McpAuditEvent[] | null>(null)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [tool, setTool] = useState("")
  const [user, setUser] = useState("")
  const [decision, setDecision] = useState(ALL)
  const [openId, setOpenId] = useState<number | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await fetchMcpAuditEvents(dep.name, {
        tool: tool.trim() || undefined, user: user.trim() || undefined,
        decision: decision === ALL ? undefined : decision, page, pageSize: PAGE,
      })
      setRows(r.items)
      setTotal(r.total)
    } catch (e) {
      notifyError(e, "감사 이벤트를 불러오지 못했습니다.")
    }
  }, [dep.name, tool, user, decision, page])

  useEffect(() => {
    load()
  }, [load])

  return (
    <div className="flex flex-col gap-4">
      {canEdit && <IngestTokenCard dep={dep} onChanged={onChanged} />}

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2 text-sm">변경 도구 호출 <OriginBadge origin="server_config" /></CardTitle>
            <p className="text-xs text-muted-foreground">
              판정은 승인 리소스 범위(없으면 관측 범위) 기준입니다. “범위 밖”·“차단 도구” 호출은 최근 7일 기준 finding 이 됩니다.
              보존 기간: {dep.audit_retention_days}일.
            </p>
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={load}><RefreshCw className="mr-1 h-4 w-4" /> 새로고침</Button>
            {canEdit && (
              <Button size="sm" variant="outline" onClick={() => setUploadOpen(true)}><Upload className="mr-1 h-4 w-4" /> JSONL 업로드</Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input className="h-8 w-[180px]" placeholder="도구" value={tool} onChange={(e) => { setTool(e.target.value); setPage(1) }} />
            <Input className="h-8 w-[160px]" placeholder="사용자" value={user} onChange={(e) => { setUser(e.target.value); setPage(1) }} />
            <Select value={decision} onValueChange={(v) => { setDecision(v); setPage(1) }}>
              <SelectTrigger size="sm" className="w-[150px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>판정 전체</SelectItem>
                <SelectItem value="violation">위반만</SelectItem>
                <SelectItem value="enforced">서버가 거부(enforce)</SelectItem>
                <SelectItem value="allow">허용</SelectItem>
                <SelectItem value="deny">범위 밖</SelectItem>
                <SelectItem value="blocked">차단 도구</SelectItem>
                <SelectItem value="unknown">판정 불가</SelectItem>
              </SelectContent>
            </Select>
            <span className="text-xs text-muted-foreground">{total}건</span>
          </div>
          <AuditTable rows={rows} openId={openId} setOpenId={setOpenId} />
          {total > PAGE && (
            <div className="flex items-center justify-end gap-2 text-xs">
              <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>이전</Button>
              <span>{page} / {Math.ceil(total / PAGE)}</span>
              <Button size="sm" variant="outline" disabled={page * PAGE >= total} onClick={() => setPage(page + 1)}>다음</Button>
            </div>
          )}
        </CardContent>
      </Card>

      <UploadDialog open={uploadOpen} onOpenChange={setUploadOpen} name={dep.name}
        onDone={async () => { setPage(1); await load(); await onChanged() }} />
    </div>
  )
}

export function AuditTable({ rows, openId, setOpenId, showDeployment = false }: {
  rows: McpAuditEvent[] | null
  openId: number | null
  setOpenId: (id: number | null) => void
  showDeployment?: boolean
}) {
  const cols = showDeployment ? 8 : 7
  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[150px]">시각</TableHead>
            {showDeployment && <TableHead>배포</TableHead>}
            <TableHead>도구</TableHead>
            <TableHead>사용자 · 에이전트</TableHead>
            <TableHead>리소스</TableHead>
            <TableHead className="w-[60px]">결과</TableHead>
            <TableHead className="w-[90px]">판정</TableHead>
            <TableHead>사유</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {!rows ? (
            <TableRow><TableCell colSpan={cols} className="h-14 text-center"><Loader2 className="mx-auto h-4 w-4 animate-spin" /></TableCell></TableRow>
          ) : rows.length === 0 ? (
            <TableRow><TableCell colSpan={cols} className="h-14 text-center text-muted-foreground">감사 이벤트가 없습니다.</TableCell></TableRow>
          ) : rows.map((e) => (
            <Fragment key={e.id}>
              <TableRow className="cursor-pointer" onClick={() => setOpenId(openId === e.id ? null : e.id)}>
                <TableCell className="text-xs">{fmtDate(e.ts)}</TableCell>
                {showDeployment && <TableCell className="font-mono text-xs">{e.deployment_name}</TableCell>}
                <TableCell>
                  <span className="font-mono text-xs">{e.tool_name}</span>
                  <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
                    {e.group_key ?? "미등록 도구"} {e.risk && <RiskBadge risk={e.risk} />}
                  </div>
                </TableCell>
                <TableCell className="text-xs">
                  {e.user_name ?? "-"}
                  {e.agent_name && <div className="font-mono text-[11px] text-muted-foreground">{e.agent_name}</div>}
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {(e.resources ?? []).map((r) => (
                      <code key={`${r.type}:${r.value}`} className="rounded bg-muted px-1 text-[11px]">{r.type}={r.value}</code>
                    ))}
                  </div>
                </TableCell>
                <TableCell className="text-xs">{e.ok ? "성공" : <span className="text-destructive">실패</span>}</TableCell>
                <TableCell>
                  {e.server_decision === "denied" ? (
                    <Badge variant="outline" className="border-violet-300 text-[11px] text-violet-700 dark:text-violet-300" title="서버가 카탈로그 정책으로 거부(enforce)">
                      서버 거부
                    </Badge>
                  ) : (
                    <DecisionBadge decision={e.policy_decision} />
                  )}
                </TableCell>
                <TableCell className="max-w-[260px] truncate text-xs text-muted-foreground" title={e.policy_reason ?? ""}>{e.policy_reason}</TableCell>
              </TableRow>
              {openId === e.id && (
                <TableRow>
                  <TableCell colSpan={cols} className="bg-muted/30">
                    <div className="mb-2 grid gap-1 text-xs sm:grid-cols-3">
                      <div>세션: <span className="font-mono">{e.session_id ?? "-"}</span></div>
                      <div>결과 ID: <span className="font-mono">{e.result_id ?? "-"}</span></div>
                      <div>수신: {e.received_via === "token" ? "서비스 토큰" : "업로드"} · {fmtDate(e.received_at)}</div>
                    </div>
                    {e.error && <div className="mb-2 text-xs text-destructive">오류: {e.error}</div>}
                    {e.args && <JsonBlock value={e.args} />}
                  </TableCell>
                </TableRow>
              )}
            </Fragment>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function IngestTokenCard({ dep, onChanged }: { dep: McpDeploymentDetail; onChanged: () => Promise<void> }) {
  const askConfirm = useConfirm()
  const [issued, setIssued] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const url = typeof window !== "undefined"
    ? `${window.location.origin}/api/v1/mcp-deployments/${encodeURIComponent(dep.name)}/audit-events`
    : `/api/v1/mcp-deployments/${dep.name}/audit-events`
  const curl = `tail -n 500 audit.jsonl | jq -Rs '{jsonl: .}' | \\\n  curl -sS -X POST -H "Authorization: Bearer ${issued ?? "<서비스 토큰>"}" \\\n  -H "Content-Type: application/json" --data-binary @- ${url}`

  const issue = async () => {
    if (dep.has_ingest_token && !(await askConfirm({
      title: "서비스 토큰 재발급",
      description: "재발급하면 기존 토큰은 즉시 무효가 됩니다. 계속할까요?",
      confirmText: "재발급",
      destructive: true,
    }))) return
    setBusy(true)
    try {
      const r = await issueMcpIngestToken(dep.name)
      setIssued(r.token)
      await onChanged()
    } catch (e) {
      notifyError(e, "서비스 토큰을 발급하지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm"><KeyRound className="h-4 w-4" /> 서비스 토큰 <OriginBadge origin="argus" /></CardTitle>
        <p className="text-xs text-muted-foreground">
          이 배포 전용 토큰입니다 — 감사 로그 shipper 의 이벤트 전송과 서버의 정책 번들 pull(enforce)에 씁니다.
          원문은 발급 직후 한 번만 표시되고 해시만 저장됩니다.
        </p>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          {dep.has_ingest_token ? (
            <span className="text-xs">
              발급됨: <code className="rounded bg-muted px-1">{dep.ingest_token_hint}</code> · {fmtDate(dep.ingest_token_created_at)}
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">발급된 토큰이 없습니다.</span>
          )}
          <Button size="sm" variant="outline" className="ml-auto" disabled={busy} onClick={issue}>
            {dep.has_ingest_token ? "재발급" : "발급"}
          </Button>
          {dep.has_ingest_token && (
            <Button
              size="sm"
              variant="outline"
              className="text-destructive"
              disabled={busy}
              onClick={async () => {
                if (!(await askConfirm({
                  title: "서비스 토큰 폐기",
                  description: "토큰을 폐기할까요? 이 토큰을 쓰는 shipper 는 더 이상 보낼 수 없습니다.",
                  confirmText: "폐기",
                  destructive: true,
                }))) return
                try {
                  await revokeMcpIngestToken(dep.name)
                  toast.success("서비스 토큰을 폐기했습니다.")
                  setIssued(null)
                  await onChanged()
                } catch (e) {
                  notifyError(e, "서비스 토큰을 폐기하지 못했습니다.")
                }
              }}
            >
              <Trash2 className="mr-1 h-4 w-4" /> 폐기
            </Button>
          )}
        </div>
        {issued && (
          <div className="rounded-md border border-amber-300 bg-amber-50 p-2 dark:border-amber-700 dark:bg-amber-950/30">
            <div className="mb-1 text-xs font-medium text-amber-800 dark:text-amber-300">지금 복사하세요 — 다시 볼 수 없습니다.</div>
            <div className="flex items-center gap-2">
              <code className="flex-1 break-all text-xs">{issued}</code>
              <Button size="icon" variant="ghost" className="h-7 w-7" aria-label="복사"
                onClick={() => navigator.clipboard.writeText(issued).then(() => toast.success("복사했습니다."))}>
                <Copy className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
        <div>
          <div className="mb-1 text-xs text-muted-foreground">전송 예 (Forge <code>audit.path</code> 의 JSONL)</div>
          <pre className="overflow-x-auto rounded bg-muted p-2 text-[11px]">{curl}</pre>
        </div>
      </CardContent>
    </Card>
  )
}

function UploadDialog({ open, onOpenChange, name, onDone }: {
  open: boolean
  onOpenChange: (v: boolean) => void
  name: string
  onDone: () => Promise<void>
}) {
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<McpAuditIngestResult | null>(null)
  useEffect(() => {
    if (open) setResult(null)
  }, [open])

  const onFile = async (file: File | undefined) => {
    if (!file) return
    if (file.size > 20_000_000) {
      toast.error("20MB 이하 파일만 올릴 수 있습니다.")
      return
    }
    setText(await file.text())
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader><DialogTitle>감사 로그(JSONL) 업로드</DialogTitle></DialogHeader>
        <p className="text-xs text-muted-foreground">
          한 줄에 이벤트 하나: <code>{`{"ts","session","tool","user","args","ok","error"?,"id"?}`}</code>.
          이미 받은 줄은 중복으로 건너뛰고, 보존 기간보다 오래된 줄은 받지 않습니다.
        </p>
        <Input type="file" accept=".jsonl,.log,.txt,application/json" onChange={(e) => onFile(e.target.files?.[0])} />
        <Textarea rows={10} className="font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)}
          placeholder={'{"ts":"2026-10-21T11:15:30+0900","session":"…","tool":"create_topic","user":"…","args":{"topic":"orders"},"ok":true}'} />
        {result && (
          <div className="rounded-md border p-2 text-xs">
            받음 {result.received} · 저장 {result.inserted} · 중복 {result.duplicates} · 무효 {result.invalid} ·
            <span className={result.violations ? "font-medium text-destructive" : ""}> 위반 {result.violations}</span>
            {result.purged > 0 && ` · 보존 기간 경과 정리 ${result.purged}`}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>닫기</Button>
          <Button
            disabled={busy || !text.trim()}
            onClick={async () => {
              setBusy(true)
              try {
                const r = await uploadMcpAuditEvents(name, text)
                setResult(r)
                setText("")
                await onDone()
              } catch (e) {
                notifyError(e, "감사 이벤트를 업로드하지 못했습니다.")
              } finally {
                setBusy(false)
              }
            }}
          >
            {busy && <Loader2 className="mr-1 h-4 w-4 animate-spin" />} 업로드
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
