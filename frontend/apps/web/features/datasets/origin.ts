// 환경(origin) — 이름과 색을 한 곳에서 정한다(DEF-107).
// 데이터 소스 트리 · 범례, 데이터셋 목록 배지, 대시보드 도넛, 데이터 소스 상세가 모두 이것을 쓴다.
// 색은 예전부터 쓰던 값 그대로: 운영 초록 · 스테이징 주황 · 개발 하늘.

export const ORIGINS = ["PROD", "STAGING", "DEV"] as const
export type Origin = (typeof ORIGINS)[number]

type OriginMeta = {
  /** 한글 이름 */
  label: string
  /** 차트 · 인라인 스타일용 색(Tailwind 의 같은 단계) */
  color: string
  /** 점 · 배지 배경 클래스 */
  bg: string
}

export const ORIGIN_META: Record<Origin, OriginMeta> = {
  PROD: { label: "운영", color: "#059669", bg: "bg-emerald-600" },      // emerald-600
  STAGING: { label: "스테이징", color: "#f97316", bg: "bg-orange-500" }, // orange-500
  DEV: { label: "개발", color: "#0ea5e9", bg: "bg-sky-500" },           // sky-500
}

const UNKNOWN: OriginMeta = { label: "", color: "#a1a1aa", bg: "bg-muted-foreground" }

/** 같은 환경의 다른 표기 — MCP 배포는 스테이징을 STG 로 쓴다(TODO-107). */
const ORIGIN_ALIAS: Record<string, Origin> = { STG: "STAGING" }

function known(origin: string | null | undefined): OriginMeta | undefined {
  if (!origin) return undefined
  return ORIGIN_META[(ORIGIN_ALIAS[origin] ?? origin) as Origin]
}

export function originMeta(origin: string | null | undefined): OriginMeta {
  return known(origin) || { ...UNKNOWN, label: origin ?? "" }
}

/** "운영 (PROD)" — 코드는 받은 그대로 괄호에, 모르는 값은 코드만 */
export function originLabel(origin: string | null | undefined): string {
  const m = known(origin)
  return m ? `${m.label} (${origin})` : origin ?? "-"
}

/** 순서 — 운영 · 스테이징 · 개발, 모르는 값은 뒤 */
export function originRank(origin: string): number {
  const i = (ORIGINS as readonly string[]).indexOf(origin)
  return i < 0 ? ORIGINS.length : i
}
