import { SidebarInset, SidebarProvider } from "@workspace/ui/components/sidebar"
import { TooltipProvider } from "@workspace/ui/components/tooltip"
import { AppSidebar } from "@/components/app-sidebar"
import { AuthGuardWrapper } from "@/components/auth-guard-wrapper" // Added for SSO AUTH
import { FloatingChat } from "@/features/assistant/floating-chat"
import { PermissionProvider } from "@/features/permissions/use-permissions"

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    // Added for SSO AUTH - redirects unauthenticated users to /login
    <AuthGuardWrapper>
      <TooltipProvider>
        {/* 권한 컨텍스트 — 사이드바 필터·기능 게이팅이 공유 */}
        <PermissionProvider>
        <SidebarProvider>
          <AppSidebar />
          <SidebarInset>
            {children}
          </SidebarInset>
          {/* AI 어시스턴트 — LLM 제공자 활성 시에만 우하단 플로팅 버튼 표시 */}
          <FloatingChat />
        </SidebarProvider>
        </PermissionProvider>
      </TooltipProvider>
    </AuthGuardWrapper>
  )
}
