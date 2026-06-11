"use client"

import { useCallback, useEffect, useState } from "react"
import { Check, Loader2, Play, Save, X } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Switch } from "@workspace/ui/components/switch"

import { fetchMailConfig, updateMailConfig, testMail, type MailConfig } from "./api"

const EMPTY: MailConfig = {
  enabled: false,
  smtp_host: "smtp.gmail.com",
  smtp_port: 587,
  use_tls: true,
  use_ssl: false,
  timeout_seconds: 10,
  from_email: "",
  from_name: "Argus Catalog",
  smtp_user: "",
  smtp_password: "",
  subject_prefix: "[Argus Catalog]",
  default_recipients: "",
}

export function EmailSettings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null)
  const [cfg, setCfg] = useState<MailConfig>(EMPTY)
  const [testTo, setTestTo] = useState("")

  const set = <K extends keyof MailConfig>(key: K, value: MailConfig[K]) =>
    setCfg((c) => ({ ...c, [key]: value }))

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      setCfg(await fetchMailConfig())
    } catch {
      setMessage({ type: "error", text: "메일 설정을 불러오지 못했습니다." })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  const handleSave = async () => {
    setSaving(true)
    setMessage(null)
    try {
      await updateMailConfig(cfg)
      setMessage({ type: "success", text: "메일 설정을 저장했습니다." })
      await loadConfig() // 비밀번호 마스킹 값 재반영
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
      // 테스트는 저장된 설정으로 발송되므로, 먼저 저장 후 테스트한다.
      await updateMailConfig(cfg)
      const result = await testMail({ to: testTo })
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
          <CardTitle className="text-base">이메일 (SMTP)</CardTitle>
          <CardDescription>
            알림·공유 등에 사용할 SMTP 발송 설정입니다. Gmail 은 2단계 인증 후 앱 비밀번호를 사용하세요.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* 활성화 */}
          <div className="flex items-center justify-between">
            <div>
              <Label>이메일 발송 활성화</Label>
              <p className="text-xs text-muted-foreground mt-0.5">비활성화 시 발송 요청은 조용히 무시됩니다.</p>
            </div>
            <Switch checked={cfg.enabled} onCheckedChange={(v) => set("enabled", v)} />
          </div>

          {/* SMTP 호스트/포트 */}
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2 space-y-1.5">
              <Label>SMTP 호스트</Label>
              <Input value={cfg.smtp_host} onChange={(e) => set("smtp_host", e.target.value)} placeholder="smtp.gmail.com" />
            </div>
            <div className="space-y-1.5">
              <Label>포트</Label>
              <Input type="number" value={cfg.smtp_port} onChange={(e) => set("smtp_port", Number(e.target.value) || 0)} placeholder="587" />
            </div>
          </div>

          {/* 보안 */}
          <div className="grid grid-cols-2 gap-3">
            <div className="flex items-center justify-between rounded-md border px-3 py-2">
              <div>
                <Label className="text-sm">STARTTLS</Label>
                <p className="text-xs text-muted-foreground mt-0.5">587 포트</p>
              </div>
              <Switch checked={cfg.use_tls} onCheckedChange={(v) => set("use_tls", v)} />
            </div>
            <div className="flex items-center justify-between rounded-md border px-3 py-2">
              <div>
                <Label className="text-sm">SSL</Label>
                <p className="text-xs text-muted-foreground mt-0.5">465 포트</p>
              </div>
              <Switch checked={cfg.use_ssl} onCheckedChange={(v) => set("use_ssl", v)} />
            </div>
          </div>

          {/* 발신자 */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>발신자 이메일</Label>
              <Input value={cfg.from_email} onChange={(e) => set("from_email", e.target.value)} placeholder="noreply@example.com" />
            </div>
            <div className="space-y-1.5">
              <Label>발신자 표시 이름</Label>
              <Input value={cfg.from_name} onChange={(e) => set("from_name", e.target.value)} placeholder="Argus Catalog" />
            </div>
          </div>

          {/* 인증 */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>SMTP 사용자 (선택)</Label>
              <Input value={cfg.smtp_user} onChange={(e) => set("smtp_user", e.target.value)} placeholder="비우면 발신자 이메일 사용" />
            </div>
            <div className="space-y-1.5">
              <Label>비밀번호 / 앱 비밀번호</Label>
              <Input type="password" value={cfg.smtp_password} onChange={(e) => set("smtp_password", e.target.value)} placeholder="••••••••" />
            </div>
          </div>

          {/* 제목 접두어 / 기본 수신자 */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>제목 접두어</Label>
              <Input value={cfg.subject_prefix} onChange={(e) => set("subject_prefix", e.target.value)} placeholder="[Argus Catalog]" />
            </div>
            <div className="space-y-1.5">
              <Label>기본 수신자 (선택)</Label>
              <Input value={cfg.default_recipients} onChange={(e) => set("default_recipients", e.target.value)} placeholder="a@x.com, b@y.com" />
            </div>
          </div>

          {/* 저장 / 테스트 */}
          <div className="space-y-2 border-t pt-4">
            <div className="space-y-1.5">
              <Label>테스트 수신자 (선택)</Label>
              <Input value={testTo} onChange={(e) => setTestTo(e.target.value)} placeholder="비우면 기본 수신자/발신자 본인에게 발송" />
            </div>
            <div className="flex items-center gap-2">
              <Button onClick={handleSave} disabled={saving || testing}>
                {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Save className="h-4 w-4 mr-1" />}
                저장
              </Button>
              <Button variant="outline" onClick={handleTest} disabled={saving || testing}>
                {testing ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Play className="h-4 w-4 mr-1" />}
                테스트 메일 발송
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">테스트는 현재 입력값을 저장한 뒤 발송합니다.</p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
