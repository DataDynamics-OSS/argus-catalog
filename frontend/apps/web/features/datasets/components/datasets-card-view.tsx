"use client"

import { useEffect, useState } from "react"
import { Trash2, X } from "lucide-react"

import { StatusBadge } from "@workspace/ui/components/status-badge"
import { DropdownMenuItem, DropdownMenuSeparator } from "@workspace/ui/components/dropdown-menu"

import type { DatasetSummary } from "../data/schema"
import { pathFromUrn } from "./datasets-columns"
import { datasetStatus } from "../status"
import { OriginText } from "./origin-text"
import { SyncedBadge } from "./synced-badge"
import { ageLabel } from "../card-format"
import { EntityCard, EntityCardGrid, EntityCardText } from "@/components/entity-card"
import { LinkMenuItems } from "@/components/link-menu-items"

type CardActions = {
  onDelete?: (row: DatasetSummary) => void
  /** 지정 시 true 인 카드에만 "삭제" 를 보인다(생성자 · 관리자 + datasets.delete). */
  canDelete?: (row: DatasetSummary) => boolean
  onRemove?: (id: number) => void
  removeLabel?: string
  /** 지정 시 true 인 카드에만 "제거" 를 보인다. */
  canRemove?: (row: DatasetSummary) => boolean
}

function formatYmd(d: Date | string): string {
  const date = d instanceof Date ? d : new Date(d)
  if (Number.isNaN(date.getTime())) return ""
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 앞쪽 컬럼 이름 칩 + "+N" — 한 줄에 두고 좁으면 칩 글자가 줄어든다("+N" 은 늘 보인다) */
function ColumnChips({ names, total }: { names: string[]; total: number }) {
  if (names.length === 0) return null
  const rest = total - names.length
  return (
    <div className="flex min-w-0 items-center gap-1" title={names.join(", ") + (rest > 0 ? ` 외 ${rest}개` : "")}>
      {names.map((n) => (
        <span key={n} className="min-w-0 max-w-[8rem] truncate rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] leading-4 text-foreground/80">
          {n}
        </span>
      ))}
      {rest > 0 && <span className="shrink-0 px-0.5 text-[11px] leading-4 tabular-nums">+{rest}</span>}
    </div>
  )
}

function DatasetCard({
  row, now, onDelete, canDelete, onRemove, removeLabel = "제거", canRemove,
}: { row: DatasetSummary; now: number | null } & CardActions) {
  const href = `/dashboard/datasets/${row.id}`
  // URN 경로에서 마지막(테이블)을 뗀 앞부분 — 데이터 소스 이름 뒤에 경로로 붙인다
  const path = pathFromUrn(row.urn)
  const lastDot = path.lastIndexOf(".")
  const prefix = lastDot >= 0 ? path.slice(0, lastDot) : ""
  const crumbs = [row.datasource_name, ...(prefix ? prefix.split(".") : [])].filter(Boolean)
  const showRemove = onRemove && (!canRemove || canRemove(row))

  return (
    <EntityCard
      crumb={crumbs.join(" › ")}
      title={row.display_name || row.name}
      prominent={!!row.display_name}
      identifier={row.display_name ? row.name : null}
      href={href}
      menuLabel={`${row.name} 메뉴`}
      menu={
        <>
          <LinkMenuItems href={href} urn={row.urn} />
          {onDelete && (!canDelete || canDelete(row)) && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => onDelete(row)} variant="destructive">
                <Trash2 /> 삭제
              </DropdownMenuItem>
            </>
          )}
          {showRemove && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => onRemove(row.id)} variant="destructive">
                <X /> {removeLabel}
              </DropdownMenuItem>
            </>
          )}
        </>
      }
      footer={
        <>
          <span className="tabular-nums">
            컬럼 {row.schema_field_count.toLocaleString()} · 태그 {row.tag_count} · 담당자 {row.owner_count}
          </span>
          <span className="ml-auto whitespace-nowrap tabular-nums" title={`갱신 ${formatYmd(row.updated_at)} · 등록 ${formatYmd(row.created_at)}`}>
            {now == null ? formatYmd(row.updated_at) : `${ageLabel(row.updated_at, now)} 갱신`}
          </span>
        </>
      }
    >
      {/* 환경 · 상태(활성이 아닐 때만) · 동기화 — 표시 방식은 목록 표와 같다(TODO-107) */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <OriginText origin={row.origin || "DEV"} className="mr-1" />
        {row.status !== "active" && <StatusBadge {...datasetStatus(row.status)} />}
        {row.is_synced === "true" && <SyncedBadge />}
      </div>

      {/* 요약(없으면 설명) — 두 줄 */}
      <EntityCardText text={row.summary || row.description} />

      {row.column_preview && <ColumnChips names={row.column_preview} total={row.schema_field_count} />}
    </EntityCard>
  )
}

/**
 * 데이터셋 카드 보기(TODO-111). 너비에 맞춰 열 수가 바뀌는 격자이고 한 줄의 카드 높이는 같다.
 * 카드 어디를 눌러도 상세로 가고, ⋯ 메뉴는 마우스를 올렸을 때(터치 기기는 늘) 보인다.
 * 컬럼 미리보기는 목록을 ``card=true`` 로 받았을 때만 있다.
 */
export function DatasetsCardView({
  rows,
  onDelete,
  canDelete,
  onRemove,
  removeLabel,
  canRemove,
}: { rows: DatasetSummary[] } & CardActions) {
  // "N일 전" 기준 시각 — 렌더 중에 시계를 읽지 않도록 마운트 뒤에 정한다
  const [now, setNow] = useState<number | null>(null)
  useEffect(() => { setNow(Date.now()) }, [])

  if (rows.length === 0) {
    return (
      <div className="rounded-md border border-dashed py-12 text-center text-sm text-muted-foreground">
        표시할 데이터셋이 없습니다.
      </div>
    )
  }
  return (
    <EntityCardGrid>
      {rows.map((row) => (
        <DatasetCard
          key={row.id}
          row={row}
          now={now}
          onDelete={onDelete}
          canDelete={canDelete}
          onRemove={onRemove}
          removeLabel={removeLabel}
          canRemove={canRemove}
        />
      ))}
    </EntityCardGrid>
  )
}
