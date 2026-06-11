/**
 * 조직 · 시스템 · 토폴로지 API 클라이언트.
 *
 * 사이드바 데이터 소스 메뉴의 조직(트리) → 시스템 → 데이터 소스 계층을 다룬다.
 * 백엔드: /api/v1/catalog/{topology,organizations,systems,datasources/{id}/system}
 */

const BASE = "/api/v1/catalog"

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { detail?: string }).detail || `요청 실패: ${res.status}`)
  }
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// 타입
// ---------------------------------------------------------------------------

export interface Organization {
  id: number
  code: string | null
  name: string
  parent_id: number | null
  description: string | null
  sort_order: number
  created_at: string
  updated_at: string
}

export interface System {
  id: number
  code: string | null
  name: string
  org_id: number | null
  summary: string | null
  description: string | null
  owner: string | null
  status: string
  sort_order: number
  created_at: string
  updated_at: string
}

export interface TopologyDatasource {
  id: number
  name: string
  type: string
  origin: string
  dataset_count: number
}

export interface TopologySystem {
  id: number
  name: string
  status: string
  owner: string | null
  org_id: number | null
  summary: string | null
  description: string | null
  datasources: TopologyDatasource[]
}

export interface TopologyOrganization {
  id: number
  name: string
  parent_id: number | null
  children: TopologyOrganization[]
  systems: TopologySystem[]
}

export interface Topology {
  organizations: TopologyOrganization[]
  unassigned: { datasources: TopologyDatasource[]; systems: TopologySystem[] }
}

// ---------------------------------------------------------------------------
// 토폴로지
// ---------------------------------------------------------------------------

export async function fetchTopology(): Promise<Topology> {
  return jsonOrThrow(await fetch(`${BASE}/topology`))
}

// ---------------------------------------------------------------------------
// 조직 (Organization)
// ---------------------------------------------------------------------------

export async function createOrganization(payload: {
  name: string
  parent_id?: number | null
  description?: string
}): Promise<Organization> {
  return jsonOrThrow(
    await fetch(`${BASE}/organizations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  )
}

export async function updateOrganization(
  id: number,
  payload: { name?: string; parent_id?: number | null; description?: string },
): Promise<Organization> {
  return jsonOrThrow(
    await fetch(`${BASE}/organizations/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  )
}

export async function deleteOrganization(id: number): Promise<void> {
  const res = await fetch(`${BASE}/organizations/${id}`, { method: "DELETE" })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { detail?: string }).detail || `요청 실패: ${res.status}`)
  }
}

// ---------------------------------------------------------------------------
// 시스템 (System)
// ---------------------------------------------------------------------------

export async function createSystem(payload: {
  name: string
  org_id?: number | null
  summary?: string
  description?: string
  owner?: string
  status?: string
}): Promise<System> {
  return jsonOrThrow(
    await fetch(`${BASE}/systems`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  )
}

export async function updateSystem(
  id: number,
  payload: {
    name?: string
    org_id?: number | null
    summary?: string
    description?: string
    owner?: string
    status?: string
  },
): Promise<System> {
  return jsonOrThrow(
    await fetch(`${BASE}/systems/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  )
}

export async function deleteSystem(id: number, force = false): Promise<void> {
  const res = await fetch(`${BASE}/systems/${id}?force=${force}`, { method: "DELETE" })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { detail?: string }).detail || `요청 실패: ${res.status}`)
  }
}

// ---------------------------------------------------------------------------
// 데이터 소스 배정
// ---------------------------------------------------------------------------

export async function assignDatasourceSystem(
  datasourceId: number,
  systemId: number | null,
): Promise<void> {
  const res = await fetch(`${BASE}/datasources/${datasourceId}/system`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ system_id: systemId }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { detail?: string }).detail || `요청 실패: ${res.status}`)
  }
}
