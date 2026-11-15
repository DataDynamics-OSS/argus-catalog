"use client"

import { useEffect, useMemo, useState } from "react"
import Link from "next/link"

import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { assignQualityDefinition, fetchQualityDefinitions, type QualityDefinition } from "@/features/quality/api"
import { describeAssignResult } from "@/features/quality/components/quality-assign-dialog"
import { QualitySqlPreviewBox } from "@/features/quality/components/quality-sql-preview"
import { criteriaText } from "@/features/quality/definitions"
import { toast, notifyError } from "@/lib/notify"

/** 데이터셋 품질 탭의 "지표 할당" — 미리 정한 품질 지표 하나를 골라 이 데이터셋의 컬럼들에 할당한다. */
export function QualityDatasetAssignDialog({ open, datasetId, columns, assigned, onOpenChange, onAssigned }: {
  open: boolean
  datasetId: number
  /** 이 데이터셋의 컬럼 이름 */
  columns: string[]
  /** 이미 할당된 것 — 지표 id 와 컬럼 */
  assigned: { definition_id: number; column_name: string }[]
  onOpenChange: (open: boolean) => void
  onAssigned: () => void
}) {
  const [definitions, setDefinitions] = useState<QualityDefinition[]>([])
  // 불러오는 중 · 실패 · 없음을 구분한다
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading")
  const [definitionId, setDefinitionId] = useState("")
  const [picked, setPicked] = useState<Set<string>>(new Set())
  // 쿼리를 볼 컬럼 — 고른 컬럼마다 쿼리는 컬럼 이름만 다르므로 한 번에 하나만 보여 준다
  const [previewColumn, setPreviewColumn] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setDefinitionId("")
    setPicked(new Set())
    setPreviewColumn(null)
    setState("loading")
    fetchQualityDefinitions().then((rows) => {
      setDefinitions(rows)
      setState("ready")
    }).catch((e) => {
      setState("failed")
      notifyError(e, "품질 지표 목록을 불러오지 못했습니다.")
    })
  }, [open])

  const definition = definitions.find((d) => String(d.id) === definitionId)
  const taken = useMemo(() => new Set(
    assigned.filter((a) => String(a.definition_id) === definitionId).map((a) => a.column_name.toLowerCase()),
  ), [assigned, definitionId])
  const groups = useMemo(() => {
    const out: { name: string; items: QualityDefinition[] }[] = []
    for (const d of definitions) {
      const name = `${d.area_name} · ${d.category_name}`
      const group = out.find((g) => g.name === name)
      if (group) group.items.push(d)
      else out.push({ name, items: [d] })
    }
    return out
  }, [definitions])
  const pickedColumns = columns.filter((c) => picked.has(c))
  // 고른 컬럼에서 빠졌으면 첫 컬럼으로
  const shown = previewColumn && picked.has(previewColumn) ? previewColumn : pickedColumns[0]

  const toggle = (column: string) => {
    // 새로 고른 컬럼의 쿼리를 바로 보여 준다
    if (!picked.has(column)) setPreviewColumn(column)
    setPicked((prev) => {
      const next = new Set(prev)
      if (!next.delete(column)) next.add(column)
      return next
    })
  }

  const assign = async () => {
    if (!definition || picked.size === 0) return
    setBusy(true)
    try {
      const result = await assignQualityDefinition(
        definition.id, columns.filter((c) => picked.has(c)).map((c) => ({ dataset_id: datasetId, column_name: c })))
      toast.success(describeAssignResult(result))
      onAssigned()
      onOpenChange(false)
    } catch (e) {
      notifyError(e, "품질 지표를 할당하지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>품질 지표 할당</DialogTitle>
          <DialogDescription>
            미리 정해 둔 품질 지표를 이 데이터셋의 컬럼에 할당합니다. 지표는{" "}
            <Link href="/dashboard/quality-runs?tab=definitions" className="underline">데이터 품질 &gt; 품질 지표</Link>
            {" "}에서 만들고 고칩니다.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label className="text-sm">품질 지표</Label>
            <Select value={definitionId} onValueChange={(v) => {
              setDefinitionId(v)
              setPicked(new Set())
            }}>
              <SelectTrigger className="h-9">
                <SelectValue placeholder={state === "loading" ? "불러오는 중…" : state === "failed" ? "품질 지표를 불러오지 못했습니다"
                  : definitions.length ? "지표를 고르세요" : "정의한 품질 지표가 없습니다"} />
              </SelectTrigger>
              <SelectContent>
                {groups.map((g) => (
                  <SelectGroup key={g.name}>
                    <SelectLabel>{g.name}</SelectLabel>
                    {g.items.map((d) => <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>)}
                  </SelectGroup>
                ))}
              </SelectContent>
            </Select>
            {definition && (
              <p className="text-xs">
                <span className="font-mono">{criteriaText(definition)}</span>
                {definition.description ? ` — ${definition.description}` : ""}
              </p>
            )}
          </div>

          {definition && (
            <div className="grid gap-1.5">
              <Label className="text-sm">할당할 컬럼 ({picked.size})</Label>
              {columns.length === 0 ? (
                <p className="text-sm text-muted-foreground">이 데이터셋에 컬럼 정보가 없습니다.</p>
              ) : (
                <div className="grid max-h-56 gap-x-4 gap-y-1.5 overflow-auto rounded border p-3 sm:grid-cols-2">
                  {columns.map((c) => {
                    const done = taken.has(c.toLowerCase())
                    return (
                      <label key={c} className={`flex items-center gap-2 text-sm ${done ? "text-muted-foreground" : "cursor-pointer"}`}>
                        <Checkbox checked={done || picked.has(c)} disabled={done} onCheckedChange={() => toggle(c)} />
                        <span className="truncate font-mono text-sm" title={c}>{c}</span>
                        {done && <span className="text-sm">할당됨</span>}
                      </label>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {definition && shown && (
            <div className="grid gap-2 rounded border p-3">
              {pickedColumns.length > 1 && (
                <div className="grid gap-1.5">
                  <p className="text-sm">
                    고른 컬럼마다 규칙이 하나씩 만들어지고, 규칙의 쿼리는 컬럼 이름만 다릅니다. 컬럼을 눌러 그 컬럼의 쿼리를 확인하세요.
                  </p>
                  <div className="flex max-h-24 flex-wrap gap-1.5 overflow-auto" role="group" aria-label="쿼리를 볼 컬럼">
                    {pickedColumns.map((c) => (
                      <Button key={c} type="button" size="sm" variant={c === shown ? "default" : "outline"}
                        className="h-7 px-2 font-mono text-sm" aria-pressed={c === shown} onClick={() => setPreviewColumn(c)}>
                        {c}
                      </Button>
                    ))}
                  </div>
                </div>
              )}
              <QualitySqlPreviewBox
                request={{
                  check_type: definition.check_type, expected_value: definition.expected_value,
                  threshold: definition.threshold, dataset_id: datasetId, column_name: shown,
                }}
              />
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>닫기</Button>
          <Button onClick={() => void assign()} disabled={busy || !definition || picked.size === 0}>
            {picked.size}개 컬럼에 할당
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
