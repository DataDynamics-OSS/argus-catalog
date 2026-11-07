// 플러그인 화면 접근 판단 — 호스트 라우트(/dashboard/x/[...slug])가 사용한다.
//
// 사이드바(components/app-sidebar-nav.tsx)와 같은 기준으로 판단한다:
//   1) 메뉴 아이템이 adminOnly 면 관리자만
//   2) 권한 매트릭스에서 해당 메뉴 키(plugin.<id>)가 내 역할에 막혀 있으면 차단
// UI 게이팅일 뿐이며, 데이터 보호는 플러그인 API 의 인증·권한 의존성이 담당한다.

import type { MenuGroup } from "@/types/menu"
import { urlToMenuKey } from "@/lib/permission-registry"

/** slug 경로(예: "acme/billing")에 대응하는 플러그인 화면 URL. */
export function pluginScreenUrl(slugPath: string): string {
  return `/dashboard/x/${slugPath}`
}

export function canAccessPluginScreen(
  url: string,
  menus: MenuGroup[],
  isAdmin: boolean,
  isMenuAllowed: (key: string | null) => boolean,
): boolean {
  const item = menus.flatMap((g) => g.items).find((i) => i.url === url)
  if (item?.adminOnly && !isAdmin) return false
  return isMenuAllowed(urlToMenuKey(url))
}
