/** 포인터와 툴팁 사이 간격 — Recharts 기본값과 같다 */
export const TOOLTIP_OFFSET = 10

/**
 * 차트 상자 안에서 툴팁의 왼쪽 좌표 — 포인터 오른쪽에 두고, 오른쪽 끝을 넘으면 왼쪽으로 뒤집고,
 * 그래도 넘치면(툴팁이 상자 폭에 가까울 때) 상자 안으로 당긴다.
 * Recharts 는 그림 영역(축 라벨 · 여백 제외) 안에서만 뒤집어, 그림 영역이 툴팁보다 좁은 카드에서는
 * 툴팁이 카드 밖으로 나가 잘린다(DEF-178).
 */
export function tooltipLeft(pointerX: number, tooltipWidth: number, boxWidth: number, offset = TOOLTIP_OFFSET): number {
  const right = pointerX + offset
  if (right + tooltipWidth <= boxWidth) return Math.max(0, right)
  const left = pointerX - offset - tooltipWidth
  if (left >= 0) return left
  return Math.max(0, boxWidth - tooltipWidth)
}
