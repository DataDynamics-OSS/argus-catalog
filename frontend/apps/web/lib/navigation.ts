/**
 * 화면 이동 보조 — 로그인 뒤 돌아갈 주소(next) 검사와 "뒤로" 버튼의 앱 내 기록 판단.
 *
 * - DEF-063: 로그인 화면으로 보낼 때 원래 경로를 `?next=` 로 넘기고, 로그인 뒤 그 경로로 돌아간다.
 *   열린 리다이렉트를 막으려고 `/` 로 시작하는 내부 경로만 허용한다(`//host`·`/\host`·`/login` 제외).
 * - DEF-066: `router.back()` 은 새 탭·직접 연 링크에서 아무 데도 가지 않으므로,
 *   앱 안에서 이동해 온 기록이 있을 때만 뒤로 가고 아니면 정해진 화면으로 보낸다.
 */

/** 로그인 뒤 기본 이동 경로. */
export const DEFAULT_AFTER_LOGIN = "/dashboard"

/**
 * `next` 값이 안전한 내부 경로면 그대로, 아니면 null.
 * 허용: `/` 로 시작, `//`·`/\` 로 시작하지 않음, 제어문자 없음, 로그인 화면 자신이 아님.
 */
export function safeNextPath(next: string | null | undefined): string | null {
  if (!next) return null
  if (!next.startsWith("/")) return null
  if (next.startsWith("//") || next.startsWith("/\\")) return null
  // 제어문자·공백류로 브라우저가 경로를 다르게 해석하는 경우를 막는다
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\s]/.test(next)) return null
  const pathOnly = next.split(/[?#]/)[0] ?? ""
  if (pathOnly === "/login" || pathOnly.startsWith("/login/")) return null
  return next
}

/** 현재 경로(path+query)를 next 로 담은 로그인 주소. 로그인 화면 자신이거나 안전하지 않으면 `/login`. */
export function loginPath(current: string | null | undefined): string {
  const next = safeNextPath(current)
  // 루트("/")는 어차피 대시보드로 가므로 넘기지 않는다
  if (!next || next === "/") return "/login"
  return `/login?next=${encodeURIComponent(next)}`
}

/** 로그인 화면 주소의 query 문자열(`?next=...`)에서 돌아갈 경로를 꺼낸다. 없으면 기본 경로. */
export function afterLoginPath(search: string | null | undefined): string {
  const params = new URLSearchParams(search ?? "")
  return safeNextPath(params.get("next")) ?? DEFAULT_AFTER_LOGIN
}

/* ------------------------------------------------------------------ */
/* 앱 내 이동 기록 (DEF-066)                                           */
/* ------------------------------------------------------------------ */

/**
 * 첫 화면 이후 앱 안에서 경로가 바뀐 적이 있는지 추적한다.
 * 첫 기록은 진입 화면이고, 그 뒤 다른 경로가 기록되면 "앱 안에서 이동해 왔다" 로 본다.
 */
export function createNavigationTracker() {
  let first: string | null = null
  let navigated = false
  return {
    record(path: string) {
      if (first === null) first = path
      else if (path !== first) navigated = true
    },
    get navigated() {
      return navigated
    },
  }
}

const tracker = createNavigationTracker()

/** 경로가 바뀔 때마다 호출(대시보드 레이아웃의 `NavigationTracker`). */
export function recordNavigation(path: string): void {
  tracker.record(path)
}

/** 같은 출처 referrer 인지 — 전체 새로고침으로 앱의 다른 화면에서 넘어온 경우. */
export function isSameOriginReferrer(referrer: string, origin: string): boolean {
  if (!referrer) return false
  try {
    return new URL(referrer).origin === origin
  } catch {
    return false
  }
}

/** 뒤로 가도 앱 안 화면으로 돌아가는지. 새 탭·직접 입력으로 열었으면 false. */
export function hasInAppHistory(): boolean {
  if (typeof window === "undefined") return false
  if (tracker.navigated) return true
  return (
    window.history.length > 1 &&
    isSameOriginReferrer(document.referrer, window.location.origin)
  )
}

/** 앱 안 기록이 있으면 뒤로, 없으면 fallback 경로로 이동한다. */
export function goBackOr(
  router: { back: () => void; push: (href: string) => void },
  fallback: string
): void {
  if (hasInAppHistory()) router.back()
  else router.push(fallback)
}
