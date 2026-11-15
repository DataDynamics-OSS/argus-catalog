// 차트 공통 색 · 축 · 격자 — 모든 차트는 Recharts(MIT)로 그린다.
// (Highcharts 는 상용 라이선스라 걷어냄 — TODO-100)
//
// 계열 색은 순서대로 쓰고 순위에 따라 바꾸지 않는다. 같은 지표는 어느 화면에서나 같은 색.
// 이 순서는 dataviz 검증 스크립트를 통과한 기준 팔레트다(인접 쌍 색각이상 ΔE ≥ 8, 일반 시야 ≥ 15).
// 한 차트에 넣는 범주는 6개까지 — 넘으면 나머지는 "기타"(회색)로 묶는다.

export const SERIES = [
  "#2a78d6", // 1 파랑
  "#eb6834", // 2 주황
  "#1baf7a", // 3 청록
  "#eda100", // 4 노랑
  "#e87ba4", // 5 분홍
  "#008300", // 6 초록
] as const

/** "기타" · 알 수 없음 — 의미 없는 묶음은 회색 */
export const OTHER = "#a1a1aa"

/** 범주 i 의 색 — 6번째를 넘으면 회색(새 색을 만들지 않는다) */
export const seriesColor = (i: number): string => SERIES[i] ?? OTHER

/** 축 눈금 글자 */
export const AXIS_TICK = { fontSize: 11, fill: "var(--muted-foreground)" } as const

/** 격자 — 옅은 실선, 가로선만(점선은 기준선처럼 읽혀 쓰지 않는다) */
export const GRID = { stroke: "var(--border)", vertical: false } as const

/** 막대 끝 둥글기 — 세로 막대(위쪽만) · 가로 막대(오른쪽만) */
export const BAR_RADIUS_TOP: [number, number, number, number] = [4, 4, 0, 0]
export const BAR_RADIUS_END: [number, number, number, number] = [0, 4, 4, 0]

/** 마우스를 올린 범주의 배경 */
export const HOVER_CURSOR = { fill: "var(--muted)", opacity: 0.6 } as const
