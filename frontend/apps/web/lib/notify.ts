/**
 * 앱 공통 알림(toast) 헬퍼.
 *
 * 규칙
 * - 화면 코드는 `sonner` 를 직접 import 하지 않고 이 모듈만 쓴다 (lib/notify.test.ts 가 정적 검사).
 * - 위치·색상·닫기 버튼 등 표시 방식은 `NotifyToaster` 한 곳에서만 정한다.
 * - 문구: 성공은 "<대상>을(를) <동작>했습니다.", 실패는 "<대상>을(를) <동작>하지 못했습니다." + 서버 사유.
 *   "○○ 실패" 같은 명사형은 쓰지 않는다.
 * - 같은 문구가 반복되면 하나로 합친다(id = 문구) — 연속 실패 시 toast 가 쌓이지 않게.
 */
import { toast as sonnerToast, type ExternalToast } from "sonner"

/** 오류는 읽을 시간을 조금 더 준다. */
export const ERROR_DURATION_MS = 6000
export const DEFAULT_DURATION_MS = 4000

type Options = ExternalToast

function withDedupe(message: string, opts?: Options): Options {
  return { id: message, ...opts }
}

/** 브라우저 fetch 네트워크 오류를 대신 보여 줄 한국어 문구. */
export const NETWORK_ERROR_MESSAGE = "서버에 연결하지 못했습니다. 네트워크 상태를 확인한 뒤 다시 시도하세요."

// 브라우저별 fetch 네트워크 오류 원문 — Chrome "Failed to fetch", Firefox "NetworkError when attempting
// to fetch resource.", Safari "Load failed" / "The network connection was lost.", Node(undici) "fetch failed"
const NETWORK_ERROR_RE = /^(failed to fetch|networkerror when attempting to fetch resource\.?|load failed|the network connection was lost\.?|fetch failed)$/i

/** fetch 가 서버에 닿지 못해 던진 오류(TypeError)인지. */
export function isNetworkError(err: unknown): boolean {
  return err instanceof TypeError && NETWORK_ERROR_RE.test(err.message.trim())
}

/** 예외 객체에서 사용자에게 보여줄 메시지를 꺼낸다 (throwOnError 가 던진 서버 detail 포함). */
export function errorMessage(err: unknown): string | null {
  // 브라우저 영어 원문("Failed to fetch" 등) 대신 한국어 안내
  if (isNetworkError(err)) return NETWORK_ERROR_MESSAGE
  if (err instanceof Error && err.message.trim()) return err.message.trim()
  if (typeof err === "string" && err.trim()) return err.trim()
  return null
}

/**
 * 실패 알림. `fallback` 은 "<대상>을(를) <동작>하지 못했습니다." 형태로 쓴다.
 * 서버 사유가 fallback 과 다르면 사유를 설명줄로 붙인다.
 */
export function notifyError(err: unknown, fallback: string, opts?: Options) {
  let reason = errorMessage(err)
  // throwOnError 폴백("<fallback> (HTTP 500)")처럼 제목을 되풀이하는 사유는 남는 부분만 쓴다
  if (reason && reason.startsWith(fallback)) reason = reason.slice(fallback.length).trim() || null
  if (!reason) {
    return sonnerToast.error(fallback, withDedupe(fallback, { duration: ERROR_DURATION_MS, ...opts }))
  }
  return sonnerToast.error(fallback, {
    id: `${fallback}\n${reason}`,
    description: reason,
    duration: ERROR_DURATION_MS,
    ...opts,
  })
}

export function notifySuccess(message: string, opts?: Options) {
  return sonnerToast.success(message, withDedupe(message, opts))
}

/** 기존 `toast.xxx(...)` 호출부와 같은 모양의 API (중복 합치기·오류 지속시간만 공통 적용). */
export const toast = {
  success: (message: string, opts?: Options) => sonnerToast.success(message, withDedupe(message, opts)),
  error: (message: string, opts?: Options) =>
    sonnerToast.error(message, withDedupe(message, { duration: ERROR_DURATION_MS, ...opts })),
  warning: (message: string, opts?: Options) => sonnerToast.warning(message, withDedupe(message, opts)),
  info: (message: string, opts?: Options) => sonnerToast.info(message, withDedupe(message, opts)),
  message: (message: string, opts?: Options) => sonnerToast(message, withDedupe(message, opts)),
  loading: (message: string, opts?: Options) => sonnerToast.loading(message, opts),
  dismiss: (id?: string | number) => sonnerToast.dismiss(id),
}
