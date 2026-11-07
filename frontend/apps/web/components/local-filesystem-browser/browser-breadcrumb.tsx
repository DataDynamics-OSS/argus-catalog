"use client"

import { ChevronRight, HardDrive } from "lucide-react"

import { cn } from "@workspace/ui/lib/utils"

type BrowserBreadcrumbProps = {
  /** Current absolute path. */
  currentPath: string
  /** Callback when user navigates to a different path. */
  onNavigate: (path: string) => void
  /**
   * 이 경로 위로는 올라갈 수 없다(예: 일반 사용자가 보는 모델 버전 폴더). 루트 버튼은 이 경로로 가고,
   * 그 위의 경로 조각은 눌러지지 않는 글자로만 보인다. 없으면 "/" 가 루트.
   */
  rootPath?: string
}

/** "/a/b/" · "/a/b" → "/a/b" (루트는 "/"). */
function normalize(path: string): string {
  const p = "/" + path.split("/").filter(Boolean).join("/")
  return p
}

export function BrowserBreadcrumb({
  currentPath,
  onNavigate,
  rootPath,
}: BrowserBreadcrumbProps) {
  const segments = currentPath
    .split("/")
    .filter(Boolean)
  const root = normalize(rootPath ?? "/")
  const rootDepth = root === "/" ? 0 : root.split("/").filter(Boolean).length
  const atRoot = normalize(currentPath) === root

  return (
    <nav className="flex items-center gap-1 text-base overflow-x-auto py-1">
      {/* Root */}
      <button
        type="button"
        onClick={() => onNavigate(root === "/" ? "/" : root + "/")}
        title={root === "/" ? undefined : root}
        className={cn(
          "flex items-center gap-1.5 px-2 py-1 rounded-md font-medium shrink-0",
          "hover:bg-accent hover:text-accent-foreground transition-colors",
          atRoot ? "text-foreground" : "text-muted-foreground",
        )}
      >
        <HardDrive className="h-4 w-4" />
        /
      </button>

      {segments.map((segment, i) => {
        const segmentPath = "/" + segments.slice(0, i + 1).join("/") + "/"
        const isLast = i === segments.length - 1
        // 잠긴 루트보다 위(또는 루트 직전까지)의 조각 — 위치만 보여 주고 이동은 막는다
        const locked = i + 1 < rootDepth

        return (
          <div key={segmentPath} className="flex items-center gap-1 shrink-0">
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
            {locked ? (
              <span className="px-2 py-1 text-muted-foreground/70">{segment}</span>
            ) : (
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
            )}
          </div>
        )
      })}
    </nav>
  )
}
