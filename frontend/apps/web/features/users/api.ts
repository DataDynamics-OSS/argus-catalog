/**
 * User Management API client.
 *
 * Provides functions to communicate with the backend user management endpoints
 * (`/api/v1/usermgr/*`). All requests are proxied through the Next.js proxy
 * to the argus-insight-server.
 *
 * Key responsibilities:
 * - Fetching paginated user lists with optional filters (status, role, search).
 * - Creating, modifying, and deleting user accounts.
 * - Checking uniqueness of usernames and emails before submission.
 * - Activating and deactivating user accounts.
 * - Converting backend snake_case responses to frontend camelCase via `mapUser()`.
 */

import { type User } from "./data/schema"
import { authFetch, throwOnError } from "@/features/auth/auth-fetch" // Added for SSO AUTH
import type { TokenResponse } from "@/features/auth/api"

/** Base URL prefix for all user management API calls. */
const BASE = "/api/v1/usermgr"

/**
 * Query parameters for the user list endpoint.
 *
 * All fields are optional; when omitted, the backend returns unfiltered results.
 */
type UserListParams = {
  /** Filter by account status ("active" or "inactive"). */
  status?: string
  /** Filter by role name ("Admin" or "User"). */
  role?: string
  /** Free-text search across username, name, email, and phone number. */
  search?: string
  /** Filter by organization (exact match). */
  organization?: string
  /** Page number (1-based). Defaults to 1. */
  page?: number
  /** Number of items per page. Defaults to 10. */
  pageSize?: number
  /** 서버 정렬 열 (username, name, email, organization, department, status, role, created_at, last_login_at). */
  sort?: string
  /** 정렬 방향. */
  order?: "asc" | "desc"
}

/**
 * Paginated response shape returned by `fetchUsers()`.
 */
export type PaginatedUsers = {
  items: User[]
  total: number
  page: number
  pageSize: number
}

/**
 * Map a raw backend user object (snake_case) to the frontend User type (camelCase).
 *
 * The backend returns fields like `first_name`, `last_name`, `phone_number`, etc.
 * This function normalizes them to the camelCase convention used throughout the UI.
 * The role name is also lowercased to match the frontend enum ("Admin" -> "admin").
 */
function mapUser(u: Record<string, unknown>): User {
  return {
    ...u,
    id: String(u.id),
    firstName: u.first_name,
    lastName: u.last_name,
    organization: u.organization ?? "",
    department: u.department ?? "",
    phoneNumber: u.phone_number ?? "",
    role: u.role,
    createdAt: new Date(u.created_at as string),
    updatedAt: new Date(u.updated_at as string),
    lastLoginAt: u.last_login_at ? new Date(u.last_login_at as string) : null,
    lockedUntil: u.locked_until ? new Date(u.locked_until as string) : null,
  } as User
}

/**
 * Fetch a paginated list of users from the backend.
 *
 * Constructs query parameters from the provided filters and pagination settings,
 * then sends a GET request to `/api/v1/usermgr/users`. The response items are
 * mapped from snake_case to camelCase before being returned.
 *
 * @param params - Optional filters and pagination settings.
 * @returns Paginated user list with total count.
 * @throws Error if the HTTP response is not OK.
 */
export async function fetchUsers(params?: UserListParams): Promise<PaginatedUsers> {
  const query = new URLSearchParams()
  if (params?.status) query.set("status", params.status)
  if (params?.role) query.set("role", params.role)
  if (params?.search) query.set("search", params.search)
  if (params?.organization) query.set("organization", params.organization)
  query.set("page", String(params?.page ?? 1))
  query.set("page_size", String(params?.pageSize ?? 10))
  if (params?.sort) {
    query.set("sort", params.sort)
    query.set("order", params.order ?? "asc")
  }

  const url = `${BASE}/users?${query.toString()}`
  const res = await authFetch(url)
  if (!res.ok) throw new Error(`사용자 목록을 불러오지 못했습니다. (HTTP ${res.status})`)
  const data = await res.json()
  return {
    items: (data.items as Record<string, unknown>[]).map(mapUser),
    total: data.total,
    page: data.page,
    pageSize: data.page_size,
  }
}

