/**
 * 알림·확인창 일관성 정적 가드.
 * - `sonner` 직접 import 는 lib/notify.ts·components/notify-toaster.tsx 만 허용
 * - 브라우저 기본 alert()/confirm()/prompt() 금지 (toast / useConfirm / usePrompt 사용)
 * - 직접 만든 고정 위치 toast(portal + fixed bottom) 금지
 * - 오류 원문(err.message) 직접 표시 금지 — errorMessage()/notifyError() 로 네트워크 오류를 한국어로
 */
import { readdirSync, readFileSync, statSync } from "node:fs"
import { join, relative } from "node:path"
import { describe, expect, it } from "vitest"

const ROOT = join(__dirname, "..")
const SKIP_DIRS = new Set(["node_modules", ".next", "public"])
const SONNER_ALLOWED = new Set(["lib/notify.ts", "components/notify-toaster.tsx"])

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue
    const p = join(dir, name)
    if (statSync(p).isDirectory()) sourceFiles(p, out)
    else if (/\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

const files = sourceFiles(ROOT).map((p) => ({ rel: relative(ROOT, p).replace(/\\/g, "/"), src: readFileSync(p, "utf8") }))

describe("알림 일관성", () => {
  it("sonner 는 notify 모듈만 직접 쓴다", () => {
    const bad = files.filter((f) => !SONNER_ALLOWED.has(f.rel) && /from\s+["']sonner["']/.test(f.src)).map((f) => f.rel)
    expect(bad).toEqual([])
  })

  it("브라우저 기본 alert()/confirm()/prompt() 을 쓰지 않는다", () => {
    const re = /(^|[^.\w])(window\.)?(alert|confirm|prompt)\s*\(/
    const bad: string[] = []
    for (const f of files) {
      f.src.split("\n").forEach((line, i) => {
        // 주석(// …, /** … */ 줄)은 검사하지 않는다
        if (/^\s*(\*|\/\*)/.test(line)) return
        const code = line.replace(/\/\/.*$/, "")
        if (re.test(code)) bad.push(`${f.rel}:${i + 1}`)
      })
    }
    expect(bad).toEqual([])
  })

  it("오류 원문(err.message)을 그대로 쓰지 않고 errorMessage() 를 거친다 — 네트워크 오류 한국어 안내", () => {
    // "Failed to fetch" 같은 브라우저 원문이 화면에 나오지 않도록 lib/notify 의 errorMessage()/notifyError() 를 쓴다
    const re = /instanceof Error (\? \w+\.message|&& \w+\.message \?)|\(\w+ as Error\)\.message/
    const allowed = new Set(["lib/notify.ts", "components/local-filesystem-browser/utils.ts"]) // utils 는 TypeError 를 먼저 한국어로 바꾼다
    const bad: string[] = []
    for (const f of files) {
      if (allowed.has(f.rel)) continue
      f.src.split("\n").forEach((line, i) => {
        if (/^\s*(\*|\/\*|\/\/)/.test(line)) return
        if (re.test(line)) bad.push(`${f.rel}:${i + 1}`)
      })
    }
    expect(bad).toEqual([])
  })

  it("직접 만든 하단 고정 toast 가 없다", () => {
    const bad = files
      .filter((f) => /createPortal\(/.test(f.src) && /fixed bottom-\d+[^"]*-translate-x-1\/2/.test(f.src))
      .map((f) => f.rel)
    expect(bad).toEqual([])
  })
})
