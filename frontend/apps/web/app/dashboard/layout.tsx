import { cookies } from "next/headers"

import { SidebarInset, SidebarProvider } from "@workspace/ui/components/sidebar"
import { TooltipProvider } from "@workspace/ui/components/tooltip"
import { AppSidebar } from "@/components/app-sidebar"
import { AuthGuardWrapper } from "@/components/auth-guard-wrapper" // Added for SSO AUTH
import { FloatingChat } from "@/features/assistant/floating-chat"
import { MenuRouteGuard } from "@/features/permissions/menu-route-guard"
import { PermissionProvider } from "@/features/permissions/use-permissions"

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode
}) {
  // 사이드바 접힘 상태 복원 — SidebarProvider 가 토글 시 `sidebar_state` 쿠키에 기록한다.
  // 서버에서 읽어 defaultOpen 으로 넘겨야 새로고침 시 펼침→접힘 깜빡임이 없다.
  const sidebarOpen = (await cookies()).get("sidebar_state")?.value !== "false"

  return (
    // Added for SSO AUTH - redirects unauthenticated users to /login
    <AuthGuardWrapper>
      <TooltipProvider>
        {/* 권한 컨텍스트 — 사이드바 필터·기능 게이팅이 공유 */}
        <PermissionProvider>
        <SidebarProvider defaultOpen={sidebarOpen}>
          <AppSidebar />
          <SidebarInset>
            {/* 막힌 메뉴는 주소로 직접 들어와도 페이지를 보여 주지 않는다 */}
            <MenuRouteGuard>{children}</MenuRouteGuard>
          </SidebarInset>
          {/* AI 어시스턴트 — LLM 제공자 활성 시에만 우하단 플로팅 버튼 표시 */}
          <FloatingChat />
        </SidebarProvider>
        </PermissionProvider>
      </TooltipProvider>
    </AuthGuardWrapper>
  )
}
