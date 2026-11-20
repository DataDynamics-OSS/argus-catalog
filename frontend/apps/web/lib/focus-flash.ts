/**
 * 링크로 가리킨 곳을 화면 가운데로 스크롤하고 잠깐 강조한다(TODO-151 — 신고 · 알림 링크의 댓글, 체크리스트의 메타데이터 항목).
 */

const FLASH_CLASSES = ["ring-2", "ring-primary/60", "bg-primary/10", "transition-colors"]
const FLASH_MS = 2500

/** `data-<attr>="<value>"` 인 요소를 찾아 스크롤 · 강조한다. 찾으면 true. */
export function scrollToAndFlash(attr: string, value: string | number): boolean {
  if (typeof document === "undefined") return false
  const v = String(value)
  const sel = `[data-${attr}="${typeof CSS !== "undefined" && CSS.escape ? CSS.escape(v) : v.replace(/"/g, '\\"')}"]`
  const el = document.querySelector<HTMLElement>(sel)
  if (!el) return false
  el.scrollIntoView({ behavior: "smooth", block: "center" })
  el.classList.add(...FLASH_CLASSES)
  window.setTimeout(() => el.classList.remove(...FLASH_CLASSES), FLASH_MS)
  return true
}
