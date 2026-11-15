"use client"

import { useCallback, useEffect, useState, type ReactNode } from "react"
import Link from "next/link"
import { History, Table2 } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { StatusBadge } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { CodeViewer } from "@/components/code-viewer"
import { datasetHref } from "@/features/datasets/links"
import { fmtDuration, fmtTime } from "@/features/datasources/sync-format"
import {
  ACTIVE_QUALITY_STATUSES,
  fetchQualityRun,
  fetchQualityRunQueries,
  fetchQualityRunResults,
  loadQualityIndicators,
  type QualityIndicator,
  type QualityRuleResult,
  type QualityRun,
  type QualityRunQuery,
} from "@/features/quality/api"
import {
  errorRateOf,
  fmtErrorRate,
  describeMeasured,
  engineLabel,
  isProfileRun,
  QUALITY_STATUS_LABEL,
  QUALITY_STATUS_TONE,
  QUALITY_TRIGGER_LABEL,
  qualityElapsedSeconds,
  qualityPercent,
  checkResult,
} from "@/features/quality/format"
import { qualityRunsHref } from "@/features/quality/links"
import { useLatest, usePolling } from "@/features/quality/use-polling"
import { errorMessage, notifyError } from "@/lib/notify"

const POLL_MS = 3000

type Props = {
  runId: number | null
  onOpenChange: (open: boolean) => void
}

