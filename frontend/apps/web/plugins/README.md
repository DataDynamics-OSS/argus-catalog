# 프론트엔드 플러그인 (고객사 커스텀 화면)

고객사마다 필요한 화면은 제각각이지만, 그렇다고 오픈소스 코어를 직접 뜯어고치기 시작하면
나중에 업스트림 업데이트를 따라가기가 점점 어려워집니다. 그래서 Argus는 코어를 **건드리지 않고도**
고객사 전용 화면을 얹을 수 있는 인리포 오버레이 방식을 택했습니다.
다행히 익혀야 할 확장 지점은 단 두 종류뿐입니다.

| 코어 측 (한 번만 존재) | 역할 |
|---|---|
| `app/dashboard/x/[...slug]/page.tsx` | 모든 플러그인 화면을 받는 단일 호스트 라우트 |
| `lib/menu.ts` · `lib/icon-map.ts` | `plugins/registry` 의 메뉴·아이콘을 베이스에 병합 |

## 새 고객사 플러그인 추가하기

1. **폴더 생성** — `plugins/<고객사>/` (예: `plugins/acme/`)
2. **`ArgusPlugin` 정의** — `plugins/<고객사>/index.tsx`
   ```tsx
   import { Receipt } from "lucide-react"
   import type { ArgusPlugin } from "../types"
   import { BillingScreen } from "./billing-screen"

   export const acmePlugin: ArgusPlugin = {
     id: "acme",
     icons: { Receipt },                                  // 코어 icon-map 에 없는 아이콘만
     menu: [{ id: "acme", label: "ACME", items: [
       { id: "acme-billing", title: "정산",
         url: "/dashboard/x/acme/billing", icon: "Receipt" },
     ]}],
     screens: [{ slug: "acme/billing", component: BillingScreen }],
   }
   ```
3. **화면 컴포넌트 작성** — `plugins/<고객사>/billing-screen.tsx` (`"use client"`).
   코어의 `DashboardHeader`, `@workspace/ui`, `authFetch` 를 그대로 재사용하세요.
4. **레지스트리에 등록** — `plugins/registry.ts` 의 `plugins` 배열에 한 줄 추가.

여기서 한 가지만 기억하면 됩니다. 메뉴의 `url`(`/dashboard/x/<slug>`)과 화면의 `slug` 는
정확히 짝이 맞아야 합니다. 둘이 어긋나면 메뉴를 눌러도 화면이 뜨지 않습니다.

## 백엔드 API가 필요하면

화면만으로 끝나지 않고 서버 쪽 로직까지 필요할 때가 있죠. 이때도 코어를 손대지 않습니다.
`extensions/argus-plugin-<고객사>/` 패키지로 FastAPI 라우터를 만들고
`argus.plugins` entry point 에 등록하면 `/api/v1/<...>` 로 자동 노출됩니다.
프론트에서는 코어의 `authFetch("/api/v1/acme/...")` 로 호출하세요.
(예시: `extensions/argus-plugin-acme/`)

## 권한 게이팅

플러그인이라고 해서 권한 검사에서 빠지지는 않습니다. 플러그인 메뉴도 코어와 똑같이
`urlToMenuKey(url)` → 권한 매트릭스를 거쳐 노출 여부가 결정됩니다.
특정 메뉴를 관리자 전용으로 두고 싶다면 메뉴 아이템에 `adminOnly: true` 를 지정하세요.

## OSS 기본 동작 보장

플러그인을 하나도 쓰지 않는 순수 오픈소스 사용자라면 어떻게 될까요? 걱정하지 않아도 됩니다.
`registry.ts` 의 `plugins` 배열을 비우면(`[]`) 메뉴·화면·아이콘이 전혀 추가되지 않아
오픈소스 코어와 **완전히 동일하게** 동작합니다. (= 원래 기능 무훼손)

## 대용량 화면

화면 하나가 무거워서 초기 번들에 부담을 주는 경우도 있습니다. 이럴 때는
`screens` 의 `component` 를 `next/dynamic` 으로 지연 로딩해서 필요할 때만 불러오세요.
