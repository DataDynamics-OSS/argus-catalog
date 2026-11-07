import { describe, expect, it } from "vitest"

import { datasetExcerpt, markdownToPlain } from "./excerpt"

describe("markdownToPlain", () => {
  it("제목 줄·강조·인라인 코드 기호를 뺀다", () => {
    const md = "## 개요\nDVD 대여점이 보유한 **영화 상품 마스터**. `film_id` 기준.\n\n## 주요 컬럼\n- `title` — 제목"
    expect(markdownToPlain(md)).toBe("DVD 대여점이 보유한 영화 상품 마스터. film_id 기준. title — 제목")
  })

  it("링크는 텍스트만, 표는 칸 내용만 남긴다", () => {
    const md = "[문서](http://x) 참고\n\n| 컬럼 | 설명 |\n|---|---|\n| id | 키 |"
    expect(markdownToPlain(md)).toBe("문서 참고 컬럼 설명 id 키")
  })

  it("빈 값은 빈 문자열", () => {
    expect(markdownToPlain(null)).toBe("")
    expect(markdownToPlain("")).toBe("")
  })

  // 페더레이션 통합 검색 카드(features/federation/search-panel.tsx)도 같은 함수로 설명을 평문화한다.
  it("페더레이션 검색 카드 설명 — 제목·강조·백틱이 섞인 설명", () => {
    const md = "## 개요\n대여점 **회원(고객) 마스터**. `CONFIDENTIAL` 등급\n## 주요 컬럼\n- `email` — 마케팅 발송 키 (**PII: EMAIL**)"
    expect(markdownToPlain(md)).toBe("대여점 회원(고객) 마스터. CONFIDENTIAL 등급 email — 마케팅 발송 키 (PII: EMAIL)")
  })

  it("식별자 안의 밑줄은 그대로 둔다", () => {
    expect(markdownToPlain("rental_rate 와 rental_duration")).toBe("rental_rate 와 rental_duration")
  })
})

describe("datasetExcerpt", () => {
  it("요약이 있으면 요약을 쓴다", () => {
    expect(datasetExcerpt({ summary: "DVD 대여 트랜잭션", description: "## 개요\n긴 설명" })).toBe("DVD 대여 트랜잭션")
  })

  it("요약이 없으면 설명을 평문으로", () => {
    expect(datasetExcerpt({ summary: "  ", description: "## 개요\n**주문** 테이블" })).toBe("주문 테이블")
    expect(datasetExcerpt({})).toBe("")
  })
})