/** 진단 실행 1건의 상세 — 개요(대상·시각·건수·점수), 이 실행의 규칙별 결과, 낸 쿼리(Trino 쿼리 ID)를 탭으로 나눠 본다. */
export function QualityRunDetailDialog({ runId, onOpenChange }: Props) {
  const [run, setRun] = useState<QualityRun | null>(null)
  const [results, setResults] = useState<QualityRuleResult[] | null>(null)
  const [indicators, setIndicators] = useState<QualityIndicator[]>([])
  const [tab, setTab] = useState("summary")
  const [queries, setQueries] = useState<QualityRunQuery[] | null>(null)
  // 조회 실패는 "없음" 과 구분해 보여 준다
  const [runError, setRunError] = useState<string | null>(null)
  const [resultsError, setResultsError] = useState<string | null>(null)
  const [queriesError, setQueriesError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const begin = useLatest()

  const load = useCallback(async (id: number, quiet = false) => {
    const current = begin()
    try {
      const next = await fetchQualityRun(id)
      if (!current()) return // 그 사이 다른 실행을 열었다
      setRun(next)
      setRunError(null)
    } catch (e) {
      if (!current()) return
      const message = errorMessage(e) ?? String(e)
      setRunError(message)
      if (!quiet) notifyError(e, "품질 진단 실행을 불러오지 못했습니다.")
    }
  }, [begin])

  useEffect(() => {
    setRun(null)
    setResults(null)
    setQueries(null)
    setRunError(null)
    setTab("summary")
    if (runId != null) void load(runId)
  }, [runId, load])

  // 규칙별 결과는 실행이 끝난 뒤에 생긴다 — 끝난 상태가 되면 읽는다
  const finished = run != null && !ACTIVE_QUALITY_STATUSES.has(run.status)
  useEffect(() => {
    if (runId == null || !finished) return
    let stale = false
    setResultsError(null)
    fetchQualityRunResults(runId).then((r) => !stale && setResults(r))
      .catch((e) => !stale && setResultsError(errorMessage(e) ?? String(e)))
    loadQualityIndicators().then((i) => !stale && setIndicators(i)).catch(() => undefined)
    return () => {
      stale = true
    }
  }, [runId, finished, reload])

  // 진단 쿼리 — 쿼리 탭을 열었을 때 읽고, 실행 중이면 쿼리 ID 가 늘 때마다 다시 읽는다
  const queryCount = run?.query_ids.length ?? 0
  const started = run != null && run.status !== "QUEUED"
  useEffect(() => {
    if (runId == null || tab !== "queries" || !started) return
    let stale = false
    setQueriesError(null)
    fetchQualityRunQueries(runId).then((q) => !stale && setQueries(q))
      .catch((e) => !stale && setQueriesError(errorMessage(e) ?? String(e)))
    return () => {
      stale = true
    }
  }, [runId, tab, started, queryCount, finished, reload])

  // 끝날 때까지 다시 읽는다. 한 번 실패해도 다음 차례에 다시 읽는다
  const active = run != null && ACTIVE_QUALITY_STATUSES.has(run.status)
  usePolling(runId != null && active, async () => {
    if (runId != null) await load(runId, true)
  }, POLL_MS)

  const failed = (message: string) => (
    <p className="flex flex-wrap items-center gap-2 text-sm text-destructive">
      불러오지 못했습니다: {message}
      <Button variant="outline" size="sm" onClick={() => setReload((n) => n + 1)}>다시 시도</Button>
    </p>
  )

  const pct = run ? qualityPercent(run) : null
  return (
    <Dialog open={runId != null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="break-all">{run ? (run.dataset_name ?? `데이터셋 #${run.dataset_id}`) : "품질 진단"}</span>
            {run && <StatusBadge tone={QUALITY_STATUS_TONE[run.status]}>{QUALITY_STATUS_LABEL[run.status]}</StatusBadge>}
            {run?.unresponsive && <span className="text-xs font-normal text-destructive">워커 응답 없음 ⚠</span>}
          </DialogTitle>
          <DialogDescription>
            {run
              ? [`진단 실행 #${run.id}`, run.datasource_name, `${fmtTime(run.started_at ?? run.queued_at)} ${run.started_at ? "시작" : "접수"}`]
                .filter(Boolean).join(" · ")
              : runError ? "진단 실행을 불러오지 못했습니다." : "불러오는 중…"}
          </DialogDescription>
        </DialogHeader>
        {runError && (
          <p className="flex flex-wrap items-center gap-2 text-sm text-destructive">
            {run ? `새로고침하지 못했습니다 — 마지막으로 읽은 내용입니다. (${runError})` : runError}
            {!run && runId != null && (
              <Button variant="outline" size="sm" onClick={() => void load(runId)}>다시 시도</Button>
            )}
          </p>
        )}
        {run && (
          <div className="grid gap-4 text-sm">
            <div className="flex flex-wrap gap-2">
              <Button asChild variant="outline" size="sm">
                <Link href={datasetHref(run.dataset_id, "quality")}>
                  <Table2 className="mr-1 size-4" /> 데이터셋 품질 탭 열기
                </Link>
              </Button>
              {run.datasource_id != null && (
                <Button asChild variant="outline" size="sm">
                  <Link href={qualityRunsHref({ datasource: String(run.datasource_id), period: "all" })}
                    onClick={() => onOpenChange(false)}>
                    <History className="mr-1 size-4" /> 이 데이터 소스의 전체 이력
                  </Link>
                </Button>
              )}
            </div>

            {run.status === "RUNNING" && pct !== null && (
              <div className="grid gap-1">
                <div className="h-2 overflow-hidden rounded bg-muted">
                  <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
                </div>
                <span className="text-sm">
                  쿼리 {run.progress_done}/{run.progress_total}{run.current_target ? ` · ${run.current_target}` : ""}
                </span>
              </div>
            )}

            <Tabs value={tab} onValueChange={setTab}>
              <TabsList variant="line">
                <TabsTrigger value="summary">개요</TabsTrigger>
                <TabsTrigger value="results">규칙별 결과{results ? ` (${results.length})` : ""}</TabsTrigger>
                <TabsTrigger value="queries">
                  {run.engine === "DIRECT" ? "원천 쿼리" : `Trino 쿼리 (${run.query_ids.length})`}
                </TabsTrigger>
              </TabsList>

              {/* 대화상자 크기는 개요에 맞춘다 — 개요는 늘 자리를 차지하고, 다른 탭은 그 자리 안에서 스크롤한다 */}
              <div className="relative mt-3">
              <TabsContent value="summary" forceMount className="mt-0 data-[state=inactive]:invisible">
                <KeyValueTable items={[
                  { key: "원본 데이터 소스", value: run.datasource_name ?? "-" },
                  { key: "원본 테이블", value: <span className="break-all">{run.dataset_name ?? `데이터셋 #${run.dataset_id}`}</span> },
                  { key: "엔진", value: engineLabel(run.engine) },
                  { key: "엔진 데이터 소스", value: run.engine_datasource_name ?? "-" },
                  run.engine === "DIRECT" ? {
                    key: "읽은 테이블",
                    value: (
                      <span className="break-all" title="품질 워커가 원천 DB 에서 직접 읽은 테이블 — 데이터 소스.스키마.테이블">
                        <span className="font-mono">{run.engine_table ?? "-"}</span>
                        <span className="ml-2">데이터 소스.스키마.테이블</span>
                      </span>
                    ),
                    wide: true,
                  } : {
                    key: "Trino 테이블",
                    value: (
                      <span className="break-all" title="Trino 가 이 테이블을 부르는 이름 — Trino 카탈로그.스키마.테이블">
                        <span className="font-mono">{run.engine_table ?? "-"}</span>
                        <span className="ml-2">Trino 카탈로그.스키마.테이블</span>
                      </span>
                    ),
                    wide: true,
                  },
                  { key: "실행 방식", value: QUALITY_TRIGGER_LABEL[run.trigger_type] ?? run.trigger_type },
                  { key: "요청자", value: run.requested_by ?? "-" },
                  run.job_name ? { key: "품질 진단 작업", value: run.job_name } : null,
                  { key: "종류", value: isProfileRun(run) ? "프로파일 (컬럼 통계 수집)" : "규칙 진단" },
                  { key: "품질 지표", value: isProfileRun(run) ? "-" : describeMeasured(run, indicators) },
                  { key: "접수 시각", value: fmtTime(run.queued_at) },
                  { key: "시작 시각", value: run.started_at ? fmtTime(run.started_at) : "아직 시작하지 않음" },
                  { key: "종료 시각", value: run.finished_at ? fmtTime(run.finished_at) : "-" },
                  { key: "소요 시간", value: run.started_at ? fmtDuration(qualityElapsedSeconds(run)) : "-" },
                  { key: "워커", value: <span className="break-all font-mono">{run.worker_id ?? "-"}</span>, wide: true },
                  { key: "활성 규칙", value: `${run.rules_total.toLocaleString()}개` },
                  {
                    key: "평가한 규칙",
                    value: (
                      <span title={run.rules_total > run.rules_evaluated && finished
                        ? "나머지는 고르지 않은 진단 기준이거나 이 경로로 평가하지 않는 유형 — 직전 결과를 이어받습니다" : undefined}>
                        {run.rules_evaluated.toLocaleString()}개
                        {run.rules_total > run.rules_evaluated && finished && (
                          <span className="ml-1">(나머지는 직전 결과를 이어받음)</span>
                        )}
                      </span>
                    ),
                  },
                  { key: "진단 건수", value: run.checked_count != null ? `${run.checked_count.toLocaleString()}건` : "-" },
                  { key: "오류 건수", value: run.checked_count != null ? `${(run.error_count ?? 0).toLocaleString()}건` : "-" },
                  {
                    key: "오류율",
                    value: run.checked_count != null
                      ? <span className={run.error_rate ? "text-destructive" : ""}>{fmtErrorRate(run.error_rate)}</span>
                      : "-",
                  },
                  run.cancel_requested_by ? { key: "중지 요청", value: run.cancel_requested_by } : null,
                  run.score != null ? { key: "품질 점수", value: <ScoreBar score={run.score} />, wide: true } : null,
                  run.error_message ? {
                    key: run.status === "SUCCEEDED" ? "참고" : "오류",
                    value: <span className={`break-all ${run.status === "SUCCEEDED" ? "" : "text-destructive"}`}>{run.error_message}</span>,
                    wide: true,
                  } : null,
                ]} />
              </TabsContent>

              <TabsContent value="results" className="absolute inset-0 mt-0 overflow-auto">
                {!finished ? (
                  <p className="text-sm">규칙별 결과는 실행이 끝나면 나옵니다.</p>
                ) : resultsError ? failed(resultsError) : results == null ? (
                  <p className="text-sm text-muted-foreground">불러오는 중…</p>
                ) : results.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    이 실행이 남긴 규칙 결과가 없습니다(결과를 저장하지 못한 실행이거나, 실행 ID 를 남기기 전의 실행입니다).
                  </p>
                ) : (
                  <div className="rounded border">
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 bg-muted text-left">
                        <tr>
                          <th className="px-2 py-1.5">규칙</th>
                          <th className="px-2 py-1.5">진단 기준</th>
                          <th className="px-2 py-1.5">결과</th>
                          <th className="px-2 py-1.5 text-right">진단</th>
                          <th className="px-2 py-1.5 text-right">오류</th>
                          <th className="px-2 py-1.5 text-right">오류율</th>
                        </tr>
                      </thead>
                      <tbody>
                        {results.map((r) => {
                          const rate = errorRateOf(r.checked_count, r.error_count)
                          return (
                            <tr key={r.id ?? r.rule_id} className="border-t align-top">
                              <td className="px-2 py-1.5">
                                <div className="font-medium">{r.rule_name ?? `규칙 #${r.rule_id}`}</div>
                                <div >
                                  {r.check_type}{r.column_name ? ` · ${r.column_name}` : ""}
                                </div>
                                {r.passed === "false" && r.detail && (
                                  <div className="break-all text-destructive">{r.detail}</div>
                                )}
                              </td>
                              <td className="px-2 py-1.5">{indicators.find((i) => i.key === r.indicator)?.name ?? r.indicator ?? "-"}</td>
                              <td className="px-2 py-1.5">
                                <StatusBadge {...checkResult(r.passed === "true")} />
                              </td>
                              <td className="px-2 py-1.5 text-right">{r.checked_count?.toLocaleString() ?? "-"}</td>
                              <td className="px-2 py-1.5 text-right">{r.error_count?.toLocaleString() ?? "-"}</td>
                              <td className={`px-2 py-1.5 text-right ${rate ? "text-destructive" : ""}`}>{fmtErrorRate(rate)}</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </TabsContent>

              <TabsContent value="queries" className="absolute inset-0 mt-0 grid content-start gap-3 overflow-auto">
                {!started ? (
                  <p className="text-sm">진단 쿼리는 워커가 실행을 시작하면 나옵니다.</p>
                ) : queriesError ? failed(queriesError) : queries == null ? (
                  <p className="text-sm text-muted-foreground">불러오는 중…</p>
                ) : queries.length === 0 ? (
                  <p className="text-sm text-muted-foreground">이 실행의 진단 쿼리를 다시 만들 수 없습니다(실행 계획이 남아 있지 않습니다).</p>
                ) : (
                  queries.map((q, n) => (
                    <div key={`${q.key}-${q.retry}`} className="grid gap-1.5">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="font-medium">{n + 1}. {q.label}</span>
                        {q.retry && <Badge variant="outline" title="묶은 스캔 쿼리가 실패해 규칙별로 다시 실행한 쿼리">규칙별 재실행</Badge>}
                        <span className="ml-auto font-mono">
                          {q.query_id ?? (unmatchedIds(run, queries) ? "" : "실행하지 않음")}
                        </span>
                      </div>
                      <CodeViewer
                        code={q.formatted_sql}
                        language="sql"
                        copyLabel="진단 쿼리"
                        border
                        className="rounded"
                        height={Math.min(320, Math.max(96, q.formatted_sql.split("\n").length * 19 + 30))}
                        options={{ folding: false, glyphMargin: false, lineDecorationsWidth: 8 }}
                      />
                    </div>
                  ))
                )}
                {(queries == null || unmatchedIds(run, queries)) && run.query_ids.length > 0 && started && (
                  <div className="grid gap-1">
                    <span className="font-medium">Trino 쿼리 ID ({run.query_ids.length})</span>
                    <div className="max-h-40 overflow-auto rounded border p-3 font-mono text-sm">
                      {run.query_ids.map((q) => <div key={q}>{q}</div>)}
                    </div>
                  </div>
                )}
                <p className="text-sm">
                  {run.engine === "DIRECT"
                    ? "품질 워커가 원천 DB 에 낸 읽기 쿼리입니다(읽기 전용 트랜잭션). 테이블을 한 번 읽으며 모든 규칙을 함께 평가합니다."
                    : "품질 워커가 Trino 에서 실행한 쿼리입니다. 같은 테이블의 규칙은 스캔 쿼리 하나로 묶습니다. Trino UI 에서 쿼리 ID 로 쿼리 계획과 소요 시간을 볼 수 있습니다."}
                </p>
              </TabsContent>
              </div>
            </Tabs>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

/** 쿼리 ID 를 쿼리와 순서로 짝짓지 못했는가 — 그러면 ID 를 따로 목록으로 보여 준다. */
function unmatchedIds(run: QualityRun, queries: QualityRunQuery[]): boolean {
  return run.query_ids.length > 0 && queries.every((q) => q.query_id == null)
}

type KeyValue = { key: string; value: ReactNode; /** 값이 길어 한 줄을 다 쓰는 항목 */ wide?: boolean }

/**
 * 키-값 표 — 한 줄에 두 쌍씩 놓고, 긴 값(wide)은 한 줄을 다 쓴다. 키는 회색 배경.
 * 없는 항목(null)은 건너뛴다.
 */
function KeyValueTable({ items }: { items: (KeyValue | null)[] }) {
  const rows: KeyValue[][] = []
  let pending: KeyValue | null = null
  for (const item of items) {
    if (!item) continue
    if (item.wide) {
      if (pending) rows.push([pending])
      pending = null
      rows.push([item])
    } else if (pending) {
      rows.push([pending, item])
      pending = null
    } else {
      pending = item
    }
  }
  if (pending) rows.push([pending])
  const keyClass = "w-32 whitespace-nowrap bg-muted px-3 py-2 text-left align-top font-medium"
  return (
    <div className="overflow-hidden rounded border">
      <table className="w-full table-fixed text-sm">
        <tbody className="[&>tr]:border-b [&>tr:last-child]:border-b-0">
          {rows.map((row) => (
            <tr key={row[0]!.key}>
              {row.map((item, n) => (
                <KeyValueCells key={item.key} item={item} keyClass={keyClass} span={row.length === 1 ? 3 : 1} divider={n > 0} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function KeyValueCells({ item, keyClass, span, divider }: { item: KeyValue; keyClass: string; span: number; divider: boolean }) {
  return (
    <>
      <th scope="row" className={`${keyClass} ${divider ? "border-l" : ""}`}>{item.key}</th>
      <td colSpan={span} className="px-3 py-2 align-top">{item.value}</td>
    </>
  )
}

/** 품질 점수(0~100)를 막대로 — 숫자는 막대 옆에 함께 적는다. ``compact`` 는 목록의 칸에 넣는 작은 모양. */
export function ScoreBar({ score, compact = false }: { score: number; compact?: boolean }) {
  const value = Math.min(100, Math.max(0, score))
  const text = Number.isInteger(score) ? String(score) : score.toFixed(1)
  return (
    <div className={`flex items-center ${compact ? "gap-2" : "gap-3"}`} role="meter" aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={value} aria-label="품질 점수" title={`품질 점수 ${text} / 100 — 이 실행에서 평가한 규칙 가운데 통과한 비율`}>
      <div className={`overflow-hidden rounded bg-muted ${compact ? "h-1.5 w-16" : "h-2.5 flex-1"}`}>
        <div className="h-full rounded bg-primary transition-all" style={{ width: `${value}%` }} />
      </div>
      <span className={`shrink-0 text-right tabular-nums ${compact ? "w-9" : "w-24"}`}>
        <span className={compact ? "" : "font-semibold"}>{text}</span>
        {!compact && <span > / 100</span>}
      </span>
    </div>
  )
}
