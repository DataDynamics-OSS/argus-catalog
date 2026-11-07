/**
 * 알림 상태가 바뀌었음을 같은 탭의 다른 컴포넌트(헤더 벨 등)에 알리는 브라우저 이벤트.
 * 헤더 벨은 30초 주기로도 갱신하지만, 이 이벤트를 받으면 바로 요약을 다시 읽는다.
 */
export const ALERTS_CHANGED_EVENT = "argus:alerts-changed"

export function notifyAlertsChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT))
}

export function onAlertsChanged(handler: () => void): () => void {
  window.addEventListener(ALERTS_CHANGED_EVENT, handler)
  return () => window.removeEventListener(ALERTS_CHANGED_EVENT, handler)
}
