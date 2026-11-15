// 품질 진단 작업 등록 화면의 "컬럼 ↔ 품질 지표" 매핑.
// 매핑은 그 데이터셋의 지표 할당(지표를 가리키는 규칙)과 같은 것이다 — 화면에서 고친 매핑과 지금 할당의 차이를 구해
// 더할 것은 할당하고, 뺀 것은 할당을 해제한다.

/** 데이터셋의 규칙 가운데 매핑에 필요한 부분 */
export type MappedRule = { id: number; column_name: string | null; definition_id?: number | null }

/** 컬럼 이름(소문자) → 그 컬럼에 매핑한 품질 지표 ID 들 */
export type ColumnMapping = Map<string, Set<number>>

const keyOf = (column: string) => column.toLowerCase()

/** 지금 할당된 규칙에서 매핑을 만든다. 지표 없이 직접 만든 규칙과 컬럼이 없는 규칙은 매핑이 아니다. */
export function mappingFromRules(rules: MappedRule[]): ColumnMapping {
  const mapping: ColumnMapping = new Map()
  for (const r of rules) {
    if (r.definition_id == null || !r.column_name) continue
    const key = keyOf(r.column_name)
    if (!mapping.has(key)) mapping.set(key, new Set())
    mapping.get(key)!.add(r.definition_id)
  }
  return mapping
}

/** 컬럼에 매핑한 지표 ID(오름차순) */
export function definitionsOf(mapping: ColumnMapping, column: string): number[] {
  return [...(mapping.get(keyOf(column)) ?? [])].sort((a, b) => a - b)
}

/** 컬럼에 지표를 더하거나 뺀 새 매핑 */
export function toggleMapping(mapping: ColumnMapping, column: string, definitionId: number, on: boolean): ColumnMapping {
  const next: ColumnMapping = new Map([...mapping].map(([k, v]) => [k, new Set(v)]))
  const key = keyOf(column)
  const set = next.get(key) ?? new Set<number>()
  if (on) set.add(definitionId)
  else set.delete(definitionId)
  if (set.size > 0) next.set(key, set)
  else next.delete(key)
  return next
}

/** 매핑에 쓰인 지표 ID(겹치지 않게, 오름차순) — 품질 진단 작업이 측정할 품질 지표 */
export function mappedDefinitionIds(mapping: ColumnMapping): number[] {
  return [...new Set([...mapping.values()].flatMap((s) => [...s]))].sort((a, b) => a - b)
}

/** 매핑한 컬럼-지표 조합의 수 */
export function mappingSize(mapping: ColumnMapping): number {
  return [...mapping.values()].reduce((n, s) => n + s.size, 0)
}

export type MappingChanges = {
  /** 새로 할당할 것 — 지표마다 컬럼 이름(원래 대소문자) */
  assign: { definitionId: number; columns: string[] }[]
  /** 할당을 해제할 것 — 지표마다 그 규칙 ID */
  unassign: { definitionId: number; ruleIds: number[] }[]
  added: number
  removed: number
}

/** 지금 할당(rules)과 화면의 매핑의 차이. ``columns`` 는 데이터셋의 컬럼 이름(원래 대소문자). */
export function mappingChanges(rules: MappedRule[], mapping: ColumnMapping, columns: string[]): MappingChanges {
  const current = mappingFromRules(rules)
  const assign = new Map<number, string[]>()
  for (const column of columns) {
    for (const id of definitionsOf(mapping, column)) {
      if (current.get(keyOf(column))?.has(id)) continue
      assign.set(id, [...(assign.get(id) ?? []), column])
    }
  }
  const unassign = new Map<number, number[]>()
  for (const r of rules) {
    if (r.definition_id == null || !r.column_name) continue
    if (mapping.get(keyOf(r.column_name))?.has(r.definition_id)) continue
    unassign.set(r.definition_id, [...(unassign.get(r.definition_id) ?? []), r.id])
  }
  const sorted = <T,>(m: Map<number, T>) => [...m].sort((a, b) => a[0] - b[0])
  return {
    assign: sorted(assign).map(([definitionId, cols]) => ({ definitionId, columns: cols })),
    unassign: sorted(unassign).map(([definitionId, ruleIds]) => ({ definitionId, ruleIds })),
    added: [...assign.values()].reduce((n, c) => n + c.length, 0),
    removed: [...unassign.values()].reduce((n, c) => n + c.length, 0),
  }
}
