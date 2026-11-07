"use client"

import { useEffect, useRef, useState } from "react"
import { ArrowRight, FileCode2, Loader2, RefreshCcw } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Card, CardContent } from "@workspace/ui/components/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { cn } from "@workspace/ui/lib/utils"

import { type ApiSpec, type ApiSpecDiff, type ApiStatusHistory, fetchApiDiff } from "../api"
import { API_STATUS_LABEL } from "./apis-constants"
import { TabEmpty, TabError, fmtDateTime } from "./apis-detail-shared"
import { errorMessage } from "@/lib/notify"

/** 알림의 "자세히"에서 요청한 비교 대상 — seq 가 바뀔 때마다 적용 */
export type DiffRequest = { from: number | null; to: number | null; seq: number }

type TimelineItem =
  | { kind: "status"; at: string; h: ApiStatusHistory }
  | { kind: "spec"; at: string; s: ApiSpec }

const specLabel = (s: ApiSpec) => `v${s.version} · ${fmtDateTime(s.created_at)}${s.is_current === "true" ? " (현재)" : ""}`

// ---------------------------------------------------------------------------
// 스펙 버전 비교 — 추가/제거/변경 엔드포인트와 Breaking 여부
// ---------------------------------------------------------------------------
function SpecDiffView({ apiName, specs, request }: { apiName: string; specs: ApiSpec[]; request: DiffRequest | null }) {
  // specs 는 최신순 — 기본은 직전 → 최신
  const [fromId, setFromId] = useState<string>(String(specs[1]?.id ?? ""))
  const [toId, setToId] = useState<string>(String(specs[0]?.id ?? ""))
  // 알림에서 요청한 비교가 바뀌면(렌더 중 반영 — 이펙트에서 동기 setState 하지 않음)
  const [appliedSeq, setAppliedSeq] = useState(0)
  if (request && request.seq !== appliedSeq) {
    setAppliedSeq(request.seq)
    if (request.from != null && specs.some((s) => s.id === request.from)) setFromId(String(request.from))
    if (request.to != null && specs.some((s) => s.id === request.to)) setToId(String(request.to))
  }

  const key = `${fromId}>${toId}`
  const [result, setResult] = useState<{ key: string; data?: ApiSpecDiff; error?: string } | null>(null)
  const [reloadSeq, setReloadSeq] = useState(0)
  const same = fromId === toId
  useEffect(() => {
    if (!fromId || !toId || fromId === toId) return
    let active = true
    const k = `${fromId}>${toId}`
    fetchApiDiff(apiName, Number(fromId), Number(toId))
      .then((data) => { if (active) setResult({ key: k, data }) })
      .catch((e) => { if (active) setResult({ key: k, error: errorMessage(e) ?? "버전을 비교하지 못했습니다." }) })
    return () => { active = false }
  }, [apiName, fromId, toId, reloadSeq])
  const cur = result?.key === key ? result : null
  const d = cur?.data

  const picker = (value: string, onChange: (v: string) => void, label: string) => (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="h-8 w-[240px] text-sm" aria-label={label}><SelectValue /></SelectTrigger>
      <SelectContent>{specs.map((s) => <SelectItem key={s.id} value={String(s.id)}>{specLabel(s)}</SelectItem>)}</SelectContent>
    </Select>
  )

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {picker(fromId, setFromId, "비교 기준(이전) 버전")}
        <ArrowRight className="h-4 w-4 text-muted-foreground" />
        {picker(toId, setToId, "비교 대상(이후) 버전")}
      </div>
      {same ? (
        <TabEmpty>서로 다른 두 버전을 고르세요.</TabEmpty>
      ) : !cur ? (
        <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> 비교 중...</div>
      ) : cur.error ? (
        <TabError message={cur.error} onRetry={() => setReloadSeq((n) => n + 1)} />
      ) : d?.message ? (
        <TabEmpty>{d.message}</TabEmpty>
      ) : d ? (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {d.breaking
              ? <Badge className="bg-red-600 text-white">Breaking {d.breaking_count}건</Badge>
              : <Badge variant="outline" className="text-emerald-600">Breaking 변경 없음</Badge>}
            <Badge variant="outline">추가 {d.added_count}</Badge>
            <Badge variant="outline">제거 {d.removed_count}</Badge>
            <Badge variant="outline">변경 {d.changed_count}</Badge>
          </div>
          {d.added_count + d.removed_count + d.changed_count === 0 ? (
            <TabEmpty>엔드포인트 변경이 없습니다(설명·스키마 세부 등 엔드포인트 외 변경만 있을 수 있습니다).</TabEmpty>
          ) : (
            <div className="overflow-hidden rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/40"><tr>
                  <th className="w-[80px] px-3 py-2 text-left font-medium text-muted-foreground">구분</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">엔드포인트</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">내용</th>
                </tr></thead>
                <tbody>
                  {d.removed.map((k) => (
                    <tr key={`r:${k}`} className="border-t bg-red-50/50 align-top">
                      <td className="px-3 py-2"><Badge className="bg-red-600 text-[10px] text-white">제거</Badge></td>
                      <td className="px-3 py-2 break-all">{k}</td>
                      <td className="px-3 py-2 text-red-700">엔드포인트 제거 (Breaking)</td>
                    </tr>
                  ))}
                  {d.changed.map((c) => (
                    <tr key={`c:${c.key}`} className={cn("border-t align-top", c.breaking && "bg-red-50/50")}>
                      <td className="px-3 py-2"><Badge variant="outline" className={cn("text-[10px]", c.breaking && "border-red-600 text-red-700")}>변경</Badge></td>
                      <td className="px-3 py-2 break-all">{c.key}</td>
                      <td className="px-3 py-2">
                        <ul className="space-y-0.5">
                          {c.items.map((it, i) => (
                            <li key={i} className={it.breaking ? "text-red-700" : "text-muted-foreground"}>{it.detail}{it.breaking && " (Breaking)"}</li>
                          ))}
                        </ul>
                      </td>
                    </tr>
                  ))}
                  {d.added.map((k) => (
                    <tr key={`a:${k}`} className="border-t align-top">
                      <td className="px-3 py-2"><Badge variant="outline" className="text-[10px] text-emerald-700">추가</Badge></td>
                      <td className="px-3 py-2 break-all">{k}</td>
                      <td className="px-3 py-2 text-muted-foreground">새 엔드포인트</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : null}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 이력 탭 — 상태 변경·스펙 버전 등록을 시간순으로 + 스펙 버전 비교
// ---------------------------------------------------------------------------
export function HistoryTab({ apiName, history, specs, diffRequest }: {
  apiName: string
  history: ApiStatusHistory[]
  /** 스펙 버전(최신순) — 수동 API 는 빈 목록 */
  specs: ApiSpec[]
  diffRequest: DiffRequest | null
}) {
  const diffRef = useRef<HTMLDivElement>(null)
  // 알림의 "자세히"로 들어오면 비교 영역으로 스크롤
  useEffect(() => {
    if (diffRequest) diffRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })
  }, [diffRequest])

  const items: TimelineItem[] = [
    ...history.map((h) => ({ kind: "status" as const, at: h.changed_at, h })),
    ...specs.map((s) => ({ kind: "spec" as const, at: s.created_at, s })),
  ].sort((a, b) => b.at.localeCompare(a.at))

  return (
    <>
      <p className="text-sm text-muted-foreground">API 상태(초안·게시·사용 중단·폐기) 변경과 스펙 버전 등록 이력입니다. 스펙 버전이 둘 이상이면 아래에서 두 버전을 비교할 수 있습니다.</p>
      <Card><CardContent className="pt-4">
        <p className="mb-2 text-sm font-medium">변경 이력 ({items.length})</p>
        {items.length === 0 ? (
          <TabEmpty>변경 이력이 없습니다.</TabEmpty>
        ) : (
          <div className="overflow-hidden rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-muted/40"><tr>
                <th className="w-[110px] px-3 py-2 text-left">구분</th>
                <th className="px-3 py-2 text-left">내용</th>
                <th className="w-[170px] px-3 py-2 text-left">일시</th>
                <th className="w-[140px] px-3 py-2 text-left">변경자</th>
              </tr></thead>
              <tbody>
                {items.map((it) => it.kind === "status" ? (
                  <tr key={`h:${it.h.id}`} className="border-t align-top">
                    <td className="px-3 py-2 text-muted-foreground"><RefreshCcw className="mr-1 inline h-3.5 w-3.5" />상태 변경</td>
                    <td className="px-3 py-2">
                      {it.h.from_status ? <Badge variant="outline" className="text-xs">{API_STATUS_LABEL[it.h.from_status] ?? it.h.from_status}</Badge> : "—"}
                      <span className="mx-1 text-muted-foreground">→</span>
                      <Badge variant="secondary" className="text-xs">{API_STATUS_LABEL[it.h.to_status] ?? it.h.to_status}</Badge>
                      {it.h.note && <p className="mt-1 text-xs whitespace-pre-wrap text-muted-foreground">사유: {it.h.note}</p>}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">{fmtDateTime(it.h.changed_at)}</td>
                    <td className="px-3 py-2 text-muted-foreground">{it.h.changed_by || "-"}</td>
                  </tr>
                ) : (
                  <tr key={`s:${it.s.id}`} className="border-t align-top">
                    <td className="px-3 py-2 text-muted-foreground"><FileCode2 className="mr-1 inline h-3.5 w-3.5" />스펙 등록</td>
                    <td className="px-3 py-2">
                      <span className="font-medium">v{it.s.version}</span>
                      {it.s.is_current === "true" && <Badge variant="secondary" className="ml-2 text-[10px]">현재</Badge>}
                      {it.s.source_url && <p className="mt-0.5 text-xs break-all text-muted-foreground">출처: {it.s.source_url}</p>}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">{fmtDateTime(it.s.created_at)}</td>
                    <td className="px-3 py-2 text-muted-foreground">{it.s.created_by || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent></Card>

      {specs.length >= 2 && (
        <div ref={diffRef} className="scroll-mt-4">
          <Card><CardContent className="pt-4">
            <p className="mb-2 text-sm font-medium">스펙 버전 비교</p>
            <SpecDiffView apiName={apiName} specs={specs} request={diffRequest} />
          </CardContent></Card>
        </div>
      )}
    </>
  )
}
