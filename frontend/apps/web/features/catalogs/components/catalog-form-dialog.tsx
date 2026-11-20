"use client"

import { useEffect, useState } from "react"

import { Button } from "@workspace/ui/components/button"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { Textarea } from "@workspace/ui/components/textarea"
import { createCatalog, updateCatalog, type Catalog } from "@/features/catalogs/api"
import { fetchTopology, type TopologyOrganization } from "@/features/topology/api"
import { errorMessage, notifySuccess } from "@/lib/notify"

const NO_SYSTEM = "none"

type SystemOption = { id: number; label: string }

/** 토폴로지 → 시스템 고르기 목록("조직 › 시스템", 미분류 시스템은 시스템 이름만). */
function systemOptions(orgs: TopologyOrganization[], unassigned: { id: number; name: string }[]): SystemOption[] {
  const out: SystemOption[] = []
  const walk = (list: TopologyOrganization[]) => {
    for (const o of list) {
      for (const s of o.systems) out.push({ id: s.id, label: `${o.name} › ${s.name}` })
      walk(o.children)
    }
  }
  walk(orgs)
  for (const s of unassigned) out.push({ id: s.id, label: `${s.name} (조직 미지정)` })
  return out
}

/**
 * 카탈로그 등록 · 수정 (TODO-CAT-001). 화면에서 등록하면 직접 등록(ARGUS) 카탈로그 — 데이터셋을 직접 넣는다.
 * 데이터 소스와 함께 생긴 자동 카탈로그는 이름을 바꿀 수 없다(데이터 소스 이름을 따른다).
 */
export function CatalogFormDialog({
  open,
  onOpenChange,
  catalog,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 있으면 수정, 없으면 등록 */
  catalog?: Catalog | null
  onSaved: (c: Catalog) => void
}) {
  const editing = Boolean(catalog)
  const [name, setName] = useState(catalog?.name ?? "")
  const [kind, setKind] = useState(catalog?.kind ?? "")
  const [summary, setSummary] = useState(catalog?.summary ?? "")
  const [description, setDescription] = useState(catalog?.description ?? "")
  const [owner, setOwner] = useState(catalog?.owner ?? "")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const nameLocked = Boolean(catalog?.auto_created)
  // 소속 시스템 — 직접 등록 카탈로그만. 시스템의 조직이 수동 데이터셋의 소관기관(DEF-CAT-001)
  const systemEditable = !catalog || catalog.source_type === "ARGUS"
  const [systemId, setSystemId] = useState<string>(catalog?.system_id != null ? String(catalog.system_id) : NO_SYSTEM)
  const [systems, setSystems] = useState<SystemOption[] | null>(null)

  useEffect(() => {
    if (!open || !systemEditable) return
    let alive = true
    fetchTopology()
      .then((t) => { if (alive) setSystems(systemOptions(t.organizations, t.unassigned.systems)) })
      .catch(() => { if (alive) setSystems([]) })
    return () => { alive = false }
  }, [open, systemEditable])

  const submit = async () => {
    if (!name.trim()) return
    setSaving(true)
    setError(null)
    const curated = {
      summary: summary.trim() || null,
      description: description.trim() || null,
      owner: owner.trim() || null,
      ...(systemEditable ? { system_id: systemId === NO_SYSTEM ? null : Number(systemId) } : {}),
    }
    try {
      const saved = catalog
        ? await updateCatalog(catalog.id, { ...(nameLocked ? {} : { name: name.trim() }), ...curated })
        : await createCatalog({ name: name.trim(), kind: kind.trim() || null, source_type: "ARGUS", ...curated })
      notifySuccess(editing ? "카탈로그를 저장했습니다." : `카탈로그 '${saved.name}' 을(를) 등록했습니다.`)
      onSaved(saved)
      onOpenChange(false)
    } catch (e) {
      setError(errorMessage(e) ?? (editing ? "카탈로그를 저장하지 못했습니다." : "카탈로그를 만들지 못했습니다."))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{editing ? "카탈로그 수정" : "카탈로그 등록"}</DialogTitle>
          {!editing && (
            <DialogDescription>
              데이터 소스 없이 데이터셋을 직접 등록하는 카탈로그입니다. 데이터셋은 나중에 실제 테이블에 매핑할 수 있습니다.
            </DialogDescription>
          )}
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="cat-name">이름</Label>
            <Input id="cat-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200}
              disabled={nameLocked} placeholder="예: 엑셀 관리 자료" />
            {nameLocked && <p className="text-xs">데이터 소스와 함께 생긴 카탈로그라 이름은 데이터 소스 이름을 따릅니다.</p>}
          </div>
          {!editing && (
            <div className="grid gap-2">
              <Label htmlFor="cat-kind">종류 <span className="text-xs">(선택)</span></Label>
              <Input id="cat-kind" value={kind} onChange={(e) => setKind(e.target.value)} maxLength={100}
                placeholder="예: excel · 업무 시스템 · oracle" />
            </div>
          )}
          <div className="grid gap-2">
            <Label htmlFor="cat-summary">요약 <span className="text-xs">(한 줄, 최대 200자)</span></Label>
            <Input id="cat-summary" value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={200} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="cat-desc">설명</Label>
            <Textarea id="cat-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={3} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="cat-owner">소유자</Label>
            <Input id="cat-owner" value={owner} onChange={(e) => setOwner(e.target.value)} maxLength={200} />
          </div>
          {systemEditable && (
            <div className="grid gap-2">
              <Label htmlFor="cat-system">시스템 <span className="text-xs">(선택)</span></Label>
              <Select value={systemId} onValueChange={setSystemId}>
                <SelectTrigger id="cat-system" className="w-full">
                  <SelectValue placeholder={systems == null ? "불러오는 중…" : "지정 안 함"} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_SYSTEM}>지정 안 함</SelectItem>
                  {systemId !== NO_SYSTEM && systems != null && !systems.some((s) => String(s.id) === systemId) && (
                    <SelectItem value={systemId}>{catalog?.system_name ?? `시스템 ${systemId}`}</SelectItem>
                  )}
                  {(systems ?? []).map((s) => <SelectItem key={s.id} value={String(s.id)}>{s.label}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="text-xs">시스템의 조직이 이 카탈로그 데이터셋의 소관기관(AI-Ready 필수 메타데이터)이 됩니다.</p>
            </div>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>취소</Button>
          <Button onClick={() => void submit()} disabled={saving || !name.trim()}>
            {saving ? "저장 중…" : editing ? "저장" : "등록"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
