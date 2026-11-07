"use client"

import { type ColumnDef } from "@tanstack/react-table"

import { Checkbox } from "@workspace/ui/components/checkbox"
import { formatDateTime, timeAgo } from "@/components/model-hub/format"
import { type ModelSummary } from "../data/schema"
import { StageBadge, VersionStatusBadge } from "./detail/shared"

export function createModelsColumns(
  onModelClick: (name: string) => void,
): ColumnDef<ModelSummary>[] {
  return [
    {
      id: "select",
      header: ({ table }) => (
        <Checkbox
          checked={
            table.getIsAllPageRowsSelected() ||
            (table.getIsSomePageRowsSelected() && "indeterminate")
          }
          onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
          aria-label="전체 선택"
        />
      ),
      cell: ({ row }) => (
        <Checkbox
          checked={row.getIsSelected()}
          onCheckedChange={(value) => row.toggleSelected(!!value)}
          aria-label="행 선택"
          onClick={(e) => e.stopPropagation()}
        />
      ),
      enableSorting: false,
      enableHiding: false,
      meta: { className: "w-[40px] text-center" },
    },
    {
      accessorKey: "name",
      header: "모델",
      cell: ({ row }) => {
        const m = row.original
        return (
          <div className="min-w-0 py-0.5">
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="text-left text-sm font-medium hover:underline"
                onClick={(e) => {
                  e.stopPropagation()
                  onModelClick(m.name)
                }}
              >
                {m.name}
              </button>
              {/* 등록 중·실패처럼 주의가 필요한 상태만 표시한다 */}
              {m.latest_version_status && m.latest_version_status !== "READY" && (
                <VersionStatusBadge status={m.latest_version_status} />
              )}
            </div>
            {m.description && (
              <p className="max-w-[520px] truncate text-xs text-muted-foreground" title={m.description}>
                {m.description}
              </p>
            )}
          </div>
        )
      },
    },
    {
      id: "production",
      header: "운영 버전",
      cell: ({ row }) => {
        const m = row.original
        return (
          <div className="flex items-center gap-1.5 text-sm">
            {m.production_version
              ? <StageBadge stage="PRODUCTION" version={m.production_version} />
              : <span className="text-muted-foreground">—</span>}
            <span className="text-xs text-muted-foreground">최신 v{m.max_version_number}</span>
          </div>
        )
      },
      meta: { className: "w-[170px]" },
    },
    {
      accessorKey: "owner",
      header: "소유자",
      cell: ({ row }) => (
        <span className="text-sm">{row.original.owner || <span className="text-muted-foreground">—</span>}</span>
      ),
      meta: { className: "w-[130px]" },
    },
    {
      id: "framework",
      header: "프레임워크",
      cell: ({ row }) => (
        <span className="block truncate text-sm text-muted-foreground" title={row.original.framework ?? undefined}>
          {row.original.framework ?? "—"}
        </span>
      ),
      meta: { className: "w-[150px]" },
    },
    {
      id: "updated_at",
      header: "최근 변경",
      cell: ({ row }) => {
        // 목록 응답은 zod 파싱 없이 들어와 문자열일 수 있다
        const iso = String(row.original.updated_at)
        return (
          <span className="text-sm text-muted-foreground" title={formatDateTime(iso)}>
            {timeAgo(iso)}
          </span>
        )
      },
      meta: { className: "w-[100px]" },
    },
  ]
}
