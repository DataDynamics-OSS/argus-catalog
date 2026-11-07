"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { ChevronDown, Search, X } from "lucide-react"

import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarInput,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@workspace/ui/components/sidebar"
import { cn } from "@workspace/ui/lib/utils"
import { getIcon } from "@/lib/icon-map"
import { usePermissions } from "@/features/permissions/use-permissions"
import { urlToMenuKey } from "@/lib/permission-registry"
import { getMyInboxCount } from "@/features/change-mgmt/api"
import { useAuth } from "@/features/auth"
import type { MenuGroup } from "@/types/menu"

interface AppSidebarNavProps {
  groups: MenuGroup[]
}

// 접힌 그룹 상태 persistence. 저장 형태: string[] (접힌 그룹 id 목록).
// 스키마 변경 시 버전 번호를 올려 기존 값을 무효화.
const COLLAPSED_GROUPS_KEY = "argus-sidebar-collapsed-groups-v1"

function loadCollapsedGroups(): Set<string> {
  if (typeof window === "undefined") return new Set()
  try {
    const raw = window.localStorage.getItem(COLLAPSED_GROUPS_KEY)
    if (!raw) return new Set()
    const arr = JSON.parse(raw)
    return new Set(Array.isArray(arr) ? arr.filter((x) => typeof x === "string") : [])
  } catch {
    return new Set()
  }
}

function saveCollapsedGroups(set: Set<string>) {
  try {
    window.localStorage.setItem(COLLAPSED_GROUPS_KEY, JSON.stringify([...set]))
  } catch {
    /* localStorage 비허용/가득참 환경은 무시 */
  }
}

function isActive(pathname: string, url: string) {
  return pathname === url || (url !== "/dashboard" && pathname.startsWith(url + "/"))
}

