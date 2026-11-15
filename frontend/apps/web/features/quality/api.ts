// 품질 진단 실행 API (관리 > 데이터 품질) — Trino 로 돌리는 진단의 대기열·진행·결과.
// 실행은 별도 서비스인 품질 워커(extensions/argus-catalog-quality-worker)가 한다.
import { authFetch, throwOnError } from "@/features/auth/auth-fetch"

const BASE = "/api/v1/quality"

export type QualityRunStatus = "QUEUED" | "RUNNING" | "SUCCEEDED" | "PARTIAL" | "FAILED" | "CANCELLED"

export type QualityRun = {
  id: number
  dataset_id: number
  dataset_name: string | null
  /** 진단 대상 데이터셋의 원본 데이터 소스 */
  datasource_id: number | null
  datasource_name: string | null
  engine: string
  /** CHECK: 규칙 진단, PROFILE: 프로파일(컬럼 통계 수집) */
  kind?: string
  engine_datasource_id: number | null
  engine_datasource_name: string | null
  engine_table: string | null
  trigger_type: string
  /** 이 실행을 넣은 작업 실행과 그 작업 (직접 요청한 실행은 null) */
  job_run_id: number | null
  job_id: number | null
  job_name: string | null
  /** 측정한 품질 지표 키 — 빈 목록이면 전체 */
  indicators: string[]
  /** 고른 품질 지표(미리 정의한 지표)의 ID 와 이름 — 비어 있으면 가리지 않는다 */
  definition_ids: number[]
  definitions: string[]
  status: QualityRunStatus
  /** 실행 중인데 워커 보고가 2분 넘게 없다 */
  unresponsive: boolean
  current_target: string | null
  progress_done: number
  progress_total: number | null
  rules_total: number
  rules_evaluated: number
  checked_count: number | null
  error_count: number | null
  /** 오류율(%) = 오류 건수 / 진단 건수 */
  error_rate: number | null
  score: number | null
  query_ids: string[]
  error_message: string | null
  requested_by: string | null
  cancel_requested_by: string | null
  worker_id: string | null
  queued_at: string | null
  started_at: string | null
  finished_at: string | null
  heartbeat_at: string | null
}

export type QualityWorker = {
  worker_id: string
  hostname: string | null
  version: string | null
  started_at: string | null
  heartbeat_at: string | null
  current_run_id: number | null
  alive: boolean
}

export type QualityServiceStatus = {
  workers_alive: number
  running: number
  queued: number
  workers: QualityWorker[]
}

export const ACTIVE_QUALITY_STATUSES: ReadonlySet<string> = new Set(["QUEUED", "RUNNING"])

export async function fetchQualityRuns(
  filters: {
    status?: string; datasourceId?: number; since?: string; cursor?: number; limit?: number
    /** 이 작업 실행이 넣은 실행만 */
    jobRunId?: number
  } = {},
): Promise<QualityRun[]> {
  const qs = new URLSearchParams()
  if (filters.status) qs.set("status", filters.status)
  if (filters.datasourceId) qs.set("datasource_id", String(filters.datasourceId))
  if (filters.since) qs.set("since", filters.since)
  if (filters.cursor) qs.set("cursor", String(filters.cursor))
  if (filters.jobRunId) qs.set("job_run_id", String(filters.jobRunId))
  qs.set("limit", String(filters.limit ?? 50))
  const res = await authFetch(`${BASE}/runs?${qs}`)
  if (!res.ok) await throwOnError(res, "진단 실행 목록을 불러오지 못했습니다.")
  return res.json()
}

export async function fetchQualityRun(runId: number): Promise<QualityRun> {
  const res = await authFetch(`${BASE}/runs/${runId}`)
  if (!res.ok) await throwOnError(res, "진단 실행을 불러오지 못했습니다.")
  return res.json()
}

export async function fetchQualityServiceStatus(): Promise<QualityServiceStatus> {
  const res = await authFetch(`${BASE}/runs/status`)
  if (!res.ok) await throwOnError(res, "품질 워커 상태를 불러오지 못했습니다.")
  return res.json()
}

