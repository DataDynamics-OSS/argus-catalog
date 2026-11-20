import { marked } from "marked"

import { isRichHtml } from "@/lib/plain-text"

/** 설명이 리치 편집기가 저장한 HTML 인지 — 아니면 마크다운 · 평문(등록 창, AI 생성, API · 수집으로 들어온 값) */
export const isRichHtmlDescription = isRichHtml

/**
 * 데이터셋 설명 → 뷰어 · 편집기에 넣을 HTML.
 * 설명은 마크다운 · 평문이거나 리치 편집기가 저장한 HTML 이다. HTML 은 그대로 두고, 나머지는
 * 마크다운으로 변환한다 — 그대로 HTML 로 넣으면 줄바꿈이 사라지고 기호(``##`` · ``**`` · ``-``)가 한 문단에
 * 그대로 보인다(DEF-181). 평문의 줄바꿈도 지킨다(``breaks``). 정화는 주입하는 쪽(``RichTextViewer``)이 한다.
 */
export function descriptionToHtml(description: string | null | undefined): string {
  if (!description) return ""
  if (isRichHtmlDescription(description)) return description
  return (marked.parse(description, { async: false, gfm: true, breaks: true }) as string).trim()
}
