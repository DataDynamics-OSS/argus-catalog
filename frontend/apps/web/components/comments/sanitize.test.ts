// @vitest-environment jsdom
import { describe, expect, it } from "vitest"

import { descriptionToHtml } from "@/features/datasets/description"

import { sanitizeRichHtml } from "./sanitize"

/** 정화 결과를 DOM 으로 다시 읽는다 — 문자열 비교가 아니라 실제로 남은 요소 · 속성을 본다. */
function parse(html: string): HTMLElement {
  const root = document.createElement("div")
  root.innerHTML = sanitizeRichHtml(html)
  return root
}

/** 실행 가능한 형태가 하나도 없음을 단언한다: 실행 · 삽입 요소, ``on*`` 속성, 스크립트 URL. */
function expectInert(root: HTMLElement) {
  expect(root.querySelector("script, style, iframe, form, object, embed, link, meta, base")).toBeNull()
  for (const el of root.querySelectorAll("*")) {
    for (const attr of el.attributes) {
      expect(attr.name.toLowerCase().startsWith("on"), `${el.tagName} ${attr.name}`).toBe(false)
      if (["href", "src", "xlink:href", "action", "formaction", "srcdoc"].includes(attr.name.toLowerCase())) {
        expect(attr.value.replace(/\s/g, "").toLowerCase()).not.toMatch(/^(javascript|vbscript):/)
        expect(attr.name.toLowerCase()).not.toBe("srcdoc")
      }
    }
  }
}

describe("sanitizeRichHtml — 악성 입력", () => {
  it("<script> 는 내용까지 지운다", () => {
    const root = parse("<p>앞</p><script>alert(1)</script><p>뒤</p>")
    expectInert(root)
    expect(root.innerHTML).not.toContain("alert(1)")
    expect(root.textContent).toBe("앞뒤")
  })

  it("<img onerror> 는 핸들러만 지우고 이미지는 남긴다", () => {
    const root = parse("<img src=x onerror=alert(1)>")
    expectInert(root)
    const img = root.querySelector("img")
    expect(img).not.toBeNull()
    expect(img?.hasAttribute("onerror")).toBe(false)
    expect(root.innerHTML).not.toContain("alert")
  })

  it("마크다운 javascript: 링크 — descriptionToHtml 변환 뒤 정화하면 href 가 없다", () => {
    const html = descriptionToHtml("[x](javascript:alert(1))")
    const root = parse(html)
    expectInert(root)
    expect(root.innerHTML).not.toMatch(/javascript:/i)
    expect(root.textContent).toContain("x")
  })

  it("마크다운에 섞은 HTML 도 변환 뒤 정화된다", () => {
    const root = parse(descriptionToHtml("## 제목\n\n<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n본문"))
    expectInert(root)
    expect(root.innerHTML).not.toContain("alert")
    expect(root.querySelector("h2")?.textContent).toBe("제목")
  })

  it('<a href="javascript:…"> 는 href 를 지운다 — 대소문자 · 공백 · 엔티티로 숨겨도', () => {
    for (const href of ["javascript:alert(1)", "JaVaScRiPt:alert(1)", " javascript:alert(1)", "java\tscript:alert(1)", "&#106;avascript:alert(1)", "vbscript:msgbox(1)"]) {
      const root = parse(`<a href="${href}">링크</a>`)
      expectInert(root)
      expect(root.querySelector("a")?.hasAttribute("href"), href).toBe(false)
      expect(root.textContent).toBe("링크")
    }
  })

  it("<svg onload> · <iframe> 은 남지 않는다", () => {
    const svg = parse('<svg onload="alert(1)"><circle r="1"></circle></svg><p>본문</p>')
    expectInert(svg)
    expect(svg.innerHTML).not.toContain("alert")

    const iframe = parse('<iframe src="https://evil.example/"></iframe><iframe srcdoc="<script>alert(1)</script>"></iframe><p>본문</p>')
    expectInert(iframe)
    expect(iframe.querySelector("iframe")).toBeNull()
    expect(iframe.textContent).toBe("본문")
  })

  it("<style> 은 내용까지 지운다", () => {
    // 맨 앞의 ``<style>`` 은 DOMPurify 기본 규칙으로도 빠진다 — 본문 뒤 · 안쪽에 둔 것까지 본다(FORBID_TAGS)
    for (const html of ["<style>body{display:none}</style><p>본문</p>", "<p>본문</p><style>body{display:none}</style>", "<div><p>본문</p><style>body{display:none}</style></div>"]) {
      const root = parse(html)
      expectInert(root)
      expect(root.innerHTML, html).not.toContain("display:none")
      expect(root.textContent).toBe("본문")
    }
  })

  it("<form> 과 그 제출 경로는 남지 않는다", () => {
    const root = parse('<form action="https://evil.example/"><input name="pw"><button formaction="javascript:alert(1)">전송</button></form><p>본문</p>')
    expectInert(root)
    expect(root.querySelector("form")).toBeNull()
    expect(root.innerHTML).not.toContain("evil.example")
    expect(root.textContent).toContain("본문")
  })

  it("빈 값", () => {
    expect(sanitizeRichHtml(null)).toBe("")
    expect(sanitizeRichHtml(undefined)).toBe("")
    expect(sanitizeRichHtml("")).toBe("")
  })
})

