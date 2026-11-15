import { describe, expect, it } from "vitest"
import { createRequestGate } from "./request-gate"

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

/** local-filesystem-browser 의 fetchData 와 같은 규칙으로 상태를 바꾸는 축소판. */
function makeBrowser() {
  const gate = createRequestGate()
  const state = { items: [] as string[], error: null as string | null, loading: false }
  async function fetchData(req: Promise<string[]>) {
    const isLatest = gate.begin()
    state.loading = true
    state.error = null
    try {
      const items = await req
      if (!isLatest()) return
      state.items = items
      state.error = null
    } catch (e) {
      if (!isLatest()) return
      state.error = String(e)
      state.items = []
    } finally {
      if (isLatest()) state.loading = false
    }
  }
  return { state, fetchData }
}

describe("createRequestGate", () => {
  it("나중에 시작한 요청만 최신", () => {
    const gate = createRequestGate()
    const a = gate.begin()
    const b = gate.begin()
    expect(a()).toBe(false)
    expect(b()).toBe(true)
  })

  it("늦게 온 이전 요청의 오류가 성공한 목록 위에 배너로 남지 않는다 (DEF-008, IT 12 OCI-T09)", async () => {
    const { state, fetchData } = makeBrowser()
    const outage = deferred<string[]>()
    const retry = deferred<string[]>()
    const first = fetchData(outage.promise) // 장애 중 보낸 요청
    const second = fetchData(retry.promise) // 복구 후 "다시 시도"
    retry.resolve(["a.txt"])
    await second
    outage.reject(new Error("HTTP 503"))
    await first
    expect(state).toEqual({ items: ["a.txt"], error: null, loading: false })
  })

  it("폴더를 빠르게 옮기면 이전 폴더 목록이 늦게 와도 덮어쓰지 않는다", async () => {
    const { state, fetchData } = makeBrowser()
    const oldDir = deferred<string[]>()
    const newDir = deferred<string[]>()
    const first = fetchData(oldDir.promise)
    const second = fetchData(newDir.promise)
    newDir.resolve(["new"])
    await second
    oldDir.resolve(["old"])
    await first
    expect(state.items).toEqual(["new"])
  })

  it("최신 요청이 끝나기 전에는 로딩 상태를 유지한다", async () => {
    const { state, fetchData } = makeBrowser()
    const oldReq = deferred<string[]>()
    const newReq = deferred<string[]>()
    const first = fetchData(oldReq.promise)
    void fetchData(newReq.promise)
    oldReq.resolve(["old"])
    await first
    expect(state.loading).toBe(true)
    newReq.resolve([])
  })
})
