"use client"

/**
 * 권한 컨텍스트 — GET /permissions/me 를 1회 로드해 전역 공유.
 *
 * open-by-default: 서버는 "차단 목록"만 주므로, 로드 전이나 실패 시에는
 * 아무것도 차단하지 않는다 (권한 기능 장애가 화면 전체를 비우지 않도록).
 *
 * 사용:
 *   const { isMenuAllowed, isFeatureAllowed } = usePermissions()
 *   if (!isFeatureAllowed("datasets.sample-view")) return null
 *
 * 명시 허용형 기능(registry 의 defaultDeny — 미설정이면 admin 전용)은
 * ``isFeatureGranted(key)`` 로 판단한다. admin 여부는 호출 측이 함께 확인한다:
 *   const canEdit = user?.is_admin || isFeatureGranted("glossary.edit")
 */

import { createContext, useContext, useEffect, useMemo, useState } from "react"

import { useAuth } from "@/features/auth"
import { authFetch } from "@/features/auth/auth-fetch"
import { DEFAULT_DENY_FEATURE_KEYS } from "@/lib/permission-registry"

type PermissionState = {
  deniedMenus: Set<string>
  deniedFeatures: Set<string>
  grantedFeatures: Set<string>
  loaded: boolean
}

const PermissionContext = createContext<PermissionState>({
  deniedMenus: new Set(),
  deniedFeatures: new Set(),
  grantedFeatures: new Set(),
  loaded: false,
})

export function PermissionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<PermissionState>({
    deniedMenus: new Set(),
    deniedFeatures: new Set(),
    grantedFeatures: new Set(),
    loaded: false,
  })

  const { user } = useAuth()
  const roleKey = user ? `${user.sub}:${user.role}` : ""
  const [tick, setTick] = useState(0)

  // 권한 매트릭스 변경·역할 변경이 새로고침 없이 반영되도록 탭 복귀 때와 1분마다 다시 읽는다
  useEffect(() => {
    const bump = () => { if (document.visibilityState === "visible") setTick((t) => t + 1) }
    document.addEventListener("visibilitychange", bump)
    const timer = setInterval(bump, 60_000)
    return () => {
      document.removeEventListener("visibilitychange", bump)
      clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    authFetch("/api/v1/permissions/me")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { denied_menus?: string[]; denied_features?: string[]; granted_features?: string[] } | null) => {
        if (cancelled) return
        setState({
          deniedMenus: new Set(d?.denied_menus ?? []),
          deniedFeatures: new Set(d?.denied_features ?? []),
          grantedFeatures: new Set(d?.granted_features ?? []),
          loaded: true,
        })
      })
      .catch(() => {
        // 실패 시 차단 없음 — 기능 게이팅은 어디까지나 UI 편의,
        // 민감 API 는 서버가 별도로 강제한다
        if (!cancelled) setState((s) => ({ ...s, loaded: true }))
      })
    return () => { cancelled = true }
  }, [roleKey, tick])

  return <PermissionContext.Provider value={state}>{children}</PermissionContext.Provider>
}

export function usePermissions() {
  const state = useContext(PermissionContext)
  const { user } = useAuth()
  const isAdmin = !!user?.is_admin
  return useMemo(() => ({
    loaded: state.loaded,
    isMenuAllowed: (key: string | null) => !key || !state.deniedMenus.has(key),
    isFeatureAllowed: (key: string) => !state.deniedFeatures.has(key),
    /** 명시 허용형 기능 — 내 역할이 매트릭스에 명시 허용된 경우만 true (admin 은 호출 측에서 별도 확인). */
    isFeatureGranted: (key: string) => state.grantedFeatures.has(key),
    /**
     * 이 기능을 쓸 수 있는가 — 서버 판정과 같다. admin 은 항상 true, 명시 허용형(defaultDeny)은
     * 허용된 역할만, 나머지는 차단되지 않았으면 true.
     */
    canUseFeature: (key: string) =>
      isAdmin ||
      (DEFAULT_DENY_FEATURE_KEYS.has(key) ? state.grantedFeatures.has(key) : !state.deniedFeatures.has(key)),
  }), [state, isAdmin])
}
