"use client"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { StatusBadge as ToneBadge, type StatusTone } from "@workspace/ui/components/status-badge"

import { OCI_STATUS_LABELS } from "@/features/oci-hub/api"
import { SOURCE_LABELS } from "@/features/oci-hub/oci-hub-dashboard"

// 표시 형식은 MLflow 모델과 공유한다.
export { formatBytes as formatSize, formatDateTime, timeAgo } from "@/components/model-hub/format"

/** 버전이 없으면(0) "-", 있으면 v{n}. */
export function versionLabel(v: number): string {
  return v > 0 ? `v${v}` : "-"
}

// 모델 · 버전 상태 톤(TODO-107) — 진한 단색 대신 의미 톤의 옅은 칠
const STATUS_TONE: Record<string, StatusTone> = {
  draft: "neutral", review: "info", approved: "success", production: "success",
  deprecated: "warning", archived: "muted",
  ready: "success", importing: "info", failed: "danger",
}

export function StatusBadge({ status }: { status: string }) {
  return <ToneBadge tone={STATUS_TONE[status] ?? "neutral"} label={OCI_STATUS_LABELS[status] ?? status} />
}

export function SourceBadge({ sourceType }: { sourceType: string | null }) {
  if (!sourceType) return null
  // 출처는 분류라 무채색 테두리(TODO-107)
  return <Badge variant="outline">{SOURCE_LABELS[sourceType] ?? sourceType}</Badge>
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
