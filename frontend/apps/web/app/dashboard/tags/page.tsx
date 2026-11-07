"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { AlertTriangle, Bell, Box, Database, Pencil, Plus, Trash2 } from "lucide-react"
import { errorMessage, toast } from "@/lib/notify"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader } from "@workspace/ui/components/card"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { DashboardHeader } from "@/components/dashboard-header"
import {
  createTag,
  deleteTag,
  fetchTags,
  fetchTagUsage,
  updateTag,
  type TagUsage,
} from "@/features/tags/api"
import type { Tag } from "@/features/datasets/data/schema"
import { useAuth } from "@/features/auth"
import { usePermissions } from "@/features/permissions/use-permissions"

const TAGS_MANAGE_FEATURE = "tags.manage"
const DEFAULT_COLOR = "#3b82f6"
const HEX_COLOR = /^#[0-9a-fA-F]{6}$/

const errMsg = (e: unknown, fallback: string) => (errorMessage(e) ?? fallback)

/** 배경색 밝기에 따라 글자색(검정/흰색)을 고른다 — 밝은 색 태그에서도 이름이 읽히도록. */
function textColorFor(bg: string | null | undefined): string {
  if (!bg || !HEX_COLOR.test(bg)) return "#fff"
  const n = parseInt(bg.slice(1), 16)
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255]
  return (r * 299 + g * 587 + b * 114) / 1000 > 160 ? "#111827" : "#fff"
}

function TagBadge({ tag, className }: { tag: Pick<Tag, "name" | "color">; className?: string }) {
  const bg = tag.color && HEX_COLOR.test(tag.color) ? tag.color : DEFAULT_COLOR
  return (
    <Badge style={{ backgroundColor: bg, color: textColorFor(bg) }} className={className}>
      {tag.name}
    </Badge>
  )
}

