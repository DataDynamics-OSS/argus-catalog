"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Code2, X } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { useConfirm } from "@/components/confirm-provider"
import { datasetHref } from "@/features/datasets/links"
import {
  assignQualityDefinition,
  fetchQualityAssignments,
  fetchQualityCandidates,
  unassignQualityDefinition,
  type QualityAssignment,
  type QualityAssignResult,
  type QualityColumnCandidate,
  type QualityDefinition,
} from "@/features/quality/api"
import { QualitySqlPreviewBox } from "@/features/quality/components/quality-sql-preview"
import { criteriaText } from "@/features/quality/definitions"
import { useLatest } from "@/features/quality/use-polling"
import { errorMessage, toast, notifyError } from "@/lib/notify"
import { SearchInput } from "@/components/search-input"

const keyOf = (c: { dataset_id: number; column_name: string }) => `${c.dataset_id}\u0000${c.column_name}`

/** 할당 결과를 한 문장으로. */
export function describeAssignResult(r: QualityAssignResult): string {
  const parts = []
  if (r.created) parts.push(`${r.created}개 컬럼에 할당했습니다`)
  if (r.adopted) parts.push(`같은 기준의 기존 규칙 ${r.adopted}개를 지표에 연결했습니다`)
  if (r.skipped.length) parts.push(`${r.skipped.length}개는 건너뛰었습니다 (${r.skipped[0]!.reason})`)
  return parts.length ? `${parts.join(" · ")}.` : "할당할 컬럼이 없습니다."
}

/**
 * 품질 지표의 할당 — 할당된 컬럼을 보고 해제하고, 컬럼을 찾아 할당한다.
 * 검색어가 없으면 컬럼 이름(`_yn`·`_amt`·`_ymd` …)과 제약(NOT NULL·PK)으로 추천한 컬럼을 보여 준다.
 */
