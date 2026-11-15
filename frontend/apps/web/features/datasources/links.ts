// 데이터 소스·메타데이터 수집 화면으로 가는 링크와 메타데이터 수집 목록 조건 ↔ 주소(쿼리스트링) 변환(TODO-074).
//
// - 데이터 소스: `/dashboard/datasources?datasource=<이름|datasource_id|id>` — 그 데이터 소스를 선택해 연다(DEF-054).
// - 메타데이터 수집: `/dashboard/sync-runs?status=&datasource=&period=` — 기본값(전체 상태·최근 24시간)은 주소에 넣지 않는다.
// - 데이터셋: 데이터 소스의 외부 식별자(문자열 datasource_id)로 데이터셋 목록을 거른다.

import { datasetListHref } from "@/features/datasets/links"

export const DATASOURCES_PATH = "/dashboard/datasources"
export const SYNC_RUNS_PATH = "/dashboard/sync-runs"

/** 메타데이터 수집 목록의 "전체" 선택값(상태·데이터 소스). */
export const SYNC_RUNS_ALL = "__all__"

/** 메타데이터 수집 기간 필터 값 — 화면의 기간 선택지와 같다. */
export const SYNC_RUN_PERIODS = ["24h", "7d", "30d", "all"] as const

export type SyncRunsState = {
  /** 상태 필터(ACTIVE·PROBLEM·SUCCEEDED·CANCELLED 등) — 전체면 SYNC_RUNS_ALL */
  status: string
  /** 데이터 소스 id(숫자 문자열) — 전체면 SYNC_RUNS_ALL */
  datasource: string
  period: string
}

/**
 * 기본 조건 — 전체 상태 + 최근 24시간. 지켜보던 실행이 끝나도 목록에서 사라지지 않는다
 * (예전 기본값 "실행 중·대기" 는 끝난 실행을 바로 숨겼다).
 */
export const DEFAULT_SYNC_RUNS_STATE: SyncRunsState = {
  status: SYNC_RUNS_ALL,
  datasource: SYNC_RUNS_ALL,
  period: "24h",
}

/** 주소의 쿼리스트링 → 메타데이터 수집 목록 조건. 잘못된 값은 기본값으로. */
export function parseSyncRunsParams(params: URLSearchParams | { get(name: string): string | null }): SyncRunsState {
  const status = params.get("status")?.trim().toUpperCase() ?? ""
  const datasource = params.get("datasource")?.trim() ?? ""
  const period = params.get("period")?.trim() ?? ""
  return {
    status: /^[A-Z_]+$/.test(status) && !/^_*ALL_*$/.test(status) ? status : DEFAULT_SYNC_RUNS_STATE.status,
    datasource: /^\d+$/.test(datasource) ? datasource : DEFAULT_SYNC_RUNS_STATE.datasource,
    period: (SYNC_RUN_PERIODS as readonly string[]).includes(period) ? period : DEFAULT_SYNC_RUNS_STATE.period,
  }
}

/** 메타데이터 수집 목록 조건 → 쿼리스트링(앞의 `?` 없이). 기본값은 넣지 않는다. */
export function syncRunsQuery(state: Partial<SyncRunsState>): string {
  const s = { ...DEFAULT_SYNC_RUNS_STATE, ...state }
  const q = new URLSearchParams()
  if (s.status !== DEFAULT_SYNC_RUNS_STATE.status) q.set("status", s.status)
  if (s.datasource !== DEFAULT_SYNC_RUNS_STATE.datasource) q.set("datasource", s.datasource)
  if (s.period !== DEFAULT_SYNC_RUNS_STATE.period) q.set("period", s.period)
  return q.toString()
}

/** 조건을 건 메타데이터 수집 주소. 예: `syncRunsHref({ datasource: "3" })` — 그 데이터 소스의 전체 이력. */
export function syncRunsHref(state: Partial<SyncRunsState> = {}): string {
  const q = syncRunsQuery(state)
  return q ? `${SYNC_RUNS_PATH}?${q}` : SYNC_RUNS_PATH
}

/**
 * 데이터 소스 화면에서 그 데이터 소스를 선택해 여는 주소. 외부 식별자(문자열 datasource_id)가 있으면
 * 그것으로, 없으면 내부 id 로 찾는다(데이터 소스 화면은 이름·datasource_id·id 순으로 찾는다).
 */
export function datasourceHref(ds: { id: number; key?: string | null }): string {
  return `${DATASOURCES_PATH}?datasource=${encodeURIComponent(ds.key || String(ds.id))}`
}

/** 이 데이터 소스의 데이터셋 목록 — 데이터셋 목록 필터는 외부 식별자(문자열 datasource_id)를 받는다. */
export function datasourceDatasetsHref(key: string): string {
  return datasetListHref({ datasource: [key] })
}
