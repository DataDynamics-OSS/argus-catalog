// 변경 요청 상세 "알림 발송 이력" 표의 상태·수신처 표시 규칙 (순수 함수 — 테스트 대상).
//
// 통지 이력의 status 는 SENT(참조자 메일·인앱 등 발송)·DELIVERED(소비자 webhook 수신 확인)·
// FAILED·ACKED(수신 확인)·PENDING·SKIPPED(통지 꺼짐으로 보내지 않은 에스컬레이션)가 있다. 예전 화면은 SENT/FAILED 만 한글로 바꿔
// DELIVERED·ACKED 는 영문 그대로 회색 배지였고, 소비자 대상 통지는 recipient 가 비어 "—" 였다.

import type { StatusMeta } from "@workspace/ui/components/status-badge"

// 톤(TODO-107): 발송 · 전달 초록, 확인 파랑, 실패 빨강, 대기 테두리, 발송 안 함 무채색
export const NOTIFICATION_STATUS: Record<string, StatusMeta> = {
  SENT: { label: "발송됨", tone: "success" },
  DELIVERED: { label: "전달됨", tone: "success" },
  ACKED: { label: "확인됨", tone: "info" },
  FAILED: { label: "실패", tone: "danger" },
  PENDING: { label: "대기", tone: "neutral" },
  // 통지가 꺼져 있거나 받는 곳·발송 수단이 없어 보내지 않음(DEF-004·DEF-005, 사유는 error)
  SKIPPED: { label: "발송 안 함", tone: "muted" },
}

export function notificationStatus(status: string): StatusMeta {
  return NOTIFICATION_STATUS[status] ?? { label: status, tone: "neutral" }
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

/**
 * 결재 상신 때 고르는 소비자 통지 채널 (DEF-005). 서버가 실제로 보내는 채널만 둔다 —
 * SMS·인앱은 소비자에게 보낼 수단이 없어 고를 수 없다(예전 기본값 EMAIL·IN_APP 은 webhook 을 빠뜨렸다).
 */
export const SUBMIT_CHANNELS: { value: string; label: string; hint: string }[] = [
  { value: "WEBHOOK", label: "Webhook", hint: "소비자에 등록한 webhook URL 로 변경 내용(JSON) 전송" },
  { value: "EMAIL", label: "이메일", hint: "소비자 담당자 이메일로 발송 (설정 > 메일)" },
  { value: "SLACK", label: "Slack·Mattermost", hint: "소비자에 등록한 채널로 발송 (설정 > 알림)" },
]

export const DEFAULT_SUBMIT_CHANNELS = ["WEBHOOK", "EMAIL"]
