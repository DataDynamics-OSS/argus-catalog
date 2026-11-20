import { Badge } from "@workspace/ui/components/badge"

import { SOURCE_MAP_STATUS_LABEL } from "@/features/catalogs/api"

const TONE_VARIANT = { ok: "success", warn: "warning", error: "destructive", muted: "muted" } as const

/**
 * 수동 데이터셋의 원천 매핑 상태(TODO-CAT-001) — 목록 source_map_status · 상세 check_status 공용.
 * status 가 없으면(수동 데이터셋이 아님) 아무것도 그리지 않는다.
 */
export function SourceMapBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null
  const s = SOURCE_MAP_STATUS_LABEL[status] ?? { label: status, tone: "muted" as const }
  return <Badge variant={TONE_VARIANT[s.tone]}>{s.label}</Badge>
}
