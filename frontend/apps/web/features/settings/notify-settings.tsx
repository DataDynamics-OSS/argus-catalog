"use client"

import { useCallback, useEffect, useState } from "react"
import { Check, Loader2, Play, Save, X } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Input } from "@workspace/ui/components/input"
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
  fetchNotifyConfig,
  updateNotifyConfig,
  testNotify,
  type NotifyConfig,
  type NotifyWebhookConfig,
} from "./api"

const EMPTY: NotifyConfig = {
  enabled: false,
  provider: "slack",
  timeout_seconds: 10,
  slack: { webhook_url: "", channel: "", username: "", icon_emoji: ":bell:" },
  mattermost: { webhook_url: "", channel: "", username: "", icon_emoji: "" },
}

export function NotifySettings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null)
  const [cfg, setCfg] = useState<NotifyConfig>(EMPTY)
  const [testText, setTestText] = useState("Argus Catalog 알림 테스트입니다.")

  const provider = cfg.provider
  const sub = cfg[provider]

  const setSub = <K extends keyof NotifyWebhookConfig>(key: K, value: NotifyWebhookConfig[K]) =>
    setCfg((c) => ({ ...c, [provider]: { ...c[provider], [key]: value } }))

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      setCfg(await fetchNotifyConfig())
    } catch {
      setMessage({ type: "error", text: "알림 설정을 불러오지 못했습니다." })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  const handleSave = async () => {
    setSaving(true)
    setMessage(null)
    try {
      await updateNotifyConfig(cfg)
      setMessage({ type: "success", text: "알림 설정을 저장했습니다." })
    } catch (e) {
      setMessage({ type: "error", text: e instanceof Error ? e.message : "저장에 실패했습니다." })
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setMessage(null)
    try {
      // 테스트는 저장된 설정으로 발송되므로 먼저 저장 후 테스트.
      await updateNotifyConfig(cfg)
      const result = await testNotify({ text: testText })
      setMessage({ type: result.success ? "success" : "error", text: result.message })
    } catch (e) {
      setMessage({ type: "error", text: e instanceof Error ? e.message : "테스트에 실패했습니다." })
    } finally {
      setTesting(false)
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
  }

  const providerLabel = provider === "slack" ? "Slack" : "Mattermost"

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
          <CardTitle className="text-base">알림 (Slack / Mattermost)</CardTitle>
          <CardDescription>
            Incoming Webhook URL 로 알림 메시지를 발송합니다. Slack·Mattermost 각각의 설정은 분리되어 저장됩니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* 활성화 */}
          <div className="flex items-center justify-between">
            <div>
              <Label>알림 발송 활성화</Label>
              <p className="text-xs text-muted-foreground mt-0.5">비활성화 시 발송 요청은 조용히 무시됩니다.</p>
            </div>
            <Switch checked={cfg.enabled} onCheckedChange={(v) => setCfg((c) => ({ ...c, enabled: v }))} />
          </div>

          {/* provider 선택 */}
          <div className="space-y-1.5">
            <Label>제공자</Label>
            <Select value={provider} onValueChange={(v) => setCfg((c) => ({ ...c, provider: v as NotifyConfig["provider"] }))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="slack">Slack</SelectItem>
                <SelectItem value="mattermost">Mattermost</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* 활성 provider 의 webhook 설정 */}
          <div className="space-y-1.5">
            <Label>{providerLabel} Incoming Webhook URL</Label>
            <Input
              value={sub.webhook_url}
              onChange={(e) => setSub("webhook_url", e.target.value)}
              placeholder={provider === "slack" ? "https://hooks.slack.com/services/..." : "https://mm.example.com/hooks/..."}
            />
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1.5">
              <Label>채널 (선택)</Label>
              <Input value={sub.channel} onChange={(e) => setSub("channel", e.target.value)} placeholder={provider === "slack" ? "#general" : "town-square"} />
            </div>
            <div className="space-y-1.5">
              <Label>표시 이름 (선택)</Label>
              <Input value={sub.username} onChange={(e) => setSub("username", e.target.value)} placeholder="Argus Catalog" />
            </div>
            <div className="space-y-1.5">
              <Label>아이콘 이모지 (선택)</Label>
              <Input value={sub.icon_emoji} onChange={(e) => setSub("icon_emoji", e.target.value)} placeholder=":bell:" />
            </div>
          </div>

          {/* 저장 / 테스트 */}
          <div className="space-y-2 border-t pt-4">
            <div className="space-y-1.5">
              <Label>테스트 메시지</Label>
              <Input value={testText} onChange={(e) => setTestText(e.target.value)} placeholder="테스트 메시지 내용" />
            </div>
            <div className="flex items-center gap-2">
              <Button onClick={handleSave} disabled={saving || testing}>
                {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Save className="h-4 w-4 mr-1" />}
                저장
              </Button>
              <Button variant="outline" onClick={handleTest} disabled={saving || testing}>
                {testing ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Play className="h-4 w-4 mr-1" />}
                테스트 메시지 발송
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">테스트는 현재 입력값을 저장한 뒤 활성 제공자({providerLabel})로 발송합니다.</p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
