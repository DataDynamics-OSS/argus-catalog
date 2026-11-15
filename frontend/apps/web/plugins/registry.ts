// 플러그인 레지스트리 — 인리포 오버레이.
//
// 고객사 플러그인을 활성화하는 "단 한 곳". 고객 폴더(plugins/<고객>/)를 만들고
// export 한 ArgusPlugin 을 아래 `plugins` 배열에 추가하면 끝이다.
//
// OSS 기본 빌드에서는 이 배열을 빈 상태([])로 두면 메뉴/화면/아이콘이 전혀 추가되지
// 않아 코어 동작과 완전히 동일하다. (= 원래 기능 무훼손)

import type { ComponentType } from "react"
import type { LucideIcon } from "lucide-react"
import type { MenuGroup } from "@/types/menu"
import type { ArgusPlugin, PluginHelp } from "./types"
import type { FeatureEntry } from "@/lib/permission-registry"

/** 활성 플러그인 목록. 고객사 플러그인을 여기에 추가한다. */
const plugins: ArgusPlugin[] = [
  // 예: acmePlugin (plugins/acme/ 예시 참고)
]

/** 플러그인이 선언한 기능 권한 — 권한 관리 › 기능 권한에 행으로 보인다(백엔드 플러그인 features 와 같은 키). */
export const pluginFeatures: FeatureEntry[] = plugins.flatMap((p) => p.features ?? [])

/** 사이드바에 병합할 플러그인 메뉴 그룹들. */
export const pluginMenus: MenuGroup[] = plugins.flatMap((p) => p.menu ?? [])

/** slug → 화면 컴포넌트. 호스트 라우트(/dashboard/x/[...slug])가 조회한다. */
export const pluginScreens: Record<string, ComponentType> = Object.fromEntries(
  plugins.flatMap((p) => (p.screens ?? []).map((s) => [s.slug, s.component] as const)),
)

/** slug → 도움말(PluginScreen.help). 헤더 도움말 아이콘이 조회한다. */
export const pluginHelp: Record<string, PluginHelp> = Object.fromEntries(
  plugins.flatMap((p) => (p.screens ?? []).flatMap((s) => (s.help ? [[s.slug, s.help] as const] : []))),
)

/** 플러그인이 제공하는 추가 lucide 아이콘(이름→컴포넌트). icon-map 이 fallback 으로 참조. */
export const pluginIcons: Record<string, LucideIcon> = Object.assign(
  {},
  ...plugins.map((p) => p.icons ?? {}),
)
