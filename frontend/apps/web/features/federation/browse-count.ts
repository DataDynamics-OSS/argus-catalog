// 탐색 트리의 인스턴스 건수(DEF-177) — 필터를 걸면 아래 데이터 소스 건수처럼 필터 결과 수를 보인다.

/**
 * 인스턴스 노드에 보일 데이터셋 수.
 * - 필터가 없으면 통계의 미러 데이터셋 수(펼치기 전에도 보인다).
 * - 필터가 있으면 그 필터로 조회한 결과 수. 아직 응답이 없으면(조회 중 · 실패) `null` — 필터 전 건수를 그대로 두지 않는다.
 */
export function instanceTreeCount(
  mirrorDatasets: number,
  appliedQ: string,
  filteredTotal: number | null | undefined
): number | null {
  if (!appliedQ) return mirrorDatasets
  return filteredTotal ?? null
}
