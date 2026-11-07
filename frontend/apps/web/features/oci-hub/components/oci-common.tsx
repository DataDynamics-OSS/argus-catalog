"use client"

import { Button } from "@workspace/ui/components/button"

import { OCI_STATUS_LABELS } from "@/features/oci-hub/api"
import { SOURCE_LABELS } from "@/features/oci-hub/oci-hub-dashboard"

// 표시 형식은 MLflow 모델과 공유한다.
export { formatBytes as formatSize, formatDateTime, timeAgo } from "@/components/model-hub/format"

/** 버전이 없으면(0) "-", 있으면 v{n}. */
export function versionLabel(v: number): string {
  return v > 0 ? `v${v}` : "-"
}

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-zinc-400", review: "bg-amber-500", approved: "bg-blue-500",
  production: "bg-green-600", deprecated: "bg-zinc-600", archived: "bg-zinc-800",
  ready: "bg-green-600", importing: "bg-sky-500", failed: "bg-red-600",
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold text-white ${STATUS_COLORS[status] || "bg-zinc-400"}`}
      title={status}
    >
      {OCI_STATUS_LABELS[status] ?? status}
    </span>
  )
}

const SOURCE_BADGE_COLORS: Record<string, string> = {
  huggingface: "text-blue-600 bg-blue-50 border-blue-200",
  my: "text-orange-600 bg-orange-50 border-orange-200",
  mlflow: "text-violet-600 bg-violet-50 border-violet-200",
  file: "text-green-600 bg-green-50 border-green-200",
  local: "text-green-600 bg-green-50 border-green-200",
}

export function SourceBadge({ sourceType }: { sourceType: string | null }) {
  if (!sourceType) return null
  const color = SOURCE_BADGE_COLORS[sourceType] || "text-zinc-600 bg-zinc-50 border-zinc-200"
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${color}`}>
      {SOURCE_LABELS[sourceType] ?? sourceType}
    </span>
  )
}

/** 가이드·사용법 예시에 넣을 서버 주소 — 사용자가 지금 접속한 주소(프록시 경유)를 그대로 쓴다. */
export function currentServerUrl(): string {
  if (typeof window === "undefined") return "http://<argus-catalog-server>"
  return window.location.origin
}

/** 1 … 4 5 [6] 7 8 … 20 형태의 페이지 번호 목록. */
export function pageItems(page: number, totalPages: number): (number | "…")[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1)
  const items: (number | "…")[] = [1]
  const start = Math.max(2, page - 1)
  const end = Math.min(totalPages - 1, page + 1)
  if (start > 2) items.push("…")
  for (let p = start; p <= end; p++) items.push(p)
  if (end < totalPages - 1) items.push("…")
  items.push(totalPages)
  return items
}

export function Pager({
  page, totalPages, onPage,
}: { page: number; totalPages: number; onPage: (p: number) => void }) {
  if (totalPages <= 1) return null
  return (
    <div className="flex items-center justify-center gap-1 pt-2">
      <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>이전</Button>
      {pageItems(page, totalPages).map((p, i) =>
        p === "…" ? (
          <span key={`gap-${i}`} className="px-1 text-sm text-muted-foreground">…</span>
        ) : (
          <Button
            key={p}
            variant={p === page ? "default" : "outline"}
            size="sm"
            className="h-8 min-w-8 px-2"
            onClick={() => onPage(p)}
            aria-current={p === page ? "page" : undefined}
          >
            {p}
          </Button>
        ),
      )}
      <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => onPage(page + 1)}>다음</Button>
    </div>
  )
}

/** 선택 값이 비어 있을 때 쓰는 Select sentinel (Radix Select 는 빈 문자열 값을 허용하지 않는다). */
export const ALL = "__all__"
