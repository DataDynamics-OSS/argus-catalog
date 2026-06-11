"use client"

import { useCallback, useEffect, useState } from "react"
import { Check, Loader2, Save, X } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { Switch } from "@workspace/ui/components/switch"

import {
  fetchChangeMgmtConfig,
  updateChangeMgmtConfig,
  type ChangeMgmtConfig,
} from "./api"

const EMPTY: ChangeMgmtConfig = {
  notify_enabled: true,
  notify_channel: "email",
}

const CHANNEL_LABEL: Record<ChangeMgmtConfig["notify_channel"], string> = {
  email: "이메일",
  slack: "Slack",
  mattermost: "Mattermost",
}

export function ChangeMgmtSettings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null)
  const [cfg, setCfg] = useState<ChangeMgmtConfig>(EMPTY)

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      setCfg(await fetchChangeMgmtConfig())
    } catch {
      setMessage({ type: "error", text: "변경관리 설정을 불러오지 못했습니다." })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  const handleSave = async () => {
    setSaving(true)
    setMessage(null)
    try {
      await updateChangeMgmtConfig(cfg)
      setMessage({ type: "success", text: "변경관리 설정을 저장했습니다." })
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
          <CardTitle className="text-base">변경관리 참조자 통지</CardTitle>
          <CardDescription>
            변경 요청 생성 시 참조자(CC)에게 발송할 통지 채널을 전역으로 설정합니다.
            이메일은 참조자별 주소로, Slack·Mattermost 는 알림 설정에 등록된 기본 Webhook 으로 발송됩니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* 통지 활성화 */}
          <div className="flex items-center justify-between">
            <div>
              <Label>참조자 통지 활성화</Label>
              <p className="text-xs text-muted-foreground mt-0.5">비활성화 시 참조자에게 통지를 발송하지 않습니다.</p>
            </div>
            <Switch
              checked={cfg.notify_enabled}
              onCheckedChange={(v) => setCfg((c) => ({ ...c, notify_enabled: v }))}
            />
          </div>

          {/* 통지 채널 */}
          <div className="space-y-1.5">
            <Label>통지 채널</Label>
            <Select
              value={cfg.notify_channel}
              onValueChange={(v) => setCfg((c) => ({ ...c, notify_channel: v as ChangeMgmtConfig["notify_channel"] }))}
            >
              <SelectTrigger className="w-[240px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                {(Object.keys(CHANNEL_LABEL) as ChangeMgmtConfig["notify_channel"][]).map((k) => (
                  <SelectItem key={k} value={k}>{CHANNEL_LABEL[k]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Slack·Mattermost Webhook 은 「알림」 탭에서 설정합니다.
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
