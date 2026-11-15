/**
 * 알림·확인창 일관성 정적 가드.
 * - `sonner` 직접 import 는 lib/notify.ts·components/notify-toaster.tsx 만 허용
 * - 브라우저 기본 alert()/confirm()/prompt() 금지 (toast / useConfirm / usePrompt 사용)
 * - 직접 만든 고정 위치 toast(portal + fixed bottom) 금지
 * - 오류 원문(err.message) 직접 표시 금지 — errorMessage()/notifyError() 로 네트워크 오류를 한국어로
 * - catch 한 오류를 사유만으로 띄우지 않는다 — notifyError(err, "<대상>을(를) <동작>하지 못했습니다.")
 * - 클립보드는 lib/clipboard.ts 만 직접 쓴다 — http 주소에서도 복사되도록
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

  it("catch 한 오류를 사유만으로 toast 하지 않는다 — notifyError(err, \"…하지 못했습니다.\") 로 무엇이 실패했는지 함께", () => {
    // toast.error(errorMessage(e) ?? …), toast.error(errMsg(e)), toast.error(String(e)), toast.error(err.message), toast.error(message)
    const re = /toast\.(error|warning)\(\s*(errorMessage\(|errMsg\(|String\(|\w+\.message\b|message\s*[,)])/
    const bad: string[] = []
    for (const f of files) {
      if (f.rel === "lib/notify.ts") continue
      f.src.split("\n").forEach((line, i) => {
        if (/^\s*(\*|\/\*|\/\/)/.test(line)) return
        if (re.test(line)) bad.push(`${f.rel}:${i + 1}`)
      })
    }
    expect(bad).toEqual([])
  })

  it("toast 문구는 문장형 — \"○○ 실패\"·\"○○ 완료\" 같은 명사형을 쓰지 않는다", () => {
    const re = /(toast\.\w+|notifySuccess|notifyError\([^,]+,)\s*\(?\s*[`"][^`"]*(실패|완료|성공)(\s*[—:-]|[`"])/
    const bad: string[] = []
    for (const f of files) {
      f.src.split("\n").forEach((line, i) => {
        if (/^\s*(\*|\/\*|\/\/)/.test(line)) return
        if (re.test(line)) bad.push(`${f.rel}:${i + 1}`)
      })
    }
    expect(bad).toEqual([])
  })

  it("navigator.clipboard 는 lib/clipboard.ts 만 쓴다 — 보안 컨텍스트가 아닌 http 주소 대비", () => {
    const bad = files.filter((f) => f.rel !== "lib/clipboard.ts" && /navigator\.clipboard/.test(f.src)).map((f) => f.rel)
    expect(bad).toEqual([])
  })
})
