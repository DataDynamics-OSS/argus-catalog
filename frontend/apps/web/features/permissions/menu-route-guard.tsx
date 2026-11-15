"use client"

/**
 * 메뉴 라우트 가드 — 권한 매트릭스에서 막힌 메뉴의 페이지를 주소로 직접 열어도 보여 주지 않는다.
 *
 * - 관리 메뉴(registry 의 adminOnly: 사용자·설정·권한 관리, MLflow·OCI 모델 파일)는 관리자만.
 *   단 grantedBy 가 있는 메뉴(메타데이터 수집)는 그 기능을 허용받은 사용자에게도 연다(TODO-074).
 * - 그 밖의 메뉴는 /permissions/me 의 차단 목록(isMenuAllowed)을 따른다.
 * - "대시보드"(/dashboard) 키는 정확히 그 주소에만 적용한다 — 메뉴에 없는 하위 페이지(계정 설정 등)가
 *   가장 긴 prefix 인 대시보드에 매핑돼 함께 막히지 않도록.
 * - 플러그인 화면(/dashboard/x/...)은 호스트 라우트가 따로 검사한다.
 * - 변경 요청 상세(/dashboard/changes/<id>)는 결재자가 내 결재함에서 열므로
 *   `changes` 또는 `my-approvals` 중 하나만 열려 있어도 보여 준다(DEF-064). 새 요청(/new)·목록은 `changes` 만.
 *
 * - 보기 전용 메뉴(TODO-124)는 페이지 위에 안내 줄을 띄운다 — 편집 단추는 각 화면이 canUseFeature 로 숨긴다.
 *
 * 화면 게이팅이며, 서버 차단은 메뉴 전용 API 의 require_menu(백엔드)가 담당한다.
 */

import { usePathname } from "next/navigation"
import { Eye, ShieldAlert } from "lucide-react"

import { DashboardHeader } from "@/components/dashboard-header"
import { useAuth } from "@/features/auth"
import { usePermissions } from "@/features/permissions/use-permissions"
import { adminMenuOpenTo, MENU_REGISTRY, urlToMenuKey } from "@/lib/permission-registry"

const ADMIN_MENU_KEYS = new Set(MENU_REGISTRY.filter((m) => m.adminOnly).map((m) => m.key))

/** 경로 → 검사할 메뉴 key (검사 대상이 아니면 null). 테스트용으로 분리. */
export function guardedMenuKey(pathname: string): string | null {
  if (pathname.startsWith("/dashboard/x/")) return null
  const key = urlToMenuKey(pathname)
  if (key === "dashboard" && pathname !== "/dashboard") return null
  return key
}

/** 경로에 대해 key 대신 함께 허용하는 메뉴 key(하나라도 열려 있으면 통과). 테스트용으로 분리. */
export function alternateMenuKeys(pathname: string): string[] {
  const m = /^\/dashboard\/changes\/([^/]+)\/?$/.exec(pathname)
  if (m && m[1] !== "new") return ["my-approvals"]
  return []
}

export function MenuRouteGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? ""
  const { user, isLoading } = useAuth()
  const { loaded, isMenuAllowed, isMenuReadOnly, canUseFeature } = usePermissions()

  const key = guardedMenuKey(pathname)
  if (!key) return <>{children}</>

  // 사용자·권한 정보를 받기 전에는 그리지 않는다 — 열렸다가 막히는 깜빡임 방지
  if (isLoading || !loaded) return null
  if (user?.is_admin) return <>{children}</>
  if (ADMIN_MENU_KEYS.has(key) && !adminMenuOpenTo(key, canUseFeature)) return <MenuAccessDenied />
  if (!isMenuAllowed(key) && !alternateMenuKeys(pathname).some((k) => isMenuAllowed(k))) {
    return <MenuAccessDenied />
  }
  if (isMenuReadOnly(key)) {
    return (
      <>
        <div role="status" className="flex items-center gap-1.5 border-b bg-muted/60 px-4 py-1.5 text-xs">
          <Eye className="h-3.5 w-3.5 shrink-0" aria-hidden />
          보기 전용 메뉴입니다. 내용은 볼 수 있지만 등록 · 수정 · 삭제 같은 편집 기능은 쓸 수 없습니다.
        </div>
        {children}
      </>
    )
  }
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
