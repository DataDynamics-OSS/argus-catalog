// 품질 지표(미리 정해 두고 컬럼에 할당하는 진단 기준)의 표시·입력 규칙 — 서버 app/quality/definitions.py 와 맞춘다.
import type { QualityDefinitionWrite, QualityIndicator } from "@/features/quality/api"
import { CHECK_TYPE_LABEL } from "@/features/quality/check-types"
import { decodeList } from "@/lib/text-list"

/**
 * 기준값을 받는 방식 — none: 기준값 없음, date: 날짜 형식 선택, number: 숫자, text: 그대로 적음,
 * list: 값 목록(배지 입력, JSON 배열 문자열로 저장 — 값 안의 쉼표 보존, TODO-143)
 */
export type ExpectedKind = "none" | "date" | "number" | "text" | "list"

export type DefinitionTypeMeta = {
  label: string
  expected: ExpectedKind
  /** 기준값 입력란의 이름과 예 */
  expectedLabel: string
  hint: string
  /** 기준값을 비워도 되는가 */
  optional: boolean
  usesThreshold: boolean
}

/** 미리 정의할 수 있는 규칙 유형 — 서버 `indicators.DEFINABLE_TYPES` 와 같은 목록. */
export const DEFINITION_TYPE_META: Record<string, DefinitionTypeMeta> = {
  NOT_NULL: { label: CHECK_TYPE_LABEL.NOT_NULL!, expected: "none", expectedLabel: "", hint: "", optional: true, usesThreshold: true },
  UNIQUE: { label: CHECK_TYPE_LABEL.UNIQUE!, expected: "none", expectedLabel: "", hint: "", optional: true, usesThreshold: true },
  BROKEN_TEXT: { label: CHECK_TYPE_LABEL.BROKEN_TEXT!, expected: "none", expectedLabel: "", hint: "", optional: true, usesThreshold: false },
  WHITESPACE: {
    label: CHECK_TYPE_LABEL.WHITESPACE!, expected: "text", expectedLabel: "쓰면 안 되는 특수문자 (선택)",
    hint: "예: #$%  — 비우면 앞뒤 공백·제어 문자만 봅니다", optional: true, usesThreshold: false,
  },
  DATE_FORMAT: { label: CHECK_TYPE_LABEL.DATE_FORMAT!, expected: "date", expectedLabel: "날짜 형식", hint: "예: YYYY/MM/DD", optional: false, usesThreshold: false },
  REGEX: {
    label: CHECK_TYPE_LABEL.REGEX!, expected: "text", expectedLabel: "정규식 (부분 일치 — 전체가 맞아야 하면 ^ 와 $ 를 붙임)",
    hint: "예: ^\\d{3}-\\d{4}$", optional: false, usesThreshold: false,
  },
  FUNCTION: {
    label: CHECK_TYPE_LABEL.FUNCTION!, expected: "text",
    expectedLabel: "함수 이름 (Trino 에 미리 등록한 함수 — 값 하나를 받아 맞으면 true)",
    hint: "예: memory.default.is_valid_brno", optional: false, usesThreshold: false,
  },
  ACCEPTED_VALUES: {
    label: CHECK_TYPE_LABEL.ACCEPTED_VALUES!, expected: "list", expectedLabel: "허용 값", hint: "예: Y 입력 후 Enter", optional: false, usesThreshold: false,
  },
  MIN_VALUE: { label: CHECK_TYPE_LABEL.MIN_VALUE!, expected: "number", expectedLabel: "하한", hint: "예: 0", optional: false, usesThreshold: false },
  MAX_VALUE: { label: CHECK_TYPE_LABEL.MAX_VALUE!, expected: "number", expectedLabel: "상한", hint: "예: 100", optional: false, usesThreshold: false },
  DUMMY_VALUE: {
    label: CHECK_TYPE_LABEL.DUMMY_VALUE!, expected: "list", expectedLabel: "더미 값 (선택)",
    hint: "예: 999999 입력 후 Enter — 비우면 흔한 자리채움 값(999999 · -999 · N/A · 없음 …)", optional: true, usesThreshold: true,
  },
  CONTINUITY: {
    label: CHECK_TYPE_LABEL.CONTINUITY!, expected: "text", expectedLabel: "기간 단위 (HOUR · DAY · MONTH · YEAR, 문자열 날짜면 뒤에 형식)",
    hint: "예: DAY · MONTH · DAY YYYYMMDD · MONTH YYYY-MM", optional: false, usesThreshold: true,
  },
}

const CONTINUITY_RE = /^(HOUR|DAY|MONTH|YEAR)(\s+\S.*)?$/i
const CONTINUITY_UNIT_LABEL: Record<string, string> = { HOUR: "시간", DAY: "일", MONTH: "월", YEAR: "연" }

/** 지표를 정의할 수 있는 분류만. */
export function definableCategories(indicators: QualityIndicator[]): QualityIndicator[] {
  return indicators.filter((i) => i.definable)
}

