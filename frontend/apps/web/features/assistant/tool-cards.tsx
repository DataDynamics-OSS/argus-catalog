"use client"

/**
 * AI 어시스턴트 도구 실행 카드 (TODO-064, TODO-065).
 *
 * ai-service 가 `tool_result` 로 보내 주는 도구 결과(ai-service/argus_catalog_ai/tools.py 반환값)를 도구별로 그린다.
 * 같은 내용을 LLM 이 문장으로 다시 쓰면 숫자가 틀리거나 빠질 수 있어, 실제 결과를 카드로 함께 보여 준다.
 * 모든 카드는 펼쳐서 원본 결과(JSON)를 볼 수 있다. 결과 모양이 예상과 다르면 원본 보기만 남긴다.
 */

import { useState, type ReactNode } from "react"
import Link from "next/link"
import { CheckCircle2, ChevronDown, ChevronRight, Database, Wrench, XCircle } from "lucide-react"

import type { ToolCall } from "./use-assistant-stream"
import { arr, datasetHref, isObj, num, str, type Obj } from "./dataset-index"
import { ErdMiniGraph, LineageMiniGraph, type ErdResult, type LineageResult } from "./mini-graphs"

/** 도구 이름 → 사용자에게 보여줄 한글 라벨. */
export const TOOL_LABELS: Record<string, string> = {
  search_datasets: "카탈로그 검색",
  get_dataset_detail: "스키마 조회",
  get_erd: "ER 관계 조회",
  get_quality: "품질 조회",
  get_lineage: "리니지 조회",
  get_glossary_term: "용어집 조회",
  get_standard_compliance: "표준 준수 조회",
  get_quality_rule_recommendations: "품질 규칙 추천",
  validate_sql: "SQL 검증",
}

