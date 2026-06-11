"use client"

import { useEffect, useRef, useState } from "react"
import {
  Download,
  Eye,
  FolderPlus,
  Info,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"

type BrowserToolbarProps = {
  searchValue: string
  onSearchChange: (value: string) => void
  selectedCount: number
  onUpload: () => void
  onCreateFolder: () => void
  onDelete: () => void
  onDownload: () => void
  onRename: () => void
  onProperties: () => void
  onView: () => void
  viewDisabled: boolean
  onRefresh: () => void
  isLoading: boolean
  /** admin 만 업로드/생성/삭제/이름변경 — false 면 해당 버튼 비활성 */
  canManage?: boolean
}

function ToolbarSeparator() {
  return <div className="w-px h-5 bg-border mx-0.5" />
}

export function BrowserToolbar({
  searchValue,
  onSearchChange,
  selectedCount,
  onUpload,
  onCreateFolder,
  onDelete,
  onDownload,
  onRename,
  onProperties,
  onView,
  viewDisabled,
  onRefresh,
  isLoading,
  canManage = false,
}: BrowserToolbarProps) {
  const [localSearch, setLocalSearch] = useState(searchValue)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Sync external -> local when parent resets (e.g. navigation)
  useEffect(() => {
    setLocalSearch(searchValue)
  }, [searchValue])

  function handleChange(value: string) {
    setLocalSearch(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      onSearchChange(value)
    }, 3000)
  }

  function handleClear() {
    setLocalSearch("")
    if (debounceRef.current) clearTimeout(debounceRef.current)
    onSearchChange("")
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") {
      if (debounceRef.current) clearTimeout(debounceRef.current)
      onSearchChange(localSearch)
    }
  }

  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex items-center gap-2 flex-1">
        <div className="relative w-[286px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            placeholder="이름으로 필터..."
            value={localSearch}
            onChange={(e) => handleChange(e.target.value)}
            onKeyDown={handleKeyDown}
            className="h-8 pl-8 pr-8 text-sm"
          />
          {localSearch && (
            <button
              type="button"
              onClick={handleClear}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {selectedCount > 0 && (
          <span className="text-sm text-muted-foreground">
            {selectedCount}개 선택됨
          </span>
        )}
      </div>

      <div className="flex items-center gap-1.5">
        {/* Upload / Download */}
        <Button variant="outline" size="sm" onClick={onUpload} disabled={!canManage}
          title={canManage ? undefined : "관리자만 업로드할 수 있습니다"} className="h-8 gap-1.5">
          <Upload className="h-4 w-4" />
          업로드
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={onDownload}
          disabled={selectedCount === 0}
          className="h-8 gap-1.5"
        >
          <Download className="h-4 w-4" />
          다운로드
        </Button>

        <ToolbarSeparator />

        {/* Create / Delete / Rename */}
        <Button variant="outline" size="sm" onClick={onCreateFolder} disabled={!canManage}
          title={canManage ? undefined : "관리자만 폴더를 생성할 수 있습니다"} className="h-8 gap-1.5">
          <FolderPlus className="h-4 w-4" />
          생성
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={onDelete}
          disabled={selectedCount === 0 || !canManage}
          title={canManage ? undefined : "관리자만 삭제할 수 있습니다"}
          className="h-8 gap-1.5"
        >
          <Trash2 className="h-4 w-4" />
          삭제
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={onRename}
          disabled={selectedCount !== 1 || !canManage}
          title={canManage ? undefined : "관리자만 이름을 변경할 수 있습니다"}
          className="h-8 gap-1.5"
        >
          <Pencil className="h-4 w-4" />
          이름 변경
        </Button>

        <ToolbarSeparator />

        {/* Properties / View */}
        <Button
          variant="outline"
          size="sm"
          onClick={onProperties}
          disabled={selectedCount !== 1}
          className="h-8 gap-1.5"
        >
          <Info className="h-4 w-4" />
          속성
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={onView}
          disabled={viewDisabled}
          className="h-8 gap-1.5"
        >
          <Eye className="h-4 w-4" />
          보기
        </Button>

        <ToolbarSeparator />

        {/* Refresh */}
        <Button
          variant="outline"
          size="sm"
          onClick={onRefresh}
          disabled={isLoading}
          className="h-8 gap-1.5"
        >
          <RefreshCw className="h-4 w-4" />
          새로고침
        </Button>
      </div>
    </div>
  )
}
