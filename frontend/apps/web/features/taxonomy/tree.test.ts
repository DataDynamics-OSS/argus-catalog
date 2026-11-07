import { describe, expect, it } from "vitest"

import type { TreeCategory } from "./api"
import { countCategories } from "./tree"

const node = (id: number, children: TreeCategory[] = [], parent_id: number | null = null): TreeCategory => ({
  id, name: `c${id}`, parent_id, dataset_count: 0, children,
})

describe("countCategories", () => {
  it("빈 트리는 0", () => {
    expect(countCategories([])).toBe(0)
  })

  it("루트만이 아니라 모든 단계의 분류를 센다 (분류체계 삭제 409 메시지의 수와 일치)", () => {
    // it-영업(it-고객, it-주문) + it-재무회계 → 서버 count_taxonomy_categories = 4
    const tree = [node(1, [node(2, [], 1), node(3, [], 1)]), node(4)]
    expect(countCategories(tree)).toBe(4)
  })

  it("3단 이상 중첩도 센다", () => {
    expect(countCategories([node(1, [node(2, [node(3, [], 2)], 1)])])).toBe(3)
  })
})
