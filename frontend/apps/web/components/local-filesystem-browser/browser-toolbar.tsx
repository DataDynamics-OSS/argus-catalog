"use client"

import { useEffect, useRef, useState } from "react"
import {
  Download,
  Eye,
  FolderPlus,
  Info,
  Pencil,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { SearchInput } from "@/components/search-input"

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
  /** 업로드/생성/삭제/이름변경 가능 여부 — false 면 해당 버튼을 숨긴다 */
  canManage?: boolean
  /** 내려받기 버튼을 보일지(기본 true) */
  canDownload?: boolean
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
  canDownload = true,
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

  function handleSearchNow() {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    onSearchChange(localSearch)
  }

  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex items-center gap-2 flex-1">
        <SearchInput
          size="sm"
          className="w-[286px]"
          placeholder="이름으로 거르기…"
          value={localSearch}
          onValueChange={handleChange}
          onSearch={handleSearchNow}
          onClear={handleClear}
        />

        {selectedCount > 0 && (
          <span className="text-sm">
            {selectedCount}개 선택됨
          </span>
        )}
      </div>

      <div className="flex items-center gap-1.5">
        {/* Upload / Download */}
        {canManage && (
          <Button variant="outline" size="sm" onClick={onUpload} className="h-8 gap-1.5">
            <Upload className="h-4 w-4" />
            업로드
          </Button>
        )}
        {canDownload && (
          <Button
            variant="outline"
            size="sm"
            onClick={onDownload}
            disabled={selectedCount === 0}
            className="h-8 gap-1.5"
          >
            <Download className="h-4 w-4" />
            내려받기
          </Button>
        )}

        {canManage && (
          <>
            <ToolbarSeparator />

            {/* Create / Delete / Rename */}
            <Button variant="outline" size="sm" onClick={onCreateFolder} className="h-8 gap-1.5">
              <FolderPlus className="h-4 w-4" />
              폴더 만들기
            </Button>
            <Button
              variant="outline" size="sm" onClick={onDelete}
              disabled={selectedCount === 0} className="h-8 gap-1.5"
            >
              <Trash2 className="h-4 w-4" />
              삭제
            </Button>
            <Button
              variant="outline" size="sm" onClick={onRename}
              disabled={selectedCount !== 1} className="h-8 gap-1.5"
            >
              <Pencil className="h-4 w-4" />
              이름 변경
            </Button>
          </>
        )}

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
