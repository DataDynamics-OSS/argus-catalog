// Added for SSO AUTH - React context provider for authentication state and token management.
"use client"

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"
import {
  login as apiLogin,
  logout as apiLogout,
  refreshToken as apiRefreshToken,
  updateAvatar as apiUpdateAvatar,
  fetchAuthType,
  fetchMe,
  type AuthType,
  type TokenResponse,
  type UserInfo,
} from "./api"

type AuthState = {
  user: UserInfo | null
  accessToken: string | null
  authType: AuthType | null
  isAuthenticated: boolean
  isLoading: boolean
  login: (username: string, password: string) => Promise<UserInfo>
  logout: () => Promise<void>
  setAvatar: (avatarPresetId: string | null) => Promise<void>
  // 비밀번호 변경 등으로 받은 새 토큰으로 세션을 교체하고 사용자 정보를 다시 읽는다.
  applyTokens: (tokens: TokenResponse) => Promise<UserInfo>
}

const AuthContext = createContext<AuthState | null>(null)

const TOKEN_KEY = "argus_tokens"

type StoredTokens = {
  access_token: string
  refresh_token: string
  expires_at: number
}

function saveTokens(tokens: TokenResponse) {
  const data: StoredTokens = {
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token,
    expires_at: Date.now() + tokens.expires_in * 1000,
  }
  sessionStorage.setItem(TOKEN_KEY, JSON.stringify(data))
}

function loadTokens(): StoredTokens | null {
  try {
    const raw = sessionStorage.getItem(TOKEN_KEY)
    if (!raw) return null
    return JSON.parse(raw) as StoredTokens
  } catch {
    return null
  }
}

function clearTokens() {
  sessionStorage.removeItem(TOKEN_KEY)
}

const ACCOUNT_RECHECK_MS = 60_000

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserInfo | null>(null)
  const [accessToken, setAccessToken] = useState<string | null>(null)
  const [authType, setAuthType] = useState<AuthType | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const scheduleRefresh = useCallback(function schedule(tokens: StoredTokens) {
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current)
    }
    // Refresh 60 seconds before expiry
    const msUntilRefresh = tokens.expires_at - Date.now() - 60_000
    if (msUntilRefresh <= 0) return

    refreshTimerRef.current = setTimeout(async () => {
      try {
        const stored = loadTokens()
        if (!stored) return
        const newTokens = await apiRefreshToken(stored.refresh_token)
        saveTokens(newTokens)
        setAccessToken(newTokens.access_token)
        schedule({
          access_token: newTokens.access_token,
          refresh_token: newTokens.refresh_token,
          expires_at: Date.now() + newTokens.expires_in * 1000,
        })
      } catch {
        // Refresh failed - force logout
        clearTokens()
        setUser(null)
        setAccessToken(null)
      }
    }, msUntilRefresh)
  }, [])

  // Fetch the deployment's auth_type once on mount — it's static for a given
  // deployment and used to gate features (e.g. profile editing).
  useEffect(() => {
    fetchAuthType()
      .then(setAuthType)
      .catch(() => {
        // Leave as null; consumers should treat null as "unknown".
      })
  }, [])

  // Initialize from stored tokens on mount
  useEffect(() => {
    async function init() {
      const stored = loadTokens()
      if (!stored) {
        setIsLoading(false)
        return
      }

      // Check if token is still valid (with some buffer)
      if (stored.expires_at < Date.now() + 10_000) {
        // Try to refresh
        try {
          const newTokens = await apiRefreshToken(stored.refresh_token)
          saveTokens(newTokens)
          const userInfo = await fetchMe(newTokens.access_token)
          setAccessToken(newTokens.access_token)
          setUser(userInfo)
          scheduleRefresh({
            access_token: newTokens.access_token,
            refresh_token: newTokens.refresh_token,
            expires_at: Date.now() + newTokens.expires_in * 1000,
          })
        } catch {
          clearTokens()
        }
      } else {
        // Token still valid
        try {
          const userInfo = await fetchMe(stored.access_token)
          setAccessToken(stored.access_token)
          setUser(userInfo)
          scheduleRefresh(stored)
        } catch {
          clearTokens()
        }
      }
      setIsLoading(false)
    }

    init()

    return () => {
      if (refreshTimerRef.current) {
        clearTimeout(refreshTimerRef.current)
      }
    }
  }, [scheduleRefresh])

  // 계정 상태 재확인 — 관리자가 비활성화·삭제·역할 변경·비밀번호 재설정을 하면 서버는 이미 받은
  // 토큰도 바로 거부하거나(401) 새 역할로 처리한다. 화면도 따라가도록 탭으로 돌아올 때와 1분마다
  // /auth/me 를 다시 읽는다. 401 이면 세션을 끝내고(AuthGuard 가 로그인으로), 아니면 역할 등을 갱신.
  useEffect(() => {
    if (!user) return
    let stopped = false
    async function recheck() {
      const stored = loadTokens()
      if (!stored || stopped) return
      try {
        const info = await fetchMe(stored.access_token)
        if (!stopped) setUser((prev) => (prev && JSON.stringify(prev) === JSON.stringify(info) ? prev : info))
      } catch (e) {
        if (!stopped && (e as { status?: number })?.status === 401) {
          clearTokens()
          setUser(null)
          setAccessToken(null)
        }
      }
    }
    const onVisible = () => { if (document.visibilityState === "visible") recheck() }
    document.addEventListener("visibilitychange", onVisible)
    const timer = setInterval(recheck, ACCOUNT_RECHECK_MS)
    return () => {
      stopped = true
      document.removeEventListener("visibilitychange", onVisible)
      clearInterval(timer)
    }
  }, [user])

  // 토큰 쌍으로 세션을 확립한다(저장 → 사용자 조회 → 상태반영 → 자동갱신 예약).
  // 로그인과 비밀번호 변경 후 토큰 교체에서 공통으로 사용한다.
  const establishSession = useCallback(
    async (tokens: TokenResponse): Promise<UserInfo> => {
      saveTokens(tokens)
      const userInfo = await fetchMe(tokens.access_token)
      setAccessToken(tokens.access_token)
      setUser(userInfo)
      scheduleRefresh({
        access_token: tokens.access_token,
        refresh_token: tokens.refresh_token,
        expires_at: Date.now() + tokens.expires_in * 1000,
      })
      return userInfo
    },
    [scheduleRefresh]
  )

  const login = useCallback(
    async (username: string, password: string) => {
      const tokens = await apiLogin(username, password)
      return establishSession(tokens)
    },
    [establishSession]
  )

  const applyTokens = useCallback(
    (tokens: TokenResponse) => establishSession(tokens),
    [establishSession]
  )

  const logout = useCallback(async () => {
    const stored = loadTokens()
    if (stored) {
      await apiLogout(stored.refresh_token).catch(() => {})
    }
    clearTokens()
    setUser(null)
    setAccessToken(null)
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current)
    }
  }, [])

  const setAvatar = useCallback(
    async (avatarPresetId: string | null) => {
      if (!accessToken) {
        throw new Error("Not authenticated")
      }
      const updated = await apiUpdateAvatar(accessToken, avatarPresetId)
      setUser(updated)
    },
    [accessToken]
  )

  const value = useMemo(
    () => ({
      user,
      accessToken,
      authType,
      isAuthenticated: !!user,
      isLoading,
      login,
      logout,
      setAvatar,
      applyTokens,
    }),
    [user, accessToken, authType, isLoading, login, logout, setAvatar, applyTokens]
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider")
  }
  return ctx
}
