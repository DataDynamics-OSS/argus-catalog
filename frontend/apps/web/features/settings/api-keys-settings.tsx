"use client"

/**
 * 설정 > API 키 — 백엔드를 부르는 프로그램(수집·AI·품질·SDK·NiFi 등)의 자격을 발급·관리한다 (관리자).
 *
 * 평문 키는 발급·재발급 직후 한 번만 보여 준다. 키는 역할(사용자와 같은 권한 기준)과 범위(부를 수 있는 API 묶음)를
 * 함께 따른다. 중지·재발급·폐기는 바로 반영된다(서버가 여러 대면 다른 서버는 최대 30초 늦게).
 */

import { Tip } from "@/components/tip"
import { useCallback, useEffect, useState } from "react"
import { Copy, KeyRound, Loader2, Plus, RefreshCw } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { StatusBadge } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Checkbox } from "@workspace/ui/components/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { Switch } from "@workspace/ui/components/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"

import {
  type ApiKeyCreated,
  type ApiKeyInfo,
  type ApiKeyScope,
  createApiKey,
  fetchApiKeyScopes,
  fetchApiKeys,
  revokeApiKey,
  rotateApiKey,
  updateApiKey,
} from "./api"
import { notifyError, notifySuccess } from "@/lib/notify"
import { copyText } from "@/lib/clipboard"
import { useAuth } from "@/features/auth"
import { useCanAssignRole, useRoleOptions } from "@/features/users/use-role-options"

// 기본 역할 — 사용자 정의 역할(TODO-124)은 useRoleOptions 로 뒤에 붙는다
const ROLES = [
  { value: "argus-user", label: "사용자" },
  { value: "argus-superuser", label: "슈퍼유저" },
  { value: "argus-admin", label: "관리자" },
]

const EXPIRY = [
  { value: "", label: "서버 기본값" },
  { value: "30", label: "30일" },
  { value: "90", label: "90일" },
  { value: "365", label: "1년" },
  { value: "0", label: "무기한" },
]

function fmt(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString("ko-KR", { hour12: false }) : "-"
}

function roleLabel(role: string, custom: readonly { value: string; label: string }[] = []): string {
  return ROLES.find((r) => r.value === role)?.label ?? custom.find((r) => r.value === role)?.label ?? role
}

function statusBadge(k: ApiKeyInfo) {
  // 톤(TODO-107): 사용 초록 · 중지 테두리 · 만료 빨강 · 폐기 무채색
  if (k.status === "revoked") return <StatusBadge tone="muted">폐기</StatusBadge>
  if (k.status === "disabled") return <StatusBadge tone="neutral">중지</StatusBadge>
  if (k.expires_at && new Date(k.expires_at) <= new Date()) return <StatusBadge tone="danger">만료</StatusBadge>
  return <StatusBadge tone="success">사용</StatusBadge>
}

type Confirm = { kind: "rotate" | "revoke"; key: ApiKeyInfo } | null

