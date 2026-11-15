/**
 * 문자열 하나에 담긴 값 목록 (TODO-143) — 백엔드 `app/core/text_list.py` 와 같은 규칙.
 *
 * 저장은 JSON 배열 문자열(`["1,000","N/A"]`)로 해 값 안의 쉼표를 지킨다. 읽을 때는 예전 쉼표 구분 문자열도 받는다.
 * 품질 허용 값 · 더미 값(expected_value), 페더레이션으로 받은 확장 메타데이터처럼 문자열로 오가는 곳에서 쓴다.
 */
/** 목록 정리 — 앞뒤 공백 · 빈 값 · 중복 제거, 순서 유지 */
export function cleanTags(values: readonly string[]): string[] {
  const out: string[] = []
  for (const v of values) {
    const s = v.trim()
    if (s && !out.includes(s)) out.push(s)
  }
  return out
}

export function decodeList(value: string | readonly string[] | null | undefined): string[] {
  if (value == null) return []
  if (Array.isArray(value)) return cleanTags(value)
  const text = String(value).trim()
  if (!text) return []
  if (text.startsWith("[")) {
    try {
      const parsed: unknown = JSON.parse(text)
      if (Array.isArray(parsed) && parsed.every((x) => typeof x === "string" || typeof x === "number")) {
        return cleanTags(parsed.map(String))
      }
    } catch {
      // JSON 이 아니면 예전 규칙
    }
  }
  return cleanTags(text.split(","))
}

/** 목록 → 저장 문자열(JSON 배열). 비면 null. */
export function encodeList(values: readonly string[]): string | null {
  const items = cleanTags(values)
  return items.length ? JSON.stringify(items) : null
}

/**
 * 쉼표 문자열로 저장하는 목록(값에 쉼표가 들어갈 수 없는 URL · 메일 주소 · 언어 코드 · 사용자 이름) ↔ 배지 입력 값.
 * 이런 항목은 서버 형식을 그대로 두고 화면에서만 목록으로 다룬다(TODO-144).
 */
export function splitCsv(value: string | null | undefined): string[] {
  return cleanTags((value ?? "").split(","))
}

export function joinCsv(values: readonly string[], sep = ", "): string {
  return cleanTags(values).join(sep)
}
