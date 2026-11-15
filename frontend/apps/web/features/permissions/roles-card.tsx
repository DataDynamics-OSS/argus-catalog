"use client"

/**
 * 권한 관리 › 역할 — 기본 역할과 사용자 정의 역할(TODO-124).
 *
 * - 사용자 정의 역할은 기반 역할(사용자 · 슈퍼유저)의 권한을 복사해 시작하고, 권한 표에 열로 더해진다.
 * - 역할 이름으로 판정하는 기능(슈퍼유저 전용 작업 등)은 기반 역할 기준이다.
 * - 쓰는 사용자 · API 키가 있으면 지울 수 없다(서버 409).
 * - Keycloak 모드에서는 같은 이름의 realm 역할을 사용자에게 주면 그 역할로 판정한다.
 */

import { useState } from "react"
import { Pencil, Plus, Trash2, Users } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
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
import { StatusBadge } from "@workspace/ui/components/status-badge"
import { useConfirm } from "@/components/confirm-provider"
import { DisabledReason, Tip } from "@/components/tip"
import { authFetch, throwOnError } from "@/features/auth/auth-fetch"
import { invalidateRoleOptions } from "@/features/users/use-role-options"
import { notifyError, toast } from "@/lib/notify"

export type PermissionRole = {
  role_id: string
  name: string
  description: string | null
  builtin: boolean
  base_role: string | null
  user_count: number
  api_key_count: number
}

export const BASE_ROLE_OPTIONS = [
  { value: "argus-user", label: "사용자 (User)" },
  { value: "argus-superuser", label: "슈퍼유저 (Superuser)" },
] as const

const ROLE_ID_RE = /^[a-z][a-z0-9-]{1,48}[a-z0-9]$/

function baseLabel(base: string | null): string {
  return BASE_ROLE_OPTIONS.find((o) => o.value === base)?.label ?? "-"
}

type Draft = { role_id: string; name: string; description: string; base_role: string }