export function ApiKeysSettings() {
  // API 키 관리를 위임받은 사람(관리자 아님)은 관리자 역할 키를 다룰 수 없다(서버도 403, TODO-123)
  const { user } = useAuth()
  const isAdmin = !!user?.is_admin
  // 위임받은 사람은 기본 사용자 역할과 자기 역할의 키만 다룬다(서버 403, DEF-163)
  const canAssignRole = useCanAssignRole(isAdmin, user?.username)
  const roleOptions = useRoleOptions()
  const [keys, setKeys] = useState<ApiKeyInfo[]>([])
  const [scopes, setScopes] = useState<ApiKeyScope[]>([])
  const [loading, setLoading] = useState(true)
  const [includeRevoked, setIncludeRevoked] = useState(false)
  const [creating, setCreating] = useState(false)
  const [issued, setIssued] = useState<ApiKeyCreated | null>(null)
  const [confirm, setConfirm] = useState<Confirm>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [list, sc] = await Promise.all([fetchApiKeys(includeRevoked), fetchApiKeyScopes()])
      setKeys(list)
      setScopes(sc)
    } catch (e) {
      notifyError(e, "API 키 목록을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [includeRevoked])

  useEffect(() => { load() }, [load])

  const scopeLabel = (code: string) => scopes.find((s) => s.code === code)?.label ?? code

  const toggle = async (k: ApiKeyInfo, enabled: boolean) => {
    try {
      await updateApiKey(k.id, { enabled })
      notifySuccess(enabled ? `"${k.name}" 키를 다시 사용합니다.` : `"${k.name}" 키를 중지했습니다.`)
      load()
    } catch (e) {
      notifyError(e, "API 키를 바꾸지 못했습니다.")
    }
  }

  const runConfirm = async () => {
    if (!confirm) return
    setBusy(true)
    try {
      if (confirm.kind === "rotate") {
        setIssued(await rotateApiKey(confirm.key.id))
      } else {
        await revokeApiKey(confirm.key.id)
        notifySuccess(`"${confirm.key.name}" 키를 폐기했습니다.`)
      }
      setConfirm(null)
      load()
    } catch (e) {
      notifyError(e, confirm.kind === "rotate" ? "재발급하지 못했습니다." : "폐기하지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRound className="h-4 w-4" /> API 키
          </CardTitle>
          <CardDescription>
            카탈로그 백엔드를 부르는 프로그램(메타데이터 수집, AI 서비스, 품질 워커, LDAP 동기화, SDK, NiFi 등)은 사용자
            계정 대신 API 키로 접속합니다. 요청 헤더에 <code className="rounded bg-muted px-1">X-API-Key: 키</code> 또는{" "}
            <code className="rounded bg-muted px-1">Authorization: Bearer 키</code> 를 넣습니다. Basic 인증만 되는
            클라이언트(스키마 레지스트리)는 비밀번호 자리에 키를 넣습니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={includeRevoked} onCheckedChange={(v) => setIncludeRevoked(v === true)} />
              폐기한 키도 보기
            </label>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={load} disabled={loading}>
                <RefreshCw className="mr-1 h-4 w-4" /> 새로고침
              </Button>
              <Button size="sm" onClick={() => setCreating(true)}>
                <Plus className="mr-1 h-4 w-4" /> 발급
              </Button>
            </div>
          </div>

          {loading ? (
            <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
          ) : keys.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">발급한 API 키가 없습니다.</p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>이름</TableHead>
                    <TableHead>키</TableHead>
                    <TableHead>역할 · 범위</TableHead>
                    <TableHead>상태</TableHead>
                    <TableHead>만료</TableHead>
                    <TableHead>마지막 사용</TableHead>
                    <TableHead className="text-right">작업</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {keys.map((k) => (
                    <TableRow key={k.id}>
                      <TableCell>
                        <div className="font-medium">{k.name}</div>
                        <div className="text-xs">
                          apikey#{k.id}{k.description ? ` · ${k.description}` : ""}
                        </div>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{k.key_prefix}…</TableCell>
                      <TableCell>
                        <div className="text-sm">{roleLabel(k.role_id, roleOptions)}</div>
                        <div className="flex flex-wrap gap-1 pt-1">
                          {k.scopes.map((s) => <Badge key={s} variant="outline">{scopeLabel(s)}</Badge>)}
                        </div>
                      </TableCell>
                      <TableCell>{statusBadge(k)}</TableCell>
                      <TableCell className="text-xs">{k.expires_at ? fmt(k.expires_at) : "무기한"}</TableCell>
                      <TableCell className="text-xs">
                        {fmt(k.last_used_at)}
                        {k.last_used_ip && <div >{k.last_used_ip}</div>}
                      </TableCell>
                      <TableCell className="text-right">
                        {k.status === "revoked" ? (
                          <span className="text-xs">{fmt(k.revoked_at)} · {k.revoked_by}</span>
                        ) : !canAssignRole(k.role_id) ? (
                          <span className="text-xs">관리자만 변경</span>
                        ) : (
                          <div className="flex items-center justify-end gap-2">
                            <Switch
                              checked={k.status === "active"}
                              onCheckedChange={(v) => toggle(k, v)}
                              aria-label="사용"
                            />
                            <Button variant="outline" size="sm" onClick={() => setConfirm({ kind: "rotate", key: k })}>
                              재발급
                            </Button>
                            <Button variant="outline" size="sm" className="text-destructive"
                              onClick={() => setConfirm({ kind: "revoke", key: k })}>
                              폐기
                            </Button>
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <CreateDialog
        open={creating}
        scopes={scopes}
        onClose={() => setCreating(false)}
        onCreated={(k) => { setCreating(false); setIssued(k); load() }}
      />
      <IssuedDialog issued={issued} onClose={() => setIssued(null)} />

      <Dialog open={confirm !== null} onOpenChange={(o) => !o && setConfirm(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{confirm?.kind === "rotate" ? "API 키 재발급" : "API 키 폐기"}</DialogTitle>
            <DialogDescription>
              {confirm?.kind === "rotate"
                ? `"${confirm?.key.name}" 키에 새 값을 만듭니다. 지금 값은 바로 쓸 수 없으니, 이 키를 쓰는 프로그램 설정을 새 값으로 바꾸세요.`
                : `"${confirm?.key.name}" 키를 폐기합니다. 바로 쓸 수 없게 되며 되돌릴 수 없습니다(이력은 남습니다).`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(null)} disabled={busy}>취소</Button>
            <Button variant={confirm?.kind === "revoke" ? "destructive" : "default"} onClick={runConfirm} disabled={busy}>
              {busy && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
              {confirm?.kind === "rotate" ? "재발급" : "폐기"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function CreateDialog({ open, scopes, onClose, onCreated }: {
  open: boolean
  scopes: ApiKeyScope[]
  onClose: () => void
  onCreated: (k: ApiKeyCreated) => void
}) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [role, setRole] = useState("argus-user")
  const { user } = useAuth()
  const canAssignRole = useCanAssignRole(!!user?.is_admin, user?.username)
  // 사용자 정의 역할(TODO-124) — 기능을 좁힌 역할을 키에 주면 키가 쓸 수 있는 기능도 그만큼 좁아진다
  const customRoles = useRoleOptions().filter((r) => r.custom)
  const [selected, setSelected] = useState<string[]>(["catalog:read"])
  const [expiry, setExpiry] = useState("")
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open) {
      setName(""); setDescription(""); setRole("argus-user"); setSelected(["catalog:read"]); setExpiry("")
    }
  }, [open])

  const flip = (code: string, on: boolean) =>
    setSelected((prev) => (on ? [...new Set([...prev, code])] : prev.filter((c) => c !== code)))

  const submit = async () => {
    setSaving(true)
    try {
      onCreated(await createApiKey({
        name: name.trim(),
        description: description.trim() || undefined,
        role,
        scopes: selected,
        expires_in_days: expiry === "" ? null : Number(expiry),
      }))
    } catch (e) {
      notifyError(e, "API 키를 발급하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>API 키 발급</DialogTitle>
          <DialogDescription>키는 발급 직후 한 번만 보여 줍니다.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="apikey-name">이름</Label>
            <Input id="apikey-name" value={name} maxLength={100} placeholder="예: metadata-sync, nifi-flow"
              onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="apikey-desc">설명</Label>
            <Input id="apikey-desc" value={description} maxLength={500} placeholder="어디에서 쓰는지"
              onChange={(e) => setDescription(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>역할</Label>
              <Select value={role} onValueChange={(v) => v && setRole(v)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {[...ROLES, ...customRoles].filter((r) => canAssignRole(r.value))
                    .map((r) => <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>만료</Label>
              <Select value={expiry === "" ? "default" : expiry} onValueChange={(v) => setExpiry(!v || v === "default" ? "" : v)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {EXPIRY.map((x) => (
                    <SelectItem key={x.value || "default"} value={x.value || "default"}>{x.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-2">
            <Label>범위</Label>
            {scopes.map((s) => (
              <label key={s.code} className="flex items-start gap-2 text-sm">
                <Checkbox className="mt-0.5" checked={selected.includes(s.code)}
                  onCheckedChange={(v) => flip(s.code, v === true)} />
                <span>
                  <span className="font-medium">{s.label}</span>{" "}
                  <code className="text-xs">{s.code}</code>
                  <span className="block text-xs">{s.description}</span>
                </span>
              </label>
            ))}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>취소</Button>
          <Button onClick={submit} disabled={saving || !name.trim() || selected.length === 0}>
            {saving && <Loader2 className="mr-1 h-4 w-4 animate-spin" />} 발급
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function IssuedDialog({ issued, onClose }: { issued: ApiKeyCreated | null; onClose: () => void }) {
  const copy = async () => {
    if (!issued) return
    try {
      await copyText(issued.api_key)
      notifySuccess("키를 복사했습니다.")
    } catch (e) {
      notifyError(e, "복사하지 못했습니다. 직접 선택해 복사하세요.")
    }
  }
  return (
    <Dialog open={issued !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>&quot;{issued?.name}&quot; 키</DialogTitle>
          <DialogDescription>
            이 창을 닫으면 키를 다시 볼 수 없습니다. 지금 복사해 프로그램 설정(예: 환경변수{" "}
            <code className="rounded bg-muted px-1">ARGUS_CATALOG_API_KEY</code>)에 넣으세요.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2">
          <Input readOnly value={issued?.api_key ?? ""} className="font-mono text-xs"
            onFocus={(e) => e.currentTarget.select()} />
          <Tip label="복사"><Button variant="outline" size="icon" onClick={copy} aria-label="복사">
            <Copy className="h-4 w-4" />
          </Button></Tip>
        </div>
        <DialogFooter>
          <Button onClick={onClose}>확인</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
