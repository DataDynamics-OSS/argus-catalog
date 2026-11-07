"use client"

import { Toaster } from "sonner"

import { DEFAULT_DURATION_MS } from "@/lib/notify"

/** 앱 전체에서 유일한 toast 표시 영역. 위치·색·닫기 버튼은 여기서만 정한다. */
export function NotifyToaster() {
  return (
    <Toaster
      richColors
      closeButton
      position="top-center"
      duration={DEFAULT_DURATION_MS}
      visibleToasts={4}
    />
  )
}
