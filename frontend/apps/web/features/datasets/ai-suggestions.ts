// AI 제안(승인 대기) 표시용 순수 함수 — 데이터셋 상세의 "AI 제안" 다이얼로그가 쓴다.
//
// 제안은 서버(backend/app/ai/service.py)의 AIGenerationLog 중 applied=false 인 행이다.
// 카탈로그 화면의 미리보기(생성 후 적용하지 않음)와 AI 서비스 enrich(provider="agent") 가 만든다.
// generated_text / current_text 형식:
//   - 설명·요약·컬럼 설명: 평문
//   - 태그: 서버 생성은 {"tags": [...], "new_tags": [{name, description}]}, enrich 는 ["a", "b"]
//     현재 값은 현재 태그 이름 배열
//   - PII: [{"name", "pii_type", ...}] — 현재 값은 같은 컬럼들의 [{"name", "pii_type"}]

import type { SuggestionItem } from "./ai-api"

type SuggestionLike = Pick<SuggestionItem, "entity_type" | "generation_type" | "field_name" | "generated_text"> & {
  current_text?: string | null
}

/** 제안 유형 이름 — 목록의 "유형" 열. */
export function suggestionKindLabel(s: SuggestionLike): string {
  if (s.generation_type === "tag_suggestion") return "태그"
  if (s.generation_type === "pii_detection") return "PII"
  if (s.generation_type === "summary") return "요약"
  if (s.generation_type === "description") return s.entity_type === "column" ? "컬럼 설명" : "설명"
  return s.generation_type
}

/** 적용 대상 — 컬럼 제안이면 컬럼 이름, 나머지는 데이터셋. */
export function suggestionTarget(s: SuggestionLike): string {
  return s.entity_type === "column" ? (s.field_name || "컬럼") : "데이터셋"
}

function parseJson(text: string | null | undefined): unknown {
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

/** 태그 제안 → 기존 태그·새 태그 이름. 형식이 다르면 둘 다 빈 배열. */
export function parseTagSuggestion(text: string): { tags: string[]; newTags: string[] } {
  const data = parseJson(text)
  if (Array.isArray(data)) {
    return { tags: data.map((t) => String(t).trim()).filter(Boolean), newTags: [] }
  }
  if (data && typeof data === "object") {
    const d = data as { tags?: unknown; new_tags?: unknown }
    const tags = Array.isArray(d.tags) ? d.tags.map((t) => String(t).trim()).filter(Boolean) : []
    const newTags = Array.isArray(d.new_tags)
      ? d.new_tags
          .map((t) => (t && typeof t === "object" ? String((t as { name?: unknown }).name ?? "").trim() : ""))
          .filter(Boolean)
      : []
    return { tags, newTags }
  }
  return { tags: [], newTags: [] }
}

/** PII 제안·현재 값 → [{name, piiType}] (pii_type 이 없으면 null). */
export function parsePiiList(text: string | null | undefined): { name: string; piiType: string | null }[] {
  const data = parseJson(text)
  if (!Array.isArray(data)) return []
  return data
    .filter((p): p is { name?: unknown; pii_type?: unknown } => !!p && typeof p === "object")
    .map((p) => ({ name: String(p.name ?? ""), piiType: p.pii_type ? String(p.pii_type) : null }))
    .filter((p) => p.name)
}

/** 제안값을 한 덩어리 글로 — 목록의 "제안값" 열. */
export function formatSuggestedValue(s: SuggestionLike): string {
  if (s.generation_type === "tag_suggestion") {
    const { tags, newTags } = parseTagSuggestion(s.generated_text)
    const parts = [...tags, ...newTags.map((t) => `${t} (새 태그)`)]
    return parts.length ? parts.join(", ") : s.generated_text
  }
  if (s.generation_type === "pii_detection") {
    const cols = parsePiiList(s.generated_text)
    return cols.length ? cols.map((c) => `${c.name}: ${c.piiType ?? "-"}`).join(", ") : s.generated_text
  }
  return s.generated_text
}

/** 현재 값을 글로 — 비어 있으면 "(없음)". 목록의 "현재 값" 열. */
export function formatCurrentValue(s: SuggestionLike): string {
  const cur = s.current_text
  if (s.generation_type === "tag_suggestion") {
    const data = parseJson(cur)
    const names = Array.isArray(data) ? data.map((t) => String(t)).filter(Boolean) : []
    return names.length ? names.join(", ") : "(없음)"
  }
  if (s.generation_type === "pii_detection") {
    const cols = parsePiiList(cur)
    return cols.length ? cols.map((c) => `${c.name}: ${c.piiType ?? "-"}`).join(", ") : "(없음)"
  }
  return cur && cur.trim() ? cur : "(없음)"
}

/** 생성 출처 — AI 서비스 enrich 는 provider "agent" 로 반입한다. */
export function suggestionSource(s: Pick<SuggestionItem, "provider" | "model">): string {
  const where = s.provider === "agent" ? "AI 서비스(enrich)" : `카탈로그 AI 생성 · ${s.provider}`
  return s.model ? `${where} · ${s.model}` : where
}
