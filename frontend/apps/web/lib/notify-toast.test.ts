import { beforeEach, describe, expect, it, vi } from "vitest"

const calls: { kind: string; message: string; opts: Record<string, unknown> }[] = []

vi.mock("sonner", () => {
  const rec = (kind: string) => (message: string, opts: Record<string, unknown> = {}) => {
    calls.push({ kind, message, opts })
    return message
  }
  const toast = Object.assign(rec("message"), {
    success: rec("success"), error: rec("error"), warning: rec("warning"), info: rec("info"), loading: rec("loading"),
    dismiss: () => {},
  })
  return { toast }
})

import { DEFAULT_DURATION_MS, notifyError, toast } from "./notify"

beforeEach(() => {
  calls.length = 0
})

describe("notifyError · toast — 표시 시간과 복사 버튼", () => {
  it("사유가 있으면 제목 + 설명줄 + 복사 버튼, 오류는 10초", () => {
    notifyError(new Error("이름이 중복됩니다."), "태그를 추가하지 못했습니다.")
    const c = calls[0]!
    expect(c.kind).toBe("error")
    expect(c.message).toBe("태그를 추가하지 못했습니다.")
    expect(c.opts.description).toBe("이름이 중복됩니다.")
    expect(c.opts.duration).toBe(10_000)
    expect((c.opts.action as { label: string }).label).toBe("복사")
  })

  it("사유가 없거나 제목과 같으면 설명줄·복사 버튼 없이", () => {
    notifyError(null, "태그를 추가하지 못했습니다.")
    notifyError(new Error("태그를 추가하지 못했습니다."), "태그를 추가하지 못했습니다.")
    expect(calls).toHaveLength(2)
    for (const c of calls) {
      expect(c.opts.description).toBeUndefined()
      expect(c.opts.action).toBeUndefined()
    }
  })

  it("문자열 사유도 받는다 — 서버가 결과로 돌려준 실패 메시지", () => {
    notifyError("ORA-12541: no listener", "데이터 소스에 연결하지 못했습니다.")
    expect(calls[0]!.opts.description).toBe("ORA-12541: no listener")
  })

  it("경고 5초 · 성공은 Toaster 기본(3초)", () => {
    toast.warning("일부 항목을 건너뛰었습니다.")
    toast.success("저장했습니다.")
    expect(calls[0]!.opts.duration).toBe(5_000)
    expect(calls[1]!.opts.duration).toBeUndefined()
    expect(DEFAULT_DURATION_MS).toBe(3_000)
  })
})
