"use client"

import { Tip } from "@/components/tip"
import { SourceMapBadge } from "@/features/catalogs/components/source-map-badge"
import { type ColumnDef } from "@tanstack/react-table"
import { Database, MoreHorizontal, Trash2 } from "lucide-react"
import Link from "next/link"

import { StatusBadge } from "@workspace/ui/components/status-badge"
import { cn } from "@workspace/ui/lib/utils"
import { Button } from "@workspace/ui/components/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import { useCanDeleteDataset } from "../use-can-delete-dataset"
import { datasetStatus } from "../status"
import { type DatasetSummary } from "../data/schema"
import { useDatasets } from "./datasets-provider"
import { OriginText } from "./origin-text"
import { SyncedBadge } from "./synced-badge"
import { LinkMenuItems } from "@/components/link-menu-items"

// URN path 추출은 avro.ts 로 옮김 — 기존 import 호환용 재노출
export { pathFromUrn } from "../avro"

function RowActions({ row }: { row: DatasetSummary }) {
  const { setOpen, setCurrentRow } = useDatasets()
  // 삭제는 서버와 같은 기준(생성자 또는 관리자 + datasets.delete 기능 권한)일 때만 노출
  const canDelete = useCanDeleteDataset()(row)
  return (
    <DropdownMenu>
      <Tip label={`${row.name} 메뉴`}><DropdownMenuTrigger asChild>
        <Button aria-label={`${row.name} 메뉴`}
          variant="ghost"
          className="h-8 w-8 p-0"
          onClick={(e) => e.stopPropagation()}
        >
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger></Tip>
      <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
        <LinkMenuItems href={`/dashboard/datasets/${row.id}`} urn={row.urn} />
        {canDelete && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() => {
                setCurrentRow(row)
                setOpen("delete")
              }}
              variant="destructive"
            >
              <Trash2 /> 삭제
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export const datasetsColumns: ColumnDef<DatasetSummary>[] = [
  {
    accessorKey: "name",
    header: "이름",
    cell: ({ row }) => {
      const logical = row.original.display_name
      // logical 이 없으면 database.schema.table 경로 표시.
      // name 에 동기화 시 "{database}.{table}"(혹은 schema 포함)이 그대로 저장되어 있어 그대로 사용.
      const qualified = row.original.name
      const summary = row.original.summary
      return (
        <div className="min-w-0">
          {/* 표시 이름(한글 등)이 있으면 왼쪽에 표시 이름, 오른쪽에 정규화 경로(SAKILA.CUSTOMERS) — 둘 다 보인다(TODO-142).
              없으면 정규화 경로만 메인으로. */}
          <div className="flex min-w-0 items-baseline gap-2">
            <Link
              href={`/dashboard/datasets/${row.original.id}`}
              className={cn("shrink-0 text-sm font-medium hover:underline", !logical && "uppercase")}
              onClick={(e) => e.stopPropagation()}
            >
              {logical || qualified}
            </Link>
            {logical && (
              <span className="min-w-0 truncate font-mono text-xs uppercase text-muted-foreground" title={qualified}>
                {qualified}
              </span>
            )}
          </div>
          {/* 메인 라인 하단에 summary 노출 */}
          {summary && (
            <p className="text-xs truncate max-w-[400px]" title={summary}>
              {summary}
            </p>
          )}
        </div>
      )
    },
  },
  {
    accessorKey: "datasource_name",
    header: "데이터 소스",
    cell: ({ row }) => (
      <div className="flex flex-col items-center gap-0.5">
        <div className="flex items-center justify-center gap-1.5">
          <Database className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-sm">{row.original.datasource_name}</span>
        </div>
        {/* 수동 데이터셋(직접 등록한 카탈로그, TODO-CAT-001)은 원천 매핑 상태를 같이 — 데이터 소스 이름은 카탈로그 이름 */}
        <SourceMapBadge status={row.original.source_map_status} />
      </div>
    ),
    meta: { className: "w-[150px] text-center" },
  },
  {
    accessorKey: "origin",
    header: "환경",
    cell: ({ row }) => {
      // 환경은 점 + 글자(TODO-107) — 데이터 소스 화면 · 트리와 같은 `OriginText`
      return <OriginText origin={row.original.origin} />
    },
    meta: { className: "w-[120px] text-center" },
  },
  {
    accessorKey: "status",
    header: "상태",
    cell: ({ row }) => {
      return <StatusBadge {...datasetStatus(row.original.status)} />
    },
    meta: { className: "w-[100px] text-center" },
  },
  {
    id: "is_synced",
    header: "동기화",
    cell: ({ row }) =>
      row.original.is_synced === "true" ? (
        <SyncedBadge />
      ) : null,
    meta: { className: "w-[80px] text-center" },
  },
  {
    id: "schema_field_count",
    header: "필드",
    cell: ({ row }) => (
      <span className="text-sm">
        {row.original.schema_field_count}
      </span>
    ),
    meta: { className: "w-[70px] text-center" },
  },
  {
    accessorKey: "created_at",
    header: "등록일",
    cell: ({ row }) => {
      // schema 는 ``z.coerce.date()`` 지만, fetch 경로에서 zod parse 가 안 되면
      // string 으로 들어올 수 있어 안전하게 Date 로 변환한다.
      const raw = row.original.created_at as unknown
      const d = raw instanceof Date ? raw : new Date(raw as string)
      if (Number.isNaN(d.getTime())) return null
      const pad = (n: number) => String(n).padStart(2, "0")
      return (
        <span className="text-sm">
          {`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`}
        </span>
      )
    },
    meta: { className: "w-[110px] text-center" },
  },
  {
    id: "actions",
    cell: ({ row }) => <RowActions row={row.original} />,
    meta: { className: "w-[50px]" },
  },
]
