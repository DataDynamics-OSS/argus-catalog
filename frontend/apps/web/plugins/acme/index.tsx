// 예시 고객사 플러그인 "ACME".
//
// OSS 에는 없는 '정산(billing)' 화면을 추가하는 예시다. 코어 파일을 전혀 수정하지
// 않고 이 폴더(plugins/acme/)만으로 구성되며, plugins/registry.ts 의 배열에 등록된다.
//
// 새 고객사를 만들려면 이 폴더를 복사해 id/menu/screens 를 바꾸면 된다.

import { Receipt } from "lucide-react"

import type { ArgusPlugin } from "../types"
import { BillingScreen } from "./billing-screen"

export const acmePlugin: ArgusPlugin = {
  id: "acme",
  // 코어 icon-map 에 없는 아이콘은 여기서 제공한다.
  icons: { Receipt },
  // 사이드바에 'ACME' 그룹을 새로 추가한다. (기존 그룹 id 를 쓰면 그 그룹에 덧붙는다)
  menu: [
    {
      id: "acme",
      label: "ACME",
      items: [
        {
          id: "acme-billing",
          title: "정산",
          url: "/dashboard/x/acme/billing",
          icon: "Receipt",
        },
      ],
    },
  ],
  // slug → 화면 컴포넌트. URL 의 /dashboard/x/ 뒤 경로와 일치해야 한다.
  screens: [{ slug: "acme/billing", component: BillingScreen }],
}
