// 데이터 품질 화면 공용 표시 값 — 상태 이름·색, 진행률, 소요 시간.
import type { StatusTone } from "@workspace/ui/components/status-badge"
import { parseServerTime } from "@/features/datasources/sync-format"
import type {
  QualityIndicator,
  QualityJobRunStatus,
  QualityJobSchedule,
  QualityJobTarget,
  QualityRun,
  QualityRunStatus,
} from "@/features/quality/api"

/** 정밀 진단 경로 — TRINO: Trino 연결로 SQL 집계, DIRECT: 품질 워커가 원천 DB 를 직접 읽어 평가(Trino 연결이 없을 때) */
export const ENGINE_LABEL: Record<string, string> = { TRINO: "Trino", DIRECT: "원천 직접" }

/** 실행 종류 — 규칙 진단과 프로파일(컬럼 통계 수집)이 같은 대기열에서 돈다 */
export const isProfileRun = (run: { kind?: string | null } | null | undefined) => run?.kind === "PROFILE"

export function engineLabel(engine: string | null | undefined): string {
  return ENGINE_LABEL[engine ?? ""] ?? engine ?? "-"
}

export const QUALITY_STATUS_LABEL: Record<QualityRunStatus, string> = {
  QUEUED: "대기 중",
  RUNNING: "실행 중",
  SUCCEEDED: "완료",
  PARTIAL: "일부 실패",
  FAILED: "실패",
  CANCELLED: "중지됨",
}

/** 상태 배지 톤 — 성공 초록 · 일부 실패 주황 · 실패 빨강 · 실행 중 파랑 · 대기/취소 무채색(DEF-114). */
export const QUALITY_STATUS_TONE: Record<QualityRunStatus, StatusTone> = {
  QUEUED: "neutral",
  RUNNING: "info",
  SUCCEEDED: "success",
  PARTIAL: "warning",
  FAILED: "danger",
  CANCELLED: "neutral",
}

/** 규칙 판정 — 통과 초록 · 실패 빨강(DEF-116, 예전엔 통과가 회색) */
export const checkResult = (passed: boolean): { label: string; tone: StatusTone } =>
  passed ? { label: "통과", tone: "success" } : { label: "실패", tone: "danger" }

/** 실행 상태 이름 · 톤 한 벌 — `statusMeta(QUALITY_RUN_STATUS, status)` */
export const QUALITY_RUN_STATUS = Object.fromEntries(
  (Object.keys(QUALITY_STATUS_LABEL) as QualityRunStatus[]).map((k) => [k, { label: QUALITY_STATUS_LABEL[k], tone: QUALITY_STATUS_TONE[k] }]),
) as Record<QualityRunStatus, { label: string; tone: StatusTone }>

export const QUALITY_TRIGGER_LABEL: Record<string, string> = { MANUAL: "수동", SCHEDULE: "예약" }

/** 끝난 쿼리 비율(%). 전체 쿼리 수를 아직 모르면 null. */
export function qualityPercent(run: Pick<QualityRun, "progress_done" | "progress_total">): number | null {
  if (!run.progress_total) return null
  return Math.min(100, Math.round((run.progress_done / run.progress_total) * 100))
}

/** 실행이 걸린(또는 지금까지 걸린) 초 — 대기 중이면 null. */
export function qualityElapsedSeconds(
  run: Pick<QualityRun, "started_at" | "finished_at">, now: number = Date.now(),
): number | null {
  const start = parseServerTime(run.started_at)
  if (!start) return null
  const end = parseServerTime(run.finished_at)?.getTime() ?? now
  return Math.max(0, (end - start.getTime()) / 1000)
}

/** 오류율 표시 — "1.2346%" 를 "1.23%" 로, 0 은 "0%". 건수가 없으면 "-". */
export function fmtErrorRate(rate: number | null | undefined): string {
  if (rate == null) return "-"
  if (rate === 0) return "0%"
  return `${rate < 0.01 ? rate.toFixed(4) : rate.toFixed(2)}%`
}

// --- 품질 진단 작업 ---

export const QUALITY_JOB_RUN_STATUS_LABEL: Record<QualityJobRunStatus, string> = {
  RUNNING: "실행 중",
  SUCCEEDED: "완료",
  PARTIAL: "일부 실패",
  FAILED: "실패",
  CANCELLED: "중지됨",
  SKIPPED: "건너뜀",
}

export const QUALITY_JOB_RUN_STATUS_TONE: Record<QualityJobRunStatus, StatusTone> = {
  RUNNING: "info",
  SUCCEEDED: "success",
  PARTIAL: "warning",
  FAILED: "danger",
  CANCELLED: "neutral",
  SKIPPED: "neutral",
}

/** 요일 — 서버의 0(월)~6(일) 순서 */
export const WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"] as const

/** 주기를 한 줄로 — "매주 월요일 06:00", "6시간마다", "수동 실행". */
export function describeSchedule(job: QualityJobSchedule): string {
  const time = job.schedule_time ?? ""
  switch (job.schedule_type) {
    case "HOURLY":
      return job.schedule_interval_hours === 1 ? "매시간" : `${job.schedule_interval_hours ?? "?"}시간마다`
    case "DAILY":
      return `매일 ${time}`
    case "WEEKLY":
      return `매주 ${WEEKDAYS[job.schedule_day ?? 0] ?? "?"}요일 ${time}`
    case "MONTHLY":
      // 31일처럼 그 달에 없는 날은 말일에 실행된다
      return `매월 ${job.schedule_day ?? "?"}일 ${time}`
    default:
      return "수동 실행"
  }
}

