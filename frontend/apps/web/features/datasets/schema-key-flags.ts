// 스키마 편집 그리드의 키 플래그 자동 계산.
// - 고유(is_unique): 단일 컬럼 PK 면 PK 가 곧 유일성 근거라 true 로 채운다. 복합 PK 의 일부 컬럼은
//   단독으로 유일하지 않으므로 채우지 않는다 — ERD 가 is_unique 로 1:1 을 판정하므로 오판 방지
//   (백엔드 동기화 sync.py 와 같은 기준).
// - 인덱스(is_indexed): PK 또는 고유면 true.

type KeyFlags = { is_primary_key: string; is_unique: string; is_indexed: string }

export function applyKeyFlags<T extends KeyFlags>(rows: T[]): T[] {
  const pkCount = rows.filter((r) => r.is_primary_key === "true").length
  return rows.map((r) => {
    const row = { ...r }
    if (row.is_primary_key === "true" && pkCount === 1) row.is_unique = "true"
    row.is_indexed = row.is_primary_key === "true" || row.is_unique === "true" ? "true" : "false"
    return row
  })
}
