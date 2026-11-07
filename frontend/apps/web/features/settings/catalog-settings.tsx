"use client"

import { useCallback, useEffect, useState } from "react"
import { Loader2, Save, X } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Label } from "@workspace/ui/components/label"
import { Switch } from "@workspace/ui/components/switch"

import {
  fetchCatalogPolicyConfig,
  updateCatalogPolicyConfig,
  type CatalogPolicyConfig,
} from "./api"
import { notifyError, notifySuccess } from "@/lib/notify"

const EMPTY: CatalogPolicyConfig = { dataset_create_by_users: false }

/** 데이터셋 등록 정책 — 일반 사용자의 데이터셋 직접 등록 허용 여부(기본 꺼짐). */
export function CatalogSettings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [cfg, setCfg] = useState<CatalogPolicyConfig>(EMPTY)

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      setLoadError(null)
      setCfg(await fetchCatalogPolicyConfig())
    } catch {
      setLoadError("데이터셋 등록 설정을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  const handleSave = async () => {
    setSaving(true)
    try {
      await updateCatalogPolicyConfig(cfg)
      notifySuccess("데이터셋 등록 설정을 저장했습니다.")
    } catch (e) {
      notifyError(e, "데이터셋 등록 설정을 저장하지 못했습니다.")
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
          <CardTitle className="text-base">데이터셋 직접 등록</CardTitle>
          <CardDescription>
            데이터셋 목록의 「데이터셋 추가」로 데이터셋을 직접 등록할 수 있는 사람을 정합니다.
            관리자는 항상 등록할 수 있고, 동기화로 생기는 데이터셋에는 영향이 없습니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between gap-4">
            <div>
              <Label htmlFor="dataset-create-by-users">일반 사용자의 데이터셋 직접 등록 허용</Label>
              <p className="text-xs text-muted-foreground mt-0.5">
                끄면(기본) 관리자만 등록합니다. 켜면 일반 사용자도 등록할 수 있으며, 권한 관리의
                기능 권한 「데이터셋 등록」(datasets.create)은 계속 적용됩니다.
              </p>
            </div>
            <Switch
              id="dataset-create-by-users"
              checked={cfg.dataset_create_by_users}
              onCheckedChange={(v) => setCfg({ dataset_create_by_users: v })}
            />
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
