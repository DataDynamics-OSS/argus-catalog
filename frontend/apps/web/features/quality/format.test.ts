import { describe, expect, it } from "vitest"

import {
  describeChange,
  describeIndicators,
  describeMeasured,
  describeSchedule,
  describeTargets,
  engineLabel,
  errorRateOf,
  failedFirst,
  fmtErrorRate,
  qualityElapsedSeconds,
  qualityPercent,
  rateBarPercent,
  targetKey,
  scheduleProblem,
} from "@/features/quality/format"
import { qualityJobRunsHref, qualityReportHref, qualityRunsHref } from "@/features/quality/links"

describe("데이터 품질 화면 표시 값", () => {
  it("진행률 — 전체 쿼리 수를 모르면 null, 넘치면 100", () => {
    expect(qualityPercent({ progress_done: 0, progress_total: null })).toBeNull()
    expect(qualityPercent({ progress_done: 1, progress_total: 3 })).toBe(33)
    expect(qualityPercent({ progress_done: 5, progress_total: 3 })).toBe(100)
  })

  it("소요 시간 — 시작 전이면 null, 끝났으면 종료 시각까지", () => {
    expect(qualityElapsedSeconds({ started_at: null, finished_at: null })).toBeNull()
    expect(qualityElapsedSeconds({ started_at: "2026-11-14T00:00:00Z", finished_at: "2026-11-14T00:01:05Z" })).toBe(65)
    const now = Date.parse("2026-11-14T00:00:10Z")
    expect(qualityElapsedSeconds({ started_at: "2026-11-14T00:00:00", finished_at: null }, now)).toBe(10)
  })

  it("정밀 진단 경로 이름 — 모르는 값은 그대로", () => {
    expect(engineLabel("TRINO")).toBe("Trino")
    expect(engineLabel("DIRECT")).toBe("원천 직접")
    expect(engineLabel("SPARK")).toBe("SPARK")
    expect(engineLabel(null)).toBe("-")
  })

  it("오류율", () => {
    expect(fmtErrorRate(null)).toBe("-")
    expect(fmtErrorRate(0)).toBe("0%")
    expect(fmtErrorRate(16.3513)).toBe("16.35%")
    expect(fmtErrorRate(0.0042)).toBe("0.0042%")
  })

  it("데이터 품질 화면 링크 — 기본 조건은 주소에 넣지 않는다", () => {
    expect(qualityRunsHref()).toBe("/dashboard/quality-runs")
    expect(qualityRunsHref({ datasource: "7", status: "PROBLEM" })).toBe("/dashboard/quality-runs?status=PROBLEM&datasource=7")
  })
})

describe("품질 진단 작업 표시 값", () => {
  const base = { schedule_time: "06:00", schedule_day: null, schedule_interval_hours: null, timezone: "Asia/Seoul" }

  it("주기를 한 줄로", () => {
    expect(describeSchedule({ ...base, schedule_type: "DAILY" })).toBe("매일 06:00")
    expect(describeSchedule({ ...base, schedule_type: "WEEKLY", schedule_day: 0 })).toBe("매주 월요일 06:00")
    expect(describeSchedule({ ...base, schedule_type: "WEEKLY", schedule_day: 6 })).toBe("매주 일요일 06:00")
    expect(describeSchedule({ ...base, schedule_type: "MONTHLY", schedule_day: 31 })).toBe("매월 31일 06:00")
    expect(describeSchedule({ ...base, schedule_type: "HOURLY", schedule_time: null, schedule_interval_hours: 6 })).toBe("6시간마다")
    expect(describeSchedule({ ...base, schedule_type: "HOURLY", schedule_time: null, schedule_interval_hours: 1 })).toBe("매시간")
    expect(describeSchedule({ ...base, schedule_type: "NONE", schedule_time: null })).toBe("수동 실행")
  })

  it("대상 — 앞의 두 개와 나머지 개수", () => {
    expect(describeTargets([])).toBe("대상 없음")
    expect(describeTargets([{ type: "catalog", catalog_id: 1, label: "sales" }])).toBe("sales")
    expect(describeTargets([
      { type: "catalog", catalog_id: 1, label: "sales" },
      { type: "schema", catalog_id: 2, schema_name: "public", label: "crm.public" },
      { type: "dataset", dataset_id: 9, label: "orders" },
    ])).toBe("sales, crm.public 외 1개")
    expect(describeTargets([{ type: "dataset", dataset_id: 9 }])).toBe("#9")
  })

  it("지표 — 빈 목록은 전체, 모르는 키는 그대로", () => {
    const indicators = [
      { key: "REQUIRED", name: "필수값", area: "COMPLETENESS", area_name: "완결성", check_types: ["NOT_NULL"], description: "" },
    ]
    expect(describeIndicators([], indicators)).toBe("전체")
    expect(describeIndicators(["REQUIRED", "GONE"], indicators)).toBe("필수값, GONE")
    // 품질 지표를 골랐으면 그 이름, 예전처럼 분류로 골랐으면 "분류: …", 아무것도 안 골랐으면 전체
    expect(describeMeasured({ definitions: ["여부 Y/N", "우편번호"], indicators: [] }, indicators)).toBe("여부 Y/N, 우편번호")
    expect(describeMeasured({ definitions: [], indicators: ["REQUIRED"] }, indicators)).toBe("진단 기준: 필수값")
    expect(describeMeasured({ indicators: [] }, indicators)).toBe("전체")
  })

  it("대상 키 — 종류마다 다른 키", () => {
    expect(targetKey({ type: "catalog", catalog_id: 1 })).toBe("catalog:1")
    expect(targetKey({ type: "schema", catalog_id: 1, schema_name: "public" })).toBe("schema:1:public")
    expect(targetKey({ type: "dataset", dataset_id: 1 })).toBe("dataset:1")
  })
})

