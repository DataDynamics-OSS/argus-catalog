// 데이터셋 카드 보기(TODO-111)의 표시 규칙 — 갱신 경과.

/** 갱신 경과 — "오늘" · "N일 전" · "N개월 전" · "N년 전". ``now`` 는 호출하는 쪽이 준다(렌더 중 시각 읽기 금지). */
export function ageLabel(when: Date | string | null | undefined, now: number): string {
  if (!when) return ""
  const t = (when instanceof Date ? when : new Date(when)).getTime()
  if (Number.isNaN(t)) return ""
  const days = Math.floor((now - t) / 86_400_000)
  if (days < 1) return "오늘"
  if (days < 30) return `${days}일 전`
  if (days < 365) return `${Math.floor(days / 30)}개월 전`
  return `${Math.floor(days / 365)}년 전`
}
