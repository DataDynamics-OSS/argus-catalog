/**
 * 툴팁 정적 가드 (TODO-120 · DEF-118). 부품은 `components/tip.tsx`.
 *
 * - TooltipProvider 는 루트 레이아웃(`app/layout.tsx`) 하나 — 화면마다 두면 대기 시간이 제각각이 된다.
 * - 아이콘만 있는 단추는 `Tip` 으로 감싸 마우스 사용자에게도 이름을 보인다(aria-label 만으로는 안 보임).
 * - 못 누르는 단추의 사유는 `DisabledReason` 으로 — 공용 Button 은 비활성일 때 pointer-events 가 꺼져
 *   title 이 뜨지 않는다(DEF-118). 비활성 Button 에 조건부 title(`cond ? "사유" : …`)을 달지 않는다.
 * - 화면에서 Tooltip · TooltipTrigger · TooltipContent 를 직접 짜지 않는다(예외: 아래 TOOLTIP_DIRECT).
 */
import { readdirSync, readFileSync, statSync } from "node:fs"
import { join, relative } from "node:path"
import { describe, expect, it } from "vitest"

const ROOT = join(__dirname, "..")
const SKIP_DIRS = new Set(["node_modules", ".next", "public"])

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue
    const p = join(dir, name)
    if (statSync(p).isDirectory()) sourceFiles(p, out)
    else if (/\.tsx$/.test(name) && !/\.test\.tsx$/.test(name)) out.push(p)
  }
  return out
}

const files = sourceFiles(ROOT).map((p) => ({ rel: relative(ROOT, p).replace(/\\/g, "/"), src: readFileSync(p, "utf8") }))

/** 여는 태그의 끝(">") — 속성 안 { } · 따옴표를 건너뛴다. */
function endOfOpenTag(s: string, i: number): number {
  let depth = 0
  let quote: string | null = null
  for (let j = i; j < s.length; j++) {
    const c = s[j]!
    if (quote) {
      if (c === quote && s[j - 1] !== "\\") quote = null
    } else if (c === '"' || c === "'" || c === "`") quote = c
    else if (c === "{") depth++
    else if (c === "}") depth--
    else if (c === ">" && depth === 0) return j
  }
  return -1
}

const lineOf = (src: string, i: number) => src.slice(0, i).split("\n").length

/** Tooltip 을 직접 짜도 되는 곳 — 부품 자체, 내용이 여러 줄인 사람 · 출처 · 긴 글 표시 */
const TOOLTIP_DIRECT = new Set([
  "components/tip.tsx",
  "components/long-text.tsx",
  "components/dashboard-header.tsx",
  "components/data-table/bulk-actions.tsx",
  "features/help/help-button.tsx",
  "features/apis/components/api-owner.tsx",
  "features/ai-agents/components/ai-agents-detail.tsx",
  "features/mcp-servers/components/mcp-badges.tsx",
  "features/datasets/components/dataset-metadata-summary.tsx",
  "features/datasets/components/datasets-semantic-search.tsx",
  "features/datasets/components/quality-tab.tsx",
  "features/quality/components/quality-category-select.tsx",
  "app/dashboard/ai-ready/page.tsx",
])

/** 본문이 아이콘뿐인지 — 글자(문자열 · {label})가 있으면 아니다 */
function iconOnly(body: string): boolean {
  const noTags = body.replace(/<[^>]*>/g, "")
  if (/["'`]/.test(noTags) || /[가-힣]/.test(noTags) || /sr-only/.test(body)) return false
  if (!/<[A-Z]\w*/.test(body)) return false
  const txt = body.replace(/<[A-Za-z][^>]*\/>|<[A-Za-z][^>]*>|<\/[^>]*>/g, "")
  if (txt.replace(/\{[^{}]*\}/g, "").replace(/\{[^{}]*\}/g, "").trim()) return false
  // {label} · {t.name} · {nameOf(g)} 처럼 값을 그리는 식이면 글자 단추
  return ![...txt.matchAll(/\{([^{}]*)\}/g)].some((m) => /^[\w.?![\]]+(\(.*\))?$/.test(m[1]!.trim()))
}

describe("툴팁 (TODO-120)", () => {
  it("TooltipProvider 는 루트 레이아웃 하나", () => {
    const bad = files.filter((f) => f.rel !== "app/layout.tsx" && /<TooltipProvider\b/.test(f.src)).map((f) => f.rel)
    expect(bad).toEqual([])
  })

  it("화면에서 Tooltip 을 직접 짜지 않는다 — Tip · InfoTip · DisabledReason", () => {
    const bad = files.filter((f) => !TOOLTIP_DIRECT.has(f.rel) && /<TooltipTrigger\b/.test(f.src)).map((f) => f.rel)
    expect(bad).toEqual([])
  })

  it("아이콘만 있는 단추는 Tip 으로 감싼다", () => {
    const bad: string[] = []
    for (const f of files) {
      if (f.rel === "components/tip.tsx") continue
      for (const m of f.src.matchAll(/<(Button|button)\b/g)) {
        const tag = m[1]!
        const end = endOfOpenTag(f.src, m.index! + m[0].length)
        if (end < 0 || f.src[end - 1] === "/") continue
        const attrs = f.src.slice(m.index!, end + 1)
        if (/aria-hidden/.test(attrs)) continue
        const close = f.src.indexOf(`</${tag}>`, end)
        const body = f.src.slice(end + 1, close)
        if (close < 0 || body.length > 400 || body.includes(`<${tag}`) || !iconOnly(body)) continue
        const pre = f.src.slice(Math.max(0, m.index! - 400), m.index!)
        const open = Math.max(pre.lastIndexOf("<Tip"), pre.lastIndexOf("<TooltipTrigger"), pre.lastIndexOf("<DisabledReason"))
        const closed = Math.max(pre.lastIndexOf("</Tip>"), pre.lastIndexOf("</TooltipTrigger>"), pre.lastIndexOf("</DisabledReason>"))
        if (open >= 0 && open > closed) continue
        bad.push(`${f.rel}:${lineOf(f.src, m.index!)}`)
      }
    }
    expect(bad).toEqual([])
  })

  it("비활성 Button 에 조건부 title 로 사유를 달지 않는다 — DisabledReason (DEF-118)", () => {
    const bad: string[] = []
    for (const f of files) {
      for (const m of f.src.matchAll(/<Button\b/g)) {
        const end = endOfOpenTag(f.src, m.index! + m[0].length)
        if (end < 0) continue
        const attrs = f.src.slice(m.index!, end + 1)
        if (/\sdisabled[=\s>]/.test(attrs) && /\stitle=\{[^}]*\?/.test(attrs)) bad.push(`${f.rel}:${lineOf(f.src, m.index!)}`)
      }
    }
    expect(bad).toEqual([])
  })
})