export async function cancelQualityRun(runId: number): Promise<QualityRun> {
  const res = await authFetch(`${BASE}/runs/${runId}/cancel`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "취소하지 못했습니다.")
  return res.json()
}

export async function retryQualityRun(runId: number): Promise<QualityRun> {
  const res = await authFetch(`${BASE}/runs/${runId}/retry`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "다시 실행하지 못했습니다.")
  return res.json()
}

// ---------------------------------------------------------------------------
// 품질 지표 · 품질 진단 작업 — 대상·품질 지표·주기를 정해 주기적으로 진단한다
// ---------------------------------------------------------------------------

export type QualityIndicator = {
  key: string
  name: string
  /** 진단 영역 키 (COMPLETENESS / VALIDITY / CONSISTENCY / OTHER) */
  area: string
  area_name: string
  check_types: string[]
  description: string
  /** 같은 유형을 나누는 도메인 — 이 지표로 세려면 규칙이 가져야 하는 값(없으면 유형만으로 정해짐) */
  domain?: string | null
  /** 이 분류로 품질 지표를 미리 정의해 컬럼에 할당할 수 있는가 */
  definable?: boolean
  /** AI-Ready 품질 지표(가이드라인 §2.3) — 키(AI_VALIDITY 등)·이름·세부 지표. 건수처럼 해당 없으면 null */
  ai_ready?: string | null
  ai_ready_name?: string | null
  ai_ready_detail?: string | null
}

export type QualityJobTargetType = "catalog" | "schema" | "dataset"

export type QualityJobTarget = {
  type: QualityJobTargetType
  catalog_id?: number | null
  schema_name?: string | null
  dataset_id?: number | null
  /** 응답에서만 — 화면에 보일 이름 */
  label?: string | null
}

export type QualityScheduleType = "NONE" | "HOURLY" | "DAILY" | "WEEKLY" | "MONTHLY"

export type QualityJobRunStatus = "RUNNING" | "SUCCEEDED" | "PARTIAL" | "FAILED" | "CANCELLED" | "SKIPPED"

export type QualityJobRun = {
  id: number
  job_id: number
  job_name: string | null
  trigger_type: string
  status: QualityJobRunStatus
  indicators: string[]
  /** 고른 품질 지표(미리 정의한 지표)의 ID 와 이름 — 비어 있으면 가리지 않는다 */
  definition_ids: number[]
  definitions: string[]
  /** 대상 데이터셋 수 */
  targets_total: number
  /** 이 작업 실행이 넣은 데이터셋 실행에서 모은 값 — 실행이 하나뿐이면 그 실행과 데이터셋도 준다 */
  run_id: number | null
  dataset_id: number | null
  dataset_name: string | null
  engine_table: string | null
  rules_total: number
  rules_evaluated: number
  progress_done: number
  progress_total: number | null
  current_target: string | null
  /** 데이터셋 실행의 품질 점수 평균 */
  score: number | null
  /** 대기열에 넣은 데이터셋 실행 수 */
  runs_total: number
  runs_succeeded: number
  runs_failed: number
  runs_active: number
  /** 함께 넣은 프로파일 실행(작업의 include_profile) — 진단 실행 수(runs_*)에는 들지 않는다 */
  profiles_total?: number
  profiles_succeeded?: number
  profiles_failed?: number
  /** 고른 지표의 규칙이 없어 진단하지 않은 데이터셋 수 */
  no_rules: number
  /** 진단을 시작하지 못한 데이터셋 수 */
  skipped_total: number
  skipped: { dataset_id: number; name: string | null; reason: string }[]
  checked_count: number | null
  error_count: number | null
  error_rate: number | null
  message: string | null
  requested_by: string | null
  cancel_requested_by: string | null
  started_at: string | null
  finished_at: string | null
}

export type QualityJobSchedule = {
  schedule_type: QualityScheduleType
  /** HH:MM (DAILY·WEEKLY·MONTHLY) */
  schedule_time: string | null
  /** WEEKLY 0(월)~6(일), MONTHLY 1~31 */
  schedule_day: number | null
  schedule_interval_hours: number | null
  timezone: string
}

