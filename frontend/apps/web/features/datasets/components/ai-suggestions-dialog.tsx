"use client"

// 데이터셋 상세의 "AI 제안" 버튼 + 검토 다이얼로그.
// 미리보기로 생성만 하고 적용하지 않은 결과, AI 서비스 enrich 가 반입한 결과(승인 대기)를
// 한 목록으로 보여 주고 개별 승인·거절, 선택 일괄 승인을 한다. 승인 대기 제안이 없으면 아무것도
// 그리지 않는다. 승인·거절 권한은 서버 `_require_suggestion_editor`(데이터셋 생성자 또는 관리자)와
// 같아서, 호출하는 쪽이 canManageDataset 일 때만 이 컴포넌트를 렌더링한다.

import { useCallback, useEffect, useMemo, useState } from "react"
import { Check, Loader2, Sparkles, X } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@workspace/ui/components/table"
import { notifyError, toast } from "@/lib/notify"
import {
  applySuggestion, applySuggestions, getSuggestions, rejectSuggestion, type SuggestionItem,
} from "@/features/datasets/ai-api"
import {
  formatCurrentValue, formatSuggestedValue, suggestionKindLabel, suggestionSource, suggestionTarget,
} from "@/features/datasets/ai-suggestions"

function formatTime(iso: string | null): string {
  if (!iso) return "-"
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ko-KR")
}

