import type { TreeCategory } from "./api"

/**
 * 트리의 분류 노드 전체 수(모든 단계 포함).
 * 서버의 분류체계 삭제 거부(409 "하위 분류 N개")는 분류체계 소속 분류 전체를 세므로,
 * 화면 확인 대화상자도 루트만이 아니라 하위까지 모두 센 값을 보여 준다.
 */
export function countCategories(nodes: TreeCategory[]): number {
  return nodes.reduce((n, c) => n + 1 + countCategories(c.children), 0)
}