export type QualityJob = QualityJobSchedule & {
  id: number
  name: string
  description: string | null
  targets: QualityJobTarget[]
  /** 빈 목록이면 전체 */
  indicators: string[]
  /** 고른 품질 지표(미리 정의한 지표)의 ID 와 이름 — 비어 있으면 가리지 않는다 */
  definition_ids: number[]
  definitions: string[]
  enabled: boolean
  /** 회차마다 대상 데이터셋의 프로파일(컬럼 통계)도 함께 수집한다 */
  include_profile?: boolean
  next_run_at: string | null
  last_run: QualityJobRun | null
  created_by: string | null
  updated_by: string | null
  created_at: string | null
  updated_at: string | null
}

export type QualityJobWrite = QualityJobSchedule & {
  name: string
  description: string | null
  targets: QualityJobTarget[]
  /** 측정할 분류 키 — 화면은 이제 품질 지표로 고르므로 비워 보낸다 */
  indicators: string[]
  /** 측정할 품질 지표 ID — 비우면 전체(지표 없이 직접 만든 규칙 포함) */
  definition_ids: number[]
  enabled: boolean
  /** 회차마다 프로파일(컬럼 통계)도 함께 수집 */
  include_profile: boolean
}

export type QualityJobPreview = { datasets_total: number; datasets_with_rules: number }

export type QualitySchemaOption = { schema_name: string; datasets: number }

const JSON_HEADERS = { "Content-Type": "application/json" }

export async function fetchQualityIndicators(): Promise<QualityIndicator[]> {
  const res = await authFetch(`${BASE}/indicators`)
  if (!res.ok) await throwOnError(res, "진단 기준을 불러오지 못했습니다.")
  return res.json()
}

export async function fetchQualityJobs(): Promise<QualityJob[]> {
  const res = await authFetch(`${BASE}/jobs`)
  if (!res.ok) await throwOnError(res, "품질 진단 작업 목록을 불러오지 못했습니다.")
  return res.json()
}

