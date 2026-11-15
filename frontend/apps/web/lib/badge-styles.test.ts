/**
 * 배지 색 · 크기 정적 가드 (TODO-107 6단계).
 *
 * 규칙(전체는 packages/ui/src/components/status-badge.tsx)
 * - 상태는 `StatusBadge` 의 tone(neutral · info · success · warning · danger · muted)으로만 색을 정한다.
 *   `StatusBadge` 의 className 은 여백 · 글꼴 같은 배치만 — 색(bg · text · border)과 크기(h · text-xs · text-[Npx])는
 *   tone · size 로 바꾼다.
 * - 배지에 진한 단색 배경(bg-red-600 같은 500 이상)과 text-white 를 쓰지 않는다.
 * - `Badge` 에 팔레트 색(bg-amber-100 · text-purple-800 …)을 직접 칠하는 곳은 아래 PALETTE_ALLOWED 뿐
 *   (리니지 DB 종류 · 관계 종류처럼 범주를 구별하는 색). 상태 · 위험도 · 결과는 tone 으로.
 *   MCP 필드 출처 배지(`features/mcp-servers/components/mcp-badges.tsx` `ORIGIN_STYLE` — Argus · MCP · Registry · 설정)도
 *   범주 구별 색이라 맵으로 칠한다.
 * - 크기는 `Badge` · `StatusBadge` 의 size(기본 · sm)로만 — className 에 글자 크기(text-xs · text-sm · text-[10px])나
 *   배지 높이(h-4 · h-5)를 넣지 않는다(TODO-109). 필터 칩처럼 단추를 품은 높은 칩(h-8)은 배지가 아니라 예외.
 * - 상태 · 심각도 · 판정 · 우선순위 · 통과 여부 값은 `Badge` 에 그대로 넣지 않고 `StatusBadge`(톤 맵)로 — 예전엔 `WARNING` 같은 코드가
 *   무채색으로 보였다(DEF-116). 같은 이유로 `Badge variant="destructive"` 도 쓰지 않는다(빨강은 `StatusBadge tone="danger"`).
 * - 손으로 만든 상태 알약(<span className="rounded-full px-2 bg-green-100 …">)을 만들지 않는다.
 * - 없는 토큰 `text-destructive-foreground` 를 쓰지 않는다(DEF-113).
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

type Tag = { tag: string; at: string; attrs: string }

function openTags(names: string[]): Tag[] {
  const out: Tag[] = []
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

const PALETTE =
  "(?:red|green|emerald|amber|yellow|orange|blue|sky|violet|purple|rose|teal|cyan|indigo|pink|fuchsia|lime|zinc|slate|gray|neutral|stone)"
const PALETTE_COLOR = new RegExp(`(?<![\\w-])(?:dark:)?(?:bg|text|border)-${PALETTE}-\\d{2,3}\\b`)
/** 다크 모드의 어두운 배경(dark:bg-amber-900)은 단색 배지가 아니다 */
const SOLID_BG = new RegExp(`(?<![\\w:-])bg-${PALETTE}-[5-9]00\\b`)
const TEXT_WHITE = /(?<![\w-])text-white\b/
/** StatusBadge 의 색 덮어쓰기 — 토큰(success · warning …) 포함 */
const ANY_COLOR = /(?<![\w-])(?:bg|text|border)-(?!(?:xs|sm|base|lg|left|right|center|\[\d+px\])\b)[a-z]+(?:-\d{2,3})?\b/
const BADGE_SIZE = /(?<![\w:-])(?:h-[3-6]|text-(?:xs|sm|base|lg)|text-\[\d+px\])(?![\w-])/
const SIZE = /(?<![\w-])(?:h-\d|text-(?:xs|sm|base)|text-\[\d+px\])(?![\w-])/

/** 범주 구별 색을 그대로 둔 곳(TODO-107 "그대로 둔 것") */
const PALETTE_ALLOWED = new Set(["features/datasets/components/lineage-tab.tsx", "features/datasets/components/relationships-tab.tsx"])

/** 손 알약 예외 — 헤더 알림 종의 개수 점(겹쳐 그리는 숫자, 배지가 아님) */
const SPAN_ALLOWED = new Set(["components/dashboard-header.tsx"])

