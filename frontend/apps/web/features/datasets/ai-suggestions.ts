// AI 제안(승인 대기) 표시용 순수 함수 — 데이터셋 상세의 "AI 제안" 다이얼로그가 쓴다.
//
// 제안은 서버(backend/app/ai/service.py)의 AIGenerationLog 중 applied=false 인 행이다.
// 카탈로그 화면의 미리보기(생성 후 적용하지 않음)와 AI 서비스 enrich(provider="agent") 가 만든다.
// generated_text / current_text 형식:
//   - 설명·요약·컬럼 설명: 평문
//   - 태그: 서버 생성은 {"tags": [...], "new_tags": [{name, description}]}, enrich 는 ["a", "b"]
//     현재 값은 현재 태그 이름 배열
//   - PII: [{"name", "pii_type", ...}] — 현재 값은 같은 컬럼들의 [{"name", "pii_type"}]
//   - AI-Ready 초안(generation_type "ai_ready", field_name 이 항목): 평문 — 관련법령은 법령명을 쉼표로,
//     허용 이용 유형은 코드를 쉼표로(VIEW,ANALYSIS — 화면에서는 이름으로). 고른 근거는 reason

import type { AiReadyDraftResult, SuggestionItem } from "./ai-api"

/** AI-Ready 초안 항목 이름 — 서버 app/ai/ai_ready_drafts.py FIELDS 와 같다. */
export const AI_READY_DRAFT_LABEL: Record<string, string> = {
  data_biases: "알려진 편향",
  known_limitations: "한계 · 유의사항",
  legal_basis: "관련법령",
  privacy_measures: "개인정보 보호조치",
  allowed_uses: "허용 이용 유형",
}

/** 허용 이용 유형 코드 이름 — 서버 app/catalog/ai_ready.py ALLOWED_USES 와 같다. */
export const ALLOWED_USE_LABEL: Record<string, string> = {
  VIEW: "열람",
  ANALYSIS: "분석",
  COMBINE: "결합",
  AI_TRAINING: "AI 학습",
}

/** 허용 이용 유형 저장 값(코드 쉼표 목록) → 이름. 빈 문자열은 '모두 허용하지 않음'. */
export function formatAllowedUses(text: string): string {
  const codes = text.split(",").map((c) => c.trim()).filter(Boolean)
  return codes.length ? codes.map((c) => ALLOWED_USE_LABEL[c] ?? c).join(", ") : "모두 허용하지 않음"
}

const isAllowedUses = (s: Pick<SuggestionLike, "generation_type" | "field_name">) =>
  s.generation_type === "ai_ready" && s.field_name === "allowed_uses"

type SuggestionLike = Pick<SuggestionItem, "entity_type" | "generation_type" | "field_name" | "generated_text"> & {
  current_text?: string | null
}

/** 제안 유형 이름 — 목록의 "유형" 열. */
export function suggestionKindLabel(s: SuggestionLike): string {
  if (s.generation_type === "tag_suggestion") return "태그"
  if (s.generation_type === "pii_detection") return "PII"
  if (s.generation_type === "summary") return "요약"
  if (s.generation_type === "ai_ready") return `AI-Ready · ${AI_READY_DRAFT_LABEL[s.field_name ?? ""] ?? s.field_name ?? "항목"}`
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
  if (isAllowedUses(s)) return formatAllowedUses(s.generated_text)
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
  // 허용 이용 유형은 null 이 '정하지 않음', 빈 문자열은 '모두 허용하지 않음'
  if (isAllowedUses(s)) return cur == null ? "(없음)" : formatAllowedUses(cur)
  return cur && cur.trim() ? cur : "(없음)"
}

/**
 * AI-Ready 초안 결과 → 알림 문구. ``hint`` 는 이미 값이 있어 건너뛴 항목을 다시 쓰는 방법(화면마다 다름).
 */
export function draftResultMessage(
  res: Pick<AiReadyDraftResult, "drafts" | "skipped" | "legal_reason">, hint?: string,
): { ok: boolean; title: string; description: string } {
  const lines = res.skipped.map((s) => `${s.label}: ${s.reason}`)
  if (hint && res.skipped.some((s) => s.reason.startsWith("이미 값이 있습니다"))) lines.push(hint)
  const usesReason = res.drafts.find((d) => d.field === "allowed_uses")?.reason
  if (usesReason) lines.unshift(`허용 이용 유형 근거: ${usesReason}`)
  if (res.legal_reason) lines.unshift(`관련법령 근거: ${res.legal_reason}`)
  if (res.drafts.length === 0) {
    return { ok: false, title: "새로 만든 초안이 없습니다.", description: lines.join(" / ") }
  }
  return {
    ok: true,
    title: `초안 ${res.drafts.length}개(${res.drafts.map((d) => d.label).join(" · ")})를 만들었습니다 — 머리글의 AI 제안에서 읽고 적용하세요.`,
    description: lines.join(" / "),
  }
}

/** 생성 출처 — AI 서비스 enrich 는 provider "agent" 로 반입한다. */
export function suggestionSource(s: Pick<SuggestionItem, "provider" | "model">): string {
  const where = s.provider === "agent" ? "AI 서비스(enrich)" : `카탈로그 AI 생성 · ${s.provider}`
  return s.model ? `${where} · ${s.model}` : where
}
