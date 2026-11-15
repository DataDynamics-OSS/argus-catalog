import { describe, expect, it } from "vitest"

import { flattenOrgs } from "@/features/datasources/utils"
import type { TopologyOrganization } from "@/features/topology/api"
import { SYSTEM_STATUSES, systemStatusLabel } from "@/features/topology/system-status"

const org = (id: number, name: string, children: TopologyOrganization[] = []): TopologyOrganization =>
  ({ id, name, parent_id: null, children, systems: [] })

describe("시스템 상태 이름 (TODO-103)", () => {
  it("세 값 모두 한글 이름이 있다", () => {
    expect(SYSTEM_STATUSES.map(systemStatusLabel)).toEqual(["운영 중", "사용 안 함", "폐기 예정"])
  })
  it("모르는 값은 코드 그대로", () => {
    expect(systemStatusLabel("RETIRED")).toBe("RETIRED")
    expect(systemStatusLabel(null)).toBe("-")
  })
})

describe("조직 선택지 경로 (DEF-108)", () => {
  it("같은 이름의 하위 조직을 상위 경로로 구별한다", () => {
    const flat = flattenOrgs([
      org(1, "경영본부", [org(3, "데이터팀")]),
      org(2, "기술본부", [org(4, "데이터팀", [org(5, "분석파트")])]),
    ])
    expect(flat.map((o) => [o.id, o.depth, o.path])).toEqual([
      [1, 0, "경영본부"],
      [3, 1, "경영본부 › 데이터팀"],
      [2, 0, "기술본부"],
      [4, 1, "기술본부 › 데이터팀"],
      [5, 2, "기술본부 › 데이터팀 › 분석파트"],
    ])
  })
})

describe("조직 합계 (TODO-104)", () => {
  it("하위 조직까지 시스템 · 데이터 소스 · 데이터셋을 합한다", async () => {
    const { orgTotals } = await import("@/features/datasources/utils")
    const ds = (id: number, n: number) => ({ id, name: `d${id}`, type: "mysql", origin: "DEV", dataset_count: n })
    const sys = (id: number, list: ReturnType<typeof ds>[]) =>
      ({ id, name: `s${id}`, status: "ACTIVE", owner: null, org_id: 1, summary: null, description: null, datasources: list })
    const tree: TopologyOrganization = {
      ...org(1, "본부", [{ ...org(2, "팀"), systems: [sys(2, [ds(3, 5)])] }]),
      systems: [sys(1, [ds(1, 10), ds(2, 0)])],
    }
    expect(orgTotals(tree)).toEqual({ systems: 2, datasources: 3, datasets: 15 })
    expect(orgTotals(org(9, "빈 조직"))).toEqual({ systems: 0, datasources: 0, datasets: 0 })
  })
})
