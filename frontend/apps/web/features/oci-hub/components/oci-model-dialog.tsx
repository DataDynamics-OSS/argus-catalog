"use client"

import { useEffect, useState } from "react"
import { Loader2 } from "lucide-react"
import { errorMessage, toast } from "@/lib/notify"
import { useConfirm } from "@/components/confirm-provider"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import { Textarea } from "@workspace/ui/components/textarea"
import { useAuth } from "@/features/auth"

import {
  createOciModel, updateOciModel,
  OCI_ADMIN_ONLY_STATUSES, OCI_STATUSES, OCI_STATUS_LABELS,
  type OciModelDetail,
} from "@/features/oci-hub/api"

// 서버(validate_model_name) 와 같은 규칙: 영문·숫자로 시작, 영문·숫자·. _ - 만, 128자 이하.
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/

type Form = {
  name: string
  display_name: string
  description: string
  task: string
  framework: string
  language: string
  license: string
  owner: string
  status: string
}

function toForm(m: OciModelDetail | null): Form {
  return {
    name: m?.name ?? "",
    display_name: m?.display_name ?? "",
    description: m?.description ?? "",
    task: m?.task ?? "",
    framework: m?.framework ?? "",
    language: m?.language ?? "",
    license: m?.license ?? "",
    owner: m?.owner ?? "",
    status: m?.status ?? "draft",
  }
}

const orNull = (s: string) => (s.trim() ? s.trim() : null)

/** OCI 모델 등록(model=null) / 수정 다이얼로그. 승인·운영 상태 지정은 관리자만. */
export function OciModelDialog({
  open, onOpenChange, model, onSaved,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  model: OciModelDetail | null
  onSaved: (m: OciModelDetail) => void
}) {
  const { user } = useAuth()
  const askConfirm = useConfirm()
  const isAdmin = !!user?.is_admin
  const isEdit = model !== null
  const [form, setForm] = useState<Form>(() => toForm(model))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setForm(toForm(model))
      setError(null)
    }
  }, [open, model])

  const set = (k: keyof Form) => (v: string) => setForm((f) => ({ ...f, [k]: v }))
  const nameError = !isEdit && form.name && !NAME_RE.test(form.name)
    ? "영문·숫자로 시작하고 영문·숫자·마침표(.)·밑줄(_)·하이픈(-)만 쓸 수 있습니다 (128자 이하)."
    : null
  const dirty = JSON.stringify(form) !== JSON.stringify(toForm(model))
  const canSubmit = !saving && (isEdit ? dirty : !!form.name && !nameError)

  // 현재 상태가 관리자 전용이면 비관리자에게도 선택지로 보여야 값이 비지 않는다(변경은 불가).
  const statusOptions = OCI_STATUSES.filter(
    (s) => isAdmin || !OCI_ADMIN_ONLY_STATUSES.includes(s) || s === model?.status,
  )

  async function submit() {
    setSaving(true)
    setError(null)
    const payload = {
      display_name: orNull(form.display_name),
      description: orNull(form.description),
      task: orNull(form.task),
      framework: orNull(form.framework),
      language: orNull(form.language),
      license: orNull(form.license),
      owner: orNull(form.owner),
      status: form.status,
    }
    try {
      const saved = isEdit
        ? await updateOciModel(model.name, payload)
        : await createOciModel({ name: form.name.trim(), ...payload })
      toast.success(isEdit ? "모델 정보를 저장했습니다." : `모델 '${saved.name}' 을(를) 등록했습니다.`)
      onSaved(saved)
      onOpenChange(false)
    } catch (e) {
      setError(errorMessage(e) ?? "저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  async function requestClose(o: boolean) {
    if (saving) return
    if (!o && dirty && !(await askConfirm({
      title: "변경 내용 버리기",
      description: "저장하지 않은 변경 내용이 있습니다. 닫을까요?",
      confirmText: "닫기",
      destructive: true,
    }))) return
    onOpenChange(o)
  }

  const field = (k: keyof Form, label: string, placeholder?: string) => (
    <div className="grid gap-1.5">
      <Label htmlFor={`oci-${k}`}>{label}</Label>
      <Input id={`oci-${k}`} value={form[k]} onChange={(e) => set(k)(e.target.value)} placeholder={placeholder} disabled={saving} />
    </div>
  )

  return (
    <Dialog open={open} onOpenChange={(o) => { void requestClose(o) }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? "모델 정보 수정" : "OCI 모델 등록"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? `${model.name} — 이름은 바꿀 수 없습니다.`
              : "빈 모델을 만든 뒤 CLI·SDK 로 파일을 push 해 버전을 올립니다. 등록한 사람이 소유자가 됩니다."}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-1">
          {!isEdit && (
            <div className="grid gap-1.5">
              <Label htmlFor="oci-name">이름 *</Label>
              <Input
                id="oci-name"
                value={form.name}
                onChange={(e) => set("name")(e.target.value)}
                placeholder="my-custom-model"
                disabled={saving}
                autoFocus
              />
              {nameError && <p className="text-xs text-destructive">{nameError}</p>}
            </div>
          )}
          <div className="grid grid-cols-2 gap-4">
            {field("display_name", "표시 이름")}
            {field("owner", "소유자", user?.username)}
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="oci-description">설명</Label>
            <Textarea
              id="oci-description"
              value={form.description}
              onChange={(e) => set("description")(e.target.value)}
              rows={3}
              disabled={saving}
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            {field("task", "태스크", "text-classification")}
            {field("framework", "프레임워크", "pytorch")}
            {field("language", "언어", "ko")}
            {field("license", "라이선스", "apache-2.0")}
          </div>
          <div className="grid gap-1.5">
            <Label>상태</Label>
            <Select value={form.status} onValueChange={set("status")} disabled={saving}>
              <SelectTrigger className="w-60"><SelectValue /></SelectTrigger>
              <SelectContent>
                {statusOptions.map((s) => (
                  <SelectItem key={s} value={s}>{OCI_STATUS_LABELS[s]} ({s})</SelectItem>
                ))}
              </SelectContent>
            </Select>
            {!isAdmin && (
              <p className="text-xs text-muted-foreground">승인·운영 상태는 관리자만 지정할 수 있습니다.</p>
            )}
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => { void requestClose(false) }} disabled={saving}>취소</Button>
          <Button onClick={submit} disabled={!canSubmit}>
            {saving && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}
            {isEdit ? "저장" : "등록"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
