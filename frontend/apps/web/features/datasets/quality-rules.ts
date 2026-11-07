// 품질 탭 규칙 목록 표시 규칙 — 서버 시맨틱(app/quality/service.py)과 맞춘다.

/** 임계값(%)을 쓰는 검증 유형 — 나머지 유형은 저장된 threshold(기본 100)를 판정에 쓰지 않는다. */
export const THRESHOLD_CHECK_TYPES: ReadonlySet<string> = new Set(["NOT_NULL", "UNIQUE"])

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
 * 품질 배치(python-quality.py)는 bytes 컬럼을 "BINARY" 로 보내고, 그 밖은 pandas dtype 대문자를 보낸다.
 * pandas 2 의 문자열 컬럼(및 전부 NULL 인 컬럼)은 dtype 이 "OBJECT" 이므로 바이너리로 보지 않는다.
 */
export function isBinaryColumnType(t: string | null | undefined): boolean {
  if (!t) return false
  const upper = t.toUpperCase()
  return ["BLOB", "BINARY", "VARBINARY", "BYTEA", "BYTES", "IMAGE", "GEOMETRY", "RAW"].some((k) => upper.includes(k))
}
