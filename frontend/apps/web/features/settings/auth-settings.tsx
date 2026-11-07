"use client"

import { useCallback, useEffect, useState } from "react"
import { Check, CheckCircle2, Eye, EyeOff, Loader2, Play, Rocket, Save, SkipForward, X, XCircle } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"

import { useAuth } from "@/features/auth"
import {
  AuthChangeConfirmError,
  fetchAuthConfig, fetchAuthSecret, initializeKeycloak,
  testAuthConnection, updateAuthConfig,
  type InitStep,
} from "./api"
import { errorMessage, notifyError, notifySuccess, toast } from "@/lib/notify"

function StepIcon({ status }: { status: string }) {
  if (status === "ok") return <CheckCircle2 className="h-4 w-4 text-blue-500" />
  if (status === "created") return <CheckCircle2 className="h-4 w-4 text-emerald-500" />
  if (status === "skip") return <SkipForward className="h-4 w-4 text-muted-foreground" />
  return <XCircle className="h-4 w-4 text-red-500" />
}

function StepBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    ok: "bg-blue-50 text-blue-700 border-blue-200",
    created: "bg-emerald-50 text-emerald-700 border-emerald-200",
    skip: "bg-muted text-muted-foreground border-muted",
    error: "bg-red-50 text-red-700 border-red-200",
  }
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${colors[status] || colors.error}`}>
      {status.toUpperCase()}
    </span>
  )
}

export function AuthSettings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null)

  const [authType, setAuthType] = useState("keycloak")
  const [serverUrl, setServerUrl] = useState("")
  const [realm, setRealm] = useState("")
  const [clientId, setClientId] = useState("")
  const [clientSecret, setClientSecret] = useState("")
  const [adminRole, setAdminRole] = useState("")
  const [superuserRole, setSuperuserRole] = useState("")
  const [userRole, setUserRole] = useState("")
  const [showSecret, setShowSecret] = useState(false)
  const [realSecret, setRealSecret] = useState<string | null>(null)
  const [showAdminPass, setShowAdminPass] = useState(false)

  // Initialize dialog
  const [initOpen, setInitOpen] = useState(false)
  const [initRunning, setInitRunning] = useState(false)
  const [initSteps, setInitSteps] = useState<InitStep[]>([])
  const [initAdminUser, setInitAdminUser] = useState("admin")
  const [initAdminPass, setInitAdminPass] = useState("")

  const handleToggleSecret = async () => {
    if (!showSecret && realSecret === null) {
      try {
        const secret = await fetchAuthSecret()
        setRealSecret(secret)
        setClientSecret(secret)
      } catch {
        // ignore
      }
    }
    setShowSecret(!showSecret)
  }

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      const cfg = await fetchAuthConfig()
      setAuthType(cfg.type || "keycloak")
      setServerUrl(cfg.server_url)
      setRealm(cfg.realm)
      setClientId(cfg.client_id)
      setClientSecret(cfg.client_secret)
      setAdminRole(cfg.admin_role)
      setSuperuserRole(cfg.superuser_role)
      setUserRole(cfg.user_role)
    } catch {
      setMessage({ type: "error", text: "인증 설정을 불러오지 못했습니다." })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  // 저장하면 로그인이 끊기는 변경(방식 전환·Keycloak 연결 정보) — 서버가 알려 준 결과를 보여 주고 확인받는다
  const { logout } = useAuth()
  const [confirmMsg, setConfirmMsg] = useState<string | null>(null)

  const handleSave = async (confirm = false) => {
    setSaving(true)
    try {
      await updateAuthConfig({
        type: authType,
        server_url: serverUrl, realm, client_id: clientId, client_secret: clientSecret,
        admin_role: adminRole, superuser_role: superuserRole, user_role: userRole,
      }, confirm)
      if (confirm) {
        setConfirmMsg(null)
        await logout() // 현재 토큰은 새 설정에서 더 이상 유효하지 않다 — 다시 로그인
        return
      }
      notifySuccess(`인증 설정을 저장했습니다. (${authType} 모드)`)
    } catch (e) {
      if (e instanceof AuthChangeConfirmError) {
        setConfirmMsg(e.message)
      } else {
        setConfirmMsg(null)
        notifyError(e, "인증 설정을 저장하지 못했습니다.")
      }
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setMessage(null)
    try {
      const result = await testAuthConnection({
        type: "keycloak",
        server_url: serverUrl, realm, client_id: clientId, client_secret: clientSecret,
        admin_role: adminRole, superuser_role: superuserRole, user_role: userRole,
      })
      setMessage({ type: result.success ? "success" : "error", text: result.message })
    } catch (e) {
      setMessage({ type: "error", text: errorMessage(e) ?? "연결을 테스트하지 못했습니다." })
    } finally {
      setTesting(false)
    }
  }

  const handleOpenInit = () => {
    // Validate all required fields before opening the dialog
    const missing: string[] = []
    if (!serverUrl.trim()) missing.push("서버 URL")
    if (!initAdminUser.trim()) missing.push("관리자 사용자명")
    if (!initAdminPass.trim()) missing.push("관리자 비밀번호")
    if (!realm.trim()) missing.push("Realm")
    if (!clientId.trim()) missing.push("Client ID")
    if (!adminRole.trim()) missing.push("Admin Role")
    if (!superuserRole.trim()) missing.push("Superuser Role")
    if (!userRole.trim()) missing.push("User Role")

    if (missing.length > 0) {
      toast.error("필수 항목이 비어 있습니다.", { description: missing.join(", ") })
      return
    }
    setInitSteps([])
    setInitOpen(true)
  }

  const handleInitialize = async () => {
    setInitRunning(true)
    setInitSteps([])
    try {
      // 마스킹 값·빈 값은 그대로 보낸다 — 서버가 저장된 secret 을 쓰고, 없으면 새로 생성해 저장한다
      const secret = clientSecret === "••••••••" && realSecret ? realSecret : clientSecret

      const result = await initializeKeycloak({
        server_url: serverUrl,
        admin_username: initAdminUser,
        admin_password: initAdminPass,
        realm,
        client_id: clientId,
        client_secret: secret,
        roles: [adminRole, superuserRole, userRole].filter(Boolean),
      })
      setInitSteps(result.steps)
      // 서버가 secret 을 설정에 저장했으면 화면 값도 다시 읽는다(마스킹 표시)
      if (result.steps.some((st) => st.step === "설정 저장" && st.status === "created")) {
        setRealSecret(null)
        setShowSecret(false)
        const cfg = await fetchAuthConfig()
        setClientSecret(cfg.client_secret)
      }
    } catch (e) {
      setInitSteps([{ step: "초기화", status: "error", message: errorMessage(e) ?? "Keycloak 을 초기화하지 못했습니다." }])
    } finally {
      setInitRunning(false)
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
  }

  const hasErrors = initSteps.some((s) => s.status === "error")
  const createdCount = initSteps.filter((s) => s.status === "created").length

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
          <CardTitle className="text-base">인증</CardTitle>
          <CardDescription>사용자 로그인 인증 방식을 설정합니다.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Auth type selector */}
          <div className="space-y-1.5">
            <Label>인증 방식</Label>
            <Select value={authType} onValueChange={setAuthType}>
              <SelectTrigger className="w-[240px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="local">Local (내장 사용자 관리)</SelectItem>
                <SelectItem value="keycloak">Keycloak OIDC (SSO)</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {authType === "local"
                ? "사용자를 DB에서 로컬로 관리합니다. 외부 ID 제공자가 필요하지 않습니다."
                : "Keycloak을 통해 인증합니다. 실행 중인 Keycloak 서버가 필요합니다."
              }
            </p>
          </div>

          {authType === "keycloak" && (
          <>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>서버 URL <span className="text-red-500">*</span></Label>
              <Input value={serverUrl} onChange={(e) => setServerUrl(e.target.value)} placeholder="http://localhost:8180" />
            </div>
            <div className="space-y-1.5">
              <Label>Realm <span className="text-red-500">*</span></Label>
              <Input value={realm} onChange={(e) => setRealm(e.target.value)} placeholder="argus" />
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>관리자 사용자명</Label>
              <Input value={initAdminUser} onChange={(e) => setInitAdminUser(e.target.value)} placeholder="admin" />
              <p className="text-xs text-muted-foreground">초기화에 사용할 Keycloak 관리자 계정입니다.</p>
            </div>
            <div className="space-y-1.5">
              <Label>관리자 비밀번호</Label>
              <div className="relative">
                <Input
                  type={showAdminPass ? "text" : "password"}
                  value={initAdminPass}
                  onChange={(e) => setInitAdminPass(e.target.value)}
                  placeholder="초기화 때만 사용 (저장하지 않음)"
                  className="pr-10"
                />
                <button
                  type="button"
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground"
                  onClick={() => setShowAdminPass(!showAdminPass)}
                >
                  {showAdminPass ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Client ID <span className="text-red-500">*</span></Label>
              <Input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="argus-client" />
            </div>
            <div className="space-y-1.5">
              <Label>Client Secret <span className="text-red-500">*</span></Label>
              <div className="relative">
                <Input
                  type={showSecret ? "text" : "password"}
                  value={clientSecret}
                  onChange={(e) => setClientSecret(e.target.value)}
                  placeholder="••••••••"
                  className="pr-10"
                />
                <button
                  type="button"
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground"
                  onClick={handleToggleSecret}
                >
                  {showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              <p className="text-xs text-muted-foreground">
                변경하지 않으면 기존 secret이 유지됩니다. 비워 둔 채 Keycloak 초기화를 실행하면 임의의 secret을 새로 만들어 저장합니다.
              </p>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label>Admin Role</Label>
              <Input value={adminRole} readOnly className="bg-muted" />
            </div>
            <div className="space-y-1.5">
              <Label>Superuser Role</Label>
              <Input value={superuserRole} readOnly className="bg-muted" />
            </div>
            <div className="space-y-1.5">
              <Label>User Role</Label>
              <Input value={userRole} readOnly className="bg-muted" />
            </div>
          </div>
          </>
          )}

          <div className="flex items-center gap-2 pt-2">
            <Button onClick={() => void handleSave()} disabled={saving}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Save className="h-4 w-4 mr-1" />}
              저장
            </Button>
            {authType === "keycloak" && (
              <>
                <Button variant="outline" onClick={handleTest} disabled={testing}>
                  {testing ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Play className="h-4 w-4 mr-1" />}
                  연결 테스트
                </Button>
                <Button variant="outline" onClick={handleOpenInit}>
                  <Rocket className="h-4 w-4 mr-1" />
                  초기화
                </Button>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Initialize Dialog */}
      <Dialog open={confirmMsg !== null} onOpenChange={(open) => { if (!open && !saving) setConfirmMsg(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>인증 설정 변경 확인</DialogTitle>
            <DialogDescription>{confirmMsg}</DialogDescription>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">
            새 설정으로 로그인할 수 없게 되면 서버를 환경변수 <code>ARGUS_AUTH_FORCE_LOCAL=true</code> 로
            재기동해 로컬 관리자 계정으로 복구할 수 있습니다.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmMsg(null)} disabled={saving}>취소</Button>
            <Button onClick={() => void handleSave(true)} disabled={saving}>
              {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
              저장하고 다시 로그인
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={initOpen} onOpenChange={(open) => { if (!initRunning) setInitOpen(open) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Rocket className="h-5 w-5" />
              Keycloak 초기화
            </DialogTitle>
            <DialogDescription>
              Keycloak에 realm, client, role을 자동으로 생성합니다.
              이미 존재하는 리소스는 건너뜁니다. client는 서버 간 로그인(password grant)만 허용하고
              브라우저 리다이렉트·서비스 계정은 끈 상태로 만들며, 예전 설정으로 만든 client도 그렇게 좁힙니다.
            </DialogDescription>
          </DialogHeader>

          {initSteps.length === 0 && (
            <div className="py-2">
              <p className="text-sm text-muted-foreground">
                위에서 설정한 관리자 사용자명/비밀번호로 Keycloak Admin API에 접근합니다.
              </p>
            </div>
          )}

          {/* Progress steps */}
          {initSteps.length > 0 && (
            <div className="space-y-2 py-2">
              {initSteps.map((s, i) => (
                <div key={i} className="flex items-start gap-2 text-sm">
                  <StepIcon status={s.status} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{s.step}</span>
                      <StepBadge status={s.status} />
                    </div>
                    <p className="text-xs text-muted-foreground">{s.message}</p>
                  </div>
                </div>
              ))}

              {!initRunning && (
                <div className={`mt-3 rounded-md px-3 py-2 text-sm ${
                  hasErrors
                    ? "bg-red-50 text-red-700 border border-red-200"
                    : "bg-emerald-50 text-emerald-700 border border-emerald-200"
                }`}>
                  {hasErrors
                    ? "초기화 중 오류가 발생했습니다."
                    : createdCount > 0
                      ? `초기화 완료. ${createdCount}개 리소스가 생성되었습니다.`
                      : "이미 모든 리소스가 존재합니다."
                  }
                </div>
              )}
            </div>
          )}

          {/* Loading indicator */}
          {initRunning && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              초기화 중...
            </div>
          )}

          <DialogFooter>
            {initSteps.length === 0 ? (
              <>
                <Button variant="outline" onClick={() => setInitOpen(false)}>취소</Button>
                <Button onClick={handleInitialize} disabled={initRunning || !initAdminUser || !initAdminPass}>
                  {initRunning ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Rocket className="h-4 w-4 mr-1" />}
                  시작
                </Button>
              </>
            ) : (
              <Button onClick={() => { setInitOpen(false); setInitSteps([]) }}>
                닫기
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
