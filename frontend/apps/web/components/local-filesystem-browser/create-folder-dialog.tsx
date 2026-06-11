"use client"

import { useState } from "react"

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

type CreateFolderDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentPath: string
  onConfirm: (folderName: string) => void
  isLoading?: boolean
}

const FOLDER_NAME_REGEX = /^[a-zA-Z0-9_\-.]+$/

export function CreateFolderDialog({
  open,
  onOpenChange,
  currentPath,
  onConfirm,
  isLoading,
}: CreateFolderDialogProps) {
  const [name, setName] = useState("")
  const [error, setError] = useState("")

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()

    const trimmed = name.trim()
    if (!trimmed) {
      setError("폴더 이름을 입력하세요.")
      return
    }
    if (!FOLDER_NAME_REGEX.test(trimmed)) {
      setError("영문자, 숫자, 하이픈(-), 밑줄(_), 점(.) 만 사용할 수 있습니다.")
      return
    }

    onConfirm(trimmed)
    setName("")
    setError("")
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      setName("")
      setError("")
    }
    onOpenChange(nextOpen)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[420px]">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>폴더 생성</DialogTitle>
            <DialogDescription>
              <code className="text-xs bg-muted px-1.5 py-0.5 rounded">
                {currentPath}
              </code>{" "}
              아래에 새 폴더를 생성합니다.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 py-4">
            <Label htmlFor="folder-name">폴더 이름</Label>
            <Input
              id="folder-name"
              placeholder="my-folder"
              value={name}
              onChange={(e) => {
                setName(e.target.value)
                if (error) setError("")
              }}
              autoFocus
            />
            {error && (
              <p className="text-sm text-destructive">{error}</p>
            )}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={isLoading}
            >
              취소
            </Button>
            <Button type="submit" disabled={isLoading}>
              {isLoading ? "생성 중..." : "생성"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
