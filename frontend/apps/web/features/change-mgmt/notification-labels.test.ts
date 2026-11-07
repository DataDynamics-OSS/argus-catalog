import { describe, expect, it } from "vitest"
import { notificationRecipient, notificationStatus } from "./notification-labels"

describe("notificationStatus", () => {
  it("DELIVERED·ACKED 도 한글 라벨 (예전에는 영문 그대로 — IT CHG-T08·T10)", () => {
    expect(notificationStatus("SENT").label).toBe("발송됨")
    expect(notificationStatus("DELIVERED").label).toBe("전달됨")
    expect(notificationStatus("ACKED").label).toBe("확인됨")
    expect(notificationStatus("FAILED").label).toBe("실패")
  })
  it("모르는 값은 원문", () => {
    expect(notificationStatus("ESCALATED").label).toBe("ESCALATED")
  })
})

describe("notificationRecipient", () => {
  it("참조자 통지는 recipient", () => {
    expect(notificationRecipient({ recipient: "a@x.com", consumer_id: null }, {})).toBe("a@x.com")
  })
  it("소비자 통지는 소비자 이름, 이름을 모르면 소비자 #id", () => {
    expect(notificationRecipient({ recipient: null, consumer_id: 3 }, { 3: "it-etl-consumer" })).toBe("it-etl-consumer")
    expect(notificationRecipient({ recipient: null, consumer_id: 4 }, {})).toBe("소비자 #4")
  })
  it("둘 다 없으면 —", () => {
    expect(notificationRecipient({}, {})).toBe("—")
  })
})
