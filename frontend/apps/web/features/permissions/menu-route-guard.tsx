"use client"

/**
 * 메뉴 라우트 가드 — 권한 매트릭스에서 막힌 메뉴의 페이지를 주소로 직접 열어도 보여 주지 않는다.
 *
 * - 관리 메뉴(registry 의 adminOnly: 사용자·설정·권한 관리, MLflow·OCI 모델 파일)는 관리자만.
 * - 그 밖의 메뉴는 /permissions/me 의 차단 목록(isMenuAllowed)을 따른다.
 * - "대시보드"(/dashboard) 키는 정확히 그 주소에만 적용한다 — 메뉴에 없는 하위 페이지(계정 설정 등)가
 *   가장 긴 prefix 인 대시보드에 매핑돼 함께 막히지 않도록.
 * - 플러그인 화면(/dashboard/x/...)은 호스트 라우트가 따로 검사한다.
 *
 * 화면 게이팅이며, 서버 차단은 메뉴 전용 API 의 require_menu(백엔드)가 담당한다.
 */

import { usePathname } from "next/navigation"
import { ShieldAlert } from "lucide-react"

import { DashboardHeader } from "@/components/dashboard-header"
import { useAuth } from "@/features/auth"
import { usePermissions } from "@/features/permissions/use-permissions"
import { MENU_REGISTRY, urlToMenuKey } from "@/lib/permission-registry"

const ADMIN_MENU_KEYS = new Set(MENU_REGISTRY.filter((m) => m.adminOnly).map((m) => m.key))

/** 경로 → 검사할 메뉴 key (검사 대상이 아니면 null). 테스트용으로 분리. */
export function guardedMenuKey(pathname: string): string | null {
  if (pathname.startsWith("/dashboard/x/")) return null
  const key = urlToMenuKey(pathname)
  if (key === "dashboard" && pathname !== "/dashboard") return null
  return key
}

export function MenuRouteGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? ""
  const { user, isLoading } = useAuth()
  const { loaded, isMenuAllowed } = usePermissions()

  const key = guardedMenuKey(pathname)
  if (!key) return <>{children}</>

  // 사용자·권한 정보를 받기 전에는 그리지 않는다 — 열렸다가 막히는 깜빡임 방지
  if (isLoading || !loaded) return null
  if (user?.is_admin) return <>{children}</>
  if (ADMIN_MENU_KEYS.has(key) || !isMenuAllowed(key)) return <MenuAccessDenied />
  return <>{children}</>
}

export function MenuAccessDenied() {
  return (
    <>
      <DashboardHeader title="접근 권한 없음" />
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
        <ShieldAlert className="h-10 w-10 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">
          이 화면에 접근할 권한이 없습니다. 필요하면 관리자에게 권한을 요청하세요.
        </p>
      </div>
    </>
  )
}
