"use client"

/**
 * 검색어 입력창 공용 부품(TODO-139) — 화면마다 돋보기 · 여백 · 지우기 단추를 따로 짜지 않는다.
 *
 * - 왼쪽 안쪽에 돋보기(세로 가운데, 클릭 통과), 값이 있으면 오른쪽에 × 지우기 단추. Esc 도 지운다
 *   (값이 있을 때만 — 비어 있으면 Esc 는 대화상자 닫기 같은 바깥 동작으로 넘어간다).
 * - 크기: default h-9(목록 툴바) · sm h-8(작은 툴바 · 대화상자 · 팝오버) · xs h-7(좁은 패널 안 목록 거르기).
 * - 실행 방식: 입력하면 바로 거르는 곳은 onValueChange 만, Enter 로 서버 검색하는 곳은 onSearch 도 넘긴다.
 *   이때 플레이스홀더에 "(Enter)" 안내는 붙이지 않는다 — 돋보기 + Enter 가 기본 약속.
 * - 플레이스홀더: "○○ 검색…"처럼 한국어, 끝은 말줄임표 "…" 하나.
 * - 너비 · 배치는 className(바깥 상자), 입력창 자체 색 등은 inputClassName.
 * - `CommandInput`(콤보박스 안 검색)은 돋보기가 이미 들어 있어 이 부품을 쓰지 않는다.
 */
import * as React from "react"
import { Search, X } from "lucide-react"

import { Input } from "@workspace/ui/components/input"
import { cn } from "@workspace/ui/lib/utils"

import { Tip } from "@/components/tip"

type Size = "default" | "sm" | "xs"

const SIZE: Record<Size, { input: string; icon: string; clear: string }> = {
  default: { input: "h-9 pl-8", icon: "left-2.5 size-4", clear: "size-4" },
  sm: { input: "h-8 pl-8", icon: "left-2.5 size-4", clear: "size-3.5" },
  xs: { input: "h-7 pl-7 text-sm", icon: "left-2 size-3.5", clear: "size-3.5" },
}

export interface SearchInputProps extends Omit<React.ComponentProps<"input">, "size" | "value" | "onChange" | "type"> {
  value: string
  onValueChange: (value: string) => void
  /** Enter 로 검색 — 서버에 묻는 검색처럼 입력마다 거르지 않는 곳 */
  onSearch?: () => void
  /** × · Esc 로 지울 때 — 넘기면 onValueChange("") 대신 이것만 부른다(지운 즉시 다시 검색할 때) */
  onClear?: () => void
  size?: Size
  /** 입력창 자체(색 · 글꼴)에 붙일 클래스 — 너비 · 배치는 className */
  inputClassName?: string
}

export function SearchInput({
  value,
  onValueChange,
  onSearch,
  onClear,
  size = "default",
  className,
  inputClassName,
  onKeyDown,
  ref,
  ...props
}: SearchInputProps) {
  const inner = React.useRef<HTMLInputElement | null>(null)
  const s = SIZE[size]

  const setRefs = React.useCallback(
    (el: HTMLInputElement | null) => {
      inner.current = el
      if (typeof ref === "function") ref(el)
      else if (ref) ref.current = el
    },
    [ref],
  )

  const clear = () => {
    if (onClear) onClear()
    else onValueChange("")
  }

  return (
    <div className={cn("relative", className)}>
      <Search
        aria-hidden
        className={cn("pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted-foreground", s.icon)}
      />
      <Input
        ref={setRefs}
        type="text"
        role="searchbox"
        enterKeyHint="search"
        value={value}
        onChange={(e) => onValueChange(e.target.value)}
        onKeyDown={(e) => {
          onKeyDown?.(e)
          if (e.defaultPrevented || e.nativeEvent.isComposing) return
          if (e.key === "Enter" && onSearch) {
            e.preventDefault()
            onSearch()
          } else if (e.key === "Escape" && value) {
            // 값만 지우고 대화상자 · 시트가 닫히지 않게 한다
            e.preventDefault()
            e.stopPropagation()
            clear()
          }
        }}
        className={cn(s.input, value && "pr-8", inputClassName)}
        {...props}
      />
      {value && !props.disabled && !props.readOnly ? (
        <Tip label="검색어 지우기">
          <button
            type="button"
            aria-label="검색어 지우기"
            onClick={() => {
              clear()
              inner.current?.focus()
            }}
            className="absolute top-1/2 right-1.5 flex -translate-y-1/2 items-center justify-center rounded-sm p-0.5 text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
          >
            <X className={s.clear} />
          </button>
        </Tip>
      ) : null}
    </div>
  )
}
