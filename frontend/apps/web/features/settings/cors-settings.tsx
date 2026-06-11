"use client"

import { useCallback, useEffect, useState } from "react"
import { Check, Loader2, Save, X } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Label } from "@workspace/ui/components/label"
import { Textarea } from "@workspace/ui/components/textarea"

import { fetchCorsConfig, updateCorsConfig } from "./api"

export function CorsSettings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null)
  const [origins, setOrigins] = useState("*")

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      const cfg = await fetchCorsConfig()
      setOrigins(cfg.origins)
    } catch {
      setMessage({ type: "error", text: "CORS 설정을 불러오지 못했습니다." })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  const handleSave = async () => {
    setSaving(true)
    setMessage(null)
    try {
      await updateCorsConfig({ origins })
      setMessage({ type: "success", text: "CORS 설정을 저장했습니다. 다음 요청부터 적용됩니다." })
    } catch (e) {
      setMessage({ type: "error", text: e instanceof Error ? e.message : "저장에 실패했습니다." })
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
  }

  return (
    <div className="space-y-4 max-w-2xl">
      {message && (
        <div className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm ${
          message.type === "success" ? "bg-emerald-50 text-emerald-700 border border-emerald-200" : "bg-red-50 text-red-700 border border-red-200"
        }`}>
          {message.type === "success" ? <Check className="h-4 w-4" /> : <X className="h-4 w-4" />}
          {message.text}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">CORS 허용 출처</CardTitle>
          <CardDescription>
            API에 교차 출처(Cross-Origin) 요청을 허용할 출처를 설정합니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label>허용 출처</Label>
            <Textarea
              value={origins}
              onChange={(e) => setOrigins(e.target.value)}
              placeholder="http://localhost:3000, https://catalog.example.com"
              rows={3}
            />
            <p className="text-xs text-muted-foreground">
              쉼표로 구분된 허용 출처 목록입니다. <code className="bg-muted px-1 rounded">*</code> 는 모든 출처를 허용 (운영 환경 비권장).
            </p>
          </div>

          <Button onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Save className="h-4 w-4 mr-1" />}
            저장
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
