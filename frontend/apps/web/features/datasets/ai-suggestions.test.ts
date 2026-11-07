import { describe, expect, it } from "vitest"

import {
  formatCurrentValue,
  formatSuggestedValue,
  parseTagSuggestion,
  suggestionKindLabel,
  suggestionSource,
  suggestionTarget,
} from "./ai-suggestions"

const base = { entity_type: "dataset", field_name: null, generation_type: "description", generated_text: "새 설명" }

describe("suggestionKindLabel / suggestionTarget", () => {
  it("유형과 대상을 구분한다", () => {
    expect(suggestionKindLabel(base)).toBe("설명")
    expect(suggestionKindLabel({ ...base, generation_type: "summary" })).toBe("요약")
    const col = { ...base, entity_type: "column", field_name: "email" }
    expect(suggestionKindLabel(col)).toBe("컬럼 설명")
    expect(suggestionTarget(col)).toBe("email")
    expect(suggestionTarget(base)).toBe("데이터셋")
    expect(suggestionKindLabel({ ...base, entity_type: "tag", generation_type: "tag_suggestion" })).toBe("태그")
    expect(suggestionKindLabel({ ...base, entity_type: "pii", generation_type: "pii_detection" })).toBe("PII")
  })
})

describe("태그 제안", () => {
  it("서버 형식({tags, new_tags})과 AI 서비스 형식(이름 배열)을 모두 읽는다", () => {
    expect(parseTagSuggestion('{"tags":["sales"],"new_tags":[{"name":"finance","description":"x"}]}'))
      .toEqual({ tags: ["sales"], newTags: ["finance"] })
    expect(parseTagSuggestion('["sales","finance"]')).toEqual({ tags: ["sales", "finance"], newTags: [] })
    expect(parseTagSuggestion("깨진 값")).toEqual({ tags: [], newTags: [] })
  })

  it("제안값과 현재 값을 글로 보여 준다", () => {
    const s = {
      ...base, generation_type: "tag_suggestion",
      generated_text: '{"tags":["sales"],"new_tags":[{"name":"finance"}]}', current_text: '["sales"]',
    }
    expect(formatSuggestedValue(s)).toBe("sales, finance (새 태그)")
    expect(formatCurrentValue(s)).toBe("sales")
    expect(formatCurrentValue({ ...s, current_text: "[]" })).toBe("(없음)")
  })
})

describe("PII 제안", () => {
  it("컬럼별 유형을 제안값·현재 값으로 보여 준다", () => {
    const s = {
      ...base, generation_type: "pii_detection",
      generated_text: '[{"name":"email","pii_type":"EMAIL","reason":"r"}]',
      current_text: '[{"name":"email","pii_type":null}]',
    }
    expect(formatSuggestedValue(s)).toBe("email: EMAIL")
    expect(formatCurrentValue(s)).toBe("email: -")
  })
})

describe("현재 값 · 출처", () => {
  it("현재 값이 비면 (없음)", () => {
    expect(formatCurrentValue({ ...base, current_text: null })).toBe("(없음)")
    expect(formatCurrentValue({ ...base, current_text: "  " })).toBe("(없음)")
    expect(formatCurrentValue({ ...base, current_text: "옛 설명" })).toBe("옛 설명")
  })

  it("AI 서비스 반입(provider=agent)과 화면 생성을 구분한다", () => {
    expect(suggestionSource({ provider: "agent", model: "qwen2.5:7b" })).toBe("AI 서비스(enrich) · qwen2.5:7b")
    expect(suggestionSource({ provider: "openai", model: "gpt" })).toBe("카탈로그 AI 생성 · openai · gpt")
  })
})