describe("작업 이력", () => {
  it("건수로 오류율을 구한다", () => {
    expect(errorRateOf(null, 3)).toBeNull()
    expect(errorRateOf(0, 0)).toBeNull()
    expect(errorRateOf(200, 6)).toBe(3)
    expect(errorRateOf(3, 1)).toBe(33.3333)
    expect(errorRateOf(100, null)).toBe(0)
  })

  it("작업 이력 주소", () => {
    expect(qualityJobRunsHref()).toBe("/dashboard/quality-runs?tab=history")
    expect(qualityJobRunsHref(7)).toBe("/dashboard/quality-runs?tab=history&job=7")
  })
})

describe("리포트", () => {
  it("막대 길이 — 가장 큰 값이 100, 작은 값도 보이게", () => {
    expect(rateBarPercent(null, 10)).toBe(0)
    expect(rateBarPercent(0, 10)).toBe(0)
    expect(rateBarPercent(5, 0)).toBe(0)
    expect(rateBarPercent(10, 10)).toBe(100)
    expect(rateBarPercent(5, 10)).toBe(50)
    expect(rateBarPercent(0.01, 10)).toBe(2)
  })

  it("리포트 주소", () => {
    expect(qualityReportHref()).toBe("/dashboard/quality-runs?tab=report")
    expect(qualityReportHref(3)).toBe("/dashboard/quality-runs?tab=report&job=3")
    expect(qualityReportHref(3, 9)).toBe("/dashboard/quality-runs?tab=report&job=3&run=9")
    expect(qualityReportHref(null, 9)).toBe("/dashboard/quality-runs?tab=report")
  })
})

describe("리포트 — 직전 회차 대비", () => {
  it("줄어야 좋은 값(오류율·오류 건수)은 늘면 나빠짐", () => {
    expect(describeChange(1.5, 1.38, { unit: "%p", digits: 2 })).toEqual({ text: "▲ 0.12%p", trend: "worse" })
    expect(describeChange(7, 1210, { unit: "건" })).toEqual({ text: "▼ 1,203건", trend: "better" })
  })

  it("품질 점수는 오르면 좋아짐", () => {
    expect(describeChange(98.5, 97, { unit: "점", lowerIsBetter: false, digits: 1 })).toEqual({ text: "▲ 1.5점", trend: "better" })
    expect(describeChange(90, 97, { unit: "점", lowerIsBetter: false, digits: 1 })?.trend).toBe("worse")
  })

  it("차이가 표시 자릿수 아래면 변동 없음, 한쪽이 없으면 null", () => {
    expect(describeChange(1.001, 1.002, { digits: 2 })).toEqual({ text: "변동 없음", trend: "same" })
    expect(describeChange(0, 0)).toEqual({ text: "변동 없음", trend: "same" })
    expect(describeChange(null, 3)).toBeNull()
    expect(describeChange(3, undefined)).toBeNull()
  })

  it("규칙은 실패한 것부터, 같은 결과끼리는 순서 유지", () => {
    const rules = [{ id: 1, passed: true }, { id: 2, passed: false }, { id: 3, passed: true }, { id: 4, passed: false }]
    expect(failedFirst(rules).map((r) => r.id)).toEqual([2, 4, 1, 3])
  })
})

describe("scheduleProblem", () => {
  const base = { type: "DAILY", time: "02:00", monthDay: "1", intervalHours: "6" }
  it("서버가 받는 값이면 문제가 없다", () => {
    expect(scheduleProblem(base)).toBeNull()
    expect(scheduleProblem({ ...base, type: "NONE", time: "" })).toBeNull()
    expect(scheduleProblem({ ...base, type: "HOURLY", time: "", intervalHours: "168" })).toBeNull()
    expect(scheduleProblem({ ...base, type: "MONTHLY", monthDay: "31" })).toBeNull()
  })
  it("빈 시각 · 범위 밖 날짜 · 소수 간격을 걸러 낸다", () => {
    expect(scheduleProblem({ ...base, time: "" })).toContain("실행 시각")
    expect(scheduleProblem({ ...base, type: "WEEKLY", time: "25:00" })).toContain("실행 시각")
    expect(scheduleProblem({ ...base, type: "MONTHLY", monthDay: "0" })).toContain("날짜")
    expect(scheduleProblem({ ...base, type: "MONTHLY", monthDay: "" })).toContain("날짜")
    expect(scheduleProblem({ ...base, type: "HOURLY", intervalHours: "1.5" })).toContain("간격")
    expect(scheduleProblem({ ...base, type: "HOURLY", intervalHours: "200" })).toContain("간격")
  })
})
