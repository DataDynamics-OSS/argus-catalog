import { describe, expect, it } from "vitest"

import { buildDatasetIndex, datasetRefs, lookupDataset } from "./dataset-index"
import { applyEvent, dropsServerAnswer, type ChatTurn } from "./use-assistant-stream"

const answer = (over: Partial<Extract<ChatTurn, { kind: "assistant" }>> = {}): ChatTurn => ({
  kind: "assistant", id: "a1", question: "q", text: "", tool_calls: [], startedAt: Date.now() - 1500, ...over,
})

describe("applyEvent", () => {
  it("도구 호출·결과·토큰·완료를 답변에 쌓는다", () => {
    let t: ChatTurn[] = [{ kind: "user", text: "q" }, answer()]
    t = applyEvent(t, { type: "tool_call", data: { id: "c1", name: "search_datasets", args: { query: "x" } } })
    t = applyEvent(t, { type: "tool_result", data: { id: "c1", name: "search_datasets", result: { datasets: [] } } })
    t = applyEvent(t, { type: "text_delta", data: { text: "답" } })
    t = applyEvent(t, { type: "usage", data: { tokens_in: 10, tokens_out: 3 } })
    t = applyEvent(t, { type: "done", data: {} })
    const last = t[1]!
    expect(last.kind).toBe("assistant")
    if (last.kind !== "assistant") return
    expect(last.tool_calls?.[0]?.result).toEqual({ datasets: [] })
    expect([last.text, last.tokensIn, last.tokensOut, last.done]).toEqual(["답", 10, 3, true])
    expect(last.elapsedMs).toBeGreaterThanOrEqual(1500)
  })

  it("오류는 질문을 남겨 다시 시도할 수 있게 한다", () => {
    const t = applyEvent([{ kind: "user", text: "q" }, answer()], { type: "error", data: { reason: "실패" } })
    expect(t[1]).toEqual({ kind: "error", id: "a1", question: "q", text: "실패" })
  })
})

describe("데이터셋 링크 (TODO-066)", () => {
  const turns: ChatTurn[] = [answer({
    tool_calls: [
      { id: "1", name: "search_datasets", args: {}, result: { datasets: [{ id: 12, name: "rental" }, { id: 7, name: "sakila.staff" }] } },
      { id: "2", name: "get_lineage", args: {}, result: { dataset: { id: 12, name: "rental" }, upstream: [{ id: 3, name: "a.payment" }, { id: 4, name: "b.payment" }], downstream: [] } },
      { id: "3", name: "get_erd", args: {}, result: { tables: [{ dataset_id: 20, name: "inventory" }], relations: [] } },
      { id: "4", name: "get_quality", args: { dataset_id: 12 }, result: { score: 80 } },
    ],
  })]
  const index = buildDatasetIndex(turns)

  it("도구 결과에서 이름·id 를 모은다", () => {
    expect(datasetRefs(turns[0]!.kind === "assistant" ? turns[0]!.tool_calls![1]! : (null as never)))
      .toEqual([{ id: 12, name: "rental" }, { id: 3, name: "a.payment" }, { id: 4, name: "b.payment" }])
  })

  it("이름 그대로·대소문자·스키마 접두를 잇는다", () => {
    expect(lookupDataset(index, "rental")).toBe(12)
    expect(lookupDataset(index, "RENTAL")).toBe(12)
    expect(lookupDataset(index, "sakila.rental")).toBe(12)
    expect(lookupDataset(index, "staff")).toBe(7)           // 끝 부분이 하나뿐이면
    expect(lookupDataset(index, "inventory")).toBe(20)
  })

  it("컬럼 표기·여러 개인 이름·문장은 잇지 않는다", () => {
    expect(lookupDataset(index, "rental.customer_id")).toBeUndefined()
    expect(lookupDataset(index, "payment")).toBeUndefined()  // a.payment·b.payment 둘
    expect(lookupDataset(index, "a.payment")).toBe(3)        // 정확히 쓰면 잇는다
    expect(lookupDataset(index, "select * from rental")).toBeUndefined()
    expect(lookupDataset(null, "rental")).toBeUndefined()
  })
})

describe("dropsServerAnswer", () => {
  it("끝까지 받은 답변만 서버 이력에서 지우게 한다(DEF-076)", () => {
    expect(dropsServerAnswer(answer({ done: true }))).toBe(true)
    expect(dropsServerAnswer(answer())).toBe(false)                                     // 중단(완료 이벤트 없음)
    expect(dropsServerAnswer({ kind: "error", id: "e", question: "q", text: "실패" })).toBe(false)
    expect(dropsServerAnswer({ kind: "user", text: "q" })).toBe(false)
    expect(dropsServerAnswer(undefined)).toBe(false)
  })
})