export function AppSidebarNav({ groups }: AppSidebarNavProps) {
  const pathname = usePathname()
  const { isMenuAllowed } = usePermissions()
  const { user } = useAuth()
  const { state, open, openMobile, isMobile, setOpen, setOpenMobile } = useSidebar()
  // 아이콘 모드(데스크톱 접힘)에서는 검색·그룹 라벨이 숨겨지므로 그룹 접기도 적용하지 않는다.
  const iconMode = !isMobile && state === "collapsed"

  // 내 결재함 뱃지 — 결재 대기 건수를 주기적으로 폴링한다 (실패 시 0)
  const [inboxCount, setInboxCount] = useState(0)
  useEffect(() => {
    let alive = true
    const tick = () => getMyInboxCount().then((n) => alive && setInboxCount(n))
    void tick()
    const t = setInterval(tick, 60_000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [pathname])

  // 그룹별 접힘 상태 — 초기엔 빈 Set(모두 펼침), 마운트 후 localStorage 로드.
  // SSR 에서는 항상 빈 Set 이라 hydration mismatch 가 없다.
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set())
  useEffect(() => {
    setCollapsedGroups(loadCollapsedGroups())
  }, [])

  function toggleGroup(id: string) {
    setCollapsedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      saveCollapsedGroups(next)
      return next
    })
  }

  // 메뉴 검색. 검색어가 있으면 메뉴 이름 부분일치 항목만 보이고, 매칭이 0건인 그룹은
  // 통째로 숨긴다. 사용자가 접어둔 그룹도 검색 중에는 강제로 펼친다.
  const [query, setQuery] = useState("")
  const searchInputRef = useRef<HTMLInputElement>(null)

  // 전역 단축키: Ctrl/Cmd + / 로 검색창 포커스. 사이드바가 접혀 있으면 먼저 펼친다.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "/" || !(e.ctrlKey || e.metaKey)) return
      e.preventDefault()
      const focus = () => {
        searchInputRef.current?.focus()
        searchInputRef.current?.select()
      }
      const isOpen = isMobile ? openMobile : open
      if (isOpen) {
        focus()
        return
      }
      if (isMobile) setOpenMobile(true)
      else setOpen(true)
      // 펼친 뒤 입력창이 표시되면 포커스
      setTimeout(focus, 50)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [isMobile, open, openMobile, setOpen, setOpenMobile])

  const visibleGroups = useMemo(() => {
    const q = query.trim().toLowerCase()
    return groups
      .filter((group) => group.id !== "admin" || user?.is_admin) // 관리 그룹은 관리자만
      .map((group) => ({
        ...group,
        // 권한 매트릭스(권한 관리 메뉴)에서 내 역할이 차단된 메뉴는 숨김.
        // open-by-default — 설정 없는 메뉴는 항상 통과한다.
        items: group.items
          .filter((item) => !item.adminOnly || user?.is_admin)
          .filter((item) => isMenuAllowed(urlToMenuKey(item.url)))
          .filter((item) => !q || item.title.toLowerCase().includes(q)),
      }))
      .filter((group) => group.items.length > 0)
  }, [groups, user?.is_admin, isMenuAllowed, query])

  const isSearching = query.trim().length > 0

  // 현재 경로가 속한 그룹은 접힘 상태여도 강제로 펼친다 — 현재 페이지의 맥락 유지.
  const activeGroupId = visibleGroups.find((g) => g.items.some((it) => isActive(pathname, it.url)))?.id

  return (
    <>
      {/* 메뉴 검색 — 스크롤과 무관하게 상단 고정. 아이콘 모드에서는 숨김. */}
      <div className="sticky top-0 z-10 bg-sidebar px-2 pt-2 group-data-[collapsible=icon]:hidden">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-sidebar-foreground/60" />
          <SidebarInput
            ref={searchInputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setQuery("")
                searchInputRef.current?.blur()
              }
            }}
            placeholder="메뉴 검색"
            aria-label="메뉴 검색"
            // 기본 SidebarInput(흰 배경) 대신 사이드바 톤 — 평소엔 accent 반투명, 포커스 시 진하게
            className="border-transparent bg-sidebar-accent/40 pr-7 pl-7 text-sm text-sidebar-accent-foreground placeholder:text-sidebar-foreground/60 focus-visible:border-sidebar-border focus-visible:bg-sidebar-accent/60 focus-visible:ring-0 dark:bg-sidebar-accent/40"
          />
          {query ? (
            <button
              type="button"
              onClick={() => {
                setQuery("")
                searchInputRef.current?.focus()
              }}
              aria-label="검색 지우기"
              className="absolute top-1/2 right-1 inline-flex size-5 -translate-y-1/2 items-center justify-center rounded text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
            >
              <X className="size-3.5" />
            </button>
          ) : (
            <kbd className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 rounded border border-sidebar-border px-1 py-0.5 font-mono text-[10px] leading-none text-sidebar-foreground/60">
              Ctrl /
            </kbd>
          )}
        </div>
      </div>

      {isSearching && visibleGroups.length === 0 && (
        <p className="px-4 py-2 text-xs text-sidebar-foreground/60 group-data-[collapsible=icon]:hidden">
          &quot;{query}&quot; 와 일치하는 메뉴 없음
        </p>
      )}

      {visibleGroups.map((group) => {
        const isGroupCollapsed =
          !iconMode && !isSearching && collapsedGroups.has(group.id) && group.id !== activeGroupId
        return (
          <SidebarGroup key={group.id}>
            <SidebarGroupLabel asChild>
              <button
                type="button"
                onClick={() => toggleGroup(group.id)}
                aria-expanded={!isGroupCollapsed}
                className="w-full justify-between hover:text-sidebar-foreground"
              >
                <span>{group.label}</span>
                <ChevronDown
                  className={cn("size-3! transition-transform duration-150", isGroupCollapsed && "-rotate-90")}
                />
              </button>
            </SidebarGroupLabel>
            {!isGroupCollapsed && (
              <SidebarMenu>
                {group.items.map((item) => {
                  const Icon = getIcon(item.icon)
                  return (
                    <SidebarMenuItem key={item.id}>
                      <SidebarMenuButton
                        asChild
                        isActive={isActive(pathname, item.url)}
                        tooltip={item.title}
                        className="text-sm"
                      >
                        <Link href={item.url} prefetch={false}>
                          <Icon />
                          <span>{item.title}</span>
                        </Link>
                      </SidebarMenuButton>
                      {item.id === "my-approvals" && inboxCount > 0 && (
                        <SidebarMenuBadge>{inboxCount}</SidebarMenuBadge>
                      )}
                    </SidebarMenuItem>
                  )
                })}
              </SidebarMenu>
            )}
          </SidebarGroup>
        )
      })}
    </>
  )
}
