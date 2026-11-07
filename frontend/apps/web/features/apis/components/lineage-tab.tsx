"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { ArrowLeft, Check, Loader2, Trash2 } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"
import { useConfirm } from "@/components/confirm-provider"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import { Input } from "@workspace/ui/components/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { cn } from "@workspace/ui/lib/utils"

import {
  type ApiIncomingLineage, type ApiLineage, type LineageTargetOption,
  addApiLineage, deleteApiLineage, fetchApiIncomingLineage, searchLineageTargets,
} from "../api"
import { ApisLineageGraph } from "./apis-lineage-graph"

// ---------------------------------------------------------------------------
// 리니지 (provides / consumes / depends_on)
// ---------------------------------------------------------------------------

const LINEAGE_RELATIONS: { value: string; label: string; caption: string }[] = [
  { value: "provides", label: "제공 (provides)", caption: "이 API 가 데이터·기능을 제공하는 소비처" },
  { value: "consumes", label: "소비 (consumes)", caption: "이 API 가 내부에서 사용·소비하는 대상" },
  { value: "depends_on", label: "의존 (depends_on)", caption: "이 API 가 런타임에 의존하는 대상" },
]
const LINEAGE_TARGET_TYPES: { value: string; label: string }[] = [
  { value: "system", label: "시스템" },
  { value: "dataset", label: "데이터셋" },
  { value: "model", label: "모델" },
  { value: "agent", label: "AI 에이전트" },
  { value: "api", label: "API" },
]
const TARGET_TYPE_LABEL: Record<string, string> = Object.fromEntries(LINEAGE_TARGET_TYPES.map((t) => [t.value, t.label]))
const RELATION_LABEL: Record<string, string> = Object.fromEntries(LINEAGE_RELATIONS.map((r) => [r.value, r.label]))
// 자동완성을 지원하는 대상 유형(시스템은 카탈로그 엔티티가 아니라 자유 입력)
const SEARCHABLE_TYPES = new Set(["api", "dataset", "model", "agent"])
const REF_PLACEHOLDER: Record<string, string> = {
  system: "예: 결제 시스템", dataset: "이름으로 검색 (URN 저장)", model: "모델 이름으로 검색", agent: "에이전트 이름으로 검색", api: "API 이름으로 검색",
}

/** 대상 상세 화면 주소 — 카탈로그에 있는 대상만(데이터셋은 URN·이름으로 찾은 경우) */
function targetHref(type: string, ref: string, id?: number | null): string | null {
  if (type === "api") return `/dashboard/apis?api=${encodeURIComponent(ref)}`
  if (type === "model") return `/dashboard/models/${encodeURIComponent(ref)}`
  if (type === "agent") return `/dashboard/ai-agents?agent=${encodeURIComponent(ref)}`
  if (type === "dataset" && id) return `/dashboard/datasets/${id}`
  return null
}

function TargetName({ type, refValue, label, id }: { type: string; refValue: string; label: string | null; id?: number | null }) {
  const href = targetHref(type, refValue, id)
  const text = label || refValue
  return (
    <>
      {href ? <Link href={href} className="font-medium text-primary hover:underline">{text}</Link> : <span className="font-medium">{text}</span>}
      {label && <span className="ml-2 font-mono text-xs text-muted-foreground">{refValue}</span>}
    </>
  )
}

type SearchResult = { key: string; items: LineageTargetOption[] }