/** 소속(조직) 고유 목록 — 소속 드롭다운 필터 옵션용. */
export async function fetchOrganizations(): Promise<string[]> {
  const res = await authFetch(`${BASE}/users/organizations`)
  if (!res.ok) throw new Error(`소속 목록을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

/**
 * Check whether a username or email is already taken.
 *
 * Used for real-time validation in the user creation form. When the user tabs
 * out of the username or email field, this function is called to provide
 * immediate feedback if the value conflicts with an existing account.
 *
 * @param params - Object containing the username and/or email to check.
 * @returns Object with boolean flags `username_exists` and/or `email_exists`.
 * @throws Error if the HTTP response is not OK.
 */
export async function checkUserExists(params: {
  username?: string
  email?: string
}): Promise<{ username_exists?: boolean; email_exists?: boolean }> {
  const query = new URLSearchParams()
  if (params.username) query.set("username", params.username)
  if (params.email) query.set("email", params.email)
  const res = await authFetch(`${BASE}/check-user?${query.toString()}`)
  if (!res.ok) throw new Error(`사용자 중복 여부를 확인하지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

/**
 * Payload for creating a new user.
 *
 * Uses snake_case field names to match the backend's expected request body.
 * The role field uses role_id values (e.g., "argus-admin", "argus-user").
 */
type CreateUserPayload = {
  username: string
  email: string
  first_name: string
  last_name: string
  organization?: string
  department?: string
  phone_number: string
  password: string
  role: string
}

/**
 * Create a new user account.
 *
 * Sends a POST request to the backend with the user's profile information
 * and credentials. On success, the backend returns the created user object.
 *
 * @param payload - User creation data in snake_case format.
 * @returns The newly created user.
 * @throws Error with the backend's detail message if creation fails.
 */
export async function createUser(payload: CreateUserPayload): Promise<User> {
  const res = await authFetch(`${BASE}/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `사용자를 생성하지 못했습니다. (HTTP ${res.status})`)
  }
  return res.json()
}

/**
 * Delete a user account by ID.
 *
 * This is a permanent, irreversible operation. The UI requires the admin
 * to type the username as confirmation before calling this function.
 *
 * @param userId - The ID of the user to delete.
 * @throws Error if the HTTP response is not OK (e.g. user not found).
 */
export async function deleteUser(userId: string, transferTo?: string): Promise<void> {
  const qs = transferTo ? `?transfer_to=${encodeURIComponent(transferTo)}` : ""
  const res = await authFetch(`${BASE}/users/${userId}${qs}`, { method: "DELETE" })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    const detail = err.detail
    // 409 owned_assets 는 {code, message, assets} 객체
    throw new Error((typeof detail === "string" ? detail : detail?.message) || `사용자를 삭제하지 못했습니다. (HTTP ${res.status})`)
  }
}

export type OwnedAssets = {
  username: string
  total: number
  items: { key: string; label: string; count: number }[]
}

/** 사용자가 소유한(편집 권한이 아이디로 묶인) 자산의 종류별 건수 — 삭제 전 이관 안내용. */
export async function fetchOwnedAssets(userId: string): Promise<OwnedAssets> {
  const res = await authFetch(`${BASE}/users/${userId}/owned-assets`)
  if (!res.ok) throw new Error(`소유 자산을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

/**
 * Activate a user account (set status to "active").
 *
 * Re-enables a previously deactivated account, allowing the user to log in again.
 *
 * @param userId - The ID of the user to activate.
 * @throws Error if the HTTP response is not OK.
 */
export async function activateUser(userId: string): Promise<void> {
  const res = await authFetch(`${BASE}/users/${userId}/activate`, { method: "PUT" })
  if (!res.ok) throw new Error(`사용자를 활성화하지 못했습니다. (HTTP ${res.status})`)
}

/** 로그인 실패 반복으로 잠긴 계정의 잠금을 해제한다 (관리자, 로컬 인증). */
export async function unlockUser(userId: string): Promise<void> {
  const res = await authFetch(`${BASE}/users/${userId}/unlock`, { method: "PUT" })
  if (!res.ok) await throwOnError(res, "계정 잠금을 해제하지 못했습니다.")
}

/**
 * Deactivate a user account (set status to "inactive").
 *
 * Disables the user account without deleting it. The user will not be able
 * to log in until an admin activates the account again.
 *
 * @param userId - The ID of the user to deactivate.
 * @throws Error if the HTTP response is not OK.
 */
export async function deactivateUser(userId: string): Promise<void> {
  const res = await authFetch(`${BASE}/users/${userId}/deactivate`, { method: "PUT" })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `사용자를 비활성화하지 못했습니다. (HTTP ${res.status})`)
  }
}

/**
 * Modify an existing user's profile fields.
 *
 * Only the fields provided in the payload will be updated; omitted fields
 * remain unchanged. Username and role cannot be changed through this endpoint.
 *
 * @param userId - The ID of the user to modify.
 * @param payload - Partial profile data to update (snake_case keys).
 * @returns The updated user object.
 * @throws Error if the HTTP response is not OK.
 */
export async function modifyUser(
  userId: string,
  payload: { first_name?: string; last_name?: string; email?: string; organization?: string; department?: string; phone_number?: string },
): Promise<User> {
  const res = await authFetch(`${BASE}/users/${userId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "사용자 정보를 수정하지 못했습니다.")
  return res.json()
}

export async function changeUserRole(userId: string, role: string): Promise<User> {
  const res = await authFetch(`${BASE}/users/${userId}/role`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `역할을 변경하지 못했습니다. (HTTP ${res.status})`)
  }
  return res.json()
}

/**
 * 관리자가 대상 사용자의 비밀번호를 재설정한다(사용자 관리).
 *
 * 본인 변경(`changeMyPassword`)과 달리 현재 비밀번호를 요구하지 않는다.
 * 백엔드는 관리자 권한 + 로컬 인증 모드를 검증한다.
 *
 * @param userId 대상 사용자 ID
 * @param newPassword 설정할 새 비밀번호(평문, 백엔드에서 해시)
 */
export async function setUserPassword(
  userId: string,
  newPassword: string,
): Promise<void> {
  const res = await authFetch(`${BASE}/users/${userId}/password`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: newPassword }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `비밀번호를 변경하지 못했습니다. (HTTP ${res.status})`)
  }
}

/**
 * Change the current user's own password (계정 설정).
 *
 * Calls the auth endpoint which verifies the current password before
 * updating — local auth mode only.
 *
 * 서버는 비밀번호가 바뀌면 이전 access·refresh 토큰을 즉시 거부하고 새 토큰 쌍을 돌려준다.
 * 호출한 쪽은 반환된 토큰으로 세션을 교체해야(useAuth().applyTokens) 이어지는 요청이 401 로
 * 로그아웃되지 않는다.
 */
export async function changeMyPassword(
  currentPassword: string,
  newPassword: string,
): Promise<TokenResponse> {
  const res = await authFetch(`/api/v1/auth/change-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `비밀번호를 변경하지 못했습니다. (HTTP ${res.status})`)
  }
  return res.json()
}
