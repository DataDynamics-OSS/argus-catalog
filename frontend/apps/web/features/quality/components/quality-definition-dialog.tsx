"use client"

import { useEffect, useMemo, useState } from "react"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { Textarea } from "@workspace/ui/components/textarea"
import { DATE_FORMAT_CHOICES, listedDateFormat } from "@/features/datasets/quality-rules"
import { saveQualityDefinition, type QualityDefinition, type QualityIndicator } from "@/features/quality/api"
import { QualityCategorySelect, QualityCheckTypeSelect } from "@/features/quality/components/quality-category-select"
import { QualitySqlPreviewBox } from "@/features/quality/components/quality-sql-preview"
import {
  definableCategories,
  definitionFormError,
  definitionPayload,
  DEFINITION_TYPE_META,
  typeForCategory,
  type DefinitionForm,
} from "@/features/quality/definitions"
import { toast, notifyError } from "@/lib/notify"
import { TagInput } from "@/components/tag-input"
import { decodeList, encodeList } from "@/lib/text-list"

const CUSTOM = "__custom__"
const EMPTY: DefinitionForm = {
  name: "", category: "", checkType: "", expected: "", threshold: "100", severity: "WARNING", description: "",
}

/** 품질 지표 추가·수정 — 분류와 기준값을 정하고, 그 기준이 실행할 Trino 쿼리를 바로 본다. */
export function QualityDefinitionDialog({ open, definition, template = null, indicators, onOpenChange, onSaved }: {
  open: boolean
  /** 수정할 지표 — null 이면 새로 만든다 */
  definition: QualityDefinition | null
  /** 복사해서 새로 만들 때의 원본과 새 이름 — 분류·기준·심각도·설명을 채워서 연다(할당은 복사하지 않는다) */
  template?: { source: QualityDefinition; name: string } | null
  indicators: QualityIndicator[]
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<DefinitionForm>(EMPTY)
  const [dateCustom, setDateCustom] = useState(false)
  const [saving, setSaving] = useState(false)
  const categories = useMemo(() => definableCategories(indicators), [indicators])

  useEffect(() => {
    if (!open) return
    setDateCustom(false)
    const from = definition ?? template?.source
    setForm(from ? {
      name: template && !definition ? template.name : from.name, category: from.category, checkType: from.check_type,
      expected: from.expected_value ?? "", threshold: String(from.threshold),
      severity: from.severity, description: from.description ?? "",
    } : EMPTY)
  }, [open, definition, template])

  const set = (patch: Partial<DefinitionForm>) => setForm((f) => ({ ...f, ...patch }))
  const category = categories.find((c) => c.key === form.category)
  const meta = DEFINITION_TYPE_META[form.checkType]
  const error = definitionFormError(form)

  const pickCategory = (key: string) => {
    const next = categories.find((c) => c.key === key)
    const checkType = typeForCategory(next, form.checkType)
    // 유형이 바뀌면 앞 유형의 기준값은 뜻이 달라진다 — 비운다
    set({ category: key, checkType, expected: checkType === form.checkType ? form.expected : "" })
    setDateCustom(false)
  }

  const save = async () => {
    if (error) return
    setSaving(true)
    try {
      await saveQualityDefinition(definition?.id ?? null, definitionPayload(form))
      toast.success(definition ? "품질 지표를 수정했습니다." : "품질 지표를 추가했습니다.")
      onSaved()
      onOpenChange(false)
    } catch (e) {
      notifyError(e, "품질 지표를 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  const showCustomDate = dateCustom || (form.expected.trim() !== "" && !listedDateFormat(form.expected))

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{definition ? "품질 지표 수정" : template ? "품질 지표 복사" : "품질 지표 추가"}</DialogTitle>
          <DialogDescription>
            {template && !definition
              ? `'${template.source.name}' 의 진단 기준과 기준값을 가져왔습니다. 이름과 기준을 고쳐 새 지표로 저장합니다 — 할당된 컬럼은 복사하지 않습니다. `
              : ""}
            한 번 정해 두고 여러 컬럼에 할당하는 품질 기준입니다.
            {definition && definition.rules > 0 && ` 저장하면 할당된 컬럼 ${definition.rules}개에 바로 반영됩니다.`}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label className="text-sm">이름</Label>
              <Input value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="예: 여부 Y/N" className="h-9" />
            </div>
            <div className="grid gap-1.5">
              <Label className="text-sm">진단 기준</Label>
              <QualityCategorySelect categories={categories} value={form.category} onChange={pickCategory}
                placeholder="무엇을 보는 지표인가" />
            </div>
          </div>
          {category && <p className="-mt-1 text-xs">{category.description}</p>}

          {category && meta && (
            <div className="grid gap-3 sm:grid-cols-2">
              {category.check_types.length > 1 && (
                <div className="grid gap-1.5">
                  <Label className="text-sm">기준 방식</Label>
                  <QualityCheckTypeSelect checkTypes={category.check_types} value={form.checkType}
                    onChange={(v) => set({ checkType: v })} />
                </div>
              )}
              {meta.expected === "date" && (
                <div className="grid gap-1.5">
                  <Label className="text-sm">{meta.expectedLabel}</Label>
                  <Select
                    value={showCustomDate ? CUSTOM : (listedDateFormat(form.expected) ?? "")}
                    onValueChange={(v) => {
                      setDateCustom(v === CUSTOM)
                      if (v !== CUSTOM) set({ expected: v })
                    }}
                  >
                    <SelectTrigger className="h-9"><SelectValue placeholder="형식을 고르세요" /></SelectTrigger>
                    <SelectContent>
                      {DATE_FORMAT_CHOICES.map((c) => (
                        <SelectItem key={c.value} value={c.value}>
                          {c.value} <span className="ml-2">{c.example}</span>
                        </SelectItem>
                      ))}
                      <SelectItem value={CUSTOM}>직접 입력…</SelectItem>
                    </SelectContent>
                  </Select>
                  {showCustomDate && (
                    <Input value={form.expected} onChange={(e) => set({ expected: e.target.value })}
                      placeholder="예: DD/MM/YYYY" className="h-9 font-mono" />
                  )}
                </div>
              )}
              {meta.expected === "list" && (
                <div className="grid gap-1.5">
                  <Label className="text-sm">{meta.expectedLabel}</Label>
                  {/* 값 목록 — JSON 배열 문자열로 둔다(값 안의 쉼표 보존, TODO-143) */}
                  <TagInput mono value={decodeList(form.expected)} onChange={(v) => set({ expected: encodeList(v) ?? "" })}
                    placeholder={meta.hint} />
                </div>
              )}
              {(meta.expected === "text" || meta.expected === "number") && (
                <div className="grid gap-1.5">
                  <Label className="text-sm">{meta.expectedLabel}</Label>
                  <Input value={form.expected} onChange={(e) => set({ expected: e.target.value })}
                    placeholder={meta.hint} className="h-9 font-mono" />
                </div>
              )}
              {meta.usesThreshold && (
                <div className="grid gap-1.5">
                  <Label className="text-sm">통과 임계값 (%)</Label>
                  <Input type="number" min={0} max={100} value={form.threshold}
                    onChange={(e) => set({ threshold: e.target.value })} className="h-9" />
                </div>
              )}
              <div className="grid gap-1.5">
                <Label className="text-sm">심각도</Label>
                <Select value={form.severity} onValueChange={(v) => set({ severity: v })}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="BREAKING">BREAKING</SelectItem>
                    <SelectItem value="WARNING">WARNING</SelectItem>
                    <SelectItem value="INFO">INFO</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}

          <div className="grid gap-1.5">
            <Label className="text-sm">설명 (선택)</Label>
            <Textarea value={form.description} onChange={(e) => set({ description: e.target.value })} rows={2}
              placeholder="이 지표를 어떤 컬럼에 쓰는지" />
          </div>

          {category && meta && (
            <QualitySqlPreviewBox
              className="rounded border p-3"
              request={{
                check_type: form.checkType,
                expected_value: meta.expected === "none" ? null : (form.expected.trim() || null),
                threshold: meta.usesThreshold && !Number.isNaN(Number(form.threshold))
                  ? Math.min(100, Math.max(0, Number(form.threshold))) : 100,
              }}
            />
          )}
        </div>

        <DialogFooter className="items-center">
          {error && <span className="mr-auto text-xs">{error}</span>}
          <Button variant="outline" onClick={() => onOpenChange(false)}>닫기</Button>
          <Button onClick={() => void save()} disabled={saving || error != null}>{definition ? "저장" : "지표 추가"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
