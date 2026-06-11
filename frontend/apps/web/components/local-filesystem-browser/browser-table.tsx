"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ClipboardCopy,
  File,
  FileArchive,
  FileAudio,
  FileCode,
  FileImage,
  FileText,
  FileVideo,
  Folder,
  FileSpreadsheet,
  FileType,
  Eye,
  FileTerminal,
  Pencil,
  Trash2,
  Info,
  Download,
} from "lucide-react"

import { cn } from "@workspace/ui/lib/utils"
import { Checkbox } from "@workspace/ui/components/checkbox"
import {
  ContextMenu,
  ContextMenuTrigger,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
} from "@workspace/ui/components/context-menu"

import type { FilesystemEntry, SortConfig, SortDirection } from "./types"
import { entryId, formatBytes, formatDate, getFileCategory } from "./utils"
import { isCsvTsvFile, isViewableFile } from "./file-viewer-dialog"

export type EntryContextAction = "rename" | "delete" | "properties" | "view" | "view-cat" | "download"

type BrowserTableProps = {
  /** 현재 경로 — 경로별 스크롤 위치 기억/복원 키 */
  currentPath: string
  entries: FilesystemEntry[]
  totalEntryCount: number
  selectedKeys: Set<string>
  onSelectionChange: (keys: Set<string>) => void
  onFolderOpen: (path: string) => void
  onEntryDoubleClick?: (entry: FilesystemEntry) => void
  onContextAction?: (action: EntryContextAction, entry: FilesystemEntry) => void
  /** admin 만 rename/delete 컨텍스트 메뉴 표시 */
  canManage?: boolean
  sort: SortConfig
  onSortChange: (sort: SortConfig) => void
  isLoading: boolean
  sortDisableThreshold?: number
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

function FileIcon({ name, className }: { name: string; className?: string }) {
  const category = getFileCategory(name)
  const Icon = fileIcons[category] ?? File
  return <Icon className={className} />
}

function SortButton({
  column,
  label,
  currentSort,
  onSortChange,
  disabled,
  className,
}: {
  column: SortConfig["column"]
  label: string
  currentSort: SortConfig
  onSortChange: (sort: SortConfig) => void
  disabled?: boolean
  className?: string
}) {
  const isActive = !disabled && currentSort.column === column
  const nextDirection: SortDirection =
    isActive && currentSort.direction === "asc" ? "desc" : "asc"

  return (
    <button
      type="button"
      onClick={() => !disabled && onSortChange({ column, direction: nextDirection })}
      disabled={disabled}
      className={cn(
        "flex items-center gap-1 transition-colors",
        disabled
          ? "text-muted-foreground cursor-default"
          : "hover:text-foreground",
        !disabled && isActive ? "text-foreground" : "text-muted-foreground",
        className,
      )}
    >
      {label}
      {disabled ? null : isActive ? (
        currentSort.direction === "asc" ? (
          <ArrowUp className="h-3.5 w-3.5" />
        ) : (
          <ArrowDown className="h-3.5 w-3.5" />
        )
      ) : (
        <ArrowUpDown className="h-3.5 w-3.5" />
      )}
    </button>
  )
}

function CopyToast({ message, onDone }: { message: string; onDone: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onDone, 2000)
    return () => clearTimeout(timer)
  }, [onDone])

  return createPortal(
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[9999] animate-in fade-in slide-in-from-bottom-2 duration-200">
      <div className="bg-foreground text-background text-sm px-4 py-2 rounded-md shadow-lg">
        {message}
      </div>
    </div>,
    document.body,
  )
}

function copyToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text)
  }
  const textarea = document.createElement("textarea")
  textarea.value = text
  textarea.style.position = "fixed"
  textarea.style.opacity = "0"
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand("copy")
  document.body.removeChild(textarea)
  return Promise.resolve()
}

