/**
 * 검색어 입력창 모양 정적 가드 (TODO-139).
 *
 * 규칙(전체는 components/search-input.tsx)
 * - 검색 · 찾기 · 거르기 입력창은 공용 `SearchInput`(왼쪽 돋보기 · × 지우기 · Esc · 크기 default/sm/xs)으로 만든다.
 *   `Input` · `<input>` 에 검색 플레이스홀더를 달고 돋보기를 손으로 겹쳐 그리지 않는다.
 *   예외: 사이드바 메뉴 검색(`SidebarInput` — 사이드바 색을 따르고 같은 모양으로 직접 그림),
 *   콤보박스 안 `CommandInput`(돋보기 내장).
 * - 플레이스홀더는 "○○ 검색…" — 끝은 말줄임표 "…" 하나, "..." 이나 "(Enter)" 안내는 쓰지 않는다.
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

function openTags(names: string[]): { tag: string; at: string; attrs: string }[] {
  const out: { tag: string; at: string; attrs: string }[] = []
  for (const f of files) {
    for (const m of f.src.matchAll(new RegExp(`<(${names.join("|")})\\b`, "g"))) {
      const end = endOfOpenTag(f.src, m.index! + m[0].length)
      if (end < 0) continue
      const line = f.src.slice(0, m.index!).split("\n").length
      out.push({ tag: m[1]!, at: `${f.rel}:${line}`, attrs: f.src.slice(m.index!, end + 1) })
    }
  }
  return out
}

/** placeholder 속성의 문자열 조각들(삼항식 안의 두 갈래 포함) */
function placeholders(attrs: string): string[] {
  const m = /placeholder=(?:"([^"]*)"|\{([^}]*)\})/.exec(attrs)
  if (!m) return []
  if (m[1] != null) return [m[1]]
  return [...m[2]!.matchAll(/"([^"]*)"|`([^`]*)`/g)].map((x) => x[1] ?? x[2]!)
}

const SEARCHY = /검색|찾기|좁히|거르|필터/

describe("검색어 입력창 (TODO-139)", () => {
  const searchInputs = openTags(["SearchInput"])

  it("SearchInput 을 찾는다(검사 자체가 동작)", () => {
    expect(searchInputs.length).toBeGreaterThan(25)
  })

  it("검색 입력창은 SearchInput 으로 — Input · input 에 검색 플레이스홀더를 달지 않는다", () => {
    const bad = openTags(["Input", "input"])
      .filter((t) => placeholders(t.attrs).some((p) => SEARCHY.test(p)))
      .map((t) => t.at)
    expect(bad).toEqual([])
  })

  it("검색 플레이스홀더는 말줄임표 하나로 끝나고 (Enter) 안내 · ... 을 쓰지 않는다", () => {
    const bad = [...searchInputs, ...openTags(["SidebarInput"])]
      .filter((t) => placeholders(t.attrs).some((p) => !p.endsWith("…") || p.endsWith("……") || /\.\.\.|\(Enter\)/.test(p)))
      .map((t) => t.at)
    expect(bad).toEqual([])
  })
})
