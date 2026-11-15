// 데이터 소스 수집 — 범위·실행 표시용 공용 함수 (데이터 소스 상세의 수집 영역, 관리 > 메타데이터 수집)

import type { StatusTone } from "@workspace/ui/components/status-badge"
import type { SyncRun, SyncRunStatus, SyncScope, SyncScopeExclude, SyncScopeItem } from "@/features/datasources/api"

export const SILENT_WARN_SECONDS = 120

/** 수집 권한(datasources.sync)이 없는 사용자에게 범위 선택 대신 보여 주는 안내(DEF-069). */
export const SYNC_PERMISSION_MISSING_MESSAGE =
  "수집 권한(메타데이터 동기화)이 없어 수집 범위를 고르거나 수집할 수 없습니다. 관리자에게 요청하세요."

/** 쿼리 엔진에 '수집' 카탈로그 연결이 없을 때의 안내 — 백엔드 start_run 이 409 로 막는 조건(DEF-068). */
export const SYNC_CATALOG_MISSING_MESSAGE =
  "쿼리 엔진은 카탈로그에 '수집'으로 연결해야 엔진을 통해 수집할 수 있습니다. 위 카탈로그 연결에서 '수집' 역할로 연결한 뒤 새로 고침하세요."

export const STATUS_LABEL: Record<SyncRunStatus, string> = {
  QUEUED: "대기",
  RUNNING: "수집 중",
  SUCCEEDED: "성공",
  PARTIAL: "일부 실패",
  FAILED: "실패",
  CANCELLED: "중지됨",
}

/** 상태 배지 톤 — 품질 진단(`QUALITY_STATUS_TONE`)과 같은 기준(DEF-114). */
export const STATUS_TONE: Record<SyncRunStatus, StatusTone> = {
  QUEUED: "neutral",
  RUNNING: "info",
  SUCCEEDED: "success",
  PARTIAL: "warning",
  FAILED: "danger",
  CANCELLED: "neutral",
}

export const PHASE_LABEL: Record<string, string> = {
  CONNECTING: "접속 중",
  LISTING: "대상 조회",
  COLLECTING: "테이블 수집",
  REMOVING: "제거 반영",
  DONE: "완료",
}

export const TRIGGER_LABEL: Record<string, string> = { MANUAL: "수동", SCHEDULE: "예약" }

/** 서버 시각(UTC, 시간대 표기가 빠질 수 있음) → Date. */
export function parseServerTime(s: string | null | undefined): Date | null {
  if (!s) return null
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`)
  return Number.isNaN(d.getTime()) ? null : d
}

export function fmtTime(s: string | null | undefined): string {
  const d = parseServerTime(s)
  return d ? d.toLocaleString() : "-"
}

/** 예약 주기 선택지(분) — 백엔드 허용 범위 15분 ~ 7일 */
export const SCHEDULE_INTERVALS = [15, 30, 60, 180, 360, 720, 1440, 10080] as const

/** 분 → "15분" / "3시간" / "1일" / "7일" / "90분" */
export function intervalLabel(minutes: number | null | undefined): string {
  if (!minutes) return "-"
  if (minutes % 1440 === 0) return `${minutes / 1440}일`
  if (minutes % 60 === 0) return `${minutes / 60}시간`
  return `${minutes}분`
}

/** 초 → "1:02:03" / "12:04" */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return "-"
  const s = Math.floor(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, "0")
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`
}

/** 실행이 걸린(또는 지금까지 걸린) 초. */
export function elapsedSeconds(run: SyncRun, now: number = Date.now()): number | null {
  const start = parseServerTime(run.started_at) ?? parseServerTime(run.queued_at)
  if (!start) return null
  const end = parseServerTime(run.finished_at)?.getTime() ?? now
  return Math.max(0, (end - start.getTime()) / 1000)
}

export function percent(run: SyncRun): number | null {
  if (!run.progress_total) return null
  return Math.min(100, Math.round((run.progress_done / run.progress_total) * 100))
}

/**
 * 남은 시간(초) — 테이블 수집 단계의 평균 처리 속도로 추정. 처리한 테이블이 10개 미만이면 null.
 */
export function etaSeconds(run: SyncRun, now: number = Date.now()): number | null {
  if (run.status !== "RUNNING" || run.phase !== "COLLECTING" || !run.progress_total) return null
  if (run.progress_done < 10) return null
  const elapsed = elapsedSeconds(run, now)
  if (!elapsed) return null
  const rate = run.progress_done / elapsed
  if (rate <= 0) return null
  return Math.max(0, (run.progress_total - run.progress_done) / rate)
}

