import { describe, expect, it } from "vitest"

import { htmlToPlain, richTextToPlain } from "./plain-text"
import { datasetExcerpt } from "@/features/search/excerpt"

describe("htmlToPlain", () => {
  it("블록 태그 경계는 공백, 글자 사이 태그는 붙인다", () => {
    expect(htmlToPlain("<h3>개요</h3><p>영화 <strong>상품</strong> 마<em>스</em>터</p><ul><li>하나</li><li>둘</li></ul>")).toBe(
      "개요 영화 상품 마스터 하나 둘",
    )
    expect(htmlToPlain("첫 줄<br>둘째 줄<br/>셋째 줄")).toBe("첫 줄 둘째 줄 셋째 줄")
  })

  it("중첩 태그 · 속성이 있는 태그", () => {
    const html =
      '<table><tbody><tr><th><p>컬럼</p></th><th><p>설명</p></th></tr><tr><td><p><code>id</code></p></td><td><p>키</p></td></tr></tbody></table>' +
      '<ul data-type="taskList"><li data-checked="true"><label><input type="checkbox" checked><span></span></label><div><p>확인</p></div></li></ul>' +
      '<p style="text-align: center"><a href="http://x" target="_blank"><span style="color: red">링크</span></a></p>'
    expect(htmlToPlain(html)).toBe("컬럼 설명 id 키 확인 링크")
  })

  it("엔티티를 푼다 — 이름 · 십진 · 십육진, 모르는 이름은 그대로", () => {
    expect(htmlToPlain("<p>a &amp; b &lt;c&gt;&nbsp;&quot;d&quot; &#39;e&#x27; &hellip; &unknown;</p>")).toBe(
      "a & b <c> \"d\" 'e' … &unknown;",
    )
    // 엔티티로 적힌 태그는 글자다 — 풀린 뒤 다시 지우지 않는다
    expect(htmlToPlain("<p>&lt;script&gt;x&lt;/script&gt; &amp;lt;</p>")).toBe("<script>x</script> &lt;")
  })

  it("script · style 은 내용까지 버린다", () => {
    expect(htmlToPlain("<p>앞</p><script>alert(1)</script><style>body{display:none}</style><p>뒤</p>")).toBe("앞 뒤")
    expect(htmlToPlain("<p>앞</p><SCRIPT type='x'>alert(1)")).toBe("앞")
    expect(htmlToPlain("<p>앞</p><!-- 주석 --><p>뒤</p>")).toBe("앞 뒤")
  })

  it("빈 값 · 평문", () => {
    expect(htmlToPlain(null)).toBe("")
    expect(htmlToPlain(undefined)).toBe("")
    expect(htmlToPlain("")).toBe("")
    expect(htmlToPlain("<p></p>")).toBe("")
    expect(htmlToPlain("그냥  평문\n두 줄")).toBe("그냥 평문 두 줄")
  })

  it("dropHeadings — 제목은 내용까지 뺀다", () => {
    expect(htmlToPlain("<h2>개요</h2><p>본문</p>", { dropHeadings: true })).toBe("본문")
  })
})

describe("richTextToPlain", () => {
  it("편집기 HTML 설명 — 제목을 빼고 본문만", () => {
    const html = "<h3>개요</h3><p>DVD 대여점이 보유한 <strong>영화 상품 마스터</strong>. <code>film_id</code> 기준.</p><h3>주요 컬럼</h3><ul><li><p><code>title</code> — 제목</p></li></ul>"
    expect(richTextToPlain(html)).toBe("DVD 대여점이 보유한 영화 상품 마스터. film_id 기준. title — 제목")
  })

  it("편집기 HTML 의 글자는 마크다운으로 다시 해석하지 않는다", () => {
    expect(richTextToPlain("<p>a * b * c 와 1 | 2, # 번호</p>")).toBe("a * b * c 와 1 | 2, # 번호")
  })

  it("마크다운 설명 — 편집기 HTML 과 같은 결과", () => {
    const md = "## 개요\nDVD 대여점이 보유한 **영화 상품 마스터**. `film_id` 기준.\n\n## 주요 컬럼\n- `title` — 제목"
    expect(richTextToPlain(md)).toBe("DVD 대여점이 보유한 영화 상품 마스터. film_id 기준. title — 제목")
  })

  it("마크다운에 섞인 HTML 태그 · 엔티티", () => {
    expect(richTextToPlain("**주문** 테이블<br>일 단위 <b>적재</b> &amp; 보관<script>alert(1)</script>")).toBe("주문 테이블 일 단위 적재 & 보관")
    expect(richTextToPlain("본문\n\n<h2>제목</h2>\n\n<p>둘째</p>")).toBe("본문 둘째")
  })

  it("평문의 꺾쇠는 태그가 아니면 남긴다", () => {
    expect(richTextToPlain("a < b 인 행만, b > 0")).toBe("a < b 인 행만, b > 0")
    expect(richTextToPlain("<profile> 테이블, Map<String, Long> 컬럼")).toBe("<profile> 테이블, Map<String, Long> 컬럼")
    expect(richTextToPlain("rental_rate 와 rental_duration")).toBe("rental_rate 와 rental_duration")
  })

  it("제목뿐인 글은 제목을 보여 준다", () => {
    expect(richTextToPlain("<h3>개요</h3>")).toBe("개요")
    expect(richTextToPlain("## 개요")).toBe("개요")
  })

  it("빈 값", () => {
    expect(richTextToPlain(null)).toBe("")
    expect(richTextToPlain(undefined)).toBe("")
    expect(richTextToPlain("")).toBe("")
    expect(richTextToPlain("<p></p>")).toBe("")
    expect(richTextToPlain("   ")).toBe("")
  })
})

describe("datasetExcerpt (DEF-192)", () => {
  it("요약이 없으면 HTML 설명도 평문으로", () => {
    expect(datasetExcerpt({ summary: null, description: "<h3>개요</h3><p>주문 &amp; 결제 <em>내역</em></p>" })).toBe("주문 & 결제 내역")
    expect(datasetExcerpt({ summary: "요약", description: "<p>설명</p>" })).toBe("요약")
  })
})
