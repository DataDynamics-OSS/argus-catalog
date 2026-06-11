"use client"

import { ChevronRight, HardDrive } from "lucide-react"

import { cn } from "@workspace/ui/lib/utils"

type BrowserBreadcrumbProps = {
  /** Current absolute path. */
  currentPath: string
  /** Callback when user navigates to a different path. */
  onNavigate: (path: string) => void
}

export function BrowserBreadcrumb({
  currentPath,
  onNavigate,
}: BrowserBreadcrumbProps) {
  const segments = currentPath
    .split("/")
    .filter(Boolean)

  return (
    <nav className="flex items-center gap-1 text-base overflow-x-auto py-1">
      {/* Root */}
      <button
        type="button"
        onClick={() => onNavigate("/")}
        className={cn(
          "flex items-center gap-1.5 px-2 py-1 rounded-md font-medium shrink-0",
          "hover:bg-accent hover:text-accent-foreground transition-colors",
          currentPath === "/" ? "text-foreground" : "text-muted-foreground",
        )}
      >
        <HardDrive className="h-4 w-4" />
        /
      </button>

      {segments.map((segment, i) => {
        const segmentPath = "/" + segments.slice(0, i + 1).join("/") + "/"
        const isLast = i === segments.length - 1

        return (
          <div key={segmentPath} className="flex items-center gap-1 shrink-0">
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
            <button
              type="button"
              onClick={() => onNavigate(segmentPath)}
              className={cn(
                "px-2 py-1 rounded-md transition-colors",
                "hover:bg-accent hover:text-accent-foreground",
                isLast
                  ? "font-medium text-foreground"
                  : "text-muted-foreground",
              )}
            >
              {segment}
            </button>
          </div>
        )
      })}
    </nav>
  )
}
