"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { AlertTriangle, Database, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

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
  type TagUsage,
} from "@/features/tags/api"
import type { Tag } from "@/features/datasets/data/schema"
import { useAuth } from "@/features/auth" // Added for SSO AUTH

export default function TagsPage() {
  const { user } = useAuth()
  const [tags, setTags] = useState<Tag[]>([])
  const [isLoading, setIsLoading] = useState(true)

  // Create dialog
  const [dialogOpen, setDialogOpen] = useState(false)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [color, setColor] = useState("#3b82f6")
  const [saving, setSaving] = useState(false)

  // Delete confirm dialog
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<TagUsage | null>(null)
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    try {
      setIsLoading(true)
      setTags(await fetchTags())
    } catch {
      // ignore
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleCreate = useCallback(async () => {
    if (!name.trim()) return
    setSaving(true)
    try {
      await createTag({
        name: name.trim(),
        description: description.trim() || undefined,
        color,
      })
      setName("")
      setDescription("")
      setColor("#3b82f6")
      setDialogOpen(false)
      await load()
    } catch {
      // ignore
    } finally {
      setSaving(false)
    }
  }, [name, description, color, load])

  // Open delete dialog: fetch usage first
  const handleDeleteClick = useCallback(async (tagId: number) => {
    setDeleteLoading(true)
    setDeleteDialogOpen(true)
    setDeleteTarget(null)
    try {
      const usage = await fetchTagUsage(tagId)
      setDeleteTarget(usage)
    } catch {
      setDeleteDialogOpen(false)
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
      setDeleteDialogOpen(false)
      setDeleteTarget(null)
      await load()
    } catch (e) {
      // 차단·권한 등 백엔드 메시지를 토스트로 노출.
      toast.error(e instanceof Error ? e.message : "태그 삭제에 실패했습니다.")
    } finally {
      setDeleting(false)
    }
  }, [deleteTarget, load])

  return (
    <>
      <DashboardHeader title="태그" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            데이터셋 분류용 태그를 관리합니다
          </p>
          {user?.is_admin && (
            <Button size="sm" onClick={() => setDialogOpen(true)}>
              <Plus className="mr-1 h-4 w-4" />
              태그 추가
            </Button>
          )}
        </div>

        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {isLoading ? (
            <p className="text-muted-foreground col-span-full text-center py-8">
              태그를 불러오는 중...
            </p>
          ) : tags.length === 0 ? (
            <p className="text-muted-foreground col-span-full text-center py-8">
              아직 태그가 없습니다. 추가해 시작하세요.
            </p>
          ) : (
            tags.map((tag) => (
              <Card key={tag.id}>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Badge
                        style={{ backgroundColor: tag.color, color: "#fff" }}
                      >
                        {tag.name}
                      </Badge>
                    </div>
                    {user?.is_admin && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8"
                        onClick={() => handleDeleteClick(tag.id)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
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

      {/* Create Tag Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>태그 추가</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-2">
              <Label htmlFor="tag-name">이름</Label>
              <Input
                id="tag-name"
                placeholder="예: PII, deprecated, tier-1"
                value={name}
                onChange={(e) => setName(e.target.value)}
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
                  value={color}
                  onChange={(e) => setColor(e.target.value)}
                  className="h-9 w-12 cursor-pointer rounded border"
                />
                <Input
                  value={color}
                  onChange={(e) => setColor(e.target.value)}
                  className="flex-1"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button
                variant="outline"
                onClick={() => setDialogOpen(false)}
                disabled={saving}
              >
                취소
              </Button>
              <Button
                onClick={handleCreate}
                disabled={saving || !name.trim()}
              >
                {saving ? "생성 중..." : "생성"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Confirm Dialog with Usage Info */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              태그 삭제
            </DialogTitle>
            {deleteTarget && (
              <DialogDescription>
                <Badge
                  style={{
                    backgroundColor: deleteTarget.tag.color,
                    color: "#fff",
                  }}
                  className="text-xs mx-0.5"
                >
                  {deleteTarget.tag.name}
                </Badge>
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
              {deleteTarget.total_datasets > 0 ? (
                <>
                  <div className="rounded-md border border-destructive/20 bg-destructive/5 p-3">
                    <p className="text-sm font-medium text-destructive">
                      이 태그는 {deleteTarget.total_datasets}개의 데이터셋에서 사용 중입니다
                    </p>
                    <p className="text-xs text-muted-foreground mt-1">
                      태그를 삭제하면 아래 데이터셋에서 모두 제거됩니다.
                    </p>
                  </div>

                  <div className="max-h-[200px] overflow-y-auto rounded-md border">
                    {deleteTarget.datasets.map((ds) => (
                      <div
                        key={ds.id}
                        className="flex items-center gap-2 px-3 py-2 text-sm border-b last:border-b-0"
                      >
                        <Database className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                        <Link
                          href={`/dashboard/datasets/${ds.id}`}
                          className="font-medium hover:underline truncate"
                          onClick={() => setDeleteDialogOpen(false)}
                        >
                          {ds.name}
                        </Link>
                        <Badge variant="outline" className="text-xs shrink-0 ml-auto">
                          {ds.datasource_name}
                        </Badge>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div className="rounded-md border bg-muted/30 p-3">
                  <p className="text-sm text-muted-foreground">
                    이 태그를 사용하는 데이터셋이 없습니다. 안전하게 삭제할 수 있습니다.
                  </p>
                </div>
              )}
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
              disabled={deleting || deleteLoading || !deleteTarget}
            >
              {deleting ? "삭제 중..." : "태그 삭제"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
