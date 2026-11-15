import pkg from "../package.json"

import {
  Sidebar,
  SidebarContent,
  SidebarFooter, // Added for SSO AUTH
  SidebarHeader,
  SidebarMenu,
  SidebarMenuItem,
  SidebarRail,
} from "@workspace/ui/components/sidebar"
import { Separator } from "@workspace/ui/components/separator"
import { AppSidebarNav } from "@/components/app-sidebar-nav"
import { SidebarBrand } from "@/components/sidebar-brand"
import { SidebarCollapseButton } from "@/components/sidebar-collapse-button"
import { SidebarUser } from "@/components/sidebar-user" // Added for SSO AUTH
import { getMenu } from "@/lib/menu"

export async function AppSidebar() {
  const menu = await getMenu()

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        {/* relative — 펼침 상태의 접기 버튼(‹)이 브랜드 행 오른쪽에 absolute 로 붙는다 */}
        <SidebarMenu className="relative">
          <SidebarMenuItem>
            {/* 제품명 · 회사명 · 링크는 설정 > 사이드바 표시 */}
            <SidebarBrand />
          </SidebarMenuItem>
          {/* 사이드바 접기/펼치기 — 펼침: 브랜드 옆 ‹, 접힘: 로고 아래 › */}
          <SidebarCollapseButton />
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
        {/* 버전 표기(package.json — 루트 VERSION 과 락스텝) — 클릭 시 GitHub 리포로 새 탭 이동. 접힘(icon) 시 숨김. */}
        <a
          href="https://github.com/DataDynamics-OSS/argus-catalog"
          target="_blank"
          rel="noopener noreferrer"
          className="flex h-[22px] items-center justify-center text-xs text-sidebar-accent-foreground transition-colors hover:underline group-data-[collapsible=icon]:hidden"
        >
          Version {pkg.version}
        </a>
      </SidebarFooter>

      <SidebarRail />
    </Sidebar>
  )
}
