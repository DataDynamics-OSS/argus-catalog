/**
 * Monaco 줄 번호 정적 가드 (TODO-126) — 코드를 보여 주는 곳은 모두 줄 번호를 켠다.
 *
 * 공용 `CodeEditor`(components/code-editor.tsx) · `CodeViewer`(components/code-viewer.tsx) 는 options 를 받아도
 * 줄 번호를 마지막에 "on" 으로 덮는다. 화면에서 Monaco 를 직접 쓸 때도 줄 번호를 끄지 않는다.
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
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

describe("Monaco 줄 번호 (TODO-126)", () => {
  it("줄 번호를 끄지 않는다", () => {
    const bad: string[] = []
    for (const p of sourceFiles(ROOT)) {
      const src = readFileSync(p, "utf8")
      for (const m of src.matchAll(/lineNumbers(?:\s*:(?!\s*"on")|\s*=\{(?!true\}))/g)) {
        bad.push(`${relative(ROOT, p)}:${src.slice(0, m.index).split("\n").length}`)
      }
    }
    expect(bad).toEqual([])
  })

  it("공용 부품은 options 뒤에 줄 번호를 켠다", () => {
    for (const f of ["components/code-editor.tsx", "components/code-viewer.tsx"]) {
      const src = readFileSync(join(ROOT, f), "utf8")
      expect(src, f).toMatch(/\.\.\.options,\s*\n(?:\s*\/\/[^\n]*\n)?\s*lineNumbers: "on",/)
    }
  })
})
