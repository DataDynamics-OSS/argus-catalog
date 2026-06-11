"use client"

import {
  File,
  FileArchive,
  FileAudio,
  FileCode,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileType,
  FileVideo,
  Folder,
} from "lucide-react"

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"

import type { FilesystemEntry } from "./types"
import { formatBytes, formatDate, getFileCategory, getExtension } from "./utils"

type PropertiesDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  entry: FilesystemEntry | null
}

const fileIcons: Record<string, React.ComponentType<{ className?: string }>> = {
  image: FileImage,
  video: FileVideo,
  audio: FileAudio,
  archive: FileArchive,
  code: FileCode,
  document: FileText,
  text: FileType,
  data: FileSpreadsheet,
  generic: File,
}

function EntryIcon({ entry, className }: { entry: FilesystemEntry; className?: string }) {
  if (entry.kind === "folder") return <Folder className={className} />
  const category = getFileCategory(entry.name)
  const Icon = fileIcons[category] ?? File
  return <Icon className={className} />
}

function PropertyRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start gap-4 py-2 border-b last:border-b-0">
      <span className="text-sm font-medium text-muted-foreground w-32 shrink-0">
        {label}
      </span>
      <span className="text-sm break-all">{value}</span>
    </div>
  )
}

export function PropertiesDialog({
  open,
  onOpenChange,
  entry,
}: PropertiesDialogProps) {
  if (!entry) return null

  const isFolder = entry.kind === "folder"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <EntryIcon
              entry={entry}
              className="h-5 w-5 shrink-0 text-muted-foreground"
            />
            <span className="truncate">{entry.name}</span>
          </DialogTitle>
        </DialogHeader>

        <div className="mt-2">
          <PropertyRow label="이름" value={entry.name} />
          <PropertyRow label="유형" value={isFolder ? "디렉터리" : getFileCategory(entry.name)} />
          <PropertyRow label="경로" value={entry.key} />

          {!isFolder && (
            <>
              <PropertyRow
                label="크기"
                value={`${formatBytes(entry.size)} (${entry.size.toLocaleString()} 바이트)`}
              />
              <PropertyRow
                label="최종 수정"
                value={formatDate(entry.lastModified)}
              />
              {getExtension(entry.name) && (
                <PropertyRow
                  label="확장자"
                  value={`.${getExtension(entry.name)}`}
                />
              )}
            </>
          )}

          <PropertyRow label="소유자" value={entry.owner ?? "-"} />
          <PropertyRow label="그룹" value={entry.group ?? "-"} />
          <PropertyRow label="권한" value={entry.permissions ?? "-"} />
        </div>
      </DialogContent>
    </Dialog>
  )
}