const badges = openTags(["Badge", "StatusBadge", "ToneBadge"])
const statusBadges = badges.filter((b) => b.tag !== "Badge")

describe("배지 색 · 크기 (TODO-107)", () => {
  it("배지 태그를 찾는다(검사 자체가 동작)", () => {
    expect(badges.length).toBeGreaterThan(100)
    expect(statusBadges.length).toBeGreaterThan(50)
  })

  it("진한 단색 배경 · 흰 글자 배지가 없다", () => {
    const bad = badges.filter((b) => SOLID_BG.test(b.attrs) || TEXT_WHITE.test(b.attrs)).map((b) => b.at)
    expect(bad).toEqual([])
  })

  it("Badge 에 팔레트 색을 직접 칠하지 않는다 — 상태는 StatusBadge tone 으로", () => {
    const bad = badges
      .filter((b) => b.tag === "Badge" && PALETTE_COLOR.test(b.attrs))
      .filter((b) => !PALETTE_ALLOWED.has(b.at.split(":")[0]!))
      .map((b) => b.at)
    expect(bad).toEqual([])
  })

  it("StatusBadge className 으로 색 · 크기를 바꾸지 않는다 — tone · size 를 쓴다", () => {
    const bad = statusBadges
      .map((b) => ({ at: b.at, cls: /className=(?:"([^"]*)"|\{`([^`]*)`\})/.exec(b.attrs) }))
      .filter((b) => b.cls && (ANY_COLOR.test(b.cls[1] ?? b.cls[2]!) || SIZE.test(b.cls[1] ?? b.cls[2]!)))
      .map((b) => b.at)
    expect(bad).toEqual([])
  })

  it("배지 크기는 size 로만 — className 에 글자 크기 · 배지 높이를 넣지 않는다 (TODO-109)", () => {
    const bad = badges
      .map((b) => ({ at: b.at, cls: /className=(?:"([^"]*)"|\{`([^`]*)`\}|\{cn\(([^)]*)\)\})/.exec(b.attrs) }))
      .filter((b) => b.cls && BADGE_SIZE.test(b.cls[1] ?? b.cls[2] ?? b.cls[3]!))
      .map((b) => b.at)
    expect(bad).toEqual([])
  })

  it("상태성 값은 StatusBadge 로 — Badge 에 status · severity · decision · priority · passed 를 넣지 않는다 (DEF-116)", () => {
    const bad: string[] = []
    for (const f of files) {
      for (const m of f.src.matchAll(/<Badge\b/g)) {
        const end = endOfOpenTag(f.src, m.index! + m[0].length)
        if (end < 0 || f.src[end - 1] === "/") continue
        const close = f.src.indexOf("</Badge>", end)
        const body = f.src.slice(end + 1, close)
        if (/\{[^}]*[._](?:status|severity|decision|priority|passed)\b[^}]*\}/.test(body)) bad.push(`${f.rel}:${f.src.slice(0, m.index!).split("\n").length}`)
      }
    }
    expect(bad).toEqual([])
  })

  it("Badge variant=\"destructive\" 대신 StatusBadge tone=\"danger\" (DEF-116)", () => {
    const bad = badges.filter((b) => b.tag === "Badge" && /variant=(?:"destructive"|\{[^}]*"destructive")/.test(b.attrs)).map((b) => b.at)
    expect(bad).toEqual([])
  })

  it("손으로 만든 상태 알약(span + rounded + 팔레트 배경)이 없다", () => {
    const bad = openTags(["span"])
      .filter((t) => /\brounded(?:-full|-md|-sm)?\b/.test(t.attrs) && /(?<![\w-])px-[\d.]+/.test(t.attrs))
      .filter((t) => new RegExp(`(?<![\\w:-])bg-${PALETTE}-\\d{2,3}\\b`).test(t.attrs))
      .filter((t) => !SPAN_ALLOWED.has(t.at.split(":")[0]!))
      .map((t) => t.at)
    expect(bad).toEqual([])
  })

  it("없는 토큰 text-destructive-foreground 를 쓰지 않는다 (DEF-113)", () => {
    const bad = files.filter((f) => /\btext-destructive-foreground\b/.test(f.src)).map((f) => f.rel)
    expect(bad).toEqual([])
  })
})
