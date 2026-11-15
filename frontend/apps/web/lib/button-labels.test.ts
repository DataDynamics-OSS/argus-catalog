/**
 * 버튼 라벨 · 아이콘 일관성 정적 가드 (TODO-106).
 *
 * 용어
 * - 새로고침 · 미리보기 · 내려받기(다운로드 X) · 편집(수정 X) · 중지(실행 중인 작업, 대화상자 닫기는 "취소")
 * - 새 항목: 목록에 넣으면 "추가", 카탈로그에 외부 자산(API · MCP 서버 · 모델 · 에이전트)을 올리면 "등록",
 *   변경 요청 · 폴더처럼 새로 작성하면 "만들기". "생성" · "새 ○○" 은 버튼 라벨로 쓰지 않는다.
 * - 상세에서 목록으로 돌아가는 버튼은 "목록으로", 단계 대화상자의 앞 단계는 "이전"
 * - 영어 라벨(Refresh · Clear · manifest) 금지, 진행 중 표시는 "저장 중…"(말줄임표 한 글자)
 * 아이콘
 * - 동작마다 아이콘 하나(아래 ICONS). 대화상자 아래 확인 · 취소 버튼에는 아이콘을 달지 않는다.
 * - 아이콘만 있는 버튼은 aria-label(또는 sr-only · title)로 이름을 붙인다.
 * 메뉴(DropdownMenuItem · ContextMenuItem, 규칙 전체는 components/menu-item-content.tsx)
 * - 삭제 항목은 variant="destructive", 메뉴의 맨 아래 묶음.
 * - 라벨에 괄호 설명 · 화살표를 넣지 않는다 — 설명은 MenuItemContent 의 hint 로.
 * 입력란
 * - JSON · YAML · 설정 원문처럼 고정폭으로 적는 입력란은 고정폭(font-mono) Textarea 대신 CodeEditor.
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

type Btn = { tag: string; pos: number; file: string; at: string; attrs: string; body: string; text: string; icons: string[]; footer: boolean }

const MENU_TAGS = ["DropdownMenuItem", "ContextMenuItem"]
const TAGS = ["Button", ...MENU_TAGS]
const buttons: Btn[] = []
for (const f of files) {
  const footers: [number, number][] = []
  for (const m of f.src.matchAll(/<(DialogFooter|AlertDialogFooter)\b[\s\S]*?<\/\1>/g)) footers.push([m.index!, m.index! + m[0].length])
  for (const tag of TAGS) {
    for (const m of f.src.matchAll(new RegExp(`<${tag}\\b`, "g"))) {
      const start = m.index! + m[0].length
      const end = endOfOpenTag(f.src, start)
      if (end < 0 || f.src[end - 1] === "/") continue
      const close = f.src.indexOf(`</${tag}>`, end)
      const body = f.src.slice(end + 1, close)
      if (close < 0 || body.length > 700 || body.includes(`<${tag}`)) continue
      // 삼항 라벨({a ? "x" : "y"})은 두 값 모두, 나머지 { } 식과 태그는 지운다
      let text = body.replace(/\{[^{}]*?\?\s*["`]([^"`]*)["`]\s*:\s*["`]([^"`]*)["`]\s*\}/g, " $1 | $2 ")
      for (let k = 0; k < 3; k++) text = text.replace(/\{[^{}]*\}/g, " ")
      // <MenuItemContent label="삭제" … /> 의 라벨
      text = text.replace(/<MenuItemContent\b[^>]*?\blabel="([^"]*)"[^>]*>/g, " $1 ")
      text = text.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim()
      const icons = [
        ...[...body.matchAll(/<([A-Z]\w+)\s*(?:className=|\/>)/g)].map((x) => x[1]!),
        ...[...body.matchAll(/<MenuItemContent\b[^>]*?\bicon=\{(\w+)\}/g)].map((x) => x[1]!),
      ].filter((x) => x !== "Loader2" && x !== "MenuItemContent")
      const line = f.src.slice(0, m.index!).split("\n").length
      buttons.push({
        tag,
        pos: m.index!,
        file: f.rel,
        at: `${f.rel}:${line}`,
        attrs: f.src.slice(start, end),
        body,
        text,
        icons,
        footer: footers.some(([a, b]) => m.index! > a && m.index! < b),
      })
    }
  }
}

const labels = (b: Btn) => b.text.split(" | ").map((t) => t.trim())

describe("버튼 라벨 · 아이콘 일관성", () => {
  it("버튼을 충분히 찾는다(파서 점검)", () => {
    expect(buttons.length).toBeGreaterThan(500)
  })

  it("정한 용어를 쓴다 — 새로고침 · 미리보기 · 내려받기 · 편집 · 추가/등록/만들기 · 목록으로, 영어 라벨 없음", () => {
    const banned: [RegExp, string][] = [
      [/^새로 고침$/, "새로고침"],
      [/^미리 보기$/, "미리보기"],
      [/다운로드/, "내려받기"],
      [/^(.+ )?수정$/, "편집"],
      [/^생성$/, "추가 · 등록 · 만들기"], // AI "설명 생성" 같은 생성(generate)은 허용
      [/^새 \S+$/, "○○ 추가 · 만들기"],
      [/^(Refresh|Clear|manifest|Save|Cancel|Delete|Edit)$/i, "한국어"],
      [/^.+ 목록으로$|^목록$/, "목록으로"],
    ]
    const bad: string[] = []
    for (const b of buttons) {
      for (const label of labels(b)) {
        for (const [re, want] of banned) if (re.test(label)) bad.push(`${b.at} "${label}" → ${want}`)
      }
    }
    expect(bad).toEqual([])
  })

  it("같은 동작은 같은 아이콘", () => {
    const ICONS: Record<string, string[]> = {
      새로고침: ["RefreshCw"],
      삭제: ["Trash2"],
      추가: ["Plus"],
      편집: ["Pencil"],
      중지: ["Square"],
      가져오기: ["Import"],
      내려받기: ["Download"],
      복사: ["Copy"],
      보기: ["Eye"],
      미리보기: ["Eye"],
      저장: ["Save"],
    }
    const bad: string[] = []
    for (const b of buttons) {
      if (!b.icons.length) continue
      for (const label of labels(b)) {
        const want = ICONS[label]
        if (want && !b.icons.some((i) => want.includes(i))) bad.push(`${b.at} "${label}" ${b.icons.join(",")} → ${want.join("|")}`)
      }
    }
    expect(bad).toEqual([])
  })

  it("대화상자 아래 확인 · 취소 버튼에는 아이콘을 달지 않는다", () => {
    const confirmish = /^(저장|취소|확인|추가|등록|만들기|삭제|적용|닫기|이전|다음)$/
    const bad = buttons
      .filter((b) => b.footer && b.icons.length && labels(b).some((l) => confirmish.test(l)))
      .map((b) => `${b.at} "${b.text}" ${b.icons.join(",")}`)
    expect(bad).toEqual([])
  })

  it("다시 시도 · 이전 버튼은 아이콘 없이", () => {
    const bad = buttons.filter((b) => b.icons.length && labels(b).some((l) => l === "다시 시도" || l === "이전")).map((b) => b.at)
    expect(bad).toEqual([])
  })

  it("아이콘만 있는 버튼은 이름(aria-label · sr-only · title)이 있다", () => {
    const bad = buttons
      .filter((b) => !b.text && /size="icon|\bp-0\b/.test(b.attrs))
      .filter((b) => !/(^|>)\s*\{(?!\/\*)/.test(b.body)) // {p} 처럼 식으로 글자를 넣는 버튼(쪽 번호 등)은 제외
      .filter((b) => !/aria-label|title=/.test(b.attrs) && !/sr-only/.test(b.body))
      .map((b) => b.at)
    expect(bad).toEqual([])
  })

  it("진행 중 표시는 말줄임표 한 글자(…) — \"저장 중...\" 금지", () => {
    const bad: string[] = []
    for (const f of files) {
      f.src.split("\n").forEach((line, i) => {
        if (/[가-힣)]\.\.\.(?=["`<}']|\s*$)/.test(line)) bad.push(`${f.rel}:${i + 1}`)
      })
    }
    expect(bad).toEqual([])
  })

  it("메뉴의 삭제 항목은 variant=\"destructive\" 이고 맨 아래 묶음에 있다", () => {
    const menu = buttons.filter((b) => MENU_TAGS.includes(b.tag))
    const isDelete = (b: Btn) => labels(b).some((l) => /(^| )삭제$/.test(l))
    const bad: string[] = []
    for (const b of menu.filter(isDelete)) {
      if (!/variant="destructive"/.test(b.attrs)) bad.push(`${b.at} "${b.text}" variant="destructive" 없음`)
    }
    // 같은 메뉴(…MenuContent 한 덩어리) 안에서 삭제 뒤에 다른 항목이 오면 안 된다
    for (const f of files) {
      for (const m of f.src.matchAll(/<(DropdownMenuContent|ContextMenuContent)\b[\s\S]*?<\/\1>/g)) {
        const [a, z] = [m.index!, m.index! + m[0].length]
        const items = menu.filter((b) => b.file === f.rel && b.pos > a && b.pos < z).sort((x, y) => x.pos - y.pos)
        const first = items.findIndex(isDelete)
        if (first < 0) continue
        for (const b of items.slice(first + 1)) {
          if (!isDelete(b) && !/variant="destructive"/.test(b.attrs)) bad.push(`${b.at} "${b.text}" 이(가) 삭제 뒤에 있음`)
        }
      }
    }
    expect(bad).toEqual([])
  })

  it("메뉴 라벨에 괄호 설명 · 화살표를 넣지 않는다(hint 로)", () => {
    const bad = buttons
      .filter((b) => MENU_TAGS.includes(b.tag))
      .filter((b) => labels(b).some((l) => /[()（）→⇒]/.test(l)))
      .map((b) => `${b.at} "${b.text}"`)
    expect(bad).toEqual([])
  })

  it("고정폭 글꼴 입력란은 Textarea 가 아니라 CodeEditor(Monaco, text-sm)", () => {
    const bad: string[] = []
    for (const f of files) {
      for (const m of f.src.matchAll(/<(Textarea|textarea)\b/g)) {
        const end = endOfOpenTag(f.src, m.index! + m[0].length)
        if (end > 0 && /font-mono/.test(f.src.slice(m.index!, end))) bad.push(`${f.rel}:${f.src.slice(0, m.index!).split("\n").length}`)
      }
    }
    expect(bad).toEqual([])
  })
})
