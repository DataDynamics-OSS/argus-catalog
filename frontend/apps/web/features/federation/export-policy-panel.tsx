"use client"

import { useCallback, useEffect, useState } from "react"
import { AlertTriangle, CheckCircle2, Loader2, Save } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"

import { getExportPolicy, updateExportPolicy, type ExportPolicy } from "./api"
import { CapabilityChecklist } from "./capability-checklist"

/** 노출자(provider) 설정 — 이 인스턴스가 외부 허브에 줄 정보 항목을 고른다. */
export function FederationExportPolicyPanel() {
  const [policy, setPolicy] = useState<ExportPolicy | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const p = await getExportPolicy()
      setPolicy(p)
      setSelected(p.exposed)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const save = useCallback(async () => {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const p = await updateExportPolicy(selected)
      setPolicy(p)
      setSelected(p.exposed)
      setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }, [selected])

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-16 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> 불러오는 중...
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        이 인스턴스가 다른 허브(소비자)에 <strong>API로 제공할 정보 항목</strong>을
        선택합니다. 체크 해제한 항목은 페더레이션 상세·샘플·리니지에서 제외됩니다.
      </p>

      {error && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" /> {error}
        </div>
      )}
      {saved && (
        <div className="flex items-center gap-2 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:bg-emerald-950/30">
          <CheckCircle2 className="h-4 w-4" /> 노출 정책을 저장했습니다.
        </div>
      )}

      {policy && (
        <Card className="overflow-auto">
          <CardContent className="p-4">
            <CapabilityChecklist
              items={policy.items}
              groups={policy.groups}
              value={selected}
              onChange={(k) => {
                setSelected(k)
                setSaved(false)
              }}
            />
          </CardContent>
        </Card>
      )}

      <div className="flex items-center gap-2">
        <Button onClick={save} disabled={saving}>
          {saving ? (
            <Loader2 className="mr-1 h-4 w-4 animate-spin" />
          ) : (
            <Save className="mr-1 h-4 w-4" />
          )}
          저장
        </Button>
        <span className="text-xs text-muted-foreground">
          {selected.length}개 항목 노출
        </span>
      </div>
    </div>
  )
}
