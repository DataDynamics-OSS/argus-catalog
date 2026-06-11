"use client"

import { useCallback, useState } from "react"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Textarea } from "@workspace/ui/components/textarea"
import { createModel } from "../api"
import { useModels } from "./models-provider"

export function ModelsAddDialog() {
  const { open, setOpen, refreshModels } = useModels()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [name, setName] = useState("")
  const [owner, setOwner] = useState("")
  const [description, setDescription] = useState("")

  const handleSubmit = useCallback(async () => {
    if (!name.trim()) return
    setSaving(true)
    setError(null)
    try {
      await createModel({
        name: name.trim(),
        owner: owner.trim() || undefined,
        description: description.trim() || undefined,
      })
      setName("")
      setOwner("")
      setDescription("")
      setOpen(null)
      await refreshModels()
    } catch (e) {
      setError(e instanceof Error ? e.message : "모델 생성에 실패했습니다.")
    } finally {
      setSaving(false)
    }
  }, [name, owner, description, setOpen, refreshModels])

  return (
    <Dialog open={open === "add"} onOpenChange={() => setOpen(null)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>모델 추가</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="grid gap-2">
            <Label htmlFor="model-name">이름</Label>
            <Input
              id="model-name"
              placeholder="예: argus.ml.iris_classifier"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              3단계 이름 사용: catalog.schema.model
            </p>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="model-owner">소유자</Label>
            <Input
              id="model-owner"
              placeholder="예: data-team"
              value={owner}
              onChange={(e) => setOwner(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="model-desc">설명</Label>
            <Textarea
              id="model-desc"
              placeholder="이 모델은 무엇을 하나요?"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              onClick={() => setOpen(null)}
              disabled={saving}
            >
              취소
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={saving || !name.trim()}
            >
              {saving ? "생성 중..." : "생성"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