export function AiSuggestionsButton({
  datasetId,
  refreshKey = 0,
  onApplied,
}: {
  datasetId: number
  /** 값이 바뀌면 목록을 다시 불러온다 — 미리보기 생성·적용 직후 건수 갱신용. */
  refreshKey?: number
  /** 하나라도 승인되면 호출 — 상세 데이터 새로고침. */
  onApplied?: () => void
}) {
  const [items, setItems] = useState<SuggestionItem[]>([])
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState<number | "batch" | null>(null)

  const reload = useCallback(async () => {
    try {
      const list = await getSuggestions(datasetId)
      setItems(list)
      // 사라진 제안은 선택에서 뺀다
      setSelected((prev) => new Set([...prev].filter((id) => list.some((s) => s.id === id))))
      if (list.length === 0) setOpen(false)
    } catch (e) {
      // 부수 정보 — 실패해도 상세 화면은 계속 동작한다
      console.error("Failed to load AI suggestions", { datasetId, err: e })
    }
  }, [datasetId])

  useEffect(() => {
    reload()
  }, [reload, refreshKey])

  const allSelected = items.length > 0 && selected.size === items.length
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(items.map((s) => s.id)))
  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const handleApply = async (s: SuggestionItem) => {
    setBusy(s.id)
    try {
      await applySuggestion(s.id)
      toast.success(`${suggestionKindLabel(s)} 제안을 적용했습니다.`)
      onApplied?.()
      await reload()
    } catch (e) {
      notifyError(e, "AI 제안을 적용하지 못했습니다.")
    } finally {
      setBusy(null)
    }
  }

  const handleReject = async (s: SuggestionItem) => {
    setBusy(s.id)
    try {
      await rejectSuggestion(s.id)
      toast.success(`${suggestionKindLabel(s)} 제안을 거절했습니다.`)
      await reload()
    } catch (e) {
      notifyError(e, "AI 제안을 거절하지 못했습니다.")
    } finally {
      setBusy(null)
    }
  }

  const handleApplySelected = async () => {
    const ids = [...selected]
    if (ids.length === 0) return
    setBusy("batch")
    try {
      const res = await applySuggestions(ids)
      toast.success(`AI 제안 ${res.count}건을 적용했습니다.`)
      setSelected(new Set())
      onApplied?.()
      await reload()
    } catch (e) {
      notifyError(e, "AI 제안을 일괄 적용하지 못했습니다.")
    } finally {
      setBusy(null)
    }
  }

  // 같은 대상에 제안이 여러 건이면 승인 순서대로 덮어쓰인다 — 목록에서 알 수 있게 표시
  const duplicateKeys = useMemo(() => {
    const seen = new Map<string, number>()
    for (const s of items) {
      const key = `${s.generation_type}:${s.entity_type === "column" ? s.entity_id : "ds"}`
      seen.set(key, (seen.get(key) ?? 0) + 1)
    }
    return seen
  }, [items])

  if (items.length === 0) return null

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        className="h-8 gap-1.5"
        onClick={() => setOpen(true)}
        title="승인을 기다리는 AI 제안 보기"
      >
        <Sparkles className="h-3.5 w-3.5" />
        AI 제안
        <Badge className="h-5 min-w-5 justify-center px-1.5 text-[10px]">{items.length}</Badge>
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-5xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4" />
              AI 제안 ({items.length})
            </DialogTitle>
            <DialogDescription>
              AI 가 만들었지만 아직 적용하지 않은 결과입니다. 승인하면 데이터셋에 반영되고, 거절하면 목록에서 지워집니다.
            </DialogDescription>
          </DialogHeader>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8">
                  <Checkbox checked={allSelected} onCheckedChange={toggleAll} aria-label="전체 선택" />
                </TableHead>
                <TableHead className="w-[130px]">유형 · 대상</TableHead>
                <TableHead>제안값</TableHead>
                <TableHead>현재 값</TableHead>
                <TableHead className="w-[150px]">출처 · 시각</TableHead>
                <TableHead className="w-[140px] text-right">작업</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((s) => {
                const dupKey = `${s.generation_type}:${s.entity_type === "column" ? s.entity_id : "ds"}`
                return (
                  <TableRow key={s.id} data-testid={`ai-suggestion-${s.id}`}>
                    <TableCell className="align-top">
                      <Checkbox
                        checked={selected.has(s.id)}
                        onCheckedChange={() => toggle(s.id)}
                        aria-label={`제안 ${s.id} 선택`}
                      />
                    </TableCell>
                    <TableCell className="align-top">
                      <Badge variant="secondary">{suggestionKindLabel(s)}</Badge>
                      <div className="mt-1 font-mono text-xs text-muted-foreground break-all">{suggestionTarget(s)}</div>
                      {(duplicateKeys.get(dupKey) ?? 0) > 1 && (
                        <div className="mt-1 text-[10px] text-amber-600">같은 대상 제안 여러 건</div>
                      )}
                    </TableCell>
                    <TableCell className="align-top text-sm whitespace-pre-wrap break-words">
                      <div className="max-h-40 overflow-y-auto">{formatSuggestedValue(s)}</div>
                    </TableCell>
                    <TableCell className="align-top text-sm text-muted-foreground whitespace-pre-wrap break-words">
                      <div className="max-h-40 overflow-y-auto">{formatCurrentValue(s)}</div>
                    </TableCell>
                    <TableCell className="align-top text-xs text-muted-foreground">
                      <div>{suggestionSource(s)}</div>
                      <div className="mt-1">{formatTime(s.created_at)}</div>
                    </TableCell>
                    <TableCell className="align-top text-right">
                      <div className="flex justify-end gap-1">
                        <Button size="sm" className="h-7 px-2" disabled={busy !== null} onClick={() => handleApply(s)}>
                          {busy === s.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                          승인
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 px-2"
                          disabled={busy !== null}
                          onClick={() => handleReject(s)}
                        >
                          <X className="h-3.5 w-3.5" />
                          거절
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground">
              PII 를 승인하면 해당 컬럼의 PII 유형이 바뀝니다. 태그 승인 시 없는 태그는 새로 만듭니다.
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setOpen(false)}>
                닫기
              </Button>
              <Button size="sm" disabled={selected.size === 0 || busy !== null} onClick={handleApplySelected}>
                {busy === "batch" ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Check className="mr-1 h-4 w-4" />}
                선택 승인 ({selected.size})
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
