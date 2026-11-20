// 규칙 유형(진단 방식)의 화면 이름과 분류(관리지침 진단 기준) 묶음 — 품질 지표 대화상자와 데이터셋 품질 탭의 규칙 대화상자가 같이 쓴다.
import type { QualityIndicator } from "@/features/quality/api"
import { domainToSave } from "@/features/datasets/quality-rules"

/** 규칙 유형의 화면 이름 — 분류 아래에서 "기준 방식" 으로 고르는 이름. 서버 코드(NOT_NULL …)는 툴팁으로만 보인다. */
export const CHECK_TYPE_LABEL: Record<string, string> = {
  NOT_NULL: "NULL 없음",
  UNIQUE: "고유값",
  BROKEN_TEXT: "글자 깨짐",
  WHITESPACE: "공백·특수문자",
  DATE_FORMAT: "날짜 형식",
  REGEX: "정규식 패턴",
  FUNCTION: "Trino 함수 (UDF)",
  ACCEPTED_VALUES: "허용 값 목록",
  MIN_VALUE: "하한 (이상)",
  MAX_VALUE: "상한 (이하)",
  COLUMN_COMPARE: "컬럼 간 비교",
  LOGICAL: "논리관계",
  FORMULA: "계산식",
  REFERENTIAL: "참조관계",
  DUMMY_VALUE: "더미 값",
  CONTINUITY: "데이터 연속성",
  DIVERSITY: "값 분포 다양성",
  COMPOSITE_UNIQUE: "여러 컬럼 키 고유값",
  ROW_COUNT: "행 수 하한",
  FRESHNESS: "최신성",
  CUSTOM_SQL: "커스텀 SQL",
  CUSTOM_PYTHON: "커스텀 Python",
}

export function checkTypeLabel(checkType: string | null | undefined): string {
  return (checkType && CHECK_TYPE_LABEL[checkType]) || checkType || "-"
}

/**
 * 관리지침 분류 밖의 규칙 — 최신성 · 커스텀 SQL · 커스텀 Python. 데이터셋 품질 탭의 규칙 대화상자에서만 고를 수 있는
 * 묶음이다(품질 지표로는 정의하지 않는다). 서버 분류 목록에는 없고 저장할 때 분류(도메인)도 보내지 않는다.
 * 정밀 진단이 평가하지만, 진단 기준(지표)을 골라 돌리는 실행에는 들지 않는다.
 */
export const UNCLASSIFIED: QualityIndicator = {
  key: "__UNCLASSIFIED__",
  name: "진단 기준 없음",
  area: "UNCLASSIFIED",
  area_name: "진단 기준 밖",
  check_types: ["FRESHNESS", "CUSTOM_SQL", "CUSTOM_PYTHON"],
  description: "관리지침 진단 기준에 들지 않는 검사 — 적재 최신성, 직접 쓴 SQL · Python. 정밀 진단이 평가하며(커스텀 SQL 은 Trino 경로만, 커스텀 Python 은 원본 DB(PostgreSQL·MySQL·MariaDB·Oracle·SQL Server)를 직접 읽어서), 진단 기준을 골라 돌리는 품질 진단 작업과 리포트의 진단 기준별 집계에는 들지 않습니다.",
}

/** 규칙 대화상자에서 고를 분류 — 서버 분류 전체와 "분류 없음". */
export function ruleCategories(indicators: QualityIndicator[]): QualityIndicator[] {
  return [...indicators, UNCLASSIFIED]
}

/** 분류를 진단 영역(완결성 · 유효성 · 정합성 · 기타 …)으로 묶는다 — 선택 상자의 그룹. */
export function groupByArea(categories: QualityIndicator[]): { key: string; name: string; items: QualityIndicator[] }[] {
  const out: { key: string; name: string; items: QualityIndicator[] }[] = []
  for (const c of categories) {
    const area = out.find((a) => a.key === c.area)
    if (area) area.items.push(c)
    else out.push({ key: c.area, name: c.area_name, items: [c] })
  }
  return out
}

/** 규칙이 지금 속한 분류 — 서버가 정한 분류(indicator)가 있으면 그것, 없으면 그 유형을 가진 첫 분류(또는 "분류 없음"). */
export function categoryOfRule(
  rule: { check_type: string; indicator?: string | null }, categories: QualityIndicator[],
): string {
  if (rule.indicator && categories.some((c) => c.key === rule.indicator)) return rule.indicator
  return categories.find((c) => c.check_types.includes(rule.check_type))?.key ?? UNCLASSIFIED.key
}

/** 저장할 도메인 — 분류가 같은 유형을 나눠 갖는 경우(허용 값 → 여부/코드, 하한 → 금액/수량/율 …)만 그 분류의 도메인. */
export function domainForCategory(
  category: QualityIndicator | undefined, checkType: string, indicators: QualityIndicator[],
): string | null {
  return domainToSave(checkType, category?.domain ?? "", indicators)
}

/**
 * 이 데이터셋의 정밀 진단 경로가 평가하지 않는 유형인가. ``pathTypes`` 는 엔진 상태 API 의 ``check_types``.
 * 경로를 모르면(진단할 수 없음) false.
 */
export function skippedOnPath(checkType: string, pathTypes: string[] | null | undefined): boolean {
  if (!pathTypes || pathTypes.length === 0) return false
  return !pathTypes.includes(checkType)
}

/** 경로에서 빠지는 이유 — engine 은 TRINO(Trino 연결) 또는 DIRECT(원천 직접). */
export function pathSkipReason(engine: string | null | undefined, checkType: string): string {
  if (engine === "DIRECT") {
    return checkType === "FUNCTION" || checkType === "CUSTOM_SQL"
      ? "이 데이터셋은 Trino 연결이 없어 원천을 직접 읽어 진단합니다 — 이 유형은 Trino 에서만 평가해 정밀 진단에서 건너뜁니다(직전 결과 유지)."
      : "이 데이터셋의 원천 직접 진단은 이 유형을 평가하지 않아 건너뜁니다(직전 결과 유지)."
  }
  return checkType === "CUSTOM_PYTHON"
    ? "이 데이터셋은 Trino 로 진단합니다 — 커스텀 Python 은 원본 DB 를 직접 읽어 평가하는데, 이 데이터셋의 원본은 직접 읽을 수 있는 유형(PostgreSQL·MySQL·MariaDB·Oracle·SQL Server)이 아니라 건너뜁니다(직전 결과 유지)."
    : "이 데이터셋의 정밀 진단 경로는 이 유형을 평가하지 않아 건너뜁니다(직전 결과 유지)."
}
