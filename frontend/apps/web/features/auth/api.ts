/**
 * Authentication API client. Added for SSO AUTH.
 *
 * Communicates with the backend /api/v1/auth/* endpoints
 * which proxy to Keycloak for token management.
 */

const BASE = "/api/v1/auth"

export type TokenResponse = {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  refresh_expires_in: number
}

export type UserInfo = {
  sub: string
  username: string
  email: string
  first_name: string
  last_name: string
  organization?: string | null
  department?: string | null
  phone_number?: string | null
  roles: string[]
  realm_roles: string[]
  role: string
  is_admin: boolean
  is_superuser: boolean
  avatar_preset_id: string | null
  // 최초 로그인 시 비밀번호 강제 변경 대상 여부(true 면 변경 전까지 게이트)
  must_change_password?: boolean
}

export async function login(
  username: string,
  password: string
): Promise<TokenResponse> {
  const res = await fetch(`${BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "로그인 실패")
  }
  return res.json()
}

export async function refreshToken(
  refresh_token: string
): Promise<TokenResponse> {
  const res = await fetch(`${BASE}/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token }),
  })
  if (!res.ok) {
    throw new Error("토큰 갱신 실패")
  }
  return res.json()
}

export async function fetchMe(accessToken: string): Promise<UserInfo> {
  const res = await fetch(`${BASE}/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  if (!res.ok) {
    throw new Error("사용자 정보 조회 실패")
  }
  return res.json()
}

/**
 * 비밀번호 변경. 성공 시 백엔드가 must_change_password=false 인 새 토큰 쌍을 재발급한다
 * (강제 변경 게이트 해제). 호출 측은 반환된 토큰으로 교체해야 한다.
 */
export async function changePassword(
  accessToken: string,
  currentPassword: string,
  newPassword: string
): Promise<TokenResponse> {
  const res = await fetch(`${BASE}/change-password`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "비밀번호 변경 실패")
  }
  return res.json()
}

export async function logout(refresh_token: string): Promise<void> {
  await fetch(`${BASE}/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token }),
  })
}

export type AuthType = "local" | "keycloak"

export async function fetchAuthType(): Promise<AuthType> {
  const res = await fetch(`${BASE}/type`)
  if (!res.ok) {
    throw new Error("인증 방식 조회 실패")
  }
  const data = (await res.json()) as { auth_type: AuthType }
  return data.auth_type
}

export async function updateAvatar(
  accessToken: string,
  avatarPresetId: string | null
): Promise<UserInfo> {
  const res = await fetch(`${BASE}/me/avatar`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ avatar_preset_id: avatarPresetId }),
  })
  if (!res.ok) {
    throw new Error("아바타 업데이트 실패")
  }
  return res.json()
}
