"use client"

/**
 * 권한 컨텍스트 — GET /permissions/me 를 1회 로드해 전역 공유.
 *
 * open-by-default: 서버는 "차단 목록"만 주므로, 로드 전이나 실패 시에는
 * 아무것도 차단하지 않는다 (권한 기능 장애가 화면 전체를 비우지 않도록).
 *
 * 사용:
 *   const { isMenuAllowed, canUseFeature } = usePermissions()
 *   if (!canUseFeature("datasets.sample-view")) return null
 *
 * 기능 판정은 ``canUseFeature(key)`` 하나로 한다 — 관리자는 항상 허용, 명시 허용형(registry 의 defaultDeny —
 * 미설정이면 관리자 전용)은 허용받은 역할만, 기본 허용형은 막히지 않았으면 허용(서버 판정과 같다, TODO-122).
 * 예전의 ``isFeatureAllowed`` · ``isFeatureGranted`` 는 관리자 · 명시 허용형 처리를 호출 측에 맡겨 화면마다 기준이 달랐다.
 *
 * 보기 전용 메뉴(TODO-124)의 편집 기능은 서버가 ``denied_features`` 에 넣어 보내므로 ``canUseFeature`` 가 그대로 false 가 된다.
 * ``isMenuReadOnly`` 는 화면에 "보기 전용" 안내를 띄울 때 쓴다.
 */

import { createContext, useContext, useEffect, useMemo, useState } from "react"

import { useAuth } from "@/features/auth"
import { authFetch } from "@/features/auth/auth-fetch"
import { DEFAULT_DENY_FEATURE_KEYS } from "@/lib/permission-registry"

type PermissionState = {
  deniedMenus: Set<string>
  deniedFeatures: Set<string>
  grantedFeatures: Set<string>
  readonlyMenus: Set<string>
  loaded: boolean
}

const EMPTY_STATE: PermissionState = {
  deniedMenus: new Set(),
  deniedFeatures: new Set(),
  grantedFeatures: new Set(),
  readonlyMenus: new Set(),
  loaded: false,
}

const PermissionContext = createContext<PermissionState>(EMPTY_STATE)

export function PermissionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<PermissionState>(EMPTY_STATE)

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
      .then((d: {
        denied_menus?: string[]
        denied_features?: string[]
        granted_features?: string[]
        readonly_menus?: string[]
      } | null) => {
        if (cancelled) return
        setState({
          deniedMenus: new Set(d?.denied_menus ?? []),
          deniedFeatures: new Set(d?.denied_features ?? []),
          grantedFeatures: new Set(d?.granted_features ?? []),
          readonlyMenus: new Set(d?.readonly_menus ?? []),
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
    /** 메뉴가 보기 전용인가(TODO-124) — 열려 있지만 편집 기능은 막힌다. 관리자는 항상 false. */
    isMenuReadOnly: (key: string | null) => !isAdmin && !!key && state.readonlyMenus.has(key),
    /**
     * 이 기능을 쓸 수 있는가 — 서버 판정과 같다. admin 은 항상 true, 명시 허용형(defaultDeny)은
     * 허용된 역할만, 나머지는 차단되지 않았으면 true.
     */
    canUseFeature: (key: string) =>
      isAdmin ||
      (DEFAULT_DENY_FEATURE_KEYS.has(key) ? state.grantedFeatures.has(key) : !state.deniedFeatures.has(key)),
  }), [state, isAdmin])
}
