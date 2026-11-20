"use client"

/**
 * 데이터셋 상세 › AI-Ready 탭 — 「공공데이터의 인공지능 친화적 관리 가이드라인」 부록 4 체크리스트 점검 (TODO-096 2단계).
 *
 * - 자동 판정은 서버가 조회할 때 계산한다(메타데이터·스키마·품질·표준·리니지). 계산할 수 없는 항목은 '판정 필요'.
 * - 수동 판정(적정·미흡·해당없음 + 근거)은 자동 판정보다 우선한다. 데이터셋 소유자(생성자 · 소유자 목록)·관리자만 바꿀 수 있다.
 * - 필수·권장 충족률을 따로 보여 주고, 공유 최소 요건 4개는 배지로 따로 판정한다.
 * - 아래에 배포본 관리와 DCAT·Croissant·데이터 카드 내보내기(3단계, ai-ready-export-section)를 둔다.
 */

import { DisabledReason, Tip } from "@/components/tip"
import { Fragment, useCallback, useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { ArrowRight, CheckCircle2, CircleHelp, Loader2, MinusCircle, Pencil, RotateCcw, Sparkles, XCircle } from "lucide-react"

import { StatusBadge, type StatusTone } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Label } from "@workspace/ui/components/label"
import { RadioGroup, RadioGroupItem } from "@workspace/ui/components/radio-group"
import { Switch } from "@workspace/ui/components/switch"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@workspace/ui/components/table"
import { Textarea } from "@workspace/ui/components/textarea"
import { cn } from "@workspace/ui/lib/utils"

import { scrollToAndFlash } from "@/lib/focus-flash"
import { errorMessage, notifyError, notifySuccess } from "@/lib/notify"

import {
  clearAiReadyJudgment, fetchAiReadyCheck, saveAiReadyJudgment,
  type AiReadyCheck, type AiReadyItem, type AiReadyRate, type AiReadyStatus,
} from "../api"
import { SegmentedToggle } from "@/components/model-hub/segmented-toggle"
import { DistributionsCard, ExportCard } from "./ai-ready-export-section"
import { AI_READY_TARGETS } from "../ai-ready-targets"
import { datasetHref } from "../links"

export const STATUS_LABEL: Record<AiReadyStatus | "UNKNOWN", string> = {
  PASS: "적정", FAIL: "미흡", NA: "해당없음", UNKNOWN: "판정 필요",
}

export const STATUS_TONE: Record<AiReadyStatus | "UNKNOWN", StatusTone> = {
  PASS: "success", FAIL: "danger", NA: "muted", UNKNOWN: "warning",
}

/** 공유 최소 요건 충족 여부 — 상세 탭 · AI-Ready 점검 목록 공용. */
export function MinRequirementsBadge({ met }: { met: boolean }) {
  return <StatusBadge tone={met ? STATUS_TONE.PASS : STATUS_TONE.FAIL}>{met ? "충족" : "미충족"}</StatusBadge>
}

const STATUS_ICON = {
  PASS: CheckCircle2, FAIL: XCircle, NA: MinusCircle, UNKNOWN: CircleHelp,
} as const

export function AiReadyStatusBadge({ status }: { status: AiReadyStatus | null }) {
  const s = status ?? "UNKNOWN"
  return <StatusBadge tone={STATUS_TONE[s]} icon={STATUS_ICON[s]} label={STATUS_LABEL[s]} />
}

/** 충족률 — 적정 수 / (항목 수 − 해당없음). */
/** 충족률 색 기준 — 100% 초록, 70% 이상 주황, 그 밖 빨강. 화면에 ``RATE_LEGEND`` 로 안내한다 */
export const RATE_WARN = 70
export const RATE_LEGEND = `충족률 색: 100% 초록 · ${RATE_WARN}% 이상 주황 · 그 밖 빨강`

export function rateTone(rate: number | null): string {
  if (rate === null) return "text-muted-foreground"
  return rate >= 100 ? "text-emerald-600" : rate >= RATE_WARN ? "text-amber-600" : "text-red-600"
}

/** 평균처럼 분자 · 분모가 없는 % 값 */
export function PctText({ value, className }: { value: number | null; className?: string }) {
  return <span className={cn("tabular-nums", rateTone(value), className)}>{value === null ? "-" : `${value}%`}</span>
}

export function RateText({ rate, className }: { rate: AiReadyRate; className?: string }) {
  if (rate.rate === null) return <span className={cn("text-muted-foreground", className)}>-</span>
  return (
    <span className={cn("tabular-nums", rateTone(rate.rate), className)}>
      {rate.rate}% <span className="text-xs">({rate.passed}/{rate.total})</span>
    </span>
  )
}

