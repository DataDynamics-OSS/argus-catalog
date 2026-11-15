/**
 * 누를 수 있는 카드의 공통 hover 표시 — 테두리를 primary 색으로 바꾸고 그림자를 띄운다.
 *
 * 공통 `Card` 는 테두리를 `border` 가 아니라 `ring-1` 로 그리므로 `hover:border-*` 는 보이지 않는다.
 * 그래서 `Card` 에는 ring 색을 바꾸는 `interactiveCardClass`, 일반 `border` 를 쓰는 상자에는
 * `interactiveBoxClass` 를 쓴다. API 카탈로그 카드와 같은 모양이다.
 */
export const interactiveCardClass =
  "transition-[box-shadow,background-color] hover:shadow-md hover:ring-primary/40"

export const interactiveBoxClass =
  "transition-[box-shadow,border-color,background-color] hover:shadow-md hover:border-primary/40"
