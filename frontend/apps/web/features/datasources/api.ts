import { authFetch, throwOnError } from "@/features/auth/auth-fetch"
import type { Datasource } from "@/features/datasets/data/schema"
import { errorMessage } from "@/lib/notify"

const BASE = "/api/v1/catalog"

// 데이터 소스 응답에 소속 시스템 정보가 추가됨
export type DatasourceRow = Datasource & { system_id?: number | null; system_name?: string | null }

export type ConnectionTestResult = { ok: boolean; message: string; latency_ms?: number; unsupported?: boolean }

export async function fetchDatasources(): Promise<DatasourceRow[]> {
  const res = await authFetch(`${BASE}/datasources`)
  if (!res.ok) throw new Error(`Failed: ${res.status}`)
  return res.json()
}

export async function createDatasource(payload: {
  name: string
  type: string
  origin: string
}): Promise<Datasource> {
  const res = await authFetch(`${BASE}/datasources`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "데이터 소스를 생성하지 못했습니다.")
  return res.json()
}

export async function updateDatasource(id: number, payload: { name?: string; origin?: string }): Promise<Datasource> {
  const res = await authFetch(`${BASE}/datasources/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "데이터 소스를 수정하지 못했습니다.")
  return res.json()
}

export async function deleteDatasource(id: number, force = false): Promise<void> {
  const res = await authFetch(`${BASE}/datasources/${id}?force=${force}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "데이터 소스를 삭제하지 못했습니다.")
}

export type DeleteImpact = {
  dataset_count: number
  removed_dataset_count: number
  external_lineage_count: number
  alert_rule_count: number
  change_request_count: number
  model_count: number
  mcp_deployment_count: number
  federation_allowlisted: boolean
}
export const EMPTY_IMPACT: DeleteImpact = {
  dataset_count: 0,
  removed_dataset_count: 0,
  external_lineage_count: 0,
  alert_rule_count: 0,
  change_request_count: 0,
  model_count: 0,
  mcp_deployment_count: 0,
  federation_allowlisted: false,
}

export async function fetchDeleteImpact(id: number): Promise<DeleteImpact> {
  const res = await authFetch(`${BASE}/datasources/${id}/delete-impact`)
  if (!res.ok) await throwOnError(res, "삭제 영향을 불러오지 못했습니다.")
  return { ...EMPTY_IMPACT, ...(await res.json()) }
}

/**
 * 연결 테스트. 기존 데이터 소스면 datasourceId 를 넘겨, 비워 둔(마스킹된) 비밀번호를
 * 서버가 저장값으로 채워 테스트하게 한다. 네트워크 오류도 실패 결과로 돌려준다.
 */
export async function testDatasourceConnection(
  type: string,
  config: Record<string, unknown>,
  datasourceId?: number,
): Promise<ConnectionTestResult> {
  try {
    const res = await authFetch(`${BASE}/datasources/test-connection`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, config, datasource_id: datasourceId ?? null }),
    })
    if (!res.ok) await throwOnError(res, "연결을 테스트하지 못했습니다.")
    return await res.json()
  } catch (e) {
    return { ok: false, message: errorMessage(e) ?? String(e) }
  }
}

export type StoredConfig = {
  config: Record<string, unknown>
  /** 저장된 비밀값 키 — config 에는 빈 문자열로 마스킹되어 온다 */
  secretFields: string[]
}

export async function fetchDatasourceConfig(id: number): Promise<StoredConfig | null> {
  const res = await authFetch(`${BASE}/datasources/${id}/configuration`)
  if (res.status === 404) return null
  if (!res.ok) await throwOnError(res, "연결 설정을 불러오지 못했습니다.")
  const data = await res.json()
  return { config: data.config ?? {}, secretFields: data.secret_fields ?? [] }
}

export async function saveDatasourceConfig(id: number, config: Record<string, unknown>): Promise<void> {
  // 비밀값을 빈 문자열로 보내면 서버가 저장된 값을 유지한다(마스킹 왕복 안전).
  const res = await authFetch(`${BASE}/datasources/${id}/configuration`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config }),
  })
  if (!res.ok) await throwOnError(res, "연결 설정을 저장하지 못했습니다.")
}
