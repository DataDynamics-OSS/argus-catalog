"use client"

/**
 * 여러 값 배지 입력 공용 부품(TODO-143) — "A, B, C" 처럼 쉼표로 이어 적던 입력을 대신한다.
 *
 * - 글자를 적고 Enter 를 누르면 배지가 되고, 배지의 × 로 뺀다. 빈 칸에서 Backspace 는 마지막 배지를 뺀다.
 *   입력란을 벗어날 때 적다 만 글자도 배지로 넣는다. 같은 값은 한 번만.
 * - 쉼표는 값의 일부다 — "주소(시, 구, 동)" 도 배지 하나. 여러 값을 한 번에 넣으려면 줄마다 하나씩 붙여 넣는다.
 * - 값은 문자열 배열(`string[]`). 서버에도 배열로 보낸다 — 쉼표로 이어 붙이지 않는다.
 *   (가드: lib/tag-input.test.ts — 화면에서 `split(",")` 로 목록을 만들지 않는다)
 * - 크기: default(최소 h-9) · sm(최소 h-8). 바깥 상자가 입력창 모양(테두리 · 포커스 링)을 맡는다.
 */
import * as React from "react"
import { X } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { cn } from "@workspace/ui/lib/utils"

import { Tip } from "@/components/tip"
import { cleanTags } from "@/lib/text-list"

export interface TagInputProps {
  value: readonly string[]
  onChange: (next: string[]) => void
  id?: string
  placeholder?: string
  disabled?: boolean
  size?: "default" | "sm"
  className?: string
  "aria-label"?: string
  "aria-invalid"?: boolean
  /** 배지 글꼴 — 패턴 · 코드 값은 mono */
  mono?: boolean
  /**
   * 쉼표도 값 구분으로 볼 때(값에 쉼표가 들어갈 수 없는 URL · 메일 주소 · 언어 코드 · 사용자 이름) — "ko, en" Enter → 배지 둘.
   * 서버가 쉼표 문자열로 저장하는 항목에 쓴다(`lib/text-list.ts` `splitCsv` · `joinCsv`).
   */
  commaSeparates?: boolean
}

export function TagInput({
  value,
  onChange,
  id,
  placeholder = "입력 후 Enter",
  disabled,
  size = "default",
  className,
  mono,
  commaSeparates,
  ...aria
}: TagInputProps) {
  const [text, setText] = React.useState("")
  const inputRef = React.useRef<HTMLInputElement>(null)
  const tags = value

  const add = (raw: string[]) => {
    if (commaSeparates) raw = raw.flatMap((r) => r.split(","))
    const next = cleanTags([...tags, ...raw])
    if (next.length !== tags.length) onChange(next)
    setText("")
  }
  const remove = (t: string) => onChange(tags.filter((x) => x !== t))

  return (
    <div
      className={cn(
        "flex w-full min-w-0 flex-wrap items-center gap-1 rounded-md border border-input bg-transparent px-1.5 py-1 shadow-xs transition-[color,box-shadow] dark:bg-input/30",
        "focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50",
        "has-[[aria-invalid=true]]:border-destructive has-[[aria-invalid=true]]:ring-destructive/20",
        size === "sm" ? "min-h-8" : "min-h-9",
        disabled && "pointer-events-none cursor-not-allowed opacity-50",
        className,
      )}
      onClick={() => inputRef.current?.focus()}
    >
      {tags.map((t) => (
        <Badge key={t} variant="secondary" className={cn("max-w-full gap-1", mono && "font-mono")}>
          <span className="truncate" title={t}>{t}</span>
          {!disabled && (
            <Tip label={`${t} 빼기`}>
              <button
                type="button"
                className="rounded-sm hover:opacity-70 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
                onClick={(e) => {
                  e.stopPropagation()
                  remove(t)
                }}
                aria-label={`${t} 빼기`}
              >
                <X className="size-3" />
              </button>
            </Tip>
          )}
        </Badge>
      ))}
      <input
        ref={inputRef}
        id={id}
        disabled={disabled}
        aria-label={aria["aria-label"]}
        aria-invalid={aria["aria-invalid"] || undefined}
        className={cn(
          "h-6 min-w-[8rem] flex-1 bg-transparent px-1 text-base outline-none placeholder:text-placeholder md:text-sm",
          mono && "font-mono",
        )}
        value={text}
        placeholder={tags.length ? undefined : placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.nativeEvent.isComposing) return
          if (e.key === "Enter") {
            e.preventDefault()
            if (text.trim()) add([text])
          } else if (e.key === "Backspace" && text === "" && tags.length > 0) {
            remove(tags[tags.length - 1]!)
          }
        }}
        onPaste={(e) => {
          const pasted = e.clipboardData.getData("text")
          if (!/[\r\n]/.test(pasted)) return // 한 줄은 그대로 입력란에(쉼표도 값의 일부 — commaSeparates 면 Enter 때 나눔)
          e.preventDefault()
          add([text + pasted.split(/\r?\n/)[0], ...pasted.split(/\r?\n/).slice(1)])
        }}
        onBlur={() => {
          if (text.trim()) add([text])
        }}
      />
    </div>
  )
}
