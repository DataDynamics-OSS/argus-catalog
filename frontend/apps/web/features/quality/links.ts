// 데이터 품질 화면으로 가는 링크. 목록 조건(상태·데이터 소스·기간)의 주소 형식은 메타데이터 수집과 같아
// 그 변환 함수(parseSyncRunsParams·syncRunsQuery)를 그대로 쓴다.
import { syncRunsQuery, type SyncRunsState } from "@/features/datasources/links"

export const QUALITY_RUNS_PATH = "/dashboard/quality-runs"

/** 조건을 건 데이터 품질 주소. 예: `qualityRunsHref({ datasource: "3" })` — 그 데이터 소스의 진단 이력. */
export function qualityRunsHref(state: Partial<SyncRunsState> = {}): string {
  const q = syncRunsQuery(state)
  return q ? `${QUALITY_RUNS_PATH}?${q}` : QUALITY_RUNS_PATH
}

/** 작업 이력 탭 주소. 작업 ID 를 주면 그 작업의 이력만. */
export function qualityJobRunsHref(jobId?: number | null): string {
  return jobId ? `${QUALITY_RUNS_PATH}?tab=history&job=${jobId}` : `${QUALITY_RUNS_PATH}?tab=history`
}

/** 리포트 탭 주소. 작업과 작업 실행을 주면 그 리포트가 열린다. */
export function qualityReportHref(jobId?: number | null, jobRunId?: number | null): string {
  const qs = new URLSearchParams({ tab: "report" })
  if (jobId) qs.set("job", String(jobId))
  if (jobId && jobRunId) qs.set("run", String(jobRunId))
  return `${QUALITY_RUNS_PATH}?${qs}`
}

/** 데이터 품질 > 품질 지표 탭 */
export function qualityDefinitionsHref(): string {
  return "/dashboard/quality-runs?tab=definitions"
}
