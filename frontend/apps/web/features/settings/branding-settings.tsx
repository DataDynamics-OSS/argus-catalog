"use client"

import { useCallback, useEffect, useState } from "react"
import { Loader2, Save, X } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"

import {
  BRANDING_CHANGED_EVENT,
  DEFAULT_BRANDING,
  fetchBrandingConfig,
  updateBrandingConfig,
  type BrandingConfig,
} from "./api"
import { notifyError, notifySuccess } from "@/lib/notify"

/** 사이드바 표시 — 사이드바 위 제품명 · 회사명 문구와 눌렀을 때 갈 주소 (TODO-145). */
export function BrandingSettings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [cfg, setCfg] = useState<BrandingConfig>(DEFAULT_BRANDING)

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      setLoadError(null)
      setCfg(await fetchBrandingConfig())
    } catch {
      setLoadError("사이드바 표시 설정을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  const set = (k: keyof BrandingConfig, v: string) => setCfg((c) => ({ ...c, [k]: v }))

  const handleSave = async () => {
    setSaving(true)
    try {
      const saved = await updateBrandingConfig(cfg)
      setCfg(saved)
      // 사이드바에 바로 반영
      window.dispatchEvent(new CustomEvent(BRANDING_CHANGED_EVENT, { detail: saved }))
      // 탭 제목도 지금 바로(다른 화면 · 다른 사용자는 서버가 30초 안에 새 값을 읽는다 — TODO-147)
      document.title = saved.title || DEFAULT_BRANDING.title
      notifySuccess("사이드바 표시 설정을 저장했습니다.")
    } catch (e) {
      notifyError(e, "사이드바 표시 설정을 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
  }

  return (
    <div className="space-y-4 max-w-2xl">
      {loadError && (
        <div className="flex items-center gap-2 rounded-md px-3 py-2 text-sm bg-red-50 text-red-700 border border-red-200">
          <X className="h-4 w-4" />
          {loadError}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">사이드바 제품명 · 회사명</CardTitle>
          <CardDescription>
            왼쪽 사이드바 맨 위에 보이는 두 줄 문구와, 그 문구를 눌렀을 때 갈 주소를 정합니다.
            저장하면 모든 사용자의 사이드바에 반영됩니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="branding-title">제품명</Label>
            <Input
              id="branding-title"
              value={cfg.title}
              maxLength={60}
              onChange={(e) => set("title", e.target.value)}
              placeholder={DEFAULT_BRANDING.title}
            />
            <p className="text-xs">비우면 {DEFAULT_BRANDING.title} 로 표시합니다.</p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="branding-subtitle">제품명 아래 문구</Label>
            <Input
              id="branding-subtitle"
              value={cfg.subtitle}
              maxLength={80}
              onChange={(e) => set("subtitle", e.target.value)}
              placeholder={DEFAULT_BRANDING.subtitle}
            />
            <p className="text-xs">회사명 · 팀 이름 등. 비우면 이 줄을 숨깁니다.</p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="branding-link">눌렀을 때 갈 주소</Label>
            <Input
              id="branding-link"
              value={cfg.link_url}
              maxLength={1000}
              onChange={(e) => set("link_url", e.target.value)}
              placeholder="https://www.example.com 또는 /dashboard/datasets"
            />
            <p className="text-xs">
              https:// 로 시작하는 주소는 새 탭으로, / 로 시작하는 경로는 이 화면 안에서 엽니다.
              비우면 데이터셋 목록을 카드 보기로 엽니다.
            </p>
          </div>

          <div className="flex justify-end pt-2">
            <Button onClick={handleSave} disabled={saving}>
              {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
              저장
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
