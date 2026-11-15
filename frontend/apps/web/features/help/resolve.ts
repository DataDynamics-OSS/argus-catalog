// 경로·지정 주제 → 실제로 열 도움말(주제 이름·주소). React 없이 테스트할 수 있게 따로 둔다.

import { pluginHelp } from "@/plugins/registry"
import { FALLBACK_TOPIC, helpUrl, topicById, topicForPath, type HelpTopic } from "./topics"

/** 실제로 열 도움말 — 주제 + 열 주소 */
export interface ResolvedHelp {
  id: string
  title: string
  url: string
}

const PLUGIN_PREFIX = "/dashboard/x/"

/** 경로·덮어쓴 주제 id 로 열 도움말을 정한다. */
export function resolveHelp(pathname: string, overrideId: string | null): ResolvedHelp {
  const override = overrideId ? topicById(overrideId) : undefined
  if (override) return toResolved(override)
  if (pathname.startsWith(PLUGIN_PREFIX)) {
    const slug = pathname.slice(PLUGIN_PREFIX.length).replace(/\/+$/, "")
    const help = pluginHelp[slug]
    if (help) return { id: `plugin:${slug}`, title: help.title ?? "플러그인 화면", url: help.url }
  }
  return toResolved(topicForPath(pathname) ?? FALLBACK_TOPIC)
}

function toResolved(t: HelpTopic): ResolvedHelp {
  return { id: t.id, title: t.title, url: helpUrl(t) }
}