function RateCard({ title, rate, note }: { title: string; rate: AiReadyRate; note: string }) {
  const pct = rate.rate ?? 0
  return (
    <div className="space-y-2 rounded-md border p-4">
        <div className="text-sm">{title}</div>
        <RateText rate={rate} className="text-2xl font-semibold" />
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={cn("h-full rounded-full", pct >= 100 ? "bg-emerald-500" : pct >= 70 ? "bg-amber-500" : "bg-red-500")}
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="text-xs">{note}</p>
    </div>
  )
}

type Filter = "all" | "attention" | "FAIL" | "UNKNOWN"

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "attention", label: "미흡·판정 필요" },
  { value: "FAIL", label: "미흡" },
  { value: "UNKNOWN", label: "판정 필요" },
]

function matches(item: AiReadyItem, filter: Filter, requiredOnly: boolean): boolean {
  if (requiredOnly && item.level !== "REQUIRED") return false
  if (filter === "all") return true
  if (filter === "FAIL") return item.status === "FAIL"
  if (filter === "UNKNOWN") return item.status === null
  return item.status === "FAIL" || item.status === null
}

const MODE_LABEL: Record<AiReadyItem["mode"], string> = {
  AUTO: "자동 판정",
  MANUAL: "담당자 판정",
  EXTERNAL: "제공 플랫폼 몫 — 담당자 판정",
}

/** 미흡 · 판정 필요 항목의 고칠 곳 링크(TODO-151) — 다른 탭은 주소로, 같은 탭의 배포본 · 내보내기 카드는 스크롤로. */
function FixLink({ datasetId, itemKey }: { datasetId: number; itemKey: string }) {
  const target = AI_READY_TARGETS[itemKey]
  if (!target) return null
  const cls = "mt-1 inline-flex items-center gap-1 text-xs text-primary hover:underline"
  if (target.kind === "card") {
    return (
      <button type="button" className={cls} onClick={() => scrollToAndFlash("ai-ready-card", target.card)}>
        {target.label}에서 고치기 <ArrowRight className="h-3 w-3" />
      </button>
    )
  }
  return (
    <Link href={datasetHref(datasetId, target.tab, { field: target.field })} className={cls}>
      {target.label}에서 고치기 <ArrowRight className="h-3 w-3" />
    </Link>
  )
}

function formatTime(v: string | null): string {
  if (!v) return ""
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" })
}

