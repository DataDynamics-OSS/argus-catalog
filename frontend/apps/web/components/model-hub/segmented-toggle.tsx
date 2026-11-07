"use client"

import { cn } from "@workspace/ui/lib/utils"

/** 작은 버튼 묶음 형태의 단일 선택 토글 (기간·지표 전환용). */
export function SegmentedToggle<T extends string>({
  value, onChange, options, className,
}: {
  value: T
  onChange: (v: T) => void
  options: { value: T; label: string }[]
  className?: string
}) {
  return (
    <div className={cn("inline-flex shrink-0 rounded-md border p-0.5", className)} role="tablist">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded px-2 py-0.5 text-xs transition-colors",
            value === o.value ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
