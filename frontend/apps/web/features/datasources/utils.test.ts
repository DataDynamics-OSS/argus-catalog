import { describe, expect, it } from "vitest"
import { isDatasourceFormDirty } from "./utils"

const saved = { name: "it-shop-pg", origin: "DEV", system_id: 3 }
const same = { name: "it-shop-pg", origin: "DEV", systemId: 3, configDirty: false }

describe("isDatasourceFormDirty", () => {
  it("변경이 없으면 false (저장 버튼 비활성)", () => {
    expect(isDatasourceFormDirty(same, saved)).toBe(false)
    // 환경·시스템 미설정은 DEV·미분류로 본다
    expect(isDatasourceFormDirty({ ...same, systemId: null }, { name: "it-shop-pg", origin: null, system_id: null })).toBe(false)
  })
  it("이름·환경·시스템·연결 설정 중 하나라도 바뀌면 true", () => {
    expect(isDatasourceFormDirty({ ...same, name: "x" }, saved)).toBe(true)
    expect(isDatasourceFormDirty({ ...same, origin: "PROD" }, saved)).toBe(true)
    expect(isDatasourceFormDirty({ ...same, systemId: null }, saved)).toBe(true)
    expect(isDatasourceFormDirty({ ...same, configDirty: true }, saved)).toBe(true)
  })
})
