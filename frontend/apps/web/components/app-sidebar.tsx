import Link from "next/link"

import {
  Sidebar,
  SidebarContent,
  SidebarFooter, // Added for SSO AUTH
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@workspace/ui/components/sidebar"
import { Separator } from "@workspace/ui/components/separator"
import { AppSidebarNav } from "@/components/app-sidebar-nav"
import { Logo } from "@/components/logo"
import { SidebarUser } from "@/components/sidebar-user" // Added for SSO AUTH
import { getMenu } from "@/lib/menu"

export async function AppSidebar() {
  const menu = await getMenu()

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild>
              <Link href="/dashboard">
                <div className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground group-data-[collapsible=icon]:flex">
                  <Logo className="size-5" />
                </div>
                <span className="truncate text-lg font-bold group-data-[collapsible=icon]:hidden">
                  Argus Catalog
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <AppSidebarNav groups={menu.groups} />
      </SidebarContent>

      {/* Added for SSO AUTH - displays current user info and logout button */}
      <SidebarFooter>
        <SidebarUser />
        {/* 매우 옅은 구분선 — opacity 로 거의 안 보이게만. 접힘(icon) 시 숨김. */}
        <Separator className="my-1 bg-border/30 group-data-[collapsible=icon]:hidden" />
        {/* 회사 정보 — 외부 사이트로 새 탭 이동. height 30px 고정. 접힘(icon) 시 숨김. */}
        <a
          href="https://www.data-dynamics.io"
          target="_blank"
          rel="noopener noreferrer"
          className="flex h-[20px] items-center justify-center text-[11px] text-muted-foreground hover:text-foreground transition-colors group-data-[collapsible=icon]:hidden"
        >
          Data Dynamics Inc.
        </a>
      </SidebarFooter>

      <SidebarRail />
    </Sidebar>
  )
}
