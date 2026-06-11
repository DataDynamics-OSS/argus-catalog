"use client"

import { useState, useEffect, useRef } from "react"
import { type Table } from "@tanstack/react-table"
import { X } from "lucide-react"

import { cn } from "@workspace/ui/lib/utils"
import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Separator } from "@workspace/ui/components/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@workspace/ui/components/tooltip"

type DataTableBulkActionsProps<TData> = {
  table: Table<TData>
  entityName?: string
  children?: React.ReactNode
}

export function DataTableBulkActions<TData>({
  table,
  entityName = "항목",
  children,
}: DataTableBulkActionsProps<TData>): React.ReactNode | null {
  const selectedRows = table.getFilteredSelectedRowModel().rows
  const selectedCount = selectedRows.length
  const toolbarRef = useRef<HTMLDivElement>(null)
  const [announcement, setAnnouncement] = useState("")

  useEffect(() => {
    if (selectedCount > 0) {
      const message = `${selectedCount}개의 ${entityName} 항목이 선택되었습니다.`
      queueMicrotask(() => setAnnouncement(message))
      const timer = setTimeout(() => setAnnouncement(""), 3000)
      return () => clearTimeout(timer)
    }
  }, [selectedCount, entityName])

  const handleClearSelection = () => {
    table.resetRowSelection()
  }

  const handleKeyDown = (event: React.KeyboardEvent) => {
    const buttons = toolbarRef.current?.querySelectorAll("button")
    if (!buttons) return
    const currentIndex = Array.from(buttons).findIndex(
      (button) => button === document.activeElement
    )
    switch (event.key) {
      case "ArrowRight": {
        event.preventDefault()
        buttons[(currentIndex + 1) % buttons.length]?.focus()
        break
      }
      case "ArrowLeft": {
        event.preventDefault()
        buttons[
          currentIndex === 0 ? buttons.length - 1 : currentIndex - 1
        ]?.focus()
        break
      }
      case "Escape":
        event.preventDefault()
        handleClearSelection()
        break
    }
  }

  if (selectedCount === 0) return null

  return (
    <>
      <div aria-live="polite" aria-atomic="true" className="sr-only" role="status">
        {announcement}
      </div>
      <div
        ref={toolbarRef}
        role="toolbar"
        aria-label={`선택한 ${selectedCount}개의 ${entityName} 항목에 대한 일괄 작업`}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className={cn(
          "fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-xl",
          "transition-all delay-100 duration-300 ease-out hover:scale-105"
        )}
      >
        <div
          className={cn(
            "p-2 shadow-xl rounded-xl border",
            "bg-background/95 backdrop-blur-lg",
            "flex items-center gap-x-2"
          )}
        >
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="icon"
                onClick={handleClearSelection}
                className="size-6 rounded-full"
                aria-label="선택 해제"
              >
                <X />
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>선택 해제 (Escape)</p>
            </TooltipContent>
          </Tooltip>
          <Separator className="h-5" orientation="vertical" />
          <div className="flex items-center gap-x-1 text-sm">
            <Badge variant="default" className="min-w-8 rounded-lg">
              {selectedCount}
            </Badge>{" "}
            <span className="hidden sm:inline">{entityName}</span>{" "}
            선택됨
          </div>
          {children && (
            <>
              <Separator className="h-5" orientation="vertical" />
              {children}
            </>
          )}
        </div>
      </div>
    </>
  )
}
