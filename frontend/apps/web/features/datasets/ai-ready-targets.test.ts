import { readFileSync } from "node:fs"
import { join } from "node:path"

import { describe, expect, it } from "vitest"

import { AI_READY_TARGETS } from "./ai-ready-targets"
import { datasetHref } from "./links"

describe("AI_READY_TARGETS (TODO-151)", () => {
  // 메타데이터 탭은 줄을 항목 이름(label)으로 찾는다 — 이름이 바뀌면 링크가 스크롤하지 못하므로 여기서 잡는다
  const metaSource = readFileSync(join(__dirname, "components/dataset-metadata-tab.tsx"), "utf8")

  it("메타데이터 항목 이름은 메타데이터 탭의 줄 이름과 같다", () => {
    for (const [key, t] of Object.entries(AI_READY_TARGETS)) {
      if (t.kind !== "tab" || !t.field) continue
      expect(metaSource.includes(`label: "${t.field}"`), `${key} → ${t.field}`).toBe(true)
    }
  })

  it("메타데이터 링크는 탭과 항목을 주소에 담는다", () => {
    const t = AI_READY_TARGETS.C23!
    expect(t.kind).toBe("tab")
    if (t.kind === "tab") {
      expect(datasetHref(3, t.tab, { field: t.field })).toBe(`/dashboard/datasets/3?tab=metadata&field=${encodeURIComponent("라이선스")}`)
    }
    expect(AI_READY_TARGETS.C27).toEqual({ kind: "card", card: "distributions", label: "배포본" })
  })
})