/** 저장된 범위 → 사람이 읽는 요약. 일부 테이블은 "sales (테이블 3)", 제외 패턴은 끝에 "· 제외 tmp_*". */
export function scopeSummary(scope: SyncScope | null, label: string): string {
  if (!scope) return "범위 없음"
  const ex = (scope.exclude ?? []).map((e) => (e.path?.length ? `${e.path.join(".")}:${e.table_pattern}` : e.table_pattern))
  const exText = ex.length ? ` · 제외 ${ex.slice(0, 3).join(", ")}${ex.length > 3 ? ` 외 ${ex.length - 3}개` : ""}` : ""
  if (scope.include_all) return `전체 ${label}${exText}`
  const names = scope.include.map((i) => (i.tables?.length ? `${i.path.join(".")} (테이블 ${i.tables.length})` : i.path.join(".")))
  const head = names.slice(0, 3).join(", ")
  return (names.length > 3 ? `${head} 외 ${names.length - 3}개` : head) + exText
}

/** 범위 → 입력란 문자열(컨테이너 전체만, 한 줄에 하나, 점 표기). 일부 테이블 항목은 화면 트리에서 다룬다. */
export function scopeToText(scope: SyncScope | null): string {
  return scope && !scope.include_all
    ? scope.include.filter((i) => !i.tables?.length).map((i) => i.path.join(".")).join("\n")
    : ""
}

/** 입력란 → 범위(컨테이너 전체 항목). 빈 줄·중복은 버린다. */
export function textToScope(text: string): SyncScope {
  const seen = new Set<string>()
  const include: SyncScopeItem[] = []
  for (const line of text.split(/[\n,]/)) {
    const v = line.trim()
    if (!v || seen.has(v)) continue
    seen.add(v)
    include.push({ path: v.split(".").map((p) => p.trim()).filter(Boolean) })
  }
  return { include_all: false, include: include.filter((i) => i.path.length > 0) }
}

/** 제외 패턴 → 배지 입력 값(["tmp_*", "analytics:*_bak"] — 콜론 앞은 그 컨테이너에서만, TODO-143). */
export function excludesToItems(exclude: SyncScopeExclude[] | undefined): string[] {
  return (exclude ?? []).map((e) => (e.path?.length ? `${e.path.join(".")}:${e.table_pattern}` : e.table_pattern))
}

/** 제외 패턴 → 한 줄 요약("tmp_*, analytics:*_bak"). */
export function excludesToText(exclude: SyncScopeExclude[] | undefined): string {
  return excludesToItems(exclude).join(", ")
}

/** 입력란 → 제외 패턴. 쉼표·줄바꿈으로 나눈다(예전 입력 · 시험용 — 화면은 itemsToExcludes). */
export function textToExcludes(text: string): SyncScopeExclude[] {
  return itemsToExcludes(text.split(/[\n,]/))
}

/** 배지 입력 값 → 제외 패턴. 빈 값·중복은 버린다. */
export function itemsToExcludes(items: readonly string[]): SyncScopeExclude[] {
  const out: SyncScopeExclude[] = []
  const seen = new Set<string>()
  for (const raw of items) {
    const v = raw.trim()
    if (!v || seen.has(v)) continue
    seen.add(v)
    const i = v.indexOf(":")
    if (i > 0) {
      const path = v.slice(0, i).split(".").map((p) => p.trim()).filter(Boolean)
      const pattern = v.slice(i + 1).trim()
      if (pattern) out.push(path.length ? { path, table_pattern: pattern } : { table_pattern: pattern })
    } else {
      out.push({ table_pattern: v })
    }
  }
  return out
}

/** glob(`*`·`?`) → 정규식(대소문자 무시, 전체 일치). */
function globRegExp(pattern: string): RegExp {
  const body = pattern.toLowerCase().replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".")
  return new RegExp(`^${body}$`)
}

/**
 * 테이블이 제외 패턴에 걸리는지 — 상대 이름 또는 마지막 부분(테이블 이름)과 비교, 대소문자 무시.
 * 백엔드 `scope_excludes`·metadata-sync `ScopeRules.excluded` 와 같은 규칙(경로를 준 패턴은 그 컨테이너에서만).
 */
export function isExcluded(exclude: SyncScopeExclude[], container: string[], rel: string): boolean {
  const key = container.join(".")
  const name = rel.toLowerCase()
  const last = name.split(".").pop() ?? name
  return exclude.some((e) => {
    if (e.path?.length && e.path.join(".") !== key) return false
    const re = globRegExp(e.table_pattern)
    return re.test(name) || re.test(last)
  })
}

/** CSV 한 칸 — 따옴표·쉼표·줄바꿈이 있으면 감싼다. 수식으로 해석될 수 있는 시작 문자는 ' 를 붙인다. */
export function csvCell(v: string | number | null | undefined): string {
  let s = v == null ? "" : String(v)
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}
