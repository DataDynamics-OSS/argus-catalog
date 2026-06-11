"use client"

import { CheckCircle2, File as FileIcon, Loader2, XCircle } from "lucide-react"

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"

import { formatBytes } from "./utils"

export type FileUploadStatus = {
  file: File
  /** 0-100 */
  progress: number
  status: "pending" | "uploading" | "done" | "error"
  error?: string
}

type UploadProgressDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  items: FileUploadStatus[]
}

function StatusIcon({ status }: { status: FileUploadStatus["status"] }) {
  switch (status) {
    case "pending":
      return <FileIcon className="h-4 w-4 text-muted-foreground shrink-0" />
    case "uploading":
      return <Loader2 className="h-4 w-4 animate-spin text-primary shrink-0" />
    case "done":
      return <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0" />
    case "error":
      return <XCircle className="h-4 w-4 text-destructive shrink-0" />
  }
}

export function UploadProgressDialog({
  open,
  onOpenChange,
  items,
}: UploadProgressDialogProps) {
  const allDone = items.length > 0 && items.every((i) => i.status === "done" || i.status === "error")
  const doneCount = items.filter((i) => i.status === "done").length
  const errorCount = items.filter((i) => i.status === "error").length

  return (
    <Dialog open={open} onOpenChange={(v) => { if (allDone) onOpenChange(v) }}>
      <DialogContent className="sm:max-w-[520px]" onPointerDownOutside={(e) => { if (!allDone) e.preventDefault() }}>
        <DialogHeader>
          <DialogTitle>
            {allDone
              ? `Upload Complete — ${doneCount} succeeded${errorCount > 0 ? `, ${errorCount} failed` : ""}`
              : `Uploading ${items.length} file${items.length > 1 ? "s" : ""}...`}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-2 max-h-72 overflow-y-auto">
          {items.map((item, idx) => (
            <div key={idx} className="space-y-1">
              <div className="flex items-center gap-2 text-sm">
                <StatusIcon status={item.status} />
                <span className="flex-1 truncate">{item.file.name}</span>
                <span className="text-muted-foreground tabular-nums shrink-0 text-xs">
                  {formatBytes(item.file.size)}
                </span>
                <span className="text-muted-foreground tabular-nums shrink-0 text-xs w-10 text-right">
                  {item.progress}%
                </span>
              </div>
              <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-300 ${
                    item.status === "error"
                      ? "bg-destructive"
                      : item.status === "done"
                        ? "bg-green-500"
                        : "bg-primary"
                  }`}
                  style={{ width: `${item.progress}%` }}
                />
              </div>
              {item.error && (
                <p className="text-xs text-destructive pl-6">{item.error}</p>
              )}
            </div>
          ))}
        </div>

        {allDone && (
          <div className="flex justify-end pt-2">
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="px-4 py-2 text-sm font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90"
            >
              Close
            </button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
