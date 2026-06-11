"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"

import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@workspace/ui/components/sidebar"
import { getIcon } from "@/lib/icon-map"
import { usePermissions } from "@/features/permissions/use-permissions"
import { urlToMenuKey } from "@/lib/permission-registry"
import { getMyInboxCount } from "@/features/change-mgmt/api"
import { useAuth } from "@/features/auth"
import type { MenuGroup } from "@/types/menu"

interface AppSidebarNavProps {
  groups: MenuGroup[]
}

export function AppSidebarNav({ groups }: AppSidebarNavProps) {
  const pathname = usePathname()
  const { isMenuAllowed } = usePermissions()
  const { user } = useAuth()

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

  return (
    <>
      {groups.map((group) => {
        // Administration group is only visible to admins
        if (group.id === "admin" && !user?.is_admin) return null

        // 권한 매트릭스(권한 관리 메뉴)에서 내 역할이 차단된 메뉴는 숨김.
        // open-by-default — 설정 없는 메뉴는 항상 통과한다.
        const visibleItems = group.items
          .filter((item) => !item.adminOnly || user?.is_admin)
          .filter((item) => isMenuAllowed(urlToMenuKey(item.url)))
        if (visibleItems.length === 0) return null

        return (
        <SidebarGroup key={group.id}>
          <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
          <SidebarMenu>
            {visibleItems
              .map((item) => {
              const Icon = getIcon(item.icon)
              return (
                <SidebarMenuItem key={item.id}>
                  <SidebarMenuButton
                    asChild
                    isActive={pathname === item.url || (item.url !== "/dashboard" && pathname.startsWith(item.url + "/"))}
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
        </SidebarGroup>
        )
      })}
    </>
  )
}
