"use client"

import { useMemo } from "react"

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { Tooltip, TooltipContent, TooltipTrigger } from "@workspace/ui/components/tooltip"
import type { QualityIndicator } from "@/features/quality/api"
import { checkTypeLabel, groupByArea } from "@/features/quality/check-types"

/** 분류 선택 상자 — 진단 영역별로 묶고, 항목에 마우스를 올리면 그 분류가 무엇을 보는지 설명한다. */
export function QualityCategorySelect({ categories, value, onChange, disabled, placeholder = "무엇을 보는 규칙인가" }: {
  categories: QualityIndicator[]
  value: string
  onChange: (key: string) => void
  disabled?: boolean
  placeholder?: string
}) {
  const areas = useMemo(() => groupByArea(categories), [categories])
  return (
    <Select value={value} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger className="h-9"><SelectValue placeholder={placeholder} /></SelectTrigger>
      <SelectContent>
        {areas.map((a) => (
          <SelectGroup key={a.key}>
            <SelectLabel>{a.name}</SelectLabel>
            {a.items.map((c) => (
              <Tooltip key={c.key}>
                <TooltipTrigger asChild>
                  <SelectItem value={c.key}>{c.name}</SelectItem>
                </TooltipTrigger>
                <TooltipContent side="right" sideOffset={8} className="max-w-64 whitespace-normal leading-relaxed">
                  {c.description}
                </TooltipContent>
              </Tooltip>
            ))}
          </SelectGroup>
        ))}
      </SelectContent>
    </Select>
  )
}

/** 기준 방식 선택 상자 — 분류 안의 규칙 유형. */
export function QualityCheckTypeSelect({ checkTypes, value, onChange, disabled }: {
  checkTypes: string[]
  value: string
  onChange: (checkType: string) => void
  disabled?: boolean
}) {
  return (
    <Select value={value} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
      <SelectContent>
        {checkTypes.map((t) => (
          <SelectItem key={t} value={t} title={t}>{checkTypeLabel(t)}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
