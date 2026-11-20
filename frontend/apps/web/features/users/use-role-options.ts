"use client"

/**
 * 사용자 · API 키에 줄 수 있는 역할 목록 — 기본 세 역할 + 사용자 정의 역할(TODO-124).
 *
 * 사용자 정의 역할은 권한 관리에서 만들며 GET /usermgr/roles 로 함께 내려온다. 한 번 불러와 화면끼리 나눠 쓰고,
 * 권한 관리에서 역할을 바꾸면 :func:`invalidateRoleOptions` 로 비운다. 불러오지 못하면 기본 역할만 보여 준다.
 */

import { useEffect, useState } from "react"

import { authFetch } from "@/features/auth/auth-fetch"

export type RoleOption = { value: string; label: string; custom: boolean }

export const BUILTIN_ROLE_OPTIONS: readonly RoleOption[] = [
  { value: "argus-admin", label: "관리자", custom: false },
  { value: "argus-superuser", label: "슈퍼유저", custom: false },
  { value: "argus-user", label: "사용자", custom: false },
]

const BUILTIN = new Set(BUILTIN_ROLE_OPTIONS.map((r) => r.value))

let pending: Promise<RoleOption[]> | null = null

async function fetchRoleOptions(): Promise<RoleOption[]> {
  const res = await authFetch("/api/v1/usermgr/roles")
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const rows: { role_id: string; name: string }[] = await res.json()
  const custom = rows
    .filter((r) => !BUILTIN.has(r.role_id))
    .map((r) => ({ value: r.role_id, label: r.name || r.role_id, custom: true }))
  return [...BUILTIN_ROLE_OPTIONS, ...custom]
}

/** 다음 사용 때 다시 불러오게 한다 — 역할을 추가 · 수정 · 삭제한 뒤. */
export function invalidateRoleOptions(): void {
  pending = null
}

/** 역할 코드 → 화면 이름(모르면 코드 그대로). */
export function roleOptionLabel(options: readonly RoleOption[], value: string): string {
  return options.find((r) => r.value === value)?.label ?? value
}

export function useRoleOptions(): readonly RoleOption[] {
  const [options, setOptions] = useState<readonly RoleOption[]>(BUILTIN_ROLE_OPTIONS)
  useEffect(() => {
    let cancelled = false
    if (!pending) {
      pending = fetchRoleOptions().catch((err) => {
        console.error("Failed to load role options", err)
        pending = null
        return [...BUILTIN_ROLE_OPTIONS]
      })
    }
    void pending.then((list) => { if (!cancelled) setOptions(list) })
    return () => { cancelled = true }
  }, [])
  return options
}

// 줄 수 있는 역할 — 로그인한 사람마다 한 번만 불러온다(사용자 표의 행마다 부르지 않도록)
const assignableCache = new Map<string, Promise<ReadonlySet<string> | null>>()

function fetchAssignable(who: string): Promise<ReadonlySet<string> | null> {
  let p = assignableCache.get(who)
  if (!p) {
    p = authFetch("/api/v1/usermgr/roles/assignable")
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((body: { roles: string[] | null }) => (body.roles ? new Set(body.roles) : null))
      .catch((err) => {
        console.error("Failed to load assignable roles", err)
        assignableCache.delete(who)
        return new Set(["argus-user"])
      })
    assignableCache.set(who, p)
  }
  return p
}

/**
 * 요청자가 계정 · API 키에 줄 수 있고 관리할 수 있는 역할인지 판정하는 함수(DEF-163).
 *
 * 관리자는 모두 허용. 위임받은 사람은 GET /usermgr/roles/assignable 이 돌려준 역할(기본 사용자 역할 + 자기 역할)만 —
 * 불러오기 전 · 실패 때는 기본 사용자 역할만 허용한다(서버도 같은 규칙으로 403).
 */
export function useCanAssignRole(isAdmin: boolean, who = ""): (role: string) => boolean {
  const [allowed, setAllowed] = useState<ReadonlySet<string> | null | undefined>(undefined)
  useEffect(() => {
    if (isAdmin) return
    let cancelled = false
    void fetchAssignable(who).then((set) => { if (!cancelled) setAllowed(set) })
    return () => { cancelled = true }
  }, [isAdmin, who])
  if (isAdmin || allowed === null) return () => true
  return (role: string) => (allowed ? allowed.has(role) : role === "argus-user")
}
