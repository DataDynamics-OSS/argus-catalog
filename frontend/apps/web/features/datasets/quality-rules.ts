// 품질 탭 규칙 목록 표시 규칙 — 서버 시맨틱(app/quality/service.py)과 맞춘다.

/** 임계값(%)을 쓰는 진단 유형 — 나머지 유형은 저장된 threshold(기본 100)를 판정에 쓰지 않는다. */
export const THRESHOLD_CHECK_TYPES: ReadonlySet<string> = new Set([
  "NOT_NULL", "UNIQUE", "DUMMY_VALUE", "CONTINUITY", "COMPOSITE_UNIQUE",
])

/**
 * 허용 값(ACCEPTED_VALUES)의 표준 코드 그룹 연결 — 기대값이 `{"code_group_id": N}` 이면 N, 값 목록이면 null.
 * 서버 `rule_sql.code_group_ref` 와 같은 규칙(양의 정수만).
 */
export function codeGroupRef(expected: string | null | undefined): number | null {
  const text = (expected ?? "").trim()
  if (!text.startsWith("{")) return null
  try {
    const parsed: unknown = JSON.parse(text)
    const id = (parsed as { code_group_id?: unknown } | null)?.code_group_id
    return typeof id === "number" && Number.isInteger(id) && id > 0 ? id : null
  } catch {
    return null
  }
}

/** 표준 코드 그룹 연결의 저장 값 — 서버 `rule_sql.code_group_expected` 와 같은 모양. */
export function codeGroupExpected(id: number): string {
  return JSON.stringify({ code_group_id: id })
}

/**
 * 규칙 목록 한 줄의 표시 정보.
 * - inactive: 서버는 `is_active === "true"` 인 규칙만 검증·결과·점수에 넣으므로 그 밖의 값은 비활성으로 표시한다.
 * - threshold: 임계값을 쓰는 유형일 때만 "임계값: N%" 를 보인다(그 밖은 null — 기본값 100 이 오해를 부름).
 */
export function ruleListMeta(rule: { check_type: string; threshold: number | null; is_active: string | null }) {
  return {
    inactive: rule.is_active !== "true",
    threshold: THRESHOLD_CHECK_TYPES.has(rule.check_type) ? (rule.threshold ?? 100) : null,
  }
}

/**
 * 프로파일 컬럼 타입이 바이너리인지 — 통계(최소/최대/평균/최빈값) 칸을 "바이너리 — 통계 생략" 으로 바꿀 때 쓴다.
 * 예전 품질 배치가 반입한 프로파일은 bytes 컬럼을 "BINARY", 그 밖은 pandas dtype 대문자("OBJECT" 등)로 적었다 —
 * 그 프로파일이 남아 있을 수 있어 그대로 받는다("OBJECT" 는 바이너리가 아니다).
 */
export function isBinaryColumnType(t: string | null | undefined): boolean {
  if (!t) return false
  const upper = t.toUpperCase()
  return ["BLOB", "BINARY", "VARBINARY", "BYTEA", "BYTES", "IMAGE", "GEOMETRY", "RAW"].some((k) => upper.includes(k))
}

/** 규칙 유형별 도메인 선택지에 쓰는 지표의 모양 — `GET /quality/indicators` 응답의 일부. */
export type IndicatorLike = { key: string; name: string; check_types: string[]; domain?: string | null }

/**
 * 이 유형의 규칙이 고를 수 있는 도메인(= 같은 유형을 나눠 갖는 품질 지표). 나뉘지 않는 유형은 빈 목록.
 * 예: ACCEPTED_VALUES → 여부(YN) · 코드(CODE), MIN_VALUE → 금액 · 수량 · 율, REGEX → 번호 · 날짜 · 코드 … · 문자열 형식.
 */
export function domainOptions(checkType: string, indicators: IndicatorLike[]): { value: string; label: string }[] {
  const options = indicators
    .filter((i) => i.domain && i.check_types.includes(checkType))
    .map((i) => ({ value: i.domain as string, label: i.name }))
  // 분류가 하나뿐이면 고를 것이 없다(날짜 형식 → 날짜)
  return options.length > 1 ? options : []
}

/** 저장할 도메인 — 이 유형의 선택지에 없는 값(유형을 바꾸기 전에 고른 것)과 빈 값은 null. */
export function domainToSave(checkType: string, domain: string, indicators: IndicatorLike[]): string | null {
  return domainOptions(checkType, indicators).some((o) => o.value === domain) ? domain : null
}

/** 날짜 형식 규칙(DATE_FORMAT)의 흔한 형식과 그 형식의 값 예 — 서버 `rule_sql.DATE_FORMATS` 와 같은 목록. */
export const DATE_FORMAT_CHOICES: { value: string; example: string }[] = [
  { value: "YYYYMMDD", example: "20240105" },
  { value: "YYYY-MM-DD", example: "2024-01-05" },
  { value: "YYYY/MM/DD", example: "2024/01/05" },
  { value: "YYYY.MM.DD", example: "2024.01.05" },
  { value: "YYYYMM", example: "202401" },
  { value: "YYYY-MM", example: "2024-01" },
  { value: "YYYY", example: "2024" },
  { value: "YYYYMMDDHH24MISS", example: "20240105133000" },
  { value: "YYYY-MM-DD HH24:MI:SS", example: "2024-01-05 13:30:00" },
  { value: "YYYY-MM-DD HH24:MI:SS.SSS", example: "2024-01-05 13:30:00.123" },
  { value: "YYYY-MM-DD HH24:MI", example: "2024-01-05 13:30" },
  { value: "YYYY-MM-DDTHH24:MI:SS", example: "2024-01-05T13:30:00" },
  { value: "HH24:MI:SS", example: "13:30:00" },
]

/** 목록에 있는 형식이면 그 값(대문자로 다듬어), 아니면 null — 선택 상자가 "직접 입력" 인지 가른다. */
export function listedDateFormat(expected: string): string | null {
  const value = expected.trim().toUpperCase()
  return DATE_FORMAT_CHOICES.some((c) => c.value === value) ? value : null
}

