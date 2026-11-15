"use client"

import { formatBytes, formatDateTime, timeAgo } from "@/components/model-hub/format"
import { EntityCard, EntityCardGrid, EntityCardText } from "@/components/entity-card"
import { LinkMenuItems } from "@/components/link-menu-items"
import { type ModelSummary } from "../data/schema"
import { StageBadge, VersionStatusBadge } from "./detail/shared"

export function modelDetailHref(name: string): string {
  return `/dashboard/models/${encodeURIComponent(name)}`
}

/**
 * MLflow 모델 카드(TODO-118) — 데이터셋 · AI 에이전트 · MCP · API · OCI 모델 카드와 같은 틀(`EntityCard`).
 * 배지 줄: 운영 버전(없으면 글자) · 최신 버전 상태(준비됨이 아닐 때) · 최신 버전 번호. 설명 두 줄, 프레임워크 · Python 버전,
 * 바닥 줄에 소유자 · 크기 · 다운로드와 최근 변경.
 */
export function ModelCard({ model: m }: { model: ModelSummary }) {
  const href = modelDetailHref(m.name)
  // 목록 응답은 zod 파싱 없이 들어와 문자열일 수 있다
  const updated = String(m.updated_at)
  const runtime = [m.framework, m.python_version && `Python ${m.python_version}`].filter(Boolean).join(" · ")
  const size = m.model_size_bytes ? formatBytes(m.model_size_bytes) : null
  return (
    <EntityCard
      title={m.name}
      href={href}
      menuLabel={`${m.name} 메뉴`}
      menu={<LinkMenuItems href={href} />}
      footer={
        <>
          <span className="flex min-w-0 items-center gap-1 tabular-nums">
            {m.owner ? <span className="truncate">{m.owner}</span> : <span className="text-muted-foreground">소유자 없음</span>}
            {size && <span className="shrink-0">· {size}</span>}
            <span className="shrink-0">· 다운로드 {(m.download_count ?? 0).toLocaleString()}회</span>
          </span>
          <span className="ml-auto shrink-0" title={formatDateTime(updated)}>{timeAgo(updated)} 변경</span>
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        {m.production_version ? <StageBadge stage="PRODUCTION" version={m.production_version} /> : <span>운영 버전 없음</span>}
        {/* 등록 중 · 실패처럼 주의가 필요한 상태만 */}
        {m.latest_version_status && m.latest_version_status !== "READY" && <VersionStatusBadge status={m.latest_version_status} />}
        <span className="tabular-nums">최신 v{m.max_version_number}</span>
      </div>

      <EntityCardText text={m.description} />

      <div className="truncate text-xs">
        {runtime || <span >프레임워크 미상</span>}
      </div>
    </EntityCard>
  )
}

export function ModelsCardView({ data }: { data: ModelSummary[] }) {
  return (
    <EntityCardGrid>
      {data.map((m) => <ModelCard key={m.id} model={m} />)}
    </EntityCardGrid>
  )
}