export function ToolCard({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false)
  const label = TOOL_LABELS[call.name] ?? call.name
  const running = call.result === undefined
  const error = isObj(call.result) && typeof call.result.error === "string" ? call.result.error : null
  const argText = Object.values(call.args ?? {}).filter((v) => typeof v === "string").join(", ")
  const body = running || error ? null : renderResult(call)

  return (
    <div className={`rounded-lg border-l-2 bg-muted/40 text-xs ${
      running ? "border-sky-400" : error ? "border-rose-400" : "border-emerald-400"
    }`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={running}
        aria-expanded={open}
        aria-label={`${label} 결과 ${open ? "접기" : "펼치기"}`}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left disabled:cursor-default"
      >
        <Wrench className="h-3 w-3 shrink-0" />
        <span className="font-medium">{label}</span>
        {argText && <span className="truncate">— {argText.slice(0, 60)}</span>}
        {running ? (
          <span className="ml-auto inline-flex items-center gap-1 text-sky-600">실행 중<Dots /></span>
        ) : (
          <span className="ml-auto inline-flex items-center gap-1">
            {error && <span className="text-rose-600">실패</span>}
            {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          </span>
        )}
      </button>
      {error && <div className="px-2.5 pb-1.5 text-rose-600">{error}</div>}
      {body && <div className="px-2.5 pb-2 text-sm text-foreground">{body}</div>}
      {open && !running && (
        <pre className="mx-2.5 mb-2 max-h-64 overflow-auto rounded-md border border-border bg-background p-2 font-mono text-[11px] leading-snug text-foreground">
          {JSON.stringify(call.result, null, 2)}
        </pre>
      )}
    </div>
  )
}

function renderResult(call: ToolCall): ReactNode {
  const r = call.result
  if (!isObj(r)) return null
  switch (call.name) {
    case "search_datasets":
      return <DatasetList datasets={arr(r.datasets)} />
    case "get_dataset_detail":
      return <DatasetDetail r={r} />
    case "get_quality":
      return <QualityCard r={r} datasetId={num(call.args?.dataset_id)} />
    case "validate_sql":
      return <SqlValidation r={r} />
    case "get_lineage":
      return (arr(r.upstream).length || arr(r.downstream).length)
        ? <LineageMiniGraph result={r as LineageResult} />
        : <Empty text="연결된 상·하류 데이터셋이 없습니다." />
    case "get_erd":
      return arr(r.relations).length
        ? <ErdMiniGraph result={r as ErdResult} focusId={num(call.args?.dataset_id)} />
        : <Empty text="FK 관계가 없습니다." />
    default:
      return null
  }
}

function Empty({ text }: { text: string }) {
  return <div className="text-xs">{text}</div>
}

function DatasetList({ datasets }: { datasets: Obj[] }) {
  if (!datasets.length) return <Empty text="찾은 데이터셋이 없습니다." />
  return (
    <ul className="space-y-1">
      {datasets.map((d, i) => {
        const id = num(d.id)
        const name = str(d.name) || "(이름 없음)"
        // URN 은 `{데이터 소스 ID}.{qualified_name}.dataset` — 앞부분으로 출처를 짐작하게 보여 준다
        const source = str(d.urn).split(".")[0]
        return (
          <li key={id ?? i} className="rounded-md border border-border bg-background px-2 py-1.5">
            <div className="flex items-center gap-1.5">
              <Database className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              {id !== null
                ? <Link href={datasetHref(id)} className="truncate font-medium text-primary hover:underline">{name}</Link>
                : <span className="truncate font-medium">{name}</span>}
              {source && <span className="ml-auto shrink-0 text-xs">{source}</span>}
            </div>
            {str(d.description) && <div className="mt-0.5 line-clamp-2 text-xs">{str(d.description)}</div>}
          </li>
        )
      })}
    </ul>
  )
}

function DatasetDetail({ r }: { r: Obj }) {
  const id = num(r.id)
  const columns = arr(r.columns)
  const pk = columns.filter((c) => c.pk === true).length
  const pii = columns.filter((c) => c.pii).length
  const rows = num(r.row_count)
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
      {id !== null && (
        <Link href={datasetHref(id)} className="font-medium text-sm text-primary hover:underline">{str(r.name)}</Link>
      )}
      <span>컬럼 {columns.length}</span>
      {pk > 0 && <span>PK {pk}</span>}
      {pii > 0 && <span className="text-amber-700">PII {pii}</span>}
      {rows !== null && <span>행 {rows.toLocaleString()}</span>}
      {str(r.quality_status) && <span>품질 {str(r.quality_status)}</span>}
    </div>
  )
}

/** 품질 점수 색 — 데이터셋 품질 탭과 같은 기준(90 이상 양호, 70 이상 주의). */
function scoreTone(score: number) {
  if (score >= 90) return "border-emerald-300 bg-emerald-50 text-emerald-700"
  if (score >= 70) return "border-amber-300 bg-amber-50 text-amber-700"
  return "border-rose-300 bg-rose-50 text-rose-700"
}

function QualityCard({ r, datasetId }: { r: Obj; datasetId: number | null }) {
  const score = num(r.score)
  const passed = num(r.passed)
  const total = num(r.total)
  const failed = arr(r.failed_rules)
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        {score !== null
          ? <span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${scoreTone(score)}`}>점수 {score}</span>
          : <span className="text-xs">점수 미산출</span>}
        {passed !== null && total !== null && <span className="text-xs">통과 {passed}/{total}</span>}
        {datasetId !== null && (
          <Link href={datasetHref(datasetId)} className="ml-auto text-xs text-primary hover:underline">데이터셋 보기</Link>
        )}
      </div>
      {failed.length > 0 && (
        <ul className="space-y-0.5 text-xs">
          {failed.slice(0, 5).map((f, i) => (
            <li key={i} className="flex gap-1.5">
              <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-rose-500" />
              <span>
                <span className="font-medium">{str(f.rule) || str(f.type)}</span>
                {str(f.column) && <span > · {str(f.column)}</span>}
                {str(f.severity) && <span > · {str(f.severity)}</span>}
              </span>
            </li>
          ))}
          {failed.length > 5 && <li >외 {failed.length - 5}개</li>}
        </ul>
      )}
    </div>
  )
}

function SqlValidation({ r }: { r: Obj }) {
  const ok = r.valid === true
  return (
    <div className={`inline-flex items-center gap-1.5 text-xs ${ok ? "text-emerald-700" : "text-rose-700"}`}>
      {ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
      <span className="font-medium">{ok ? "통과" : "거부"}</span>
      {!ok && str(r.reason) && <span>— {str(r.reason)}</span>}
    </div>
  )
}

function Dots() {
  return (
    <span className="inline-flex gap-0.5 items-center">
      <span className="h-1 w-1 rounded-full bg-muted-foreground/60 motion-safe:animate-bounce [animation-delay:-0.3s]" />
      <span className="h-1 w-1 rounded-full bg-muted-foreground/60 motion-safe:animate-bounce [animation-delay:-0.15s]" />
      <span className="h-1 w-1 rounded-full bg-muted-foreground/60 motion-safe:animate-bounce" />
    </span>
  )
}
