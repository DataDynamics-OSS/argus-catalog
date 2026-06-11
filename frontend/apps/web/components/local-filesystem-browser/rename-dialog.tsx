"use client"

import { useState } from "react"
import { Loader2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"

type RenameDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentName: string
  onConfirm: (newName: string) => Promise<void>
  isLoading: boolean
}

export function RenameDialog({
  open,
  onOpenChange,
  currentName,
  onConfirm,
  isLoading,
}: RenameDialogProps) {
  const [newName, setNewName] = useState(currentName)
  const [error, setError] = useState("")

  function handleOpenChange(v: boolean) {
    if (v) {
      setNewName(currentName)
      setError("")
    }
    onOpenChange(v)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = newName.trim()
    if (!trimmed) {
      setError("이름이 비어 있을 수 없습니다.")
      return
    }
    if (trimmed === currentName) {
      setError("새 이름이 기존 이름과 같습니다.")
      return
    }
    setError("")
    await onConfirm(trimmed)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[420px]">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>이름 변경</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3 py-4">
            <Label htmlFor="rename-input">새 이름</Label>
            <Input
              id="rename-input"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              autoFocus
              disabled={isLoading}
            />
            {error && <p className="text-xs text-destructive">{error}</p>}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isLoading}
            >
              취소
            </Button>
            <Button type="submit" disabled={isLoading}>
              {isLoading && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              이름 변경
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
