"use client"

import { useCallback, useRef, useState } from "react"
import { Upload, X, File as FileIcon } from "lucide-react"

import { cn } from "@workspace/ui/lib/utils"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"

import { formatBytes } from "./utils"

type UploadDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentPath: string
  onConfirm: (files: File[]) => void
  isLoading?: boolean
}

export function UploadDialog({
  open,
  onOpenChange,
  currentPath,
  onConfirm,
  isLoading,
}: UploadDialogProps) {
  const [files, setFiles] = useState<File[]>([])
  const [isDragOver, setIsDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const addFiles = useCallback((newFiles: FileList | File[]) => {
    const arr = Array.from(newFiles)
    setFiles((prev) => {
      const existingNames = new Set(prev.map((f) => f.name))
      const unique = arr.filter((f) => !existingNames.has(f.name))
      return [...prev, ...unique]
    })
  }, [])

  function removeFile(name: string) {
    setFiles((prev) => prev.filter((f) => f.name !== name))
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault()
    setIsDragOver(false)
    if (e.dataTransfer.files.length > 0) {
      addFiles(e.dataTransfer.files)
    }
  }

  function handleSubmit() {
    if (files.length === 0) return
    onConfirm(files)
    setFiles([])
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      setFiles([])
    }
    onOpenChange(nextOpen)
  }

  const totalSize = files.reduce((acc, f) => acc + f.size, 0)

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>파일 업로드</DialogTitle>
          <DialogDescription>
            업로드 위치:{" "}
            <code className="text-xs bg-muted px-1.5 py-0.5 rounded">
              {currentPath}
            </code>
          </DialogDescription>
        </DialogHeader>

        {/* Drop zone */}
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setIsDragOver(true)
          }}
          onDragLeave={() => setIsDragOver(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          className={cn(
            "border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors",
            isDragOver
              ? "border-primary bg-primary/5"
              : "border-muted-foreground/25 hover:border-muted-foreground/50",
          )}
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files) addFiles(e.target.files)
              e.target.value = ""
            }}
          />
          <Upload className="h-8 w-8 mx-auto mb-3 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            파일을 드래그하거나{" "}
            <span className="text-primary font-medium">클릭해 선택</span>하세요
          </p>
        </div>

        {/* File list */}
        {files.length > 0 && (
          <div className="space-y-1.5 max-h-48 overflow-y-auto">
            {files.map((file) => (
              <div
                key={file.name}
                className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-muted/50 text-sm"
              >
                <FileIcon className="h-4 w-4 text-muted-foreground shrink-0" />
                <span className="flex-1 truncate">{file.name}</span>
                <span className="text-muted-foreground tabular-nums shrink-0">
                  {formatBytes(file.size)}
                </span>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    removeFile(file.name)
                  }}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
            <div className="text-xs text-muted-foreground px-3 pt-1">
              파일 {files.length}개 &middot;{" "}
              총 {formatBytes(totalSize)}
            </div>
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={isLoading}
          >
            취소
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={files.length === 0 || isLoading}
          >
            {isLoading
              ? "업로드 중..."
              : `업로드 ${files.length > 0 ? `(${files.length})` : ""}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
