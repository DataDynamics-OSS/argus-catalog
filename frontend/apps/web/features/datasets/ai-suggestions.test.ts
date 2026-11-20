import { describe, expect, it } from "vitest"

import {
  draftResultMessage,
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

describe("AI-Ready 초안 (TODO-096 6단계)", () => {
  it("제안 유형 이름에 항목이 나온다", () => {
    const draft = { ...base, generation_type: "ai_ready", field_name: "data_biases" }
    expect(suggestionKindLabel(draft)).toBe("AI-Ready · 알려진 편향")
    expect(suggestionKindLabel({ ...draft, field_name: "legal_basis" })).toBe("AI-Ready · 관련법령")
    expect(suggestionTarget(draft)).toBe("데이터셋")
  })

  it("결과를 알림 문구로", () => {
    const ok = draftResultMessage({
      drafts: [{ field: "data_biases", label: "알려진 편향", text: "- x", log_id: 1 },
        { field: "legal_basis", label: "관련법령", text: "통계법", log_id: 2 }],
      skipped: [{ field: "known_limitations", label: "한계 · 유의사항", reason: "이미 값이 있습니다." }],
      legal_reason: "승인통계 원자료",
    }, "다시 쓰려면 메뉴")
    expect(ok.ok).toBe(true)
    expect(ok.title).toContain("초안 2개(알려진 편향 · 관련법령)")
    expect(ok.description).toBe("관련법령 근거: 승인통계 원자료 / 한계 · 유의사항: 이미 값이 있습니다. / 다시 쓰려면 메뉴")
    const none = draftResultMessage({
      drafts: [], skipped: [{ field: "legal_basis", label: "관련법령", reason: "목록이 없습니다" }], legal_reason: null,
    }, "다시 쓰려면 메뉴")
    expect(none).toEqual({ ok: false, title: "새로 만든 초안이 없습니다.", description: "관련법령: 목록이 없습니다" })
    const uses = draftResultMessage({
      drafts: [{ field: "allowed_uses", label: "허용 이용 유형", text: "VIEW", log_id: 3, reason: "PII 있음" }],
      skipped: [], legal_reason: null,
    })
    expect(uses.description).toBe("허용 이용 유형 근거: PII 있음")
  })

  it("허용 이용 유형은 코드 대신 이름으로", () => {
    const s = { entity_type: "dataset", generation_type: "ai_ready", field_name: "allowed_uses",
      generated_text: "VIEW,AI_TRAINING", current_text: null as string | null }
    expect(suggestionKindLabel(s)).toBe("AI-Ready · 허용 이용 유형")
    expect(formatSuggestedValue(s)).toBe("열람, AI 학습")
    expect(formatCurrentValue(s)).toBe("(없음)")
    expect(formatCurrentValue({ ...s, current_text: "" })).toBe("모두 허용하지 않음")
    expect(formatCurrentValue({ ...s, current_text: "ANALYSIS" })).toBe("분석")
    expect(suggestionKindLabel({ ...s, field_name: "privacy_measures" })).toBe("AI-Ready · 개인정보 보호조치")
  })
})
