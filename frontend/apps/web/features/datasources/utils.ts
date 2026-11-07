import { toast } from "@/lib/notify"

import type { ConnectionTestResult } from "@/features/datasources/api"
import type { DatasourceFieldDef } from "@/features/datasources/datasource-configs"
import type { Topology, TopologyOrganization, TopologySystem } from "@/features/topology/api"

// 서버가 연결 테스트를 지원하는 유형(PG/MySQL 프로토콜 호환) — 그 외 유형은 버튼을 숨긴다
export const CONNECTION_TEST_TYPES = new Set(["postgresql", "greenplum", "redshift", "mysql", "mariadb", "starrocks"])

export function toastTestResult(r: ConnectionTestResult) {
  if (r.ok) toast.success(`연결 성공${r.latency_ms != null ? ` (${r.latency_ms}ms)` : ""}`)
  else if (r.unsupported) toast.info(r.message)
  else toast.error(`연결 실패: ${r.message}`)
}

// ---------------------------------------------------------------------------
// Validation helper
// ---------------------------------------------------------------------------

// 접속 "대상"을 정하는 키 — 백엔드 datasource_secrets._TARGET_KEY_RE 와 같은 규칙.
// 이 값이 저장된 설정과 다르면 서버가 저장된 비밀값을 이어 쓰지 않으므로 다시 입력받는다.
const TARGET_KEY_RE = /(^|_)(host|hosts|port|url|uri|endpoint|address|addresses|servers|account)$/i
const SECRET_KEY_RE = /(password|passwd|secret|token|credential|connection_string)/i

export function changedTargetKeys(
  values: Record<string, unknown>,
  saved: Record<string, unknown>,
): string[] {
  const norm = (v: unknown) => (v === undefined || v === null ? "" : String(v).trim().toLowerCase())
  const keys = new Set([...Object.keys(values), ...Object.keys(saved)])
  return [...keys]
    .filter((k) => TARGET_KEY_RE.test(k) && !SECRET_KEY_RE.test(k))
    .filter((k) => norm(values[k]) !== norm(saved[k]))
    .sort()
}

export function hasRequiredFields(
  fields: DatasourceFieldDef[],
  values: Record<string, unknown>,
  storedSecrets: string[] = [],
): boolean {
  const seen = new Set<string>()
  for (const field of fields) {
    if (seen.has(field.key)) continue
    seen.add(field.key)
    if (!field.required) continue

    if (field.showWhen) {
      const allConditions = fields
        .filter((f) => f.key === field.key && f.showWhen)
        .map((f) => f.showWhen!)
      const visible = allConditions.some(
        (cond) => String(values[cond.field] ?? "") === cond.value,
      )
      if (!visible) continue
    }

    if (storedSecrets.includes(field.key)) continue // 비워 두면 저장된 값 유지
    const val = values[field.key]
    if (val === undefined || val === null || String(val).trim() === "") return false
  }
  return true
}

// ---------------------------------------------------------------------------
// Topology flatten / lookup helpers
// ---------------------------------------------------------------------------

// 데이터 소스 상세 폼 변경 여부 — 바뀐 것이 없으면 저장 버튼을 비활성화한다(시스템 상세와 동일)
export function isDatasourceFormDirty(
  form: { name: string; origin: string; systemId: number | null; configDirty: boolean },
  saved: { name: string; origin?: string | null; system_id?: number | null },
): boolean {
  return (
    form.configDirty ||
    form.name !== saved.name ||
    form.origin !== (saved.origin ?? "DEV") ||
    form.systemId !== (saved.system_id ?? null)
  )
}

export interface FlatOrg {
  id: number
  name: string
  depth: number
}
export interface FlatSystem {
  id: number
  name: string
  orgName: string
}

export function flattenOrgs(orgs: TopologyOrganization[], depth = 0, acc: FlatOrg[] = []): FlatOrg[] {
  for (const o of orgs) {
    acc.push({ id: o.id, name: o.name, depth })
    flattenOrgs(o.children, depth + 1, acc)
  }
  return acc
}

export function flattenSystems(orgs: TopologyOrganization[], acc: FlatSystem[] = []): FlatSystem[] {
  for (const o of orgs) {
    for (const s of o.systems) acc.push({ id: s.id, name: s.name, orgName: o.name })
    flattenSystems(o.children, acc)
  }
  return acc
}

export function findOrg(orgs: TopologyOrganization[], id: number): TopologyOrganization | null {
  for (const o of orgs) {
    if (o.id === id) return o
    const f = findOrg(o.children, id)
    if (f) return f
  }
  return null
}

export function findSystem(topology: Topology, id: number): TopologySystem | null {
  const walk = (orgs: TopologyOrganization[]): TopologySystem | null => {
    for (const o of orgs) {
      for (const s of o.systems) if (s.id === id) return s
      const f = walk(o.children)
      if (f) return f
    }
    return null
  }
  return walk(topology.organizations) ?? (topology.unassigned.systems ?? []).find((s) => s.id === id) ?? null
}