export function QualityAssignDialog({ definition, canManage, onClose, onChanged }: {
  /** null 이면 닫힌 상태 */
  definition: QualityDefinition | null
  canManage: boolean
  onClose: () => void
  onChanged: () => void
}) {
  const id = definition?.id ?? null
  const [assigned, setAssigned] = useState<QualityAssignment[]>([])
  const [candidates, setCandidates] = useState<QualityColumnCandidate[]>([])
  const [q, setQ] = useState("")
  const [searched, setSearched] = useState("")
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const askConfirm = useConfirm()
  const begin = useLatest()
  const [sqlFor, setSqlFor] = useState<QualityAssignment | null>(null)

  const load = useCallback(async (query: string) => {
    const current = begin()
    if (id == null) return
    setLoading(true)
    try {
      const [a, c] = await Promise.all([fetchQualityAssignments(id), fetchQualityCandidates(id, query)])
      if (!current()) return // 그 사이 다른 지표를 열었거나 다시 찾았다
      setAssigned(a)
      setCandidates(c)
      setSearched(query)
      setError(null)
    } catch (e) {
      if (!current()) return
      const message = errorMessage(e) ?? String(e)
      setError(message)
      notifyError(e, "품질 지표 할당 정보를 불러오지 못했습니다.")
    } finally {
      if (current()) setLoading(false)
    }
  }, [id, begin])

  useEffect(() => {
    setQ("")
    setPicked(new Set())
    setSqlFor(null)
    setAssigned([])
    setCandidates([])
    setError(null)
    void load("")
  }, [load])

  const open = candidates.filter((c) => !c.assigned)
  const toggle = (c: QualityColumnCandidate) => setPicked((prev) => {
    const next = new Set(prev)
    if (!next.delete(keyOf(c))) next.add(keyOf(c))
    return next
  })
  const allPicked = open.length > 0 && open.every((c) => picked.has(keyOf(c)))

  const assign = async () => {
    if (id == null) return
    const columns = candidates.filter((c) => picked.has(keyOf(c)))
      .map((c) => ({ dataset_id: c.dataset_id, column_name: c.column_name }))
    if (columns.length === 0) return
    setBusy(true)
    try {
      toast.success(describeAssignResult(await assignQualityDefinition(id, columns)))
      setPicked(new Set())
      onChanged()
      await load(searched)
    } catch (e) {
      notifyError(e, "품질 지표를 할당하지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  const unassign = async (a: QualityAssignment) => {
    if (id == null) return
    // 해제는 규칙과 그 진단 결과를 지운다 — 되돌릴 수 없으므로 먼저 묻는다
    if (!(await askConfirm({
      title: "할당 해제",
      description: `${a.dataset_name}.${a.column_name} 의 할당을 해제할까요? 이 컬럼의 규칙과 지난 진단 결과가 함께 지워집니다.`,
      confirmText: "해제", destructive: true,
    }))) return
    setBusy(true)
    try {
      await unassignQualityDefinition(id, [a.rule_id])
      toast.success(`${a.datasource_name ? `${a.datasource_name} · ` : ""}${a.dataset_name}.${a.column_name} 의 할당을 해제했습니다.`)
      if (sqlFor?.rule_id === a.rule_id) setSqlFor(null)
      onChanged()
      await load(searched)
    } catch (e) {
      notifyError(e, "할당을 해제하지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={definition != null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{definition?.name} — 컬럼 할당</DialogTitle>
          <DialogDescription>
            {definition && `${definition.area_name} · ${definition.category_name} · ${criteriaText(definition)}`}
          </DialogDescription>
        </DialogHeader>

        <section className="grid gap-2">
          <h3 className="text-sm font-medium">할당된 컬럼 ({assigned.length})</h3>
          {assigned.length === 0 ? (
            <p className={`rounded border px-3 py-4 text-center text-xs ${error ? "text-destructive" : "text-muted-foreground"}`}>
              {error ? (
                <span className="flex flex-col items-center gap-2">
                  할당을 불러오지 못했습니다: {error}
                  <Button variant="outline" size="sm" onClick={() => void load(searched)}>다시 시도</Button>
                </span>
              ) : loading ? "불러오는 중…" : "아직 할당한 컬럼이 없습니다. 아래에서 컬럼을 골라 할당하세요."}
            </p>
          ) : (
            <div className="max-h-56 overflow-auto rounded border">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-muted text-left text-xs">
                  <tr>
                    <th className="px-3 py-1.5">데이터셋</th>
                    <th className="px-3 py-1.5">데이터 소스</th>
                    <th className="px-3 py-1.5">컬럼</th>
                    <th className="px-3 py-1.5" />
                  </tr>
                </thead>
                <tbody>
                  {assigned.map((a) => (
                    <tr key={a.rule_id} className="border-t">
                      <td className="px-3 py-1.5">
                        <Link href={datasetHref(a.dataset_id, "quality")} className="hover:underline">{a.dataset_name}</Link>
                      </td>
                      <td className="px-3 py-1.5 text-xs">{a.datasource_name}</td>
                      <td className="px-3 py-1.5 font-mono text-xs">
                        {a.column_name}
                        {a.is_active !== "true" && <Badge variant="secondary" className="ml-2">비활성</Badge>}
                      </td>
                      <td className="whitespace-nowrap px-3 py-1 text-right">
                        <Button variant="ghost" size="sm" className="h-7 px-2 text-xs"
                          onClick={() => setSqlFor(sqlFor?.rule_id === a.rule_id ? null : a)}>
                          <Code2 className="mr-1 size-3" /> 쿼리
                        </Button>
                        {canManage && (
                          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={busy}
                            title="이 컬럼의 규칙과 진단 결과를 지웁니다" onClick={() => void unassign(a)}>
                            <X className="mr-1 size-3" /> 해제
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {sqlFor && definition && (
            <QualitySqlPreviewBox
              className="rounded border p-3"
              request={{
                check_type: definition.check_type, expected_value: definition.expected_value,
                threshold: sqlFor.threshold, dataset_id: sqlFor.dataset_id, column_name: sqlFor.column_name,
              }}
            />
          )}
        </section>

        {canManage && (
          <section className="grid gap-2">
            <h3 className="text-sm font-medium">컬럼 할당</h3>
            <form className="flex gap-2" onSubmit={(e) => {
              e.preventDefault()
              setPicked(new Set())
              void load(q)
            }}>
              <SearchInput className="flex-1" value={q} onValueChange={setQ}
                placeholder="데이터셋 · 컬럼 이름 검색(비우면 추천 컬럼)…" />
              <Button type="submit" variant="outline" size="sm" className="h-9" disabled={loading}>찾기</Button>
            </form>
            <p className="text-xs">
              {searched
                ? `"${searched}" 이(가) 이름에 들어간 컬럼입니다.`
                : "컬럼 이름의 끝 단어(_yn · _amt · _ymd …)와 제약(NOT NULL · PK · UNIQUE)으로 보아 이 지표가 맞을 법한 컬럼입니다."}
              {candidates.length >= 300 && " 300개까지만 보입니다 — 검색어로 좁히세요."}
            </p>
            <div className="max-h-72 overflow-auto rounded border">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-muted text-left text-xs">
                  <tr>
                    <th className="w-9 px-3 py-1.5">
                      <Checkbox checked={allPicked} disabled={open.length === 0} aria-label="모두 선택"
                        onCheckedChange={(v) => setPicked(v ? new Set(open.map(keyOf)) : new Set())} />
                    </th>
                    <th className="px-3 py-1.5">데이터셋</th>
                    <th className="px-3 py-1.5">데이터 소스</th>
                    <th className="px-3 py-1.5">컬럼</th>
                    <th className="px-3 py-1.5">타입</th>
                    <th className="px-3 py-1.5" />
                  </tr>
                </thead>
                <tbody>
                  {candidates.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-3 py-6 text-center text-xs text-muted-foreground">
                        {error ? <span className="text-destructive">컬럼을 불러오지 못했습니다: {error}</span>
                          : loading ? "불러오는 중…" : searched
                          ? "찾은 컬럼이 없습니다."
                          : "추천할 컬럼이 없습니다. 데이터셋이나 컬럼 이름으로 찾아 고르세요."}
                      </td>
                    </tr>
                  ) : candidates.map((c) => (
                    <tr key={keyOf(c)} className={`border-t ${c.assigned ? "text-muted-foreground" : "cursor-pointer"}`}
                      onClick={() => !c.assigned && toggle(c)}>
                      <td className="px-3 py-1.5">
                        <Checkbox checked={c.assigned || picked.has(keyOf(c))} disabled={c.assigned}
                          aria-label={`${c.datasource_name ?? ""} ${c.dataset_name}.${c.column_name}`}
                          onClick={(e) => e.stopPropagation()} onCheckedChange={() => toggle(c)} />
                      </td>
                      <td className="px-3 py-1.5">{c.dataset_name}</td>
                      <td className="px-3 py-1.5 text-xs">{c.datasource_name}</td>
                      <td className="px-3 py-1.5 font-mono text-xs">{c.column_name}</td>
                      <td className="px-3 py-1.5 text-xs">{c.field_type}</td>
                      <td className="px-3 py-1.5 text-right">
                        {c.assigned ? <Badge variant="outline">할당됨</Badge>
                          : c.suggested && <Badge variant="secondary">추천</Badge>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex justify-end">
              <Button onClick={() => void assign()} disabled={busy || picked.size === 0}>
                선택한 {picked.size}개 컬럼에 할당
              </Button>
            </div>
          </section>
        )}
      </DialogContent>
    </Dialog>
  )
}
