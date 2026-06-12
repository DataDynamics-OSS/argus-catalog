/**
 * Authenticated fetch wrapper. Added for SSO AUTH.
 *
 * Reads the access token from sessionStorage and injects it
 * as an Authorization header into every API request.
 */

const TOKEN_KEY = "argus_tokens"

type StoredTokens = {
  access_token: string
  refresh_token: string
  expires_at: number
}

function getAccessToken(): string | null {
  try {
    const raw = sessionStorage.getItem(TOKEN_KEY)
    if (!raw) return null
    const tokens: StoredTokens = JSON.parse(raw)
    return tokens.access_token
  } catch {
    return null
  }
}

/**
 * Drop-in replacement for `fetch` that adds the Authorization header.
 * Usage: import { authFetch } from "@/features/auth/auth-fetch"
 *        const res = await authFetch("/api/v1/catalog/datasets")
 */
export async function authFetch(
  input: RequestInfo | URL,
  init?: RequestInit
): Promise<Response> {
  const token = getAccessToken()
  const headers = new Headers(init?.headers)

  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`)
  }

  const res = await fetch(input, { ...init, headers })

  // 강제 비밀번호 변경 게이트(403 PASSWORD_CHANGE_REQUIRED) — 변경 화면으로 유도.
  // 본문을 소비하지 않도록 clone 으로 코드만 확인한다.
  if (res.status === 403 && typeof window !== "undefined") {
    const code = await res
      .clone()
      .json()
      .then((b) => b?.code)
      .catch(() => undefined)
    if (code === "PASSWORD_CHANGE_REQUIRED") {
      if (window.location.pathname !== "/change-password-required") {
        window.location.href = "/change-password-required"
      }
      return res
    }
  }

  // If we get a 401, redirect to login
  if (res.status === 401 && typeof window !== "undefined") {
    const { pathname } = window.location
    if (pathname !== "/login") {
      window.location.href = "/login"
    }
  }

  return res
}

/**
 * 실패 응답(res.ok=false)에서 백엔드 친절 메시지(`detail`)를 추출해 throw 한다.
 * detail 이 없으면 ``${fallback}: ${status}`` 로 폴백.
 *
 * 사용: ``if (!res.ok) await throwOnError(res, "데이터셋 수정 실패")``
 * 검증/권한/차단(409·422·403 등) 시 사용자에게 숫자 상태 대신 백엔드 메시지를 노출한다.
 */
export async function throwOnError(res: Response, fallback: string): Promise<never> {
  const body = (await res.json().catch(() => ({}))) as { detail?: string }
  throw new Error(body.detail || `${fallback}: ${res.status}`)
}
