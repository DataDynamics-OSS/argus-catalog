import { describe, expect, it } from "vitest"

import { descriptionToHtml, isRichHtmlDescription } from "./description"

describe("descriptionToHtml", () => {
  it("빈 값은 빈 문자열", () => {
    expect(descriptionToHtml(null)).toBe("")
    expect(descriptionToHtml(undefined)).toBe("")
    expect(descriptionToHtml("")).toBe("")
  })

  it("마크다운은 제목 · 굵게 · 목록으로 변환한다 (DEF-181)", () => {
    const html = descriptionToHtml("## 개요\nDVD 대여점의 **영화 상품 마스터**입니다.\n\n## 주요 컬럼\n- `film_id` — 식별자\n- `title` — 제목")
    expect(html).toContain("<h2>개요</h2>")
    expect(html).toContain("<strong>영화 상품 마스터</strong>")
    expect(html).toContain("<li><code>film_id</code> — 식별자</li>")
    expect(html).not.toContain("##")
    expect(html).not.toContain("**")
  })

  it("기호 없는 짧은 평문은 한 문단 그대로", () => {
    expect(descriptionToHtml("매장 주문 트랜잭션")).toBe("<p>매장 주문 트랜잭션</p>")
  })

  it("평문의 줄바꿈을 지킨다", () => {
    expect(descriptionToHtml("첫 줄\n둘째 줄")).toBe("<p>첫 줄<br>둘째 줄</p>")
    expect(descriptionToHtml("문단 1\n\n문단 2")).toBe("<p>문단 1</p>\n<p>문단 2</p>")
  })

  it("리치 편집기가 저장한 HTML 은 손대지 않는다", () => {
    const rich = '<p>주문 <strong>원장</strong></p><ul><li><p>항목 * 1</p></li></ul><pre><code class="language-sql">select *\n  from t</code></pre>'
    expect(descriptionToHtml(rich)).toBe(rich)
    expect(descriptionToHtml("<h2>개요</h2><p>본문</p>")).toBe("<h2>개요</h2><p>본문</p>")
  })
})

describe("isRichHtmlDescription", () => {
  it("블록 태그로 시작하면 HTML", () => {
    expect(isRichHtmlDescription("<p>본문</p>")).toBe(true)
    expect(isRichHtmlDescription('  <p style="text-align: center">본문</p>')).toBe(true)
    expect(isRichHtmlDescription("<hr><p>본문</p>")).toBe(true)
  })

  it("마크다운 · 평문 · 빈 값은 HTML 아님", () => {
    expect(isRichHtmlDescription("## 개요")).toBe(false)
    expect(isRichHtmlDescription("a < b 인 행만")).toBe(false)
    expect(isRichHtmlDescription("<profile> 테이블 설명")).toBe(false)
    expect(isRichHtmlDescription(null)).toBe(false)
  })
})
