"use client"

import Link from "next/link"
import { Calendar, Columns3, Database, MoreHorizontal } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"

import type { DatasetSummary } from "../data/schema"
import { pathFromUrn } from "./datasets-columns"

function originBg(origin: string): string {
  if (origin === "PROD") return "bg-emerald-600"
  if (origin === "STAGING") return "bg-orange-500"
  return "bg-sky-500"
}

function statusBadge(status: string) {
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
      ? "bg-primary text-primary-foreground text-[10px]"
      : status === "inactive"
        ? "bg-amber-500 text-white text-[10px]"
        : status === "deprecated"
          ? "bg-zinc-600 text-white text-[10px]"
          : "bg-destructive text-destructive-foreground text-[10px]"
  return <Badge className={className}>{label}</Badge>
}

function formatYmd(d: unknown): string {
  const date = d instanceof Date ? d : new Date(d as string)
  if (Number.isNaN(date.getTime())) return ""
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

type CardActions = {
  onDelete?: (row: DatasetSummary) => void
  onRemove?: (id: number) => void
  removeLabel?: string
}

function DatasetCard({ row, onDelete, onRemove, removeLabel = "제거" }: { row: DatasetSummary } & CardActions) {
  // URN 의 path 에서 마지막 토큰(table) 을 떼고 남은 prefix 를 12px 라인으로 표시.
  const path = pathFromUrn(row.urn)
  const lastDot = path.lastIndexOf(".")
  const prefix = lastDot >= 0 ? path.slice(0, lastDot) : ""
  return (
    <Card className="w-[350px] shrink-0 transition-colors hover:bg-muted/40">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            {prefix && (
              <p
                className="truncate text-[12px] leading-tight uppercase"
                title={prefix}
              >
                {prefix}
              </p>
            )}
            <CardTitle
              className={`text-sm truncate ${row.display_name ? "" : "uppercase"}`}
            >
              <Link
                href={`/dashboard/datasets/${row.id}`}
                className="hover:underline"
              >
                {row.display_name || row.name}
              </Link>
            </CardTitle>
            {row.display_name && (
              <p
                className="mt-0.5 truncate text-[11px] text-muted-foreground uppercase"
                title={row.name}
              >
                {row.name}
              </p>
            )}
            {row.summary && (
              <p
                className="mt-0.5 truncate text-xs text-muted-foreground"
                title={row.summary}
              >
                {row.summary}
              </p>
            )}
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" className="h-7 w-7 p-0" aria-label="액션">
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem asChild>
                <Link href={`/dashboard/datasets/${row.id}`}>상세 보기</Link>
              </DropdownMenuItem>
              {onDelete && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onSelect={() => onDelete(row)}
                    className="text-destructive focus:text-destructive"
                  >
                    삭제
                  </DropdownMenuItem>
                </>
              )}
              {onRemove && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onSelect={() => onRemove(row.id)}
                    className="text-destructive focus:text-destructive"
                  >
                    {removeLabel}
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </CardHeader>
      <CardContent className="space-y-2 pt-0">
        {/* 환경 + 상태 + 동기화 chip */}
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge className={`text-[10px] text-white ${originBg(row.origin)}`}>
            {row.origin || "DEV"}
          </Badge>
          {statusBadge(row.status)}
          {row.is_synced === "true" && (
            <span className="inline-flex items-center rounded-full border border-orange-400 px-1.5 py-0.5 text-[9px] font-semibold text-orange-500">
              동기화됨
            </span>
          )}
        </div>
        {/* 데이터 소스 / 필드 수 */}
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1 min-w-0">
            <Database className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{row.datasource_name}</span>
          </span>
          <span className="inline-flex items-center gap-1 whitespace-nowrap">
            <Columns3 className="h-3.5 w-3.5" />
            {row.schema_field_count}
          </span>
        </div>
        {/* 등록일 */}
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <Calendar className="h-3.5 w-3.5" />
          {formatYmd(row.created_at)}
        </div>
      </CardContent>
    </Card>
  )
}

export function DatasetsCardView({
  rows,
  onDelete,
  onRemove,
  removeLabel,
}: { rows: DatasetSummary[] } & CardActions) {
  if (rows.length === 0) {
    return (
      <div className="rounded-md border border-dashed py-12 text-center text-sm text-muted-foreground">
        표시할 데이터셋이 없습니다.
      </div>
    )
  }
  return (
    <div className="flex flex-wrap gap-3">
      {rows.map((row) => (
        <DatasetCard
          key={row.id}
          row={row}
          onDelete={onDelete}
          onRemove={onRemove}
          removeLabel={removeLabel}
        />
      ))}
    </div>
  )
}
