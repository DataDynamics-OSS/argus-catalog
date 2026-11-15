import { Badge } from "@workspace/ui/components/badge"

const MATCH_LABEL: Record<string, string> = { hybrid: "혼합", semantic: "의미", keyword: "키워드" }

/** 검색 일치 방식 — 분류라 무채색 테두리, 이름은 한글 · 원래 값은 툴팁(TODO-107). 통합 검색 · 데이터셋 의미 검색 공용. */
export function MatchTypeBadge({ type }: { type: string }) {
  return (
    <Badge variant="outline" title={type}>
      {MATCH_LABEL[type] ?? MATCH_LABEL.keyword}
    </Badge>
  )
}
