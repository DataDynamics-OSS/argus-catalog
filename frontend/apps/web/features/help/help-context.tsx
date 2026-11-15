"use client"

// 현재 화면의 도움말 주제.
//
// 기본은 경로(usePathname)로 고르고, 경로만으로 구분되지 않는 곳(데이터셋 상세의 탭, 큰 다이얼로그)은
// 화면이 `useHelpTopic(id)` 로 덮어쓴다. 여러 곳이 동시에 지정하면 나중에 지정한 쪽(예: 열린 다이얼로그)이
// 이기고, 그 컴포넌트가 사라지면 바로 앞 주제로 돌아간다.
// 대시보드 레이아웃의 HelpTopicProvider 가 F1·Shift+? 단축키도 처리한다.

import { usePathname } from "next/navigation"
import {
  createContext, useCallback, useContext, useEffect, useId, useMemo, useState,
} from "react"

import { openHelp } from "./open-help"
import { resolveHelp, type ResolvedHelp } from "./resolve"

type Entry = { key: string; id: string }

interface HelpContextValue {
  push: (key: string, id: string) => void
  remove: (key: string) => void
  overrideId: string | null
}

const HelpContext = createContext<HelpContextValue | null>(null)

function isEditable(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  if (el.isContentEditable) return true
  const tag = el.tagName
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!el.closest("[contenteditable=true], .cm-editor, .monaco-editor")
}

export function HelpTopicProvider({ children }: { children: React.ReactNode }) {
  const [stack, setStack] = useState<Entry[]>([])

  const push = useCallback((key: string, id: string) => {
    setStack((prev) => {
      const i = prev.findIndex((e) => e.key === key)
      if (i < 0) return [...prev, { key, id }]
      if (prev[i]!.id === id) return prev
      // 같은 컴포넌트가 주제만 바꿀 때(탭 전환)는 자리를 지킨다
      const next = prev.slice()
      next[i] = { key, id }
      return next
    })
  }, [])

  const remove = useCallback((key: string) => {
    setStack((prev) => (prev.some((e) => e.key === key) ? prev.filter((e) => e.key !== key) : prev))
  }, [])

  const overrideId = stack.length ? stack[stack.length - 1]!.id : null

  // F1: 어디서든 도움말(브라우저 기본 도움말 대신). Shift+?: 입력 중이 아닐 때만.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const f1 = e.key === "F1" && !e.ctrlKey && !e.metaKey && !e.altKey
      const question = e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey && !isEditable(e.target)
      if (!f1 && !question) return
      e.preventDefault()
      const help = resolveHelp(window.location.pathname, overrideId)
      openHelp(help.url, help.title)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [overrideId])

  // 다른 화면으로 옮기면 그 화면의 컴포넌트가 사라지며 지정을 거둔다(useHelpTopic 의 정리 함수).
  // 여기서 pathname 으로 스택을 비우면 새 화면이 같은 커밋에서 넣은 지정까지 지워지므로 하지 않는다.

  const value = useMemo(() => ({ push, remove, overrideId }), [push, remove, overrideId])
  return <HelpContext.Provider value={value}>{children}</HelpContext.Provider>
}

/**
 * 이 컴포넌트가 화면에 있는 동안 도움말 주제를 `id` 로 지정한다(topics.ts 의 주제 id).
 * null 이면 지정하지 않는다(경로 기준).
 */
export function useHelpTopic(id: string | null): void {
  const ctx = useContext(HelpContext)
  const key = useId()
  const push = ctx?.push
  const remove = ctx?.remove
  useEffect(() => {
    if (!push || !remove) return
    if (id) push(key, id)
    else remove(key)
  }, [push, remove, key, id])
  useEffect(() => () => remove?.(key), [remove, key])
}

/** 지금 화면에서 열 도움말 */
export function useCurrentHelp(): ResolvedHelp {
  const ctx = useContext(HelpContext)
  const pathname = usePathname() ?? "/dashboard"
  return useMemo(() => resolveHelp(pathname, ctx?.overrideId ?? null), [pathname, ctx?.overrideId])
}
