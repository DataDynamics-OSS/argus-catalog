// 플러그인 타입 — 고객사별 커스텀 화면 확장(seam).
//
// 코어는 이 타입과 registry 두 파일만 "확장 지점"으로 제공한다. 고객사 화면은
// plugins/<고객>/ 폴더에 ArgusPlugin 을 정의하고 registry 의 배열에 추가하기만
// 하면 된다. 코어의 어떤 파일도 수정하지 않는다.

import type { ComponentType } from "react"
import type { LucideIcon } from "lucide-react"
import type { MenuGroup } from "@/types/menu"

export interface PluginScreen {
  /** /dashboard/x/<slug> 로 매핑되는 경로. 예: "acme/billing" → /dashboard/x/acme/billing */
  slug: string
  /** 렌더링할 화면 컴포넌트. 보통 "use client" 컴포넌트. */
  component: ComponentType
}

export interface ArgusPlugin {
  /** 플러그인 식별자. 백엔드 plugins.enabled 와 맞추면 운영 관리가 쉽다. */
  id: string
  /** 사이드바에 병합할 메뉴 그룹/아이템. 기존 그룹 id 와 같으면 그 그룹에 아이템을 덧붙인다. */
  menu?: MenuGroup[]
  /** 이 플러그인이 제공하는 화면들. */
  screens?: PluginScreen[]
  /** 메뉴에서 쓸 추가 lucide 아이콘(이름→컴포넌트). 코어 icon-map 에 없는 아이콘을 쓸 때만 필요. */
  icons?: Record<string, LucideIcon>
}