export function RolesCard({ roles, onChanged }: { roles: PermissionRole[] | null; onChanged: () => void }) {
  const askConfirm = useConfirm()
  const [editing, setEditing] = useState<{ mode: "create" | "edit"; draft: Draft } | null>(null)
  const [saving, setSaving] = useState(false)

  const save = async () => {
    if (!editing) return
    const d = editing.draft
    setSaving(true)
    try {
      const create = editing.mode === "create"
      const res = await authFetch(
        create ? "/api/v1/permissions/roles" : `/api/v1/permissions/roles/${encodeURIComponent(d.role_id)}`,
        {
          method: create ? "POST" : "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...(create ? { role_id: d.role_id.trim() } : {}),
            name: d.name.trim(),
            description: d.description.trim(),
            base_role: d.base_role,
          }),
        },
      )
      if (!res.ok) await throwOnError(res, create ? "역할을 추가하지 못했습니다." : "역할을 저장하지 못했습니다.")
      toast.success(create ? `역할 '${d.name.trim()}'을 추가했습니다.` : `역할 '${d.name.trim()}'을 저장했습니다.`)
      setEditing(null)
      invalidateRoleOptions()
      onChanged()
    } catch (err) {
      notifyError(err, editing.mode === "create" ? "역할을 추가하지 못했습니다." : "역할을 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  const remove = async (r: PermissionRole) => {
    if (!(await askConfirm({
      title: "역할 삭제",
      description: `'${r.name}'(${r.role_id}) 역할을 지울까요? 권한 표에서 이 역할 열이 사라집니다.`,
      confirmText: "삭제",
      destructive: true,
    }))) return
    try {
      const res = await authFetch(`/api/v1/permissions/roles/${encodeURIComponent(r.role_id)}`, { method: "DELETE" })
      if (!res.ok) await throwOnError(res, "역할을 삭제하지 못했습니다.")
      toast.success(`역할 '${r.name}'을 삭제했습니다.`)
      invalidateRoleOptions()
      onChanged()
    } catch (err) {
      notifyError(err, "역할을 삭제하지 못했습니다.")
    }
  }

  const d = editing?.draft
  const idInvalid = editing?.mode === "create" && !!d && d.role_id.trim() !== "" && !ROLE_ID_RE.test(d.role_id.trim())
  const canSave = !!d && d.name.trim() !== "" && (editing?.mode === "edit" || ROLE_ID_RE.test(d.role_id.trim()))
  const setDraft = (patch: Partial<Draft>) =>
    setEditing((e) => (e ? { ...e, draft: { ...e.draft, ...patch } } : e))

  return (
    <Card>
      <CardContent className="p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="flex items-center gap-1.5 font-semibold">
              <Users className="h-4 w-4 text-muted-foreground" /> 역할
            </h2>
            <p className="mt-0.5 text-xs">
              부서 · 직무별 역할을 만들어 아래 권한 표의 열로 씁니다. 새 역할은 기반 역할의 권한을 복사해 시작하고,
              사용자 관리 · API 키에서 지정합니다. Keycloak 을 쓰면 같은 이름의 realm 역할을 사용자에게 주세요.
            </p>
          </div>
          <Button size="sm" onClick={() => setEditing({ mode: "create", draft: { role_id: "", name: "", description: "", base_role: "argus-user" } })}>
            <Plus className="mr-1 h-4 w-4" /> 역할 추가
          </Button>
        </div>
        {!roles ? (
          <p className="text-sm text-muted-foreground">역할을 불러오는 중…</p>
        ) : (
          <div className="overflow-auto rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-muted/60 text-xs">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">이름</th>
                  <th className="px-3 py-2 text-left font-medium">식별자</th>
                  <th className="px-3 py-2 text-left font-medium">기반 역할</th>
                  <th className="px-3 py-2 text-right font-medium">사용자</th>
                  <th className="px-3 py-2 text-right font-medium">API 키</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {roles.map((r) => (
                  <tr key={r.role_id} className="border-t border-border/60">
                    <td className="px-3 py-2">
                      <div className="font-medium">
                        {r.name}
                        {r.builtin && <StatusBadge tone="neutral" size="sm" className="ml-1.5 align-middle font-normal">기본</StatusBadge>}
                      </div>
                      {r.description && <div className="text-xs">{r.description}</div>}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{r.role_id}</td>
                    <td className="px-3 py-2">{r.builtin ? "-" : baseLabel(r.base_role)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{r.user_count}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{r.api_key_count}</td>
                    <td className="px-3 py-2 text-right">
                      {!r.builtin && (
                        <div className="flex justify-end gap-1">
                          <Tip label="역할 수정">
                            <Button variant="ghost" size="sm" aria-label={`${r.name} 수정`}
                              onClick={() => setEditing({ mode: "edit", draft: { role_id: r.role_id, name: r.name, description: r.description ?? "", base_role: r.base_role ?? "argus-user" } })}>
                              <Pencil className="h-4 w-4" />
                            </Button>
                          </Tip>
                          <DisabledReason
                            reason={r.user_count > 0 || r.api_key_count > 0
                              ? "이 역할을 쓰는 사용자 · API 키가 있어 지울 수 없습니다" : undefined}>
                            <Tip label="역할 삭제">
                              <Button variant="ghost" size="sm" aria-label={`${r.name} 삭제`}
                                disabled={r.user_count > 0 || r.api_key_count > 0}
                                onClick={() => void remove(r)}>
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </Tip>
                          </DisabledReason>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>

      <Dialog open={!!editing} onOpenChange={(o) => { if (!o) setEditing(null) }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{editing?.mode === "create" ? "역할 추가" : "역할 수정"}</DialogTitle>
            <DialogDescription>
              {editing?.mode === "create"
                ? "기반 역할의 메뉴 · 기능 권한을 복사해 만듭니다. 만든 뒤 권한 표에서 이 역할 열을 조정하세요."
                : "기반 역할을 바꿔도 권한 표의 체크는 그대로입니다. 역할 이름으로 판정하는 기능만 새 기반 역할을 따릅니다."}
            </DialogDescription>
          </DialogHeader>
          {d && (
            <div className="grid gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="role-id">식별자</Label>
                <Input id="role-id" value={d.role_id} disabled={editing?.mode === "edit"} placeholder="data-steward"
                  aria-invalid={idInvalid} onChange={(e) => setDraft({ role_id: e.target.value })} />
                {idInvalid && (
                  <p className="text-xs text-destructive">소문자로 시작하는 소문자 · 숫자 · 하이픈 3~50자로 입력하세요.</p>
                )}
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="role-name">이름</Label>
                <Input id="role-name" value={d.name} maxLength={50} placeholder="데이터 관리자"
                  onChange={(e) => setDraft({ name: e.target.value })} />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="role-desc">설명</Label>
                <Input id="role-desc" value={d.description} maxLength={255}
                  onChange={(e) => setDraft({ description: e.target.value })} />
              </div>
              <fieldset className="grid gap-1.5">
                <legend className="mb-1.5 text-sm font-medium">기반 역할</legend>
                {BASE_ROLE_OPTIONS.map((o) => (
                  <label key={o.value} className="flex items-center gap-2 text-sm">
                    <input type="radio" name="base-role" value={o.value} checked={d.base_role === o.value}
                      onChange={() => setDraft({ base_role: o.value })} className="h-4 w-4 accent-primary" />
                    {o.label}
                  </label>
                ))}
              </fieldset>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)} disabled={saving}>취소</Button>
            <Button onClick={() => void save()} disabled={!canSave || saving}>{saving ? "저장 중…" : "저장"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