/** 지표의 진단 기준을 한 줄로 — 목록·할당 화면에 쓴다. */
export function criteriaText(d: { check_type: string; expected_value: string | null; threshold: number }): string {
  const expected = (d.expected_value ?? "").trim()
  switch (d.check_type) {
    case "NOT_NULL":
      return d.threshold >= 100 ? "NULL 없음" : `NULL 아닌 값 ${d.threshold}% 이상`
    case "UNIQUE":
      return d.threshold >= 100 ? "중복 없음" : `고유값 ${d.threshold}% 이상`
    case "BROKEN_TEXT":
      return "깨진 글자 · 낱자 한글 없음"
    case "WHITESPACE":
      return expected ? `앞뒤 공백 · 제어 문자 · ${expected} 없음` : "앞뒤 공백 · 제어 문자 없음"
    case "DATE_FORMAT":
      return `날짜 형식 ${expected}`
    case "REGEX":
      return `정규식 ${expected}`
    case "FUNCTION":
      return `Trino 함수 ${expected}`
    case "ACCEPTED_VALUES":
      return `허용 값 ${decodeList(expected).join(" · ")}`
    case "MIN_VALUE":
      return `${expected} 이상`
    case "MAX_VALUE":
      return `${expected} 이하`
    case "DUMMY_VALUE": {
      const what = expected ? `더미 값(${decodeList(expected).join(" · ")})` : "흔한 더미 값"
      return d.threshold >= 100 ? `${what} 없음` : `${what} 아닌 값 ${d.threshold}% 이상`
    }
    case "CONTINUITY": {
      const [unit = "", ...fmt] = expected.split(/\s+/)
      const label = CONTINUITY_UNIT_LABEL[unit.toUpperCase()] ?? unit
      const tail = fmt.length ? ` (${fmt.join(" ")})` : ""
      return d.threshold >= 100 ? `빠진 ${label} 없음${tail}` : `있는 ${label} ${d.threshold}% 이상${tail}`
    }
    default:
      return expected ? `${d.check_type} ${expected}` : d.check_type
  }
}

export type DefinitionForm = {
  name: string
  category: string
  checkType: string
  expected: string
  threshold: string
  severity: string
  description: string
}

/**
 * 복사본의 이름 — "<이름> 복사", 이미 있으면 "<이름> 복사 2", "복사 3" … (이름은 대소문자를 가리지 않고 고유해야 한다).
 * 복사본을 다시 복사해도 "복사 복사" 로 늘어나지 않게 원래 이름에서 센다.
 */
export function copyName(name: string, taken: string[]): string {
  const base = name.replace(/ 복사( \d+)?$/, "")
  const used = new Set(taken.map((n) => n.trim().toLowerCase()))
  for (let n = 1; ; n += 1) {
    const candidate = n === 1 ? `${base} 복사` : `${base} 복사 ${n}`
    if (!used.has(candidate.toLowerCase())) return candidate
  }
}

/** 분류를 바꿨을 때의 유형 — 그 분류에 지금 유형이 있으면 그대로, 없으면 첫 유형. */
export function typeForCategory(category: QualityIndicator | undefined, current: string): string {
  if (!category) return current
  return category.check_types.includes(current) ? current : (category.check_types[0] ?? current)
}

/** 저장할 수 없는 이유(없으면 null) — 서버의 `QualityDefinitionWrite` 검증과 같은 것을 먼저 본다. */
export function definitionFormError(form: DefinitionForm): string | null {
  const meta = DEFINITION_TYPE_META[form.checkType]
  if (!form.name.trim()) return "이름을 적으세요."
  if (!form.category || !meta) return "진단 기준을 고르세요."
  const expected = form.expected.trim()
  if (!meta.optional && !expected) return `${meta.expectedLabel}을(를) 적으세요.`
  if (meta.expected === "number" && Number.isNaN(Number(expected))) return "기준값은 숫자여야 합니다."
  if (form.checkType === "CONTINUITY" && !CONTINUITY_RE.test(expected)) {
    return "기간 단위는 HOUR · DAY · MONTH · YEAR 중 하나로 시작해야 합니다(문자열 날짜면 뒤에 형식, 예: DAY YYYYMMDD)."
  }
  if (meta.usesThreshold) {
    const t = Number(form.threshold)
    if (form.threshold.trim() === "" || Number.isNaN(t) || t < 0 || t > 100) return "임계값은 0~100 사이의 숫자입니다."
  }
  return null
}

export function definitionPayload(form: DefinitionForm): QualityDefinitionWrite {
  const meta = DEFINITION_TYPE_META[form.checkType]
  return {
    name: form.name.trim(),
    category: form.category,
    check_type: form.checkType,
    expected_value: meta?.expected === "none" ? null : (form.expected.trim() || null),
    threshold: meta?.usesThreshold ? Number(form.threshold) : 100,
    severity: form.severity,
    description: form.description.trim() || null,
  }
}