export default function TagsPage() {
  const { user } = useAuth()
  const { isFeatureGranted } = usePermissions()
  // 태그 관리 — admin 이거나 권한 관리에서 tags.manage 를 명시 허용받은 역할 (미설정이면 admin 전용)
  const canManage = !!user?.is_admin || isFeatureGranted(TAGS_MANAGE_FEATURE)
  const [tags, setTags] = useState<Tag[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  // Create / edit dialog (editing === null → 생성)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Tag | null>(null)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [color, setColor] = useState(DEFAULT_COLOR)
  const [saving, setSaving] = useState(false)
  const colorValid = HEX_COLOR.test(color)

  // Delete confirm dialog
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<TagUsage | null>(null)
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    try {
      setIsLoading(true)
      setTags(await fetchTags())
      setLoadError(null)
    } catch (e) {
      setLoadError(errMsg(e, "태그를 불러오지 못했습니다."))
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const openCreate = () => {
    setEditing(null)
    setName("")
    setDescription("")
    setColor(DEFAULT_COLOR)
    setDialogOpen(true)
  }

  const openEdit = (tag: Tag) => {
    setEditing(tag)
    setName(tag.name)
    setDescription(tag.description ?? "")
    setColor(tag.color && HEX_COLOR.test(tag.color) ? tag.color : DEFAULT_COLOR)
    setDialogOpen(true)
  }

  const handleSave = useCallback(async () => {
    if (!name.trim() || !colorValid || saving) return
    setSaving(true)
    try {
      const payload = { name: name.trim(), description: description.trim() || null, color }
      if (editing) {
        await updateTag(editing.id, payload)
        toast.success("태그를 수정했습니다.")
      } else {
        await createTag(payload)
        toast.success("태그를 추가했습니다.")
      }
      setDialogOpen(false)
      await load()
    } catch (e) {
      // 대화상자는 열어 둔 채 사유(이름 중복·색상 형식 등)를 알린다
      toast.error(errMsg(e, editing ? "태그를 수정하지 못했습니다." : "태그를 추가하지 못했습니다."))
    } finally {
      setSaving(false)
    }
  }, [name, description, color, colorValid, editing, saving, load])

  // Open delete dialog: fetch usage first
  const handleDeleteClick = useCallback(async (tagId: number) => {
    setDeleteLoading(true)
    setDeleteDialogOpen(true)
    setDeleteTarget(null)
    try {
      setDeleteTarget(await fetchTagUsage(tagId))
    } catch (e) {
      setDeleteDialogOpen(false)
      toast.error(errMsg(e, "태그 사용 현황을 확인하지 못했습니다."))
    } finally {
      setDeleteLoading(false)
    }
  }, [])

  // Confirm delete
  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await deleteTag(deleteTarget.tag.id)
      toast.success(`'${deleteTarget.tag.name}' 태그를 삭제했습니다.`)
      setDeleteDialogOpen(false)
      setDeleteTarget(null)
      await load()
    } catch (e) {
      // 차단(알림 규칙이 감시 중)·권한 등 백엔드 메시지를 토스트로 노출.
      toast.error(errMsg(e, "태그를 삭제하지 못했습니다."))
    } finally {
      setDeleting(false)
    }
  }, [deleteTarget, load])

  const rules = deleteTarget?.alert_rules ?? []
  const models = deleteTarget?.oci_models ?? []
  const blockedByRules = rules.length > 0

  return (
    <>
      <DashboardHeader title="태그" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            데이터셋·OCI 모델 분류와 알림 규칙의 감시 범위에 쓰는 태그를 관리합니다
          </p>
          {canManage && (
            <Button size="sm" onClick={openCreate}>
              <Plus className="mr-1 h-4 w-4" />
              태그 추가
            </Button>
          )}
        </div>

        {loadError && (
          <div className="flex items-center justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm">
            <span className="text-destructive">{loadError}</span>
            <Button size="sm" variant="outline" onClick={load} disabled={isLoading}>다시 시도</Button>
          </div>
        )}

        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {isLoading && tags.length === 0 ? (
            <p className="text-muted-foreground col-span-full text-center py-8">
              태그를 불러오는 중...
            </p>
          ) : loadError && tags.length === 0 ? null : tags.length === 0 ? (
            <p className="text-muted-foreground col-span-full text-center py-8">
              {canManage ? "아직 태그가 없습니다. 추가해 시작하세요." : "아직 태그가 없습니다."}
            </p>
          ) : (
            tags.map((tag) => (
              <Card key={tag.id}>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between gap-2">
                    <TagBadge tag={tag} />
                    {canManage && (
                      <div className="flex items-center">
                        <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={`${tag.name} 태그 수정`}
                          onClick={() => openEdit(tag)}>
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={`${tag.name} 태그 삭제`}
                          onClick={() => handleDeleteClick(tag.id)}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    )}
                  </div>
                </CardHeader>
                <CardContent>
                  <p className="text-sm text-muted-foreground">
                    {tag.description || "설명 없음"}
                  </p>
                </CardContent>
              </Card>
            ))
          )}
        </div>
      </div>

      {/* Create / Edit Tag Dialog */}
      <Dialog open={dialogOpen} onOpenChange={(o) => { if (!saving) setDialogOpen(o) }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{editing ? "태그 수정" : "태그 추가"}</DialogTitle>
            {editing && (
              <DialogDescription>
                이름·설명·색상을 바꿔도 데이터셋·모델·알림 규칙 연결은 그대로 유지됩니다.
              </DialogDescription>
            )}
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-2">
              <Label htmlFor="tag-name">이름</Label>
              <Input
                id="tag-name"
                placeholder="예: PII, deprecated, tier-1"
                value={name}
                maxLength={100}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) handleSave() }}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="tag-desc">설명</Label>
              <Input
                id="tag-desc"
                placeholder="설명(선택)"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="tag-color">색상</Label>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  id="tag-color"
                  value={colorValid ? color : DEFAULT_COLOR}
                  onChange={(e) => setColor(e.target.value)}
                  className="h-9 w-12 cursor-pointer rounded border"
                />
                <Input
                  value={color}
                  onChange={(e) => setColor(e.target.value.trim())}
                  maxLength={7}
                  aria-invalid={!colorValid}
                  aria-label="색상 코드"
                  className="flex-1 font-mono"
                />
                <TagBadge tag={{ name: name.trim() || "미리보기", color: colorValid ? color : DEFAULT_COLOR }} className="shrink-0" />
              </div>
              {!colorValid && (
                <p className="text-xs text-destructive">#rrggbb 형식(예: #3b82f6)으로 입력하세요.</p>
              )}
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>
                취소
              </Button>
              <Button onClick={handleSave} disabled={saving || !name.trim() || !colorValid}>
                {saving ? "저장 중..." : editing ? "저장" : "생성"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Confirm Dialog with Usage Info */}
      <Dialog open={deleteDialogOpen} onOpenChange={(o) => { if (!deleting) setDeleteDialogOpen(o) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              태그 삭제
            </DialogTitle>
            {deleteTarget && (
              <DialogDescription>
                <TagBadge tag={deleteTarget.tag} className="text-xs mx-0.5" />
                {" "}태그를 정말 삭제하시겠습니까?
              </DialogDescription>
            )}
          </DialogHeader>

          {deleteLoading ? (
            <div className="py-6 text-center text-sm text-muted-foreground">
              태그 사용 여부 확인 중...
            </div>
          ) : deleteTarget ? (
            <div className="space-y-3">
              {blockedByRules && (
                <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3">
                  <p className="text-sm font-medium text-destructive">
                    알림 규칙 {rules.length}개가 이 태그를 감시 범위로 쓰고 있어 삭제할 수 없습니다
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    알림 규칙의 감시 범위를 바꾸거나 규칙을 먼저 삭제하세요.
                  </p>
                  <UsageList
                    icon={Bell}
                    items={rules.map((r) => ({
                      key: r.id, label: r.name, href: "/dashboard/alerts",
                      note: r.is_active === false ? "비활성" : undefined,
                    }))}
                    onNavigate={() => setDeleteDialogOpen(false)}
                  />
                </div>
              )}

              {deleteTarget.total_datasets + models.length > 0 ? (
                <div className="space-y-2">
                  <div className="rounded-md border border-destructive/20 bg-destructive/5 p-3">
                    <p className="text-sm font-medium text-destructive">
                      {[
                        deleteTarget.total_datasets > 0 && `데이터셋 ${deleteTarget.total_datasets}개`,
                        models.length > 0 && `OCI 모델 ${models.length}개`,
                      ].filter(Boolean).join(", ")}에서 사용 중입니다
                    </p>
                    <p className="text-xs text-muted-foreground mt-1">
                      태그를 삭제하면 아래 항목에서 모두 제거됩니다.
                    </p>
                  </div>
                  <UsageList
                    icon={Database}
                    items={deleteTarget.datasets.map((ds) => ({
                      key: `d${ds.id}`, label: ds.name, href: `/dashboard/datasets/${ds.id}`, note: ds.datasource_name,
                    }))}
                    onNavigate={() => setDeleteDialogOpen(false)}
                  />
                  <UsageList
                    icon={Box}
                    items={models.map((m) => ({
                      key: `m${m.id}`, label: m.name, href: `/dashboard/oci-hub/${encodeURIComponent(m.name)}`, note: "OCI 모델",
                    }))}
                    onNavigate={() => setDeleteDialogOpen(false)}
                  />
                </div>
              ) : !blockedByRules ? (
                <div className="rounded-md border bg-muted/30 p-3">
                  <p className="text-sm text-muted-foreground">
                    이 태그를 사용하는 데이터셋·OCI 모델·알림 규칙이 없습니다. 안전하게 삭제할 수 있습니다.
                  </p>
                </div>
              ) : null}
            </div>
          ) : null}

          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={deleting}>
                취소
              </Button>
            </DialogClose>
            <Button
              variant="destructive"
              onClick={handleDeleteConfirm}
              disabled={deleting || deleteLoading || !deleteTarget || blockedByRules}
            >
              {deleting ? "삭제 중..." : "태그 삭제"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function UsageList({
  icon: Icon, items, onNavigate,
}: {
  icon: React.ComponentType<{ className?: string }>
  items: { key: string | number; label: string; href: string; note?: string | null }[]
  onNavigate: () => void
}) {
  if (items.length === 0) return null
  return (
    <div className="mt-2 max-h-[160px] overflow-y-auto rounded-md border bg-background">
      {items.map((it) => (
        <div key={it.key} className="flex items-center gap-2 px-3 py-2 text-sm border-b last:border-b-0">
          <Icon className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
          <Link href={it.href} className="font-medium hover:underline truncate" onClick={onNavigate}>
            {it.label}
          </Link>
          {it.note && (
            <Badge variant="outline" className="text-xs shrink-0 ml-auto">{it.note}</Badge>
          )}
        </div>
      ))}
    </div>
  )
}
