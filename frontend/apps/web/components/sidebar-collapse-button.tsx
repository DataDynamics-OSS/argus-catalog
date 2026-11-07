"use client"

import { ChevronLeft, ChevronRight } from "lucide-react"

import {
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@workspace/ui/components/sidebar"

/**
 * 사이드바 접기/펼치기 버튼 (데스크톱 전용 — 모바일은 시트라 헤더 트리거로 닫는다).
 *
 * - 펼침: 브랜드 행 오른쪽에 ‹ 아이콘 버튼
 * - 접힘(아이콘 모드): 로고 아래 메뉴 항목 형태의 › 버튼 + 툴팁
 * 상태는 SidebarProvider 가 `sidebar_state` 쿠키에 저장하고 대시보드 레이아웃이 읽어 복원한다.
 */
export function SidebarCollapseButton() {
  const { state, isMobile, toggleSidebar } = useSidebar()
  if (isMobile) return null

  if (state === "expanded") {
    return (
      <button
        type="button"
        onClick={toggleSidebar}
        title="사이드바 접기 (Ctrl+B)"
        aria-label="사이드바 접기"
        className="absolute top-1/2 right-0 inline-flex size-6 -translate-y-1/2 items-center justify-center rounded-md text-sidebar-foreground/60 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
      >
        <ChevronLeft className="size-4" />
      </button>
    )
  }

  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        onClick={toggleSidebar}
        tooltip="사이드바 펼치기 (Ctrl+B)"
        aria-label="사이드바 펼치기"
        className="text-sidebar-foreground/60"
      >
        <ChevronRight />
      </SidebarMenuButton>
    </SidebarMenuItem>
  )
}
