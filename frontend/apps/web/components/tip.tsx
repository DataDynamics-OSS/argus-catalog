"use client"

/**
 * 툴팁 공용 부품(TODO-120) — 화면마다 Tooltip · title 을 따로 짜지 않는다.
 *
 * - `Tip`: 어떤 요소든 감싸 툴팁을 단다(아이콘 단추 · 배지 등). 자식은 ref 를 받는 요소 하나.
 *   메뉴 · 팝오버를 여는 단추는 `XxxTrigger asChild` 바깥을 감싼다.
 * - `InfoTip`: 용어 · 판정 기준 설명 — 점선 밑줄 글자에 마우스를 올리거나 포커스하면 뜬다.
 * - `DisabledReason`: 못 누르는 단추의 사유. 공용 Button 은 비활성일 때 pointer-events 가 꺼져 title · 툴팁이
 *   뜨지 않으므로(DEF-118) 감싼 span 이 마우스 · 포커스를 받는다.
 * 대기 시간은 루트 레이아웃의 TooltipProvider(300ms) 하나.
 */
import * as React from "react"

import { Tooltip, TooltipContent, TooltipTrigger } from "@workspace/ui/components/tooltip"
import { cn } from "@workspace/ui/lib/utils"

type Side = "top" | "right" | "bottom" | "left"

export function Tip({ label, side, children }: { label: React.ReactNode; side?: Side; children: React.ReactElement }) {
  if (label == null || label === "") return children
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side={side} className="max-w-sm whitespace-pre-wrap">{label}</TooltipContent>
    </Tooltip>
  )
}

export function InfoTip({
  tip,
  side = "top",
  className,
  children,
}: {
  tip: React.ReactNode
  side?: Side
  className?: string
  children: React.ReactNode
}) {
  if (tip == null || tip === "") return <>{children}</>
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          className={cn(
            "cursor-help underline decoration-dotted decoration-muted-foreground/50 underline-offset-2 outline-none focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-ring/50",
            className,
          )}
        >
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent side={side} className="max-w-sm whitespace-pre-wrap font-normal">{tip}</TooltipContent>
    </Tooltip>
  )
}

export function DisabledReason({
  reason,
  side = "top",
  className,
  children,
}: {
  /** 비어 있으면 감싸지 않는다 — 누를 수 있을 때는 그대로 */
  reason: React.ReactNode
  side?: Side
  className?: string
  children: React.ReactNode
}) {
  if (reason == null || reason === "" || reason === false) return <>{children}</>
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} className={cn("inline-flex cursor-not-allowed outline-none", className)}>{children}</span>
      </TooltipTrigger>
      <TooltipContent side={side} className="max-w-sm whitespace-pre-wrap">{reason}</TooltipContent>
    </Tooltip>
  )
}
