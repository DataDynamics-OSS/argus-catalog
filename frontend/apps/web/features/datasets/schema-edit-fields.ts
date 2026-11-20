// 스키마 편집 그리드 행의 텍스트 칸 정리 (DEF-DSET-001).
// AG Grid 텍스트 편집기는 칸을 비우면 값을 null 로 넣는다. 저장 쪽은 문자열을 기대하므로(trim)
// 그리드가 값을 바꿀 때와 저장할 때 null · undefined 를 빈 문자열로 맞춘다 — 빈 값은 저장에서 "지움"으로 보낸다(DEF-166).

export const SCHEMA_TEXT_FIELDS = [
  "field_path",
  "display_name",
  "field_type",
  "native_type",
  "description",
  "unit",
  "calculation",
] as const

type TextField = (typeof SCHEMA_TEXT_FIELDS)[number]

export function normalizeSchemaRow<T extends Partial<Record<TextField, unknown>>>(
  row: T,
): Omit<T, TextField> & Record<TextField, string> {
  const out: Record<string, unknown> = { ...row }
  for (const k of SCHEMA_TEXT_FIELDS) {
    const v = out[k]
    out[k] = v == null ? "" : String(v)
  }
  return out as Omit<T, TextField> & Record<TextField, string>
}
