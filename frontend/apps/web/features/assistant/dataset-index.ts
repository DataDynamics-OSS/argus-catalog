/**
 * AI 어시스턴트 — 도구 결과에서 데이터셋 이름·id 를 모아 답변 속 이름을 상세 링크로 바꾼다 (TODO-066).
 * 화면 컴포넌트(tool-cards·chat-panel)와 단위 테스트가 함께 쓰는 순수 함수.
 */

import type { ChatTurn, ToolCall } from "./use-assistant-stream"

export const datasetHref = (id: number | string) => `/dashboard/datasets/${id}`

export type Obj = Record<string, unknown>
export const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v)
export const arr = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter(isObj) : [])
export const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null)
export const str = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v))

/** 도구 결과 → 데이터셋 이름·id 쌍 — 답변 속 이름을 상세 링크로 바꾸는 데 쓴다(TODO-066). */
export function datasetRefs(call: ToolCall): { id: number; name: string }[] {
  const r = call.result
  if (!isObj(r)) return []
  const out: { id: number; name: string }[] = []
  const push = (id: unknown, name: unknown) => {
    const i = num(id)
    const n = str(name).trim()
    if (i !== null && n) out.push({ id: i, name: n })
  }
  switch (call.name) {
    case "search_datasets":
      for (const d of arr(r.datasets)) push(d.id, d.name)
      break
    case "get_dataset_detail":
      push(r.id, r.name)
      break
    case "get_lineage":
      if (isObj(r.dataset)) push(r.dataset.id, r.dataset.name)
      for (const d of [...arr(r.upstream), ...arr(r.downstream)]) push(d.id, d.name)
      break
    case "get_erd":
      for (const t of arr(r.tables)) push(t.dataset_id, t.name)
      break
  }
  return out
}

/**
 * 이번 대화에서 도구로 확인한 데이터셋 이름 → id (TODO-066).
 * 답변의 인라인 코드가 이름과 같거나(대소문자 무시) `스키마.이름` 처럼 끝 부분이 이름이면 상세 링크로 바꾼다.
 * 끝 부분 매칭은 그 이름의 데이터셋이 하나일 때만 — 다른 스키마의 같은 이름을 잘못 잇지 않게.
 */
export type DatasetIndex = { exact: Map<string, number>; last: Map<string, number> }
export function buildDatasetIndex(turns: ChatTurn[]): DatasetIndex {
  const exact = new Map<string, number>()
  const lastIds = new Map<string, Set<number>>()
  for (const t of turns) {
    if (t.kind !== "assistant") continue
    for (const call of t.tool_calls ?? []) {
      for (const { id, name } of datasetRefs(call)) {
        const key = name.toLowerCase()
        exact.set(key, id)
        const tail = key.split(".").pop()!
        lastIds.set(tail, (lastIds.get(tail) ?? new Set()).add(id))
      }
    }
  }
  const last = new Map<string, number>()
  for (const [k, ids] of lastIds) if (ids.size === 1) last.set(k, [...ids][0]!)
  return { exact, last }
}

export function lookupDataset(index: DatasetIndex | null, text: string): number | undefined {
  if (!index) return undefined
  const key = text.trim().toLowerCase()
  if (!key || /\s/.test(key)) return undefined
  return index.exact.get(key) ?? (key.includes(".") ? index.last.get(key.split(".").pop()!) : index.last.get(key))
}