export function AiReadyCheckTab({
  datasetId, datasetName, canManage, canJudge = canManage, aiDraft,
}: {
  datasetId: number; datasetName: string; canManage: boolean
  /** 체크리스트 수동 판정 — 데이터셋 수정 권한에 AI-Ready 수동 판정(aiready.judge, TODO-124)까지 있을 때만 */
  canJudge?: boolean
  /** AI 초안(편향 · 한계 · 관련법령 · 개인정보 보호조치 · 허용 이용 유형, 빈 항목만) 단추 — 데이터셋을 고칠 수 있고 AI 생성 권한이 있을 때만 */
  aiDraft?: { enabled: boolean; busy: boolean; run: () => void }
}) {
  const [data, setData] = useState<AiReadyCheck | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>("all")
  const [requiredOnly, setRequiredOnly] = useState(false)
  const [editing, setEditing] = useState<AiReadyItem | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await fetchAiReadyCheck(datasetId))
    } catch (e) {
      setError(errorMessage(e) ?? "AI-Ready 점검 결과를 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [datasetId])

  useEffect(() => { load() }, [load])

  const groups = useMemo(() => {
    const out: { group: string; items: AiReadyItem[] }[] = []
    for (const item of data?.items ?? []) {
      if (!matches(item, filter, requiredOnly)) continue
      const last = out[out.length - 1]
      if (last && last.group === item.group) last.items.push(item)
      else out.push({ group: item.group, items: [item] })
    }
    return out
  }, [data, filter, requiredOnly])

  if (loading && !data) {
    return <div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
  }
  if (error || !data) {
    return (
      <div className="flex items-center justify-between gap-4 py-4 text-sm">
        <span className="text-destructive">{error ?? "AI-Ready 점검 결과가 없습니다."}</span>
        <Button size="sm" variant="outline" onClick={load}>다시 시도</Button>
      </div>
    )
  }

  const mr = data.min_requirements
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="min-w-0 flex-1 text-sm">
          행정안전부·NIA 「공공데이터의 인공지능 친화적 관리 가이드라인」 V1.0 부록 4 체크리스트입니다. 카탈로그에 있는 정보로 자동 판정하고,
          판정할 수 없는 항목은 담당자가 근거와 함께 판정합니다. 수동 판정은 자동 판정보다 우선합니다.
        </p>
        {aiDraft && (
          <DisabledReason reason={!aiDraft.enabled && "LLM 이 꺼져 있습니다 — 설정 › LLM 에서 켜세요"}>
            <Tip label={aiDraft.enabled && "비어 있는 편향 · 한계 · 관련법령 · 개인정보 보호조치 · 허용 이용 유형을 AI 가 메타데이터와 프로파일 통계로 초안을 씁니다 — 머리글의 AI 제안에서 읽고 적용합니다"}>
              <Button size="sm" variant="outline" onClick={aiDraft.run} disabled={aiDraft.busy || !aiDraft.enabled}>
                {aiDraft.busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Sparkles className="mr-1 h-4 w-4" />}
                AI 초안
              </Button>
            </Tip>
          </DisabledReason>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <RateCard title="필수 항목 충족률" rate={data.required} note="적정 / (필수 항목 − 해당없음). 판정 필요는 적정이 아닙니다." />
        <RateCard title="권장 항목 충족률" rate={data.recommended} note="권장 항목은 충족하지 않아도 경고로만 봅니다." />
        <div className="space-y-2 rounded-md border p-4">
            <div className="flex items-center justify-between">
              <span className="text-sm">공유 최소 요건</span>
              <MinRequirementsBadge met={mr.met} />
            </div>
            <ul className="space-y-1.5 text-sm">
              {mr.checks.map((c) => {
                const Icon = c.met === null ? CircleHelp : c.met ? CheckCircle2 : XCircle
                return (
                  <li key={c.key} className="flex gap-2">
                    <Icon className={cn("mt-0.5 h-4 w-4 shrink-0",
                      c.met === null ? "text-amber-500" : c.met ? "text-emerald-600" : "text-red-600")} />
                    <div className="min-w-0">
                      <div>{c.label}</div>
                      <div className="text-xs break-words">{c.detail}</div>
                    </div>
                  </li>
                )
              })}
            </ul>
        </div>
      </div>
      <p className="-mt-4 text-xs">{RATE_LEGEND}</p>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <SegmentedToggle value={filter} onChange={setFilter} options={FILTERS} />
          <span className="ml-2 text-xs">
            적정 {data.counts.PASS} · 미흡 {data.counts.FAIL} · 판정 필요 {data.counts.UNKNOWN} · 해당없음 {data.counts.NA}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Switch id="ai-ready-required-only" checked={requiredOnly} onCheckedChange={setRequiredOnly} />
          <Label htmlFor="ai-ready-required-only" className="text-sm font-normal">필수 항목만</Label>
        </div>
      </div>

      <div className="overflow-hidden rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-14">번호</TableHead>
                <TableHead className="w-16">구분</TableHead>
                <TableHead>점검 내용</TableHead>
                <TableHead className="w-28">판정</TableHead>
                <TableHead className="w-[34%]">근거</TableHead>
                {canJudge && <TableHead className="w-12" />}
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.length === 0 && (
                <TableRow>
                  <TableCell colSpan={canJudge ? 6 : 5} className="py-8 text-center text-sm text-muted-foreground">
                    조건에 맞는 항목이 없습니다.
                  </TableCell>
                </TableRow>
              )}
              {groups.map((g) => (
                <Fragment key={g.group}>
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableCell colSpan={canJudge ? 6 : 5} className="py-1.5 text-xs font-medium">
                      {g.group}
                    </TableCell>
                  </TableRow>
                  {g.items.map((item) => (
                    <TableRow key={item.key} className="align-top">
                      <TableCell className="font-mono text-xs">{item.key}</TableCell>
                      <TableCell>
                        <span className={cn("text-xs", item.level === "REQUIRED" ? "font-medium text-foreground" : "text-muted-foreground")}>
                          {item.level === "REQUIRED" ? "필수" : "권장"}
                        </span>
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        <div className="text-sm">{item.text}</div>
                        <div className="mt-0.5 text-xs">{MODE_LABEL[item.mode]}{item.hint ? ` · ${item.hint}` : ""}</div>
                        {item.status !== "PASS" && item.status !== "NA" && (
                          <FixLink datasetId={datasetId} itemKey={item.key} />
                        )}
                      </TableCell>
                      <TableCell>
                        <AiReadyStatusBadge status={item.status} />
                        <div className="mt-1 text-[11px]">
                          {item.source === "MANUAL" ? "수동" : item.source === "AUTO" ? "자동" : ""}
                        </div>
                      </TableCell>
                      <TableCell className="whitespace-normal text-sm">
                        {item.judgment ? (
                          <div className="space-y-1">
                            {item.judgment.note && <div className="break-words">{item.judgment.note}</div>}
                            <div className="text-xs">
                              {item.judgment.judged_by ?? "-"} · {formatTime(item.judgment.judged_at)}
                            </div>
                            {item.evidence && (
                              <div className="text-xs break-words">
                                자동 판정 {STATUS_LABEL[item.auto_status ?? "UNKNOWN"]}: {item.evidence}
                              </div>
                            )}
                          </div>
                        ) : (
                          <span className="break-words">{item.evidence || "-"}</span>
                        )}
                      </TableCell>
                      {canJudge && (
                        <TableCell>
                          <Tip label={`${item.key} 판정`}><Button size="icon" variant="ghost" className="h-7 w-7" aria-label={`${item.key} 판정`}
                            onClick={() => setEditing(item)}>
                            <Pencil className="h-3.5 w-3.5" />
                          </Button></Tip>
                        </TableCell>
                      )}
                    </TableRow>
                  ))}
                </Fragment>
              ))}
            </TableBody>
          </Table>
      </div>

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <div data-ai-ready-card="distributions" className="rounded-xl">
          <DistributionsCard datasetId={datasetId} canManage={canManage} onChanged={load} />
        </div>
        <div data-ai-ready-card="export" className="rounded-xl">
          <ExportCard datasetId={datasetId} datasetName={datasetName} />
        </div>
      </div>

      <JudgmentDialog
        datasetId={datasetId}
        item={editing}
        onClose={() => setEditing(null)}
        onSaved={(next) => { setData(next); setEditing(null) }}
      />
    </div>
  )
}

function JudgmentDialog({
  datasetId, item, onClose, onSaved,
}: {
  datasetId: number
  item: AiReadyItem | null
  onClose: () => void
  onSaved: (data: AiReadyCheck) => void
}) {
  const [status, setStatus] = useState<AiReadyStatus>("PASS")
  const [note, setNote] = useState("")
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!item) return
    setStatus(item.judgment?.status ?? item.auto_status ?? "PASS")
    setNote(item.judgment?.note ?? "")
  }, [item])

  const save = async () => {
    if (!item) return
    setSaving(true)
    try {
      onSaved(await saveAiReadyJudgment(datasetId, item.key, { status, note: note.trim() || null }))
      notifySuccess("판정을 저장했습니다.")
    } catch (e) {
      notifyError(e, "판정을 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  const revert = async () => {
    if (!item) return
    setSaving(true)
    try {
      onSaved(await clearAiReadyJudgment(datasetId, item.key))
      notifySuccess("자동 판정으로 되돌렸습니다.")
    } catch (e) {
      notifyError(e, "수동 판정을 지우지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={!!item} onOpenChange={(open) => { if (!open && !saving) onClose() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{item?.key} 판정</DialogTitle>
          <DialogDescription>{item?.text}</DialogDescription>
        </DialogHeader>
        {item && (
          <div className="space-y-4">
            <div className="rounded-md bg-muted/50 px-3 py-2 text-xs">
              자동 판정: {STATUS_LABEL[item.auto_status ?? "UNKNOWN"]}{item.evidence ? ` — ${item.evidence}` : ""}
            </div>
            <RadioGroup value={status} onValueChange={(v) => setStatus(v as AiReadyStatus)} className="flex gap-4">
              {(["PASS", "FAIL", "NA"] as const).map((s) => (
                <div key={s} className="flex items-center gap-1.5">
                  <RadioGroupItem id={`judge-${s}`} value={s} />
                  <Label htmlFor={`judge-${s}`} className="font-normal">{STATUS_LABEL[s]}</Label>
                </div>
              ))}
            </RadioGroup>
            <div className="space-y-1.5">
              <Label htmlFor="judge-note">근거</Label>
              <Textarea id="judge-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)}
                placeholder="판정 근거 — 문서 링크, 절차, 메모" />
            </div>
          </div>
        )}
        <DialogFooter className="gap-2 sm:justify-between">
          {item?.judgment ? (
            <Button variant="outline" onClick={revert} disabled={saving}>
              <RotateCcw className="mr-1 h-4 w-4" /> 자동 판정으로 되돌리기
            </Button>
          ) : <span />}
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose} disabled={saving}>취소</Button>
            <Button onClick={save} disabled={saving}>
              {saving && <Loader2 className="mr-1 h-4 w-4 animate-spin" />} 저장
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