describe("sanitizeRichHtml — 정상 서식은 남는다", () => {
  it("제목 · 굵게 · 기울임 · 목록 · 인용", () => {
    const root = parse("<h2>개요</h2><p><strong>굵게</strong> <em>기울임</em> <u>밑줄</u> <s>취소</s></p><ul><li><p>하나</p></li></ul><ol><li><p>둘</p></li></ol><blockquote><p>인용</p></blockquote><hr>")
    expect(root.querySelector("h2")?.textContent).toBe("개요")
    expect(root.querySelector("strong")?.textContent).toBe("굵게")
    expect(root.querySelector("em")?.textContent).toBe("기울임")
    expect(root.querySelector("u")).not.toBeNull()
    expect(root.querySelector("s")).not.toBeNull()
    expect(root.querySelector("ul > li")?.textContent).toBe("하나")
    expect(root.querySelector("ol > li")?.textContent).toBe("둘")
    expect(root.querySelector("blockquote")?.textContent).toBe("인용")
    expect(root.querySelector("hr")).not.toBeNull()
  })

  it("링크 — http(s) · mailto · 상대 경로의 href 와 속성", () => {
    const root = parse('<p><a href="https://example.com/a?b=1" target="_blank" rel="noopener noreferrer nofollow">밖</a><a href="/dashboard/datasets/1">안</a><a href="mailto:a@example.com">메일</a></p>')
    const [out, inner, mail] = [...root.querySelectorAll("a")]
    expect(out?.getAttribute("href")).toBe("https://example.com/a?b=1")
    expect(out?.getAttribute("rel")).toBe("noopener noreferrer nofollow")
    expect(inner?.getAttribute("href")).toBe("/dashboard/datasets/1")
    expect(mail?.getAttribute("href")).toBe("mailto:a@example.com")
  })

  it("코드 — 인라인과 언어가 붙은 코드 블록", () => {
    const root = parse('<p><code>film_id</code></p><pre><code class="language-sql">SELECT 1 &lt; 2</code></pre>')
    expect(root.querySelector("p > code")?.textContent).toBe("film_id")
    const block = root.querySelector("pre > code")
    expect(block?.className).toBe("language-sql")
    expect(block?.textContent).toBe("SELECT 1 < 2")
  })

  it("체크리스트 — TipTap taskList 의 data-* 와 체크박스", () => {
    const root = parse('<ul data-type="taskList"><li data-checked="true" data-type="taskItem"><label><input type="checkbox" checked="checked"><span></span></label><div><p>확인</p></div></li></ul>')
    expect(root.querySelector('ul[data-type="taskList"] > li[data-type="taskItem"]')?.getAttribute("data-checked")).toBe("true")
    const box = root.querySelector<HTMLInputElement>('input[type="checkbox"]')
    expect(box).not.toBeNull()
    expect(box?.checked).toBe(true)
    expect(root.querySelector("li div p")?.textContent).toBe("확인")
  })

  it("표 — 병합 속성과 칸 내용", () => {
    const root = parse('<table style="min-width: 50px"><colgroup><col><col></colgroup><tbody><tr><th colspan="2" rowspan="1"><p>컬럼</p></th></tr><tr><td><p>id</p></td><td><p>키</p></td></tr></tbody></table>')
    expect(root.querySelector("table")).not.toBeNull()
    expect(root.querySelector("th")?.getAttribute("colspan")).toBe("2")
    expect([...root.querySelectorAll("td")].map((td) => td.textContent)).toEqual(["id", "키"])
  })

  it("정렬 · 색(style 속성)과 data: 이미지", () => {
    const root = parse('<p style="text-align: center"><span style="color: rgb(255, 0, 0)">빨강</span></p><img src="data:image/png;base64,iVBORw0KGgo=" alt="그림" width="120">')
    expect(root.querySelector("p")?.getAttribute("style")).toContain("text-align: center")
    expect(root.querySelector("span")?.getAttribute("style")).toContain("color")
    const img = root.querySelector("img")
    expect(img?.getAttribute("src")).toBe("data:image/png;base64,iVBORw0KGgo=")
    expect(img?.getAttribute("alt")).toBe("그림")
  })

  it("마크다운 설명을 변환한 결과 — 제목 · 굵게 · 목록 · 링크 · 코드 · 체크리스트 · 표", () => {
    const md = "## 개요\n**굵게** `코드` [문서](https://example.com)\n\n- 하나\n- [x] 완료\n\n```sql\nSELECT 1\n```\n\n| 컬럼 | 설명 |\n|---|---|\n| id | 키 |"
    const root = parse(descriptionToHtml(md))
    expect(root.querySelector("h2")?.textContent).toBe("개요")
    expect(root.querySelector("strong")?.textContent).toBe("굵게")
    expect(root.querySelector("p code")?.textContent).toBe("코드")
    expect(root.querySelector("a")?.getAttribute("href")).toBe("https://example.com")
    expect(root.querySelectorAll("li").length).toBe(2)
    expect(root.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked).toBe(true)
    expect(root.querySelector("pre > code")?.textContent).toContain("SELECT 1")
    expect([...root.querySelectorAll("td")].map((td) => td.textContent)).toEqual(["id", "키"])
  })
})
