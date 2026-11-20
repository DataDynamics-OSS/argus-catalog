"use client"

import type { CSSProperties } from "react"
import { useTheme } from "next-themes"
import { Toaster } from "sonner"

import { DEFAULT_DURATION_MS } from "@/lib/notify"

// 카드 바탕·글자·테두리는 앱 토큰(popover)을 따르고, 유형(성공·경고·오류·안내)은 왼쪽 띠와 아이콘 색으로만 구분한다.
// sonner 기본 규칙이 [data-sonner-toast][data-styled] 두 속성 선택자라 덮어쓰는 값에는 !(important)를 붙인다.
const TOKENS = {
  "--normal-bg": "var(--popover)",
  "--normal-text": "var(--popover-foreground)",
  "--normal-border": "var(--border)",
  "--border-radius": "var(--radius)",
  "--width": "380px",
} as CSSProperties

const CLASS_NAMES = {
  toast: "items-start! border-l-4! font-sans! shadow-lg!",
  title: "font-medium! leading-snug!",
  description: "text-muted-foreground! whitespace-pre-wrap! break-words",
  icon: "mt-px",
  actionButton: "bg-transparent! text-foreground! border! border-border! hover:bg-muted! self-center",
  closeButton: "bg-popover! text-muted-foreground! border-border! hover:text-foreground!",
  success: "border-l-emerald-600! [&_[data-icon]]:text-emerald-600",
  info: "border-l-sky-600! [&_[data-icon]]:text-sky-600",
  warning: "border-l-amber-500! [&_[data-icon]]:text-amber-600",
  error: "border-l-destructive! [&_[data-icon]]:text-destructive",
  loading: "border-l-border!",
}

/** 앱 전체에서 유일한 toast 표시 영역. 위치·색·닫기 버튼은 여기서만 정한다. */
export function NotifyToaster() {
  const { resolvedTheme } = useTheme()
  return (
    <Toaster
      theme={resolvedTheme === "dark" ? "dark" : "light"}
      position="bottom-right"
      closeButton
      // 여러 개가 동시에 뜨면 겹쳐 쌓지 않고(sonner 기본) 항상 펼쳐 세로로 나란히 보인다.
      expand
      gap={8}
      duration={DEFAULT_DURATION_MS}
      visibleToasts={4}
      style={TOKENS}
      toastOptions={{ classNames: CLASS_NAMES }}
    />
  )
}
