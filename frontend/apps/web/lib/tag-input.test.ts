/**
 * 여러 값 입력 (TODO-143) — 쉼표로 이어 적는 입력 대신 배지 입력(`components/tag-input.tsx` `TagInput`).
 *
 * - 값 목록 규칙(`lib/text-list.ts`): JSON 배열 문자열로 저장해 값 안의 쉼표를 지키고, 예전 쉼표 문자열도 읽는다
 *   (백엔드 `app/core/text_list.py` 와 같은 규칙).
 * - 정적 가드: 화면 문구로 "쉼표로 구분" 입력을 새로 만들지 않는다(TODO-144 로 남은 곳까지 모두 바꿈).
 * - 서버가 쉼표 문자열로 저장하고 값에 쉼표가 들어갈 수 없는 항목(URL · 메일 · 언어 코드 · 사용자 이름)은
 *   `TagInput commaSeparates` + `splitCsv` · `joinCsv`.
 */
import { readdirSync, readFileSync, statSync } from "node:fs"
import { join, relative } from "node:path"
import { describe, expect, it } from "vitest"

import { cleanTags, decodeList, encodeList, joinCsv, splitCsv } from "@/lib/text-list"

describe("값 목록 저장 규칙 (TODO-143)", () => {
  it("쉼표 문자열 항목 — splitCsv · joinCsv (TODO-144)", () => {
    expect(splitCsv(" ko, en ,,ko")).toEqual(["ko", "en"])
    expect(splitCsv(null)).toEqual([])
    expect(joinCsv(["a@x.com", " b@y.com"])).toBe("a@x.com, b@y.com")
    expect(joinCsv(["*"], ",")).toBe("*")
  })
  it("JSON 배열은 값 안의 쉼표를 지킨다", () => {
    expect(decodeList('["1,000", " N/A ", "", "1,000"]')).toEqual(["1,000", "N/A"])
    expect(decodeList("[1, 2]")).toEqual(["1", "2"])
  })
  it("예전 쉼표 문자열 · 빈 값", () => {
    expect(decodeList("Y, N,")).toEqual(["Y", "N"])
    expect(decodeList(null)).toEqual([])
    expect(decodeList("")).toEqual([])
    expect(decodeList("[A], [B]")).toEqual(["[A]", "[B]"]) // [ 로 시작해도 JSON 이 아니면 예전 규칙
  })
  it("저장 문자열로 — 비면 null, 되읽으면 같은 목록", () => {
    expect(encodeList([])).toBeNull()
    expect(encodeList(["환불, 결제 문의", " 주소 "])).toBe('["환불, 결제 문의","주소"]')
    expect(decodeList(encodeList(["a,b", "c"]))).toEqual(["a,b", "c"])
  })
  it("cleanTags — 공백 · 빈 값 · 중복 제거, 순서 유지", () => {
    expect(cleanTags([" b", "a", "", "b"])).toEqual(["b", "a"])
  })
})

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

/** 쉼표로 이어 적는 입력 예외 — 3단계(TODO-144)로 모두 배지 입력이 되어 비어 있다. 새로 늘리지 않는다 */
const LEGACY_ALLOWED = new Set<string>([])

/** 화면 문구 속 "쉼표로 구분" 류 — 주석 줄은 보지 않는다 */
const COMMA_INPUT = /(쉼표|콤마)\s*(로)?\s*(구분|여러)|\((쉼표|콤마)[,)]|comma-separated/

describe("쉼표 구분 입력 가드 (TODO-143)", () => {
  it("새 화면은 쉼표로 이어 적게 하지 않는다 — TagInput 을 쓴다", () => {
    const bad: string[] = []
    for (const p of sourceFiles(ROOT)) {
      const rel = relative(ROOT, p).replace(/\\/g, "/")
      if (LEGACY_ALLOWED.has(rel)) continue
      readFileSync(p, "utf8").split("\n").forEach((line, i) => {
        const t = line.trim()
        if (t.startsWith("//") || t.startsWith("*") || t.startsWith("/*")) return
        if (COMMA_INPUT.test(line)) bad.push(`${rel}:${i + 1}`)
      })
    }
    expect(bad).toEqual([])
  })
})
