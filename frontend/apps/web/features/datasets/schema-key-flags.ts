// 스키마 편집 그리드의 키 플래그 자동 계산.
// - 고유(is_unique): 단일 컬럼 PK 면 PK 가 곧 유일성 근거라 true 로 채운다. 복합 PK 의 일부 컬럼은
//   단독으로 유일하지 않으므로 채우지 않는다 — ERD 가 is_unique 로 1:1 을 판정하므로 오판 방지
//   (백엔드 동기화 sync.py 와 같은 기준).
// - 인덱스(is_indexed): PK 또는 고유면 true. 원천 인덱스로 수집된 컬럼(index_from_source)은 PK · 고유가 아니어도
//   true 를 유지한다 — 인덱스 열은 편집할 수 없으므로 다시 계산하며 지우면 되돌릴 방법이 없다(DEF-DSET-002).

type KeyFlags = { is_primary_key: string; is_unique: string; is_indexed: string; index_from_source?: boolean }

/** 편집에 들어갈 때의 값으로 "PK · 고유 말고 원천 인덱스 때문에 인덱스인가"를 정한다. */
export function indexFromSource(f: { is_primary_key?: string | null; is_unique?: string | null; is_indexed?: string | null }): boolean {
  return f.is_indexed === "true" && f.is_primary_key !== "true" && f.is_unique !== "true"
}

export function applyKeyFlags<T extends KeyFlags>(rows: T[]): T[] {
  const pkCount = rows.filter((r) => r.is_primary_key === "true").length
  return rows.map((r) => {
    const row = { ...r }
    if (row.is_primary_key === "true" && pkCount === 1) row.is_unique = "true"
    row.is_indexed = row.is_primary_key === "true" || row.is_unique === "true" || row.index_from_source ? "true" : "false"
    return row
  })
}
