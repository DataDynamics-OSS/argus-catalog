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

  // If we get a 401, redirect to login
  if (res.status === 401 && typeof window !== "undefined") {
    const { pathname } = window.location
    if (pathname !== "/login") {
      window.location.href = "/login"
    }
  }

  return res
}