export async function saveQualityJob(jobId: number | null, payload: QualityJobWrite): Promise<QualityJob> {
  const res = await authFetch(jobId == null ? `${BASE}/jobs` : `${BASE}/jobs/${jobId}`, {
    method: jobId == null ? "POST" : "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "품질 진단 작업을 저장하지 못했습니다.")
  return res.json()
}

export async function deleteQualityJob(jobId: number): Promise<void> {
  const res = await authFetch(`${BASE}/jobs/${jobId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "품질 진단 작업을 삭제하지 못했습니다.")
}

export async function runQualityJob(jobId: number): Promise<QualityJobRun> {
  const res = await authFetch(`${BASE}/jobs/${jobId}/run`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "품질 진단 작업을 실행하지 못했습니다.")
  return res.json()
}

export async function cancelQualityJobRun(jobRunId: number): Promise<QualityJobRun> {
  const res = await authFetch(`${BASE}/job-runs/${jobRunId}/cancel`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "작업 실행을 취소하지 못했습니다.")
  return res.json()
}

/** 대상 데이터셋에 있는 규칙 하나 — 지표가 실제로 무엇을 보는지(컬럼·기대값). */
export type QualityJobRuleItem = {
  rule_id: number
  rule_name: string
  dataset_id: number
  dataset_name: string | null
  check_type: string
  column_name: string | null
  /** 날짜 형식·허용 값·범위·정규식·조건 등 사용자가 정한 기준 */
  expected_value: string | null
  /** 할당된 품질 지표(없으면 직접 정한 규칙) */
  definition_id?: number | null
}

export type QualityJobIndicatorRules = {
  indicator: string
  /** 품질 지표로 묶었을 때 그 지표 ID — 지표 없이 직접 만든 규칙의 묶음은 null */
  definition_id: number | null
  rules_total: number
  /** 이 지표의 규칙이 있는 데이터셋 수 */
  datasets: number
  /** 앞에서부터 일부만 — truncated 면 rules_total 보다 적다 */
  rules: QualityJobRuleItem[]
  truncated: boolean
}

/** 이 대상의 데이터셋이 지표마다 가진 활성 규칙 — 규칙이 있는 지표만 온다. */
export async function fetchQualityJobRules(
  targets: QualityJobTarget[], groupBy: "indicator" | "definition" = "indicator",
): Promise<QualityJobIndicatorRules[]> {
  const res = await authFetch(`${BASE}/jobs/rules`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ targets, group_by: groupBy }),
  })
  if (!res.ok) await throwOnError(res, "대상의 규칙을 불러오지 못했습니다.")
  return res.json()
}

export async function previewQualityJob(
  targets: QualityJobTarget[], definitionIds: number[],
): Promise<QualityJobPreview> {
  const res = await authFetch(`${BASE}/jobs/preview`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ targets, definition_ids: definitionIds }),
  })
  if (!res.ok) await throwOnError(res, "대상을 확인하지 못했습니다.")
  return res.json()
}

export async function fetchQualityJobSchemas(catalogId: number): Promise<QualitySchemaOption[]> {
  const res = await authFetch(`${BASE}/jobs/schemas?catalog_id=${catalogId}`)
  if (!res.ok) await throwOnError(res, "스키마 목록을 불러오지 못했습니다.")
  return res.json()
}

// ---------------------------------------------------------------------------
// 작업 이력 — 작업 실행 → 데이터셋 실행 → 규칙 결과
// ---------------------------------------------------------------------------

/** 진단 실행 하나가 남긴 규칙 결과 1건 */
export type QualityRuleResult = {
  id: number | null
  rule_id: number
  rule_name: string | null
  check_type: string | null
  column_name: string | null
  dataset_id: number
  /** "true" / "false" */
  passed: string | null
  actual_value: string | null
  detail: string | null
  severity: string | null
  checked_count: number | null
  error_count: number | null
  checked_at: string | null
  /** 규칙 유형이 속한 품질 지표 키 */
  indicator: string | null
  run_id: number | null
}

/** 진단 실행이 낸 쿼리 하나 — SQL 과, 순서로 짝지은 Trino 쿼리 ID(원천 직접 진단은 없음). */
export type QualityRunQuery = {
  key: string
  label: string
  sql: string
  /** 같은 쿼리를 식마다 줄바꿈하고 규칙 이름을 주석으로 단 것 — 화면에는 이것을 보여 준다 */
  formatted_sql: string
  query_id: string | null
  /** 묶은 스캔 쿼리가 실패해 규칙별로 다시 낸 쿼리 */
  retry: boolean
}

export async function fetchQualityRunQueries(runId: number): Promise<QualityRunQuery[]> {
  const res = await authFetch(`${BASE}/runs/${runId}/queries`)
  if (!res.ok) await throwOnError(res, "진단 쿼리를 불러오지 못했습니다.")
  return res.json()
}

export async function fetchQualityRunResults(runId: number): Promise<QualityRuleResult[]> {
  const res = await authFetch(`${BASE}/runs/${runId}/results`)
  if (!res.ok) await throwOnError(res, "규칙별 결과를 불러오지 못했습니다.")
  return res.json()
}

export async function fetchQualityJobRuns(
  filters: { jobId?: number; cursor?: number; limit?: number } = {},
): Promise<QualityJobRun[]> {
  const qs = new URLSearchParams()
  if (filters.jobId) qs.set("job_id", String(filters.jobId))
  if (filters.cursor) qs.set("cursor", String(filters.cursor))
  qs.set("limit", String(filters.limit ?? 30))
  const res = await authFetch(`${BASE}/job-runs?${qs}`)
  if (!res.ok) await throwOnError(res, "작업 이력을 불러오지 못했습니다.")
  return res.json()
}

let indicatorsOnce: Promise<QualityIndicator[]> | null = null

/** 품질 지표 목록 — 바뀌지 않는 값이라 한 번만 읽는다(실패하면 다음에 다시 시도). */
export function loadQualityIndicators(): Promise<QualityIndicator[]> {
  indicatorsOnce ??= fetchQualityIndicators().catch((e) => {
    indicatorsOnce = null
    throw e
  })
  return indicatorsOnce
}

// ---------------------------------------------------------------------------
// 리포트 — 작업 실행 요약(영역·지표별, 데이터셋별), 회차별 추이, 엑셀
// ---------------------------------------------------------------------------

export type QualityReportGroup = {
  key: string
  name: string
  area: string | null
  area_name: string | null
  /** 진단 기준(AI-Ready 묶음)일 때 — AI-Ready 세부 지표(예: 형식 유효성) */
  ai_ready_detail?: string | null
  rules: number
  failed_rules: number
  checked_count: number | null
  error_count: number | null
  error_rate: number | null
}

export type QualityReportDataset = {
  dataset_id: number
  dataset_name: string | null
  datasource_name: string | null
  engine_table: string | null
  run_id: number
  status: QualityRunStatus
  rules: number
  failed_rules: number
  checked_count: number | null
  error_count: number | null
  error_rate: number | null
  error_message: string | null
}

/** 작업 실행에서 평가한 규칙 하나의 결과 */
export type QualityReportRule = {
  dataset_id: number
  dataset_name: string | null
  run_id: number | null
  rule_id: number
  rule_name: string | null
  check_type: string | null
  column_name: string | null
  /** 분류(관리지침 진단 기준) */
  indicator: string | null
  indicator_name: string | null
  /** 할당된 품질 지표 — 직접 만든 규칙은 null */
  definition_name: string | null
  severity: string | null
  passed: boolean
  checked_count: number | null
  error_count: number | null
  error_rate: number | null
  detail: string | null
}

/** 같은 작업의 직전 회차(건너뛴 차례·실행 중 제외) — 증감 표시에 쓴다 */
export type QualityReportPrevious = {
  job_run_id: number
  started_at: string | null
  status: QualityJobRunStatus
  score: number | null
  checked_count: number | null
  error_count: number | null
  error_rate: number | null
  rules_total: number
  failed_rules: number
}

export type QualityJobRunReport = {
  job_run: QualityJobRun
  areas: QualityReportGroup[]
  indicators: QualityReportGroup[]
  /** 같은 결과를 AI-Ready 품질 지표(완전성 · 일관성 · 정확성 · 적시성 · 유효성 · 유일성)로 묶은 합계 */
  ai_ready_areas?: QualityReportGroup[]
  /** 진단 기준별 — area·area_name 은 AI-Ready 지표, ai_ready_detail 은 세부 지표 */
  ai_ready_indicators?: QualityReportGroup[]
  /** 오류율 높은 순 */
  datasets: QualityReportDataset[]
  rules: QualityReportRule[]
  rules_total: number
  failed_rules: number
  /** 진단은 했지만 결과가 지금은 남아 있지 않은 규칙 수(진단 뒤 규칙을 지운 경우) — 요약과 표가 이만큼 다르다 */
  rules_missing: number
  previous: QualityReportPrevious | null
}

export type QualityJobTrendPoint = {
  job_run_id: number
  started_at: string | null
  status: QualityJobRunStatus
  runs_total: number
  runs_succeeded: number
  runs_failed: number
  checked_count: number | null
  error_count: number | null
  error_rate: number | null
  /** 지표 키 → 오류율(%) */
  indicators: Record<string, number | null>
}

export type QualityJobTrend = { job_id: number; job_name: string; points: QualityJobTrendPoint[] }

export async function fetchQualityJobRunReport(jobRunId: number): Promise<QualityJobRunReport> {
  const res = await authFetch(`${BASE}/job-runs/${jobRunId}/report`)
  if (!res.ok) await throwOnError(res, "리포트를 불러오지 못했습니다.")
  return res.json()
}

export async function fetchQualityJobTrend(jobId: number, limit = 30): Promise<QualityJobTrend> {
  const res = await authFetch(`${BASE}/jobs/${jobId}/trend?limit=${limit}`)
  if (!res.ok) await throwOnError(res, "추이를 불러오지 못했습니다.")
  return res.json()
}

/** 작업 실행 리포트 엑셀(요약 / 지표별 / 데이터셋별 / 규칙별)을 받아 온다. */
export async function fetchQualityJobRunReportXlsx(jobRunId: number): Promise<Blob> {
  const res = await authFetch(`${BASE}/job-runs/${jobRunId}/report.xlsx`)
  if (!res.ok) await throwOnError(res, "리포트를 내려받지 못했습니다.")
  return res.blob()
}

/** 작업 실행 리포트 PDF(핵심 숫자 · 실행 정보 · 영역별 · 진단 기준별 · 데이터셋별 · 규칙별)를 받아 온다. */
export async function fetchQualityJobRunReportPdf(jobRunId: number): Promise<Blob> {
  const res = await authFetch(`${BASE}/job-runs/${jobRunId}/report.pdf`)
  if (!res.ok) await throwOnError(res, "리포트를 내려받지 못했습니다.")
  return res.blob()
}

// ---------------------------------------------------------------------------
// 품질 지표 — 미리 정해 두고 컬럼에 할당하는 기준. 관리지침의 진단 기준(QualityIndicator, 정의의 category)이 지표를 묶는다.
// ---------------------------------------------------------------------------

export type QualityDefinition = {
  id: number
  name: string
  /** 분류 키 — QualityIndicator.key */
  category: string
  category_name: string
  area: string
  area_name: string
  check_type: string
  expected_value: string | null
  threshold: number
  severity: string
  description: string | null
  /** 할당된 컬럼(규칙) 수와 그 컬럼들이 있는 데이터셋 수 */
  rules: number
  datasets: number
  created_at: string
  updated_at: string
}

export type QualityDefinitionWrite = {
  name: string
  category: string
  check_type: string
  expected_value: string | null
  threshold: number
  severity: string
  description: string | null
}

export type QualityAssignment = {
  rule_id: number
  dataset_id: number
  dataset_name: string | null
  /** 이름이 같은 데이터셋을 가르는 원본 데이터 소스 */
  datasource_name: string | null
  column_name: string | null
  threshold: number
  is_active: string
}

export type QualityColumnCandidate = {
  dataset_id: number
  dataset_name: string
  /** 이름이 같은 데이터셋을 가르는 원본 데이터 소스 */
  datasource_name: string | null
  column_name: string
  field_type: string | null
  /** 컬럼 이름·제약으로 보아 이 지표가 맞을 법한 컬럼 */
  suggested: boolean
  assigned: boolean
}

export type QualityAssignResult = {
  created: number
  /** 같은 기준으로 직접 만들어 둔 규칙을 지표에 연결한 수 */
  adopted: number
  skipped: { dataset_id: number; column_name: string; reason: string }[]
}

export type QualitySqlPreviewRequest = {
  check_type: string
  expected_value?: string | null
  column_name?: string | null
  threshold?: number
  dataset_id?: number | null
}

export type QualitySqlPreview = {
  /** 실행할 Trino 쿼리 — 만들 수 없으면 null(사유는 note) */
  sql: string | null
  judgement: string
  note: string | null
  table: string
  /** 실제 Trino 테이블 이름인가(아니면 예시 이름) */
  actual_table: boolean
}

/** 데이터셋의 품질 규칙 가운데 컬럼-지표 매핑에 쓰는 부분 */
export type QualityDatasetRule = {
  id: number
  rule_name: string
  check_type: string
  column_name: string | null
  is_active: string
  definition_id: number | null
  definition_name: string | null
}

export async function fetchQualityDatasetRules(datasetId: number): Promise<QualityDatasetRule[]> {
  const res = await authFetch(`${BASE}/rules?dataset_id=${datasetId}`)
  if (!res.ok) await throwOnError(res, "데이터셋의 품질 규칙을 불러오지 못했습니다.")
  return res.json()
}

/** 이 데이터셋을 정밀 진단할 수 있는지와, 안 되면 그 이유. engine 은 경로 — TRINO(Trino 연결) 또는 DIRECT(원천 직접) */
export type QualityEngineStatus = {
  available: boolean; reason: string | null; engine: string
  engine_datasource_name: string | null; engine_table: string | null
}

export async function fetchQualityEngineStatus(datasetId: number): Promise<QualityEngineStatus> {
  const res = await authFetch(`${BASE}/datasets/${datasetId}/engine`)
  if (!res.ok) await throwOnError(res, "정밀 진단 경로를 불러오지 못했습니다.")
  return res.json()
}

export async function fetchQualityDefinitions(): Promise<QualityDefinition[]> {
  const res = await authFetch(`${BASE}/definitions`)
  if (!res.ok) await throwOnError(res, "품질 지표를 불러오지 못했습니다.")
  return res.json()
}

export async function saveQualityDefinition(
  id: number | null, payload: QualityDefinitionWrite,
): Promise<QualityDefinition> {
  const res = await authFetch(id == null ? `${BASE}/definitions` : `${BASE}/definitions/${id}`, {
    method: id == null ? "POST" : "PUT", headers: JSON_HEADERS, body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "품질 지표를 저장하지 못했습니다.")
  return res.json()
}

/** 지표를 지운다 — 할당된 규칙은 직접 정한 규칙으로 남고 그 수를 돌려준다. */
export async function deleteQualityDefinition(id: number): Promise<{ kept_rules: number }> {
  const res = await authFetch(`${BASE}/definitions/${id}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "품질 지표를 삭제하지 못했습니다.")
  return res.json()
}

/** 기본 지표 가운데 같은 이름이 없는 것을 넣는다. */
export async function addDefaultQualityDefinitions(): Promise<{ added: number }> {
  const res = await authFetch(`${BASE}/definitions/defaults`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "기본 지표를 추가하지 못했습니다.")
  return res.json()
}

export async function fetchQualityAssignments(id: number): Promise<QualityAssignment[]> {
  const res = await authFetch(`${BASE}/definitions/${id}/assignments`)
  if (!res.ok) await throwOnError(res, "할당된 컬럼을 불러오지 못했습니다.")
  return res.json()
}

/** 할당할 컬럼 후보 — 검색어가 없으면 컬럼 이름·제약으로 추천한 것만. */
export async function fetchQualityCandidates(id: number, q = ""): Promise<QualityColumnCandidate[]> {
  const qs = new URLSearchParams()
  if (q.trim()) qs.set("q", q.trim())
  const res = await authFetch(`${BASE}/definitions/${id}/candidates?${qs}`)
  if (!res.ok) await throwOnError(res, "컬럼을 불러오지 못했습니다.")
  return res.json()
}

export async function assignQualityDefinition(
  id: number, columns: { dataset_id: number; column_name: string }[],
): Promise<QualityAssignResult> {
  const res = await authFetch(`${BASE}/definitions/${id}/assign`, {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ columns }),
  })
  if (!res.ok) await throwOnError(res, "지표를 할당하지 못했습니다.")
  return res.json()
}

/** 할당 해제 — 그 컬럼의 규칙과 결과를 지운다. */
export async function unassignQualityDefinition(id: number, ruleIds: number[]): Promise<{ removed: number }> {
  const res = await authFetch(`${BASE}/definitions/${id}/unassign`, {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ rule_ids: ruleIds }),
  })
  if (!res.ok) await throwOnError(res, "할당을 해제하지 못했습니다.")
  return res.json()
}

/** 이 유형·기준값으로 진단할 때 실행하는 Trino 쿼리. 실행하지는 않는다. */
export async function previewQualitySql(req: QualitySqlPreviewRequest): Promise<QualitySqlPreview> {
  const res = await authFetch(`${BASE}/sql-preview`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(req) })
  if (!res.ok) await throwOnError(res, "진단 쿼리를 만들지 못했습니다.")
  return res.json()
}
