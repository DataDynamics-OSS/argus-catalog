import { Badge } from "@workspace/ui/components/badge"

/** 동기화된 데이터셋 표시 — 상태가 아니라 속성이라 무채색 테두리(TODO-107). 예전에는 주황 테두리 · 글자였다. */
export function SyncedBadge() {
  return (
    <Badge variant="outline"  title="원천에서 동기화(수집)된 데이터셋입니다">
      동기화됨
    </Badge>
  )
}
