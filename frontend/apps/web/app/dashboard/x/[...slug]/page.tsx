"use client"

// 플러그인 화면 호스트 라우트 — 코어의 유일한 플러그인 라우트 파일.
//
// /dashboard/x/<slug...> 로 들어오는 모든 요청을 받아, 플러그인 레지스트리에서
// slug 에 매핑된 화면 컴포넌트를 찾아 렌더링한다. 매칭이 없으면 notFound().
//
// 이 덕분에 고객사 화면은 새 라우트 파일 없이 "순수 컴포넌트 + registry 한 줄"로만
// 추가된다. 코어는 이 파일 하나만 알고 있으면 된다.

import { useParams, notFound } from "next/navigation"

import { pluginScreens } from "@/plugins/registry"

export default function PluginHostPage() {
  const params = useParams<{ slug: string[] }>()
  const slug = Array.isArray(params.slug) ? params.slug : [params.slug].filter(Boolean)
  const Screen = pluginScreens[slug.join("/")]

  if (!Screen) notFound()

  return <Screen />
}
