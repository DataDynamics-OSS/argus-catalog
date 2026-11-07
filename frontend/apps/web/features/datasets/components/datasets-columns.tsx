"use client"

import { type ColumnDef } from "@tanstack/react-table"
import { Database, MoreHorizontal } from "lucide-react"
import Link from "next/link"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import { useAuth } from "@/features/auth"
import { canManageDataset } from "../permissions"
import { type DatasetSummary } from "../data/schema"
import { useDatasets } from "./datasets-provider"

// URN path 추출은 avro.ts 로 옮김 — 기존 import 호환용 재노출
export { pathFromUrn } from "../avro"

function RowActions({ row }: { row: DatasetSummary }) {
  const { setOpen, setCurrentRow } = useDatasets()
  const { user } = useAuth()
  // 삭제는 서버와 같은 기준(생성자 또는 관리자)일 때만 노출
  const canDelete = canManageDataset(user, row.created_by)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className="h-8 w-8 p-0"
          onClick={(e) => e.stopPropagation()}
        >
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem asChild>
          <Link href={`/dashboard/datasets/${row.id}`}>상세 보기</Link>
        </DropdownMenuItem>
        {canDelete && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() => {
                setCurrentRow(row)
                setOpen("delete")
              }}
              className="text-destructive focus:text-destructive"
            >
              삭제
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
          {/* logical 이 있으면 logical 만, 없으면 정규화 경로(database.schema.table)를 메인으로. */}
          <Link
            href={`/dashboard/datasets/${row.original.id}`}
            className={
              logical
                ? "font-medium text-sm hover:underline"
                : "font-medium text-sm hover:underline uppercase"
            }
            onClick={(e) => e.stopPropagation()}
          >
            {logical || qualified}
          </Link>
          {/* 메인 라인 하단에 summary 노출 */}
          {summary && (
            <p className="text-xs text-muted-foreground truncate max-w-[400px]" title={summary}>
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
      <div className="flex items-center justify-center gap-1.5">
        <Database className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-sm">{row.original.datasource_name}</span>
      </div>
    ),
    meta: { className: "w-[150px] text-center" },
  },
  {
    accessorKey: "origin",
    header: "환경",
    cell: ({ row }) => {
      // 데이터 소스 카드 legend 와 동일: PROD=emerald, STAGING=orange, DEV=sky
      const origin = row.original.origin
      const bg =
        origin === "PROD"
          ? "bg-emerald-600"
          : origin === "STAGING"
            ? "bg-orange-500"
            : "bg-sky-500"
      return (
        <Badge className={`text-xs text-white ${bg}`}>
          {origin}
        </Badge>
      )
    },
    meta: { className: "w-[120px] text-center" },
  },
  {
    accessorKey: "status",
    header: "상태",
    cell: ({ row }) => {
      const status = row.original.status
      const label =
        status === "active"
          ? "활성"
          : status === "inactive"
            ? "비활성"
            : status === "deprecated"
              ? "사용 중단"
              : status === "removed"
                ? "삭제됨"
                : status
      const className =
        status === "active"
          ? "bg-primary text-primary-foreground text-xs"
          : status === "inactive"
            ? "bg-amber-500 text-white text-xs"
            : status === "deprecated"
              ? "bg-zinc-600 text-white text-xs"
              : "bg-destructive text-destructive-foreground text-xs"
      return <Badge className={className}>{label}</Badge>
    },
    meta: { className: "w-[100px] text-center" },
  },
  {
    id: "is_synced",
    header: "동기화",
    cell: ({ row }) =>
      row.original.is_synced === "true" ? (
        <span className="inline-flex items-center rounded-full border border-orange-400 px-1.5 py-0.5 text-[9px] font-semibold text-orange-500">
          동기화됨
        </span>
      ) : null,
    meta: { className: "w-[80px] text-center" },
  },
  {
    id: "schema_field_count",
    header: "필드",
    cell: ({ row }) => (
      <span className="text-sm text-muted-foreground">
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
        <span className="text-sm text-muted-foreground">
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
