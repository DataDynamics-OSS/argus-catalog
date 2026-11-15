/**
 * 글자 색 · 크기 정적 가드 (TODO-125).
 *
 * - 정보(표 셀 값 · 설명 · 날짜 · 건수 · 부가 정보)는 기본 글꼴 색. 회색(`text-muted-foreground`)은
 *   라벨 · 아이콘 · 빈 상태 · 빈 값("-")에만 — 회색이 많으면 내용에 집중이 안 된다.
 * - 입력 칸 안내 글자(placeholder)는 `text-placeholder`(예전 회색 그대로). 회색 값을 바꿔도 따라 바뀌지 않게.
 * - 가장 작은 글자는 11px(배지 sm · 차트 눈금). 본문 작은 글자는 `text-xs`(13px, globals.css 의 --text-xs).
 *   10px · 12px 를 직접 쓰지 않는다.
 */
import { readdirSync, readFileSync, statSync } from "node:fs"
import { join, relative } from "node:path"
import { describe, expect, it } from "vitest"

const ROOT = join(__dirname, "..")
const UI = join(ROOT, "../../packages/ui/src")
const SKIP_DIRS = new Set(["node_modules", ".next", "public"])

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue
    const p = join(dir, name)
    if (statSync(p).isDirectory()) sourceFiles(p, out)
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

const files = [...sourceFiles(ROOT), ...sourceFiles(UI)].map((p) => ({
  rel: relative(ROOT, p).replace(/\\/g, "/"),
  src: readFileSync(p, "utf8"),
}))

const lineOf = (s: string, i: number) => s.slice(0, i).split("\n").length

describe("글자 색 · 크기 (TODO-125)", () => {
  it("10px · 12px 를 직접 쓰지 않는다", () => {
    const bad: string[] = []
    for (const f of files) {
      for (const m of f.src.matchAll(/text-\[(10|12)px\]|fontSize:\s*(?:12\b|"12px"|'12px')/g)) {
        bad.push(`${f.rel}:${lineOf(f.src, m.index!)}`)
      }
    }
    expect(bad).toEqual([])
  })

  it("placeholder 는 text-placeholder", () => {
    const bad: string[] = []
    for (const f of files) {
      for (const m of f.src.matchAll(/placeholder\]?:text-muted-foreground/g)) {
        bad.push(`${f.rel}:${lineOf(f.src, m.index!)}`)
      }
    }
    expect(bad).toEqual([])
  })

  it("표 셀 · dd 값은 회색이 아니다(빈 상태 · 빈 값 제외)", () => {
    const bad: string[] = []
    for (const f of files) {
      for (const m of f.src.matchAll(/<(td|TableCell|dd)\b([^>]*)>([^<]*)/g)) {
        const [, , attrs, text] = m
        if (!/(?<![\w:/\]-])text-muted-foreground(?![\w/-])/.test(attrs!)) continue
        if (/colSpan/.test(attrs!)) continue // 표 전체를 덮는 빈 상태 · 로딩 줄
        if (/^\s*([-—–]|미지정|없음)?\s*$/.test(text!)) continue
        bad.push(`${f.rel}:${lineOf(f.src, m.index!)}`)
      }
    }
    expect(bad).toEqual([])
  })

  it("공용 설명 부품은 기본 글꼴 색", () => {
    const desc = /function (CardDescription|DialogDescription|SheetDescription|AlertDialogDescription|FormDescription|AlertDescription|TableCaption)\b[\s\S]*?\n}/g
    const bad: string[] = []
    for (const f of files.filter((x) => x.rel.includes("packages/ui/src/components/"))) {
      for (const m of f.src.matchAll(desc)) {
        if (/text-muted-foreground/.test(m[0])) bad.push(`${f.rel}:${m[1]}`)
      }
    }
    expect(bad).toEqual([])
  })
})
