"use client"

// 플러그인 화면 호스트 라우트 — 코어의 유일한 플러그인 라우트 파일.
//
// /dashboard/x/<slug...> 로 들어오는 모든 요청을 받아, 플러그인 레지스트리에서
// slug 에 매핑된 화면 컴포넌트를 찾아 렌더링한다. 매칭이 없으면 notFound().
//
// 이 덕분에 고객사 화면은 새 라우트 파일 없이 "순수 컴포넌트 + registry 한 줄"로만
// 추가된다. 코어는 이 파일 하나만 알고 있으면 된다.
//
// 접근 제어: 사이드바와 같은 기준(adminOnly · 권한 매트릭스의 메뉴 키)으로 주소를 직접
// 입력해 들어온 경우도 막는다. 어디까지나 UI 게이팅이며, 데이터 보호는 플러그인 API 의
// 인증·권한 의존성이 담당한다.

import { useParams, notFound } from "next/navigation"
import { ShieldAlert } from "lucide-react"

import { DashboardHeader } from "@/components/dashboard-header"
import { useAuth } from "@/features/auth"
import { usePermissions } from "@/features/permissions/use-permissions"
import { canAccessPluginScreen, pluginScreenUrl } from "@/plugins/access"
import { pluginMenus, pluginScreens } from "@/plugins/registry"

export default function PluginHostPage() {
  const params = useParams<{ slug: string[] }>()
  const { user, isLoading } = useAuth()
  const { loaded, isMenuAllowed } = usePermissions()

  const slug = Array.isArray(params.slug) ? params.slug : [params.slug].filter(Boolean)
  const slugPath = slug.join("/")
  const Screen = pluginScreens[slugPath]

  if (!Screen) notFound()

  // 사용자·권한 정보를 받기 전에는 화면을 그리지 않는다 — 허용된 뒤 막히는 깜빡임 방지.
  if (isLoading || !loaded) return null

  const allowed = canAccessPluginScreen(
    pluginScreenUrl(slugPath),
    pluginMenus,
    !!user?.is_admin,
    isMenuAllowed,
  )
  if (!allowed) return <AccessDenied />

  return <Screen />
}

function AccessDenied() {
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
