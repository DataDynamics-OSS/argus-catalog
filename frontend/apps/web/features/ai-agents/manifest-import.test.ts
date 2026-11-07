import { describe, expect, it } from "vitest"

import { csvTemplate, csvToDocs, mergeResults, rowToDoc } from "./manifest-import"

describe("rowToDoc", () => {
  it("기본 항목을 정의 문서 구조로 옮기고 빈 칸은 뺀다", () => {
    const doc = rowToDoc({
      name: " cs.refund ", display_name: "", owner: "a@x", department: "CS", category: "cs",
      status: "active", framework: "langgraph", base_model: "gpt-4o", endpoint: "", tags: "환불; 결제;",
    })
    expect(doc).toEqual({
      apiVersion: "argus.catalog/v1",
      kind: "Agent",
      metadata: { name: "cs.refund", owner: "a@x", department: "CS", category: "cs", tags: ["환불", "결제"] },
      spec: { status: "active", model: { name: "gpt-4o" }, runtime: { framework: "langgraph" } },
    })
  })

  it("필수 값이 비면 undefined 로 두어 서버가 그 행만 실패 처리하게 한다", () => {
    const doc = rowToDoc({ name: "x" }) as { metadata: Record<string, unknown> }
    expect(doc.metadata.owner).toBeUndefined()
  })
})

describe("csvToDocs", () => {
  it("BOM·머리글 공백을 처리하고 빈 줄은 건너뛴다", () => {
    const { docs, error } = csvToDocs("\uFEFFname, owner,department,category\na.b,o@x,D,C\n\n")
    expect(error).toBeUndefined()
    expect(docs).toHaveLength(1)
    expect((docs[0] as { metadata: { name: string } }).metadata.name).toBe("a.b")
  })

  it("필수 열이 없으면 오류 문구를 돌려준다", () => {
    expect(csvToDocs("name,owner\na,b").error).toBe("필수 열 없음(department, category)")
  })

  it("양식 파일은 그대로 1행으로 해석된다", () => {
    const { docs, error } = csvToDocs(csvTemplate())
    expect(error).toBeUndefined()
    expect(docs).toHaveLength(1)
  })
})

describe("mergeResults", () => {
  it("요약을 더하고 결과를 이어 붙인다", () => {
    const part = (n: string, created: number) => ({
      dry_run: true,
      summary: { created, updated: 0, unchanged: 1, failed: 0 },
      results: [{ name: n, action: "created" as const }],
    })
    const m = mergeResults([part("a", 1), part("b", 2)], true)
    expect(m.summary).toEqual({ created: 3, updated: 0, unchanged: 2, failed: 0 })
    expect(m.results.map((r) => r.name)).toEqual(["a", "b"])
  })
})