/** 대상을 한 줄로 — 앞의 몇 개 이름과 나머지 개수. */
export function describeTargets(targets: QualityJobTarget[], max = 2): string {
  if (targets.length === 0) return "대상 없음"
  const names = targets.slice(0, max).map((t) => t.label ?? `#${t.dataset_id ?? t.catalog_id}`)
  return targets.length > max ? `${names.join(", ")} 외 ${targets.length - max}개` : names.join(", ")
}

/** 고른 지표를 한 줄로 — 빈 목록은 "전체". 이름을 모르는 키는 키 그대로. */
export function describeIndicators(keys: string[], indicators: QualityIndicator[]): string {
  if (keys.length === 0) return "전체"
  const names = new Map(indicators.map((i) => [i.key, i.name]))
  return keys.map((k) => names.get(k) ?? k).join(", ")
}

/**
 * 무엇을 측정했는지 한 줄로 — 고른 품질 지표(미리 정의한 지표)의 이름. 고르지 않았으면 "전체".
 * 품질 지표 선택이 생기기 전에 분류로 고른 작업·실행은 "분류: …" 로 보여 준다.
 */
export function describeMeasured(
  item: { definitions?: string[]; indicators: string[] }, indicators: QualityIndicator[],
): string {
  if (item.definitions && item.definitions.length > 0) return item.definitions.join(", ")
  if (item.indicators.length > 0) return `진단 기준: ${describeIndicators(item.indicators, indicators)}`
  return "전체"
}

/** 대상의 종류 이름 */
export const QUALITY_TARGET_TYPE_LABEL: Record<QualityJobTarget["type"], string> = {
  catalog: "카탈로그",
  schema: "스키마",
  dataset: "데이터셋",
}

/** 같은 대상인지 비교하는 키 */
export function targetKey(t: QualityJobTarget): string {
  if (t.type === "dataset") return `dataset:${t.dataset_id}`
  return t.type === "schema" ? `schema:${t.catalog_id}:${t.schema_name}` : `catalog:${t.catalog_id}`
}

/** 작업 실행이 걸린(또는 지금까지 걸린) 초. */
export function jobRunElapsedSeconds(
  run: { started_at: string | null; finished_at: string | null }, now: number = Date.now(),
): number | null {
  return qualityElapsedSeconds(run, now)
}

/** 건수로 오류율(%)을 구한다 — 진단 건수가 없으면 null. */
export function errorRateOf(checked: number | null | undefined, errors: number | null | undefined): number | null {
  if (!checked) return null
  return Math.round(((errors ?? 0) / checked) * 1_000_000) / 10_000
}

/** 막대 길이(%) — 가장 큰 오류율을 100 으로. 값이 있으면 최소 2% 는 보이게 한다. */
export function rateBarPercent(rate: number | null, max: number): number {
  if (!rate || max <= 0) return 0
  return Math.max(2, Math.min(100, (rate / max) * 100))
}

/** 직전 회차 대비 증감 — 화살표와 숫자를 함께 적어 색 없이도 읽힌다. */
export type QualityChange = { text: string; trend: "worse" | "better" | "same" }

/**
 * 지금 값과 직전 값의 차이를 한 줄로 — "▲ 0.12%p", "▼ 3건", "변동 없음". 어느 한쪽이 없으면 null.
 * ``lowerIsBetter`` 는 오류율·오류 건수처럼 줄어야 좋은 값(기본), 품질 점수는 false.
 */
export function describeChange(
  current: number | null | undefined, previous: number | null | undefined,
  opts: { unit?: string; lowerIsBetter?: boolean; digits?: number } = {},
): QualityChange | null {
  if (current == null || previous == null) return null
  const { unit = "", lowerIsBetter = true, digits = 0 } = opts
  const scale = 10 ** digits
  const diff = Math.round((current - previous) * scale) / scale
  if (diff === 0) return { text: "변동 없음", trend: "same" }
  const amount = Math.abs(diff).toLocaleString(undefined, { maximumFractionDigits: digits })
  return {
    text: `${diff > 0 ? "▲" : "▼"} ${amount}${unit}`,
    trend: diff > 0 === lowerIsBetter ? "worse" : "better",
  }
}

/** 규칙별 결과를 실패한 것부터 — 같은 결과끼리는 받은 순서 그대로. */
export function failedFirst<T extends { passed: boolean }>(rules: T[]): T[] {
  return [...rules.filter((r) => !r.passed), ...rules.filter((r) => r.passed)]
}

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/
const whole = (text: string, min: number, max: number) =>
  /^\d+$/.test(text.trim()) && Number(text) >= min && Number(text) <= max

/** 주기 입력이 서버가 받는 값인지 — 문제가 있으면 그 설명, 없으면 null. 저장(매핑 반영) 전에 걸러 낸다. */
export function scheduleProblem(input: {
  type: string; time: string; monthDay: string; intervalHours: string
}): string | null {
  const timed = input.type === "DAILY" || input.type === "WEEKLY" || input.type === "MONTHLY"
  if (timed && !HHMM.test(input.time)) return "실행 시각을 시:분(예: 02:00)으로 넣으세요."
  if (input.type === "MONTHLY" && !whole(input.monthDay, 1, 31)) return "날짜는 1 ~ 31 사이의 정수로 넣으세요."
  if (input.type === "HOURLY" && !whole(input.intervalHours, 1, 168)) return "간격은 1 ~ 168 시간 사이의 정수로 넣으세요."
  return null
}