/** 대상 식별자 입력 — API·데이터셋·모델·에이전트는 카탈로그 검색 자동완성(자유 입력도 가능) */
function TargetRefInput({ type, value, onChange, onPick }: {
  type: string
  value: string
  onChange: (v: string) => void
  onPick: (o: LineageTargetOption) => void
}) {
  const [result, setResult] = useState<SearchResult | null>(null)
  const [open, setOpen] = useState(false)
  const q = value.trim()
  const searchable = SEARCHABLE_TYPES.has(type)
  const key = `${type}:${q}`

  useEffect(() => {
    if (!searchable || !q) return
    const ctrl = new AbortController()
    const timer = setTimeout(() => {
      searchLineageTargets(type, q, ctrl.signal)
        .then((items) => setResult({ key, items }))
        .catch(() => { if (!ctrl.signal.aborted) setResult({ key, items: [] }) })
    }, 250)
    return () => { clearTimeout(timer); ctrl.abort() }
  }, [searchable, type, q, key])

  const items = searchable && q && result?.key === key ? result.items : []
  // 이미 고른 값과 정확히 같은 항목 하나뿐이면 목록을 숨긴다
  const show = open && items.length > 0 && !(items.length === 1 && items[0]!.ref === q)
  return (
    <div className="relative">
      <Input
        className="h-8 w-[240px] text-sm"
        value={value}
        onChange={(e) => { onChange(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => { if (e.key === "Escape") setOpen(false) }}
        placeholder={REF_PLACEHOLDER[type] ?? "이름 또는 URN"}
        aria-autocomplete={searchable ? "list" : undefined}
      />
      {show && (
        <ul role="listbox" className="absolute left-0 top-9 z-20 max-h-60 w-[320px] overflow-y-auto rounded-md border bg-popover py-1 text-sm shadow-md">
          {items.map((o) => (
            <li key={o.ref}>
              <button
                type="button"
                role="option"
                aria-selected={o.ref === q}
                className="flex w-full flex-col items-start px-3 py-1.5 text-left hover:bg-muted"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => { onPick(o); setOpen(false) }}
              >
                <span className="font-medium">{o.label}</span>
                {o.label !== o.ref && <span className="max-w-full truncate font-mono text-[11px] text-muted-foreground">{o.ref}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

type IncomingResult = { key: string; items: ApiIncomingLineage[]; error?: boolean }

/** 들어오는 관계 — 다른 API 가 이 API 를 대상(API)으로 등록한 관계 */
function IncomingLineage({ apiName }: { apiName: string }) {
  const [result, setResult] = useState<IncomingResult | null>(null)
  const [seq, setSeq] = useState(0)
  const key = `${apiName}#${seq}`
  useEffect(() => {
    let alive = true
    fetchApiIncomingLineage(apiName)
      .then((items) => { if (alive) setResult({ key, items }) })
      .catch(() => { if (alive) setResult({ key, items: [], error: true }) })
    return () => { alive = false }
  }, [apiName, key])
  const cur = result?.key === key ? result : null
  return (
    <Card><CardContent className="pt-4">
      <div className="mb-2">
        <p className="flex items-center gap-1 text-sm font-medium"><ArrowLeft className="h-3.5 w-3.5" />들어오는 관계</p>
        <p className="text-xs text-muted-foreground">다른 API 가 이 API 를 대상으로 등록한 관계입니다(해당 API 의 리니지 탭에서 관리).</p>
      </div>
      {!cur ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />불러오는 중...</p>
      ) : cur.error ? (
        <p className="text-sm text-muted-foreground">
          들어오는 관계를 불러오지 못했습니다.{" "}
          <button type="button" className="text-primary hover:underline" onClick={() => setSeq((n) => n + 1)}>다시 시도</button>
        </p>
      ) : cur.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">이 API 를 대상으로 등록한 다른 API 가 없습니다.</p>
      ) : (
        <div className="overflow-hidden rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40"><tr>
              <th className="px-3 py-2 text-left font-medium text-muted-foreground">출발 API</th>
              <th className="w-[150px] px-3 py-2 text-left font-medium text-muted-foreground">관계</th>
              <th className="px-3 py-2 text-left font-medium text-muted-foreground">비고</th>
            </tr></thead>
            <tbody>
              {cur.items.map((e) => (
                <tr key={e.id} className="border-t">
                  <td className="px-3 py-2 align-top">
                    <TargetName type="api" refValue={e.source_api_name} label={e.source_display_name} />
                  </td>
                  <td className="px-3 py-2 align-top"><Badge variant="outline" className="text-xs">{RELATION_LABEL[e.relation] ?? e.relation}</Badge></td>
                  <td className="px-3 py-2 align-top text-muted-foreground">{e.note || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CardContent></Card>
  )
}

export function LineageTab({ apiName, lineage, canAdmin, onChanged }: {
  apiName: string
  lineage: ApiLineage[]
  /** 관계 추가·삭제 가능(관리자, 백엔드 AdminUser 가드) */
  canAdmin: boolean
  onChanged: () => Promise<void>
}) {
  const [relation, setRelation] = useState("consumes")
  const [targetType, setTargetType] = useState("dataset")
  const [targetRef, setTargetRef] = useState("")
  const [targetLabel, setTargetLabel] = useState("")
  const [note, setNote] = useState("")
  const [busy, setBusy] = useState(false)
  const [viewMode, setViewMode] = useState<"table" | "graph">("table")
  const askConfirm = useConfirm()

  const handleAdd = async () => {
    if (!targetRef.trim()) { toast.error("대상 식별자를 입력하세요."); return }
    setBusy(true)
    try {
      await addApiLineage(apiName, {
        relation, target_type: targetType, target_ref: targetRef.trim(),
        target_label: targetLabel.trim() || undefined, note: note.trim() || undefined,
      })
      setTargetRef(""); setTargetLabel(""); setNote("")
      await onChanged()
      toast.success("리니지를 추가했습니다.")
    } catch (e) { notifyError(e, "리니지를 추가하지 못했습니다.") } finally { setBusy(false) }
  }
  const handleDelete = async (id: number) => {
    if (!(await askConfirm({
      title: "리니지 삭제",
      description: "이 리니지 관계를 삭제하시겠습니까?",
      confirmText: "삭제",
      destructive: true,
    }))) return
    try { await deleteApiLineage(apiName, id) }
    catch (e) { notifyError(e, "리니지를 삭제하지 못했습니다."); return }
    toast.success("리니지를 삭제했습니다.")
    await onChanged()
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          이 API 가 제공(provides)·소비(consumes)·의존(depends_on)하는 시스템·데이터셋·모델·AI 에이전트·다른 API 와의 관계를 기록·관리하는 화면입니다. 관계를 등록하면 영향 범위 파악과 카탈로그 내 연결 추적에 활용됩니다.
        </p>
        <div className="flex shrink-0 overflow-hidden rounded-md border">
          <button type="button" onClick={() => setViewMode("table")} className={cn("px-3 py-1.5 text-xs", viewMode === "table" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:bg-muted")}>표</button>
          <button type="button" onClick={() => setViewMode("graph")} className={cn("px-3 py-1.5 text-xs", viewMode === "graph" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:bg-muted")}>그래프</button>
        </div>
      </div>

      {viewMode === "graph" && <ApisLineageGraph apiName={apiName} lineage={lineage} />}

      {viewMode === "table" && LINEAGE_RELATIONS.map((rel) => {
        const edges = lineage.filter((e) => e.relation === rel.value)
        return (
          <Card key={rel.value}><CardContent className="pt-4">
            <div className="mb-2">
              <p className="text-sm font-medium">{rel.label}</p>
              <p className="text-xs text-muted-foreground">{rel.caption}</p>
            </div>
            {edges.length === 0 ? (
              <p className="text-sm text-muted-foreground">등록된 관계가 없습니다.</p>
            ) : (
              <div className="overflow-hidden rounded-md border">
                <table className="w-full text-sm">
                  <thead className="bg-muted/40"><tr>
                    <th className="w-[110px] px-3 py-2 text-left font-medium text-muted-foreground">유형</th>
                    <th className="px-3 py-2 text-left font-medium text-muted-foreground">대상</th>
                    <th className="px-3 py-2 text-left font-medium text-muted-foreground">비고</th>
                    {canAdmin && <th className="w-[60px] px-3 py-2" />}
                  </tr></thead>
                  <tbody>
                    {edges.map((e) => (
                      <tr key={e.id} className="border-t">
                        <td className="px-3 py-2 align-top"><Badge variant="outline" className="text-xs">{TARGET_TYPE_LABEL[e.target_type] ?? e.target_type}</Badge></td>
                        <td className="px-3 py-2 align-top">
                          <TargetName type={e.target_type} refValue={e.target_ref} label={e.target_label} id={e.target_id} />
                        </td>
                        <td className="px-3 py-2 align-top text-muted-foreground">{e.note || "-"}</td>
                        {canAdmin && (
                          <td className="px-3 py-2 align-top text-right">
                            <Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => handleDelete(e.id)} title="삭제" aria-label="삭제"><Trash2 className="h-4 w-4" /></Button>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent></Card>
        )
      })}

      {viewMode === "table" && <IncomingLineage apiName={apiName} />}

      {!canAdmin ? (
        <p className="text-xs text-muted-foreground">리니지 관계 추가·삭제는 관리자만 할 수 있습니다.</p>
      ) : (
      <Card><CardContent className="pt-4">
        <p className="mb-2 text-sm font-medium">관계 추가</p>

        <details className="mb-3 rounded-md border bg-muted/20 p-3">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground">사용 방법 (펼치기)</summary>
          <div className="mt-2 space-y-2 text-xs text-muted-foreground">
            <p>이 API 기준으로 다른 대상과의 관계 한 건을 등록합니다. 아래 4가지를 채우고 “추가”를 누르세요.</p>
            <ul className="ml-4 list-disc space-y-1">
              <li><b>관계</b> — <b>제공(provides)</b>: 이 API가 데이터·기능을 제공하는 소비처 · <b>소비(consumes)</b>: 이 API가 사용·소비하는 대상 · <b>의존(depends_on)</b>: 런타임에 의존하는 대상</li>
              <li><b>대상 유형</b> — 시스템 / 데이터셋 / 모델 / AI 에이전트 / API 중 선택</li>
              <li><b>대상 식별자</b> — API·데이터셋·모델·AI 에이전트는 이름으로 검색해 고르면 카탈로그 항목과 연결되어 링크가 생깁니다. 시스템은 자유 입력(필수)</li>
              <li><b>표시명·비고</b> — 선택 입력</li>
            </ul>
            <p>예) 이 API가 <b>orders-dataset</b> 데이터셋을 읽어 쓴다면 → 관계 <b>소비</b>, 대상 유형 <b>데이터셋</b>, 대상 식별자 <b>orders-dataset</b>.</p>
            <p>같은 (관계 + 대상 유형 + 대상 식별자) 조합은 중복 등록되지 않습니다. 검색 목록에 없는 값도 직접 입력해 저장할 수 있지만, 이때는 링크가 생기지 않을 수 있습니다.</p>
          </div>
        </details>

        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">관계</p>
            <Select value={relation} onValueChange={setRelation}>
              <SelectTrigger className="h-8 w-[150px] text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>{LINEAGE_RELATIONS.map((r) => <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">대상 유형</p>
            <Select value={targetType} onValueChange={(v) => { setTargetType(v); setTargetRef(""); setTargetLabel("") }}>
              <SelectTrigger className="h-8 w-[130px] text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>{LINEAGE_TARGET_TYPES.map((t) => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">대상 식별자{SEARCHABLE_TYPES.has(targetType) ? " (검색)" : ""}</p>
            <TargetRefInput
              type={targetType}
              value={targetRef}
              onChange={setTargetRef}
              onPick={(o) => { setTargetRef(o.ref); if (!targetLabel.trim() && o.label !== o.ref) setTargetLabel(o.label) }}
            />
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">표시명(선택)</p>
            <Input className="h-8 w-[160px] text-sm" value={targetLabel} onChange={(e) => setTargetLabel(e.target.value)} placeholder="주문 데이터셋" />
          </div>
          <div className="flex-1 space-y-1">
            <p className="text-xs text-muted-foreground">비고(선택)</p>
            <Input className="h-8 w-full text-sm" value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          <Button size="sm" disabled={busy} onClick={handleAdd}>{busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Check className="mr-1 h-4 w-4" />}추가</Button>
        </div>
      </CardContent></Card>
      )}
    </div>
  )
}
