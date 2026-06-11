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
 */

import { createContext, useContext, useEffect, useMemo, useState } from "react"

import { authFetch } from "@/features/auth/auth-fetch"

type PermissionState = {
  deniedMenus: Set<string>
  deniedFeatures: Set<string>
  loaded: boolean
}

const PermissionContext = createContext<PermissionState>({
  deniedMenus: new Set(),
  deniedFeatures: new Set(),
  loaded: false,
})

export function PermissionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<PermissionState>({
    deniedMenus: new Set(),
    deniedFeatures: new Set(),
    loaded: false,
  })

  useEffect(() => {
    let cancelled = false
    authFetch("/api/v1/permissions/me")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { denied_menus?: string[]; denied_features?: string[] } | null) => {
        if (cancelled) return
        setState({
          deniedMenus: new Set(d?.denied_menus ?? []),
          deniedFeatures: new Set(d?.denied_features ?? []),
          loaded: true,
        })
      })
      .catch(() => {
        // 실패 시 차단 없음 — 기능 게이팅은 어디까지나 UI 편의,
        // 민감 API 는 서버가 별도로 강제한다
        if (!cancelled) setState((s) => ({ ...s, loaded: true }))
      })
    return () => { cancelled = true }
  }, [])

  return <PermissionContext.Provider value={state}>{children}</PermissionContext.Provider>
}

export function usePermissions() {
  const state = useContext(PermissionContext)
  return useMemo(() => ({
    loaded: state.loaded,
    isMenuAllowed: (key: string | null) => !key || !state.deniedMenus.has(key),
    isFeatureAllowed: (key: string) => !state.deniedFeatures.has(key),
  }), [state])
}