export function BrowserTable({
  currentPath,
  entries,
  totalEntryCount,
  selectedKeys,
  onSelectionChange,
  onFolderOpen,
  onEntryDoubleClick,
  onContextAction,
  canManage = false,
  sort,
  onSortChange,
  isLoading,
  sortDisableThreshold = 300,
}: BrowserTableProps) {
  const [toastMessage, setToastMessage] = useState<string | null>(null)
  const hideToast = useCallback(() => setToastMessage(null), [])

  // 경로별 스크롤 위치 기억 — 폴더로 들어갔다 뒤로 오면 이전 위치 복원.
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const scrollPositionsRef = useRef<Map<string, number>>(new Map())
  useEffect(() => {
    if (isLoading) return
    const el = scrollContainerRef.current
    if (!el) return
    const saved = scrollPositionsRef.current.get(currentPath) ?? 0
    const id = requestAnimationFrame(() => {
      el.scrollTop = saved
    })
    return () => cancelAnimationFrame(id)
  }, [currentPath, isLoading])

  async function handleCopyPath(entry: FilesystemEntry) {
    try {
      await copyToClipboard(entry.key)
      setToastMessage("클립보드에 복사되었습니다. (HTTPS 가 아닌 환경에선 동작하지 않을 수 있습니다)")
    } catch {
      setToastMessage("복사에 실패했습니다.")
    }
  }

  const sortDisabled = totalEntryCount >= sortDisableThreshold
  const allSelectableKeys = entries.map((e) => entryId(e.kind, e.key))
  const allSelected =
    allSelectableKeys.length > 0 &&
    allSelectableKeys.every((k) => selectedKeys.has(k))
  const someSelected =
    !allSelected && allSelectableKeys.some((k) => selectedKeys.has(k))

  function toggleAll() {
    if (allSelected) {
      onSelectionChange(new Set())
    } else {
      onSelectionChange(new Set(allSelectableKeys))
    }
  }

  function toggleOne(key: string) {
    const next = new Set(selectedKeys)
    if (next.has(key)) {
      next.delete(key)
    } else {
      next.add(key)
    }
    onSelectionChange(next)
  }

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center py-20">
        <div className="flex flex-col items-center gap-3 text-muted-foreground">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-current border-t-transparent" />
          <span className="text-sm">불러오는 중...</span>
        </div>
      </div>
    )
  }

  if (entries.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center py-20">
        <div className="flex flex-col items-center gap-2 text-muted-foreground">
          <Folder className="h-12 w-12 opacity-30" />
          <span className="text-sm">디렉터리가 비어 있습니다</span>
        </div>
      </div>
    )
  }

  return (
    <div
      ref={scrollContainerRef}
      onScroll={(e) => scrollPositionsRef.current.set(currentPath, e.currentTarget.scrollTop)}
      className="flex-1 overflow-auto border rounded-md"
    >
      <table className="w-full text-sm">
        <thead className="sticky top-0 z-10 bg-muted/80 backdrop-blur-sm border-b">
          <tr>
            <th className="w-10 px-3 py-2">
              <Checkbox
                checked={allSelected ? true : someSelected ? "indeterminate" : false}
                onCheckedChange={toggleAll}
                aria-label="전체 선택"
              />
            </th>
            <th className="text-left px-3 py-2 font-medium">
              <SortButton
                column="name"
                label="이름"
                currentSort={sort}
                onSortChange={onSortChange}
                disabled={sortDisabled}
              />
            </th>
            <th className="text-right px-3 py-2 font-medium w-28">
              <SortButton
                column="size"
                label="크기"
                currentSort={sort}
                onSortChange={onSortChange}
                disabled={sortDisabled}
                className="justify-end"
              />
            </th>
            <th className="text-left px-3 py-2 font-medium w-48">
              <SortButton
                column="lastModified"
                label="최종 수정"
                currentSort={sort}
                onSortChange={onSortChange}
                disabled={sortDisabled}
              />
            </th>
            <th className="text-left px-3 py-2 font-medium w-28">
              권한
            </th>
            <th className="text-left px-3 py-2 font-medium w-24">
              소유자
            </th>
            <th className="text-left px-3 py-2 font-medium w-24">
              그룹
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {entries.map((entry) => {
            const isFolder = entry.kind === "folder"
            const id = entryId(entry.kind, entry.key)
            const isSelected = selectedKeys.has(id)

            return (
              <ContextMenu key={id}>
                <ContextMenuTrigger asChild>
                  <tr
                    onDoubleClick={() => onEntryDoubleClick?.(entry)}
                    className={cn(
                      "hover:bg-muted/50 transition-colors cursor-pointer",
                      isSelected && "bg-muted/40",
                    )}
                  >
                    <td className="px-3 py-1.5">
                      <Checkbox
                        checked={isSelected}
                        onCheckedChange={() => toggleOne(id)}
                        aria-label={`Select ${entry.name}`}
                      />
                    </td>
                    <td className="px-3 py-1.5">
                      {isFolder ? (
                        <button
                          type="button"
                          onClick={() => onFolderOpen(entry.key)}
                          className="flex items-center gap-2 hover:text-primary transition-colors group"
                        >
                          <Folder className="h-4 w-4 text-muted-foreground shrink-0" />
                          <span className="group-hover:underline truncate">
                            {entry.name}
                          </span>
                        </button>
                      ) : (
                        <div className="flex items-center gap-2">
                          <FileIcon
                            name={entry.name}
                            className="h-4 w-4 text-muted-foreground shrink-0"
                          />
                          <span className="truncate">{entry.name}</span>
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-right text-muted-foreground tabular-nums">
                      {isFolder ? "-" : formatBytes(entry.size)}
                    </td>
                    <td className="px-3 py-1.5 text-muted-foreground">
                      {isFolder ? "-" : formatDate(entry.lastModified)}
                    </td>
                    <td className="px-3 py-1.5 text-muted-foreground font-mono text-xs">
                      {entry.permissions ?? "-"}
                    </td>
                    <td className="px-3 py-1.5 text-muted-foreground">
                      {entry.owner ?? "-"}
                    </td>
                    <td className="px-3 py-1.5 text-muted-foreground">
                      {entry.group ?? "-"}
                    </td>
                  </tr>
                </ContextMenuTrigger>
                <ContextMenuContent>
                  {canManage && (
                    <>
                      <ContextMenuItem onClick={() => onContextAction?.("rename", entry)}>
                        <Pencil className="h-4 w-4" />
                        이름 변경
                      </ContextMenuItem>
                      <ContextMenuItem onClick={() => onContextAction?.("delete", entry)}>
                        <Trash2 className="h-4 w-4" />
                        삭제
                      </ContextMenuItem>
                      <ContextMenuSeparator />
                    </>
                  )}
                  <ContextMenuItem onClick={() => onContextAction?.("properties", entry)}>
                    <Info className="h-4 w-4" />
                    속성
                  </ContextMenuItem>
                  {entry.kind === "file" && (
                    <ContextMenuItem
                      onClick={() => onContextAction?.("view", entry)}
                      disabled={!isViewableFile(entry.name)}
                    >
                      <Eye className="h-4 w-4" />
                      보기
                    </ContextMenuItem>
                  )}
                  {entry.kind === "file" && isCsvTsvFile(entry.name) && (
                    <ContextMenuItem
                      onClick={() => onContextAction?.("view-cat", entry)}
                    >
                      <FileTerminal className="h-4 w-4" />
                      텍스트 보기
                    </ContextMenuItem>
                  )}
                  {entry.kind === "file" && (
                    <>
                      <ContextMenuSeparator />
                      <ContextMenuItem onClick={() => onContextAction?.("download", entry)}>
                        <Download className="h-4 w-4" />
                        다운로드
                      </ContextMenuItem>
                    </>
                  )}
                  <ContextMenuSeparator />
                  <ContextMenuItem onClick={() => handleCopyPath(entry)}>
                    <ClipboardCopy className="h-4 w-4" />
                    경로 복사
                  </ContextMenuItem>
                </ContextMenuContent>
              </ContextMenu>
            )
          })}
        </tbody>
      </table>
      {toastMessage && <CopyToast message={toastMessage} onDone={hideToast} />}
    </div>
  )
}
