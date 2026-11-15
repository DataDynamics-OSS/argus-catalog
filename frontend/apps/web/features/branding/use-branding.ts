"use client"

import { useEffect, useState } from "react"

import { BRANDING_CHANGED_EVENT, DEFAULT_BRANDING, type BrandingConfig } from "@/features/settings/api"

const CACHE_KEY = "argus.branding"

function cached(): BrandingConfig | null {
  try {
    const raw = window.localStorage.getItem(CACHE_KEY)
    return raw ? (JSON.parse(raw) as BrandingConfig) : null
  } catch {
    return null
  }
}

function remember(b: BrandingConfig) {
  try {
    window.localStorage.setItem(CACHE_KEY, JSON.stringify(b))
  } catch {
    // 저장소를 못 쓰면 다음에 다시 받는다
  }
}

/**
 * 브랜드 문구(설정 > 사이드바 표시) — 사이드바 · 로그인 화면이 함께 쓴다(TODO-145 · TODO-146).
 * 마지막 값을 브라우저에 기억해 먼저 그리고 서버 값으로 맞춘다(새로 고칠 때 기본 문구가 깜빡이지 않게).
 * 설정에서 저장하면(BRANDING_CHANGED_EVENT) 바로 바뀐다.
 */
export function useBranding(load: () => Promise<BrandingConfig>): BrandingConfig {
  const [brand, setBrand] = useState<BrandingConfig>(DEFAULT_BRANDING)

  useEffect(() => {
    const c = cached()
    if (c) setBrand(c)
    let alive = true
    load()
      .then((b) => {
        if (!alive) return
        setBrand(b)
        remember(b)
      })
      .catch(() => {}) // 못 받으면 기억한 값 · 기본값 그대로
    const onChange = (e: Event) => {
      const b = (e as CustomEvent<BrandingConfig>).detail
      setBrand(b)
      remember(b)
    }
    window.addEventListener(BRANDING_CHANGED_EVENT, onChange)
    return () => {
      alive = false
      window.removeEventListener(BRANDING_CHANGED_EVENT, onChange)
    }
  }, [load])

  return brand
}
