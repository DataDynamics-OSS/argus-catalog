"use client"

/**
 * 거버넌스 › 오류 신고 처리 — 데이터셋 오류 신고·개선 요청을 한곳에서 본다 (TODO-096 4단계, 체크리스트 C31).
 *
 * 데이터셋 댓글 중 분류가 '오류 신고'·'개선 요청'인 최상위 댓글이 접수되고 처리 기한(설정 › AI-Ready)이 붙는다.
 * 기본은 처리 중인 것을 기한 이른 순으로 — 기한을 넘긴 것이 위로 온다. 행을 누르면 데이터셋의 댓글 탭이 열리고,
 * 처리(상태·처리 결과·소명)는 그 댓글에서 데이터셋 소유자(생성자)·관리자가 한다.
 */

import { Tip } from "@/components/tip"
import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Bug, ChevronLeft, ChevronRight, Lightbulb, Loader2, MessageSquare } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@workspace/ui/components/table"
import { cn } from "@workspace/ui/lib/utils"

import { DashboardHeader } from "@/components/dashboard-header"
import { DueText, ReportStatusBadge } from "@/components/comments/report-status"
import { fetchReports, type ReportPage, type ReportState } from "@/features/comments/api"
import { datasetHref } from "@/features/datasets/links"
import { errorMessage } from "@/lib/notify"

const PAGE_SIZE = 20

const STATES: { value: ReportState; label: string; note: string }[] = [
  { value: "open", label: "처리 중", note: "접수 · 처리 중" },
  { value: "overdue", label: "기한 넘김", note: "처리 기한이 지난 미처리 신고 — C31 미흡" },
  { value: "closed", label: "끝남", note: "해결 · 소명" },
  { value: "all", label: "전체", note: "" },
]

type Category = "all" | "bug" | "suggestion"

function formatDate(v: string): string {
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleDateString("ko-KR", { year: "2-digit", month: "2-digit", day: "2-digit" })
}

export default function DataReportsPage() {
  const [data, setData] = useState<ReportPage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [state, setState] = useState<ReportState>("open")
  const [category, setCategory] = useState<Category>("all")
  const [page, setPage] = useState(1)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await fetchReports({ state, category: category === "all" ? null : category, page, pageSize: PAGE_SIZE }))
    } catch (e) {
      setError(errorMessage(e) ?? "오류 신고 목록을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [state, category, page])

  useEffect(() => { load() }, [load])

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1
  const counts = data?.counts

  return (
    <>
      <DashboardHeader title="오류 신고 처리" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <p className="text-sm">
          데이터셋 댓글로 접수된 오류 신고 · 개선 요청입니다. 처리 기한은 접수 때 정해지고(설정 › AI-Ready), 기한을 넘긴 미처리 신고가 있는
          데이터셋은 AI-Ready 체크리스트 C31 이 미흡입니다. 행을 누르면 데이터셋의 댓글 탭에서 답하고 처리 상태를 바꿉니다.
        </p>

        <div className="grid gap-3 sm:grid-cols-4">
          {STATES.map((s) => (
            <button key={s.value} type="button" onClick={() => { setState(s.value); setPage(1) }}
              className={cn("rounded-lg border p-3 text-left transition-colors hover:bg-muted/50",
                state === s.value && "border-primary ring-1 ring-primary")}>
              <div className="text-sm">{s.label}</div>
              <div className={cn("text-2xl font-semibold tabular-nums",
                s.value === "overdue" && (counts?.overdue ?? 0) > 0 && "text-red-600")}>
                {counts ? counts[s.value] : "-"}
              </div>
              {s.note && <div className="text-xs">{s.note}</div>}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {([["all", "모든 분류"], ["bug", "오류 신고"], ["suggestion", "개선 요청"]] as const).map(([v, label]) => (
            <Button key={v} size="sm" variant={category === v ? "default" : "outline"}
              onClick={() => { setCategory(v); setPage(1) }}>
              {label}
            </Button>
          ))}
        </div>

        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[22%]">데이터셋</TableHead>
                  <TableHead className="w-24">분류</TableHead>
                  <TableHead>내용</TableHead>
                  <TableHead className="w-28">접수</TableHead>
                  <TableHead className="w-24">상태</TableHead>
                  <TableHead className="w-40">기한</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading && !data && (
                  <TableRow>
                    <TableCell colSpan={6} className="py-10 text-center">
                      <Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" />
                    </TableCell>
                  </TableRow>
                )}
                {error && (
                  <TableRow>
                    <TableCell colSpan={6} className="py-8 text-center text-sm text-destructive">{error}</TableCell>
                  </TableRow>
                )}
                {data && !error && data.items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="py-10 text-center text-sm text-muted-foreground">
                      {state === "overdue" ? "기한을 넘긴 신고가 없습니다." : "해당하는 신고가 없습니다."}
                    </TableCell>
                  </TableRow>
                )}
                {data && !error && data.items.map((r) => {
                  const href = r.entity_id.match(/^\d+$/) ? datasetHref(Number(r.entity_id), "comments") : null
                  return (
                    <TableRow key={r.id} className={cn("align-top", r.overdue && "bg-red-50/50 dark:bg-red-950/20")}>
                      <TableCell className="whitespace-normal">
                        {href ? (
                          <Link href={href} className="break-all font-mono text-[13px] font-medium hover:underline">
                            {r.entity_path ?? r.entity_name ?? `#${r.entity_id}`}
                          </Link>
                        ) : <span >#{r.entity_id}</span>}
                        {r.entity_name && r.entity_path && r.entity_name !== r.entity_path && (
                          <div className="text-xs">{r.entity_name}</div>
                        )}
                      </TableCell>
                      <TableCell>
                        {r.category === "bug" ? (
                          <span className="inline-flex items-center gap-1 text-xs text-red-600"><Bug className="h-3 w-3" />오류 신고</span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs text-amber-600"><Lightbulb className="h-3 w-3" />개선 요청</span>
                        )}
                      </TableCell>
                      <TableCell className="whitespace-normal text-sm">
                        <div className="line-clamp-2 break-words">{r.content_plain || "(내용 없음)"}</div>
                        {r.resolution && (
                          <div className="mt-1 line-clamp-2 break-words text-xs">
                            {r.status === "REJECTED" ? "소명" : "처리"}{r.handled_by ? `(${r.handled_by})` : ""}: {r.resolution}
                          </div>
                        )}
                        {r.reply_count > 0 && (
                          <div className="mt-1 inline-flex items-center gap-1 text-xs">
                            <MessageSquare className="h-3 w-3" /> 답글 {r.reply_count}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="text-xs">
                        <div>{formatDate(r.created_at)}</div>
                        <div className="break-all">{r.author_name}</div>
                      </TableCell>
                      <TableCell><ReportStatusBadge status={r.status} /></TableCell>
                      <TableCell>
                        <DueText status={r.status} dueAt={r.due_at} resolvedAt={r.resolved_at} overdue={r.overdue} />
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        {data && data.total > PAGE_SIZE && (
          <div className="flex items-center justify-end gap-2 text-sm">
            <span >{data.total}건 중 {page}/{pages}쪽</span>
            <Tip label="이전 쪽"><Button size="icon" variant="outline" className="h-8 w-8" aria-label="이전 쪽"
              disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              <ChevronLeft className="h-4 w-4" />
            </Button></Tip>
            <Tip label="다음 쪽"><Button size="icon" variant="outline" className="h-8 w-8" aria-label="다음 쪽"
              disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
              <ChevronRight className="h-4 w-4" />
            </Button></Tip>
          </div>
        )}
      </div>
    </>
  )
}
