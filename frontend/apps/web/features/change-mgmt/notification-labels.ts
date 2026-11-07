// 변경 요청 상세 "알림 발송 이력" 표의 상태·수신처 표시 규칙 (순수 함수 — 테스트 대상).
//
// 통지 이력의 status 는 SENT(참조자 메일·인앱 등 발송)·DELIVERED(소비자 webhook 수신 확인)·
// FAILED·ACKED(수신 확인)·PENDING 이 있다. 예전 화면은 SENT/FAILED 만 한글로 바꿔
// DELIVERED·ACKED 는 영문 그대로 회색 배지였고, 소비자 대상 통지는 recipient 가 비어 "—" 였다.

export const NOTIFICATION_STATUS: Record<string, { label: string; className: string }> = {
  SENT: { label: "발송됨", className: "bg-emerald-200 text-emerald-800" },
  DELIVERED: { label: "전달됨", className: "bg-emerald-200 text-emerald-800" },
  ACKED: { label: "확인됨", className: "bg-blue-200 text-blue-800" },
  FAILED: { label: "실패", className: "bg-rose-200 text-rose-800" },
  PENDING: { label: "대기", className: "bg-gray-200 text-gray-700" },
}

export function notificationStatus(status: string): { label: string; className: string } {
  return NOTIFICATION_STATUS[status] ?? { label: status, className: "bg-gray-200 text-gray-700" }
}

/** 수신처 — 참조자 통지는 recipient(이메일/이름), 소비자 통지는 소비자 이름(없으면 `소비자 #id`). */
export function notificationRecipient(
  n: { recipient?: string | null; consumer_id?: number | null },
  consumerNames: Record<number, string>,
): string {
  if (n.recipient) return n.recipient
  if (n.consumer_id != null) return consumerNames[n.consumer_id] ?? `소비자 #${n.consumer_id}`
  return "—"
}
