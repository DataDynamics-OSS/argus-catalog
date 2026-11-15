import { describe, expect, it } from "vitest"

import { parseDatasetListParams } from "@/features/datasets/links"
import {
  DEFAULT_SYNC_RUNS_STATE,
  datasourceDatasetsHref,
  datasourceHref,
  parseSyncRunsParams,
  syncRunsHref,
  syncRunsQuery,
  SYNC_RUNS_ALL,
} from "@/features/datasources/links"

describe("메타데이터 수집 목록 조건 ↔ 주소", () => {
  it("빈 주소는 기본 조건(전체 상태 + 최근 24시간)", () => {
    expect(parseSyncRunsParams(new URLSearchParams(""))).toEqual(DEFAULT_SYNC_RUNS_STATE)
    expect(DEFAULT_SYNC_RUNS_STATE).toEqual({ status: SYNC_RUNS_ALL, datasource: SYNC_RUNS_ALL, period: "24h" })
  })

  it("상태·데이터 소스·기간을 읽고 잘못된 값은 기본값으로", () => {
    expect(parseSyncRunsParams(new URLSearchParams("status=PROBLEM&datasource=12&period=7d"))).toEqual({
      status: "PROBLEM", datasource: "12", period: "7d",
    })
    expect(parseSyncRunsParams(new URLSearchParams("status=<x>&datasource=abc&period=1y"))).toEqual(DEFAULT_SYNC_RUNS_STATE)
    expect(parseSyncRunsParams(new URLSearchParams("status=all")).status).toBe(SYNC_RUNS_ALL)
  })

  it("기본값은 주소에 넣지 않는다 — 왕복해도 같은 조건", () => {
    expect(syncRunsQuery(DEFAULT_SYNC_RUNS_STATE)).toBe("")
    expect(syncRunsHref()).toBe("/dashboard/sync-runs")
    const state = { status: "ACTIVE", datasource: "3", period: "all" }
    expect(syncRunsQuery(state)).toBe("status=ACTIVE&datasource=3&period=all")
    expect(parseSyncRunsParams(new URLSearchParams(syncRunsQuery(state)))).toEqual(state)
  })

  it("데이터 소스의 전체 이력 링크", () => {
    expect(syncRunsHref({ datasource: "7" })).toBe("/dashboard/sync-runs?datasource=7")
  })
})

describe("데이터 소스·데이터셋 링크", () => {
  it("데이터 소스 화면 — 외부 식별자 우선, 없으면 내부 id", () => {
    expect(datasourceHref({ id: 5, key: "ds-a b" })).toBe("/dashboard/datasources?datasource=ds-a%20b")
    expect(datasourceHref({ id: 5, key: null })).toBe("/dashboard/datasources?datasource=5")
    expect(datasourceHref({ id: 5 })).toBe("/dashboard/datasources?datasource=5")
  })

  it("이 데이터 소스의 데이터셋 — 데이터셋 목록이 같은 필터로 읽는다", () => {
    const href = datasourceDatasetsHref("hive-01")
    expect(href).toBe("/dashboard/datasets?datasource=hive-01")
    const params = new URLSearchParams(href.split("?")[1])
    expect(parseDatasetListParams(params).datasource).toEqual(["hive-01"])
  })
})
