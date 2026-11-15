"use client"

import { useEffect } from "react"
import { usePathname } from "next/navigation"
import { recordNavigation } from "@/lib/navigation"

/**
 * 앱 안 경로 변경을 기록한다 — "뒤로" 버튼이 앱 밖(새 탭의 빈 기록)으로 나가지 않게 판단하는 근거(DEF-066).
 * 화면에는 아무것도 그리지 않는다.
 */
export function NavigationTracker() {
  const pathname = usePathname()
  useEffect(() => {
    if (pathname) recordNavigation(pathname)
  }, [pathname])
  return null
}
