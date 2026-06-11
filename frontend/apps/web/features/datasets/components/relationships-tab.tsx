"use client"

// Dataset 상세 페이지의 "관계"(Relationships) 탭 — 데이터 흐름 lineage 가 아니라
// "사용 기반 컬럼 관계" 를 보여준다. 수집된 실제 쿼리의 JOIN 키를 분석해 "어떤 컬럼이
// 어떤 컬럼과 자주 함께 조인되는가" 를 빈도(joinCount)로 집계한 결과다.
// 상단: 함께 쓰인 데이터셋(테이블 단위), 본문: 컬럼 단위 JOIN 관계 표.

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Badge } from "@workspace/ui/components/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { Database, ArrowRight, Link2, Network } from "lucide-react"
import { authFetch } from "@/features/auth/auth-fetch"
import { RelationshipGraph } from "./relationship-graph"

const BASE = "/api/v1/catalog"

// ---------------------------------------------------------------------------
// Types — API 응답 형태 (GET /datasets/{id}/relationships)
// ---------------------------------------------------------------------------

type TableRelationship = {
  datasetId: number
  name: string
  datasourceName: string
  datasourceType: string
  urn: string
  joinCount: number
}

type ColumnRelationship = {
  field: string
  relatedDatasetId: number
  relatedDatasetName: string
  relatedDatasourceName: string
  relatedDatasourceType: string
  relatedUrn: string
  relatedField: string
  relationType: string
  joinCount: number
  explicitCount: number
  implicitCount: number
  lastSeen: string
}

type RelationshipsData = {
  tableRelationships: TableRelationship[]
  columnRelationships: ColumnRelationship[]
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** lastSeen ISO 문자열을 날짜만(YYYY-MM-DD) 으로. 파싱 실패 시 원문 유지. */
function formatDateOnly(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 관계 유형 뱃지 — JOIN_KEY 는 lineage-tab 의 transformBadge 와 톤을 맞춘다. */
function relationTypeBadge(type: string) {
  if (type === "JOIN_KEY") {
    return (
      <Badge
        variant="secondary"
        className="text-[10px] px-1.5 py-0 font-normal bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200"
      >
        {type}
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-normal">
      {type}
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// RelationshipsTab Component
// ---------------------------------------------------------------------------

export function RelationshipsTab({ datasetId }: { datasetId: number }) {
  const [data, setData] = useState<RelationshipsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadRelationships = useCallback(async () => {
    try {
      setLoading(true)
      const resp = await authFetch(`${BASE}/datasets/${datasetId}/relationships`)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const json = await resp.json()
      setData(json)
    } catch (e: unknown) {
      console.error("Failed to load relationships", { datasetId, err: e })
      setError(e instanceof Error ? e.message : "관계 정보를 불러오지 못했습니다")
    } finally {
      setLoading(false)
    }
  }, [datasetId])

  useEffect(() => {
    loadRelationships()
  }, [loadRelationships])

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <p className="text-sm text-muted-foreground">관계 정보를 불러오는 중...</p>
        </CardContent>
      </Card>
    )
  }

  if (error) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <p className="text-sm text-destructive">오류: {error}</p>
        </CardContent>
      </Card>
    )
  }

  const tableRels = data?.tableRelationships ?? []
  const columnRels = data?.columnRelationships ?? []

  // 빈 상태 — 수집된 쿼리에서 발견된 JOIN 패턴이 전혀 없을 때.
  if (tableRels.length === 0 && columnRels.length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center justify-center py-12 gap-4">
          <Network className="h-10 w-10 text-muted-foreground/50" />
          <div className="text-center space-y-1">
            <p className="text-sm text-muted-foreground">
              수집된 쿼리에서 발견된 관계가 없습니다.
            </p>
            <p className="text-xs text-muted-foreground/80">
              쿼리 수집이 활성화되면 JOIN 패턴으로 자동 발견됩니다.
            </p>
          </div>
        </CardContent>
      </Card>
    )
  }

  // 빈도순 정렬 — joinCount 내림차순. API 가 이미 정렬해줘도 화면에서 한 번 더 보장한다.
  const sortedTableRels = [...tableRels].sort((a, b) => b.joinCount - a.joinCount)
  const sortedColumnRels = [...columnRels].sort((a, b) => b.joinCount - a.joinCount)

  return (
    <div className="space-y-4">
      {/* 관계 그래프 — 리니지처럼 무방향 네트워크로 시각화 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Network className="h-4 w-4 text-muted-foreground" />
            관계 그래프
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            이 데이터셋을 중심으로 함께 조인되는 데이터셋의 관계망(2홉)입니다.
          </p>
        </CardHeader>
        <CardContent className="p-0">
          <RelationshipGraph datasetId={datasetId} />
        </CardContent>
      </Card>

      {/* 함께 쓰인 데이터셋 (테이블 단위 빈도) */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Database className="h-4 w-4 text-muted-foreground" />
            함께 쓰인 데이터셋
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            실제 쿼리에서 이 데이터셋과 자주 함께 조인된 데이터셋입니다.
          </p>
        </CardHeader>
        <CardContent>
          {sortedTableRels.length === 0 ? (
            <p className="text-sm text-muted-foreground">함께 쓰인 데이터셋이 없습니다.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {sortedTableRels.map((t) => (
                <Link
                  key={t.datasetId}
                  href={`/dashboard/datasets/${t.datasetId}`}
                  className="group inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 transition-colors hover:border-primary hover:bg-muted"
                >
                  <Database className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
                  <span className="flex flex-col min-w-0">
                    <span className="text-sm font-medium text-foreground group-hover:underline truncate">
                      {t.name}
                    </span>
                    <span className="text-xs text-muted-foreground truncate">
                      {t.datasourceName}
                    </span>
                  </span>
                  <Badge
                    variant="secondary"
                    className="ml-1 flex-shrink-0 bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200"
                  >
                    {t.joinCount}회
                  </Badge>
                </Link>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 본문: 컬럼 관계 (컬럼 단위 JOIN 빈도) */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Link2 className="h-4 w-4 text-muted-foreground" />
            컬럼 관계
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            이 데이터셋의 컬럼이 다른 데이터셋의 컬럼과 JOIN 키로 함께 쓰인 빈도입니다.
          </p>
        </CardHeader>
        <CardContent className="px-0">
          {sortedColumnRels.length === 0 ? (
            <p className="px-6 text-sm text-muted-foreground">컬럼 관계가 없습니다.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>내 컬럼</TableHead>
                  <TableHead>관련 컬럼</TableHead>
                  <TableHead>유형</TableHead>
                  <TableHead className="text-right">빈도</TableHead>
                  <TableHead className="text-right">마지막 발견</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedColumnRels.map((c, i) => (
                  <TableRow key={`${c.field}-${c.relatedDatasetId}-${c.relatedField}-${i}`}>
                    <TableCell>
                      <code className="font-mono text-sm text-foreground">{c.field}</code>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5 min-w-0">
                        <Link
                          href={`/dashboard/datasets/${c.relatedDatasetId}`}
                          className="text-sm font-medium text-foreground hover:underline truncate"
                        >
                          {c.relatedDatasetName}
                        </Link>
                        <span className="text-muted-foreground">.</span>
                        <code className="font-mono text-sm text-foreground truncate">
                          {c.relatedField}
                        </code>
                        <ArrowRight className="hidden h-3 w-3 flex-shrink-0 text-muted-foreground" />
                      </div>
                    </TableCell>
                    <TableCell>{relationTypeBadge(c.relationType)}</TableCell>
                    <TableCell className="text-right">
                      <span className="text-sm tabular-nums">{c.joinCount}회</span>
                      {(c.explicitCount > 0 || c.implicitCount > 0) && (
                        <span className="block text-xs text-muted-foreground tabular-nums">
                          명시 {c.explicitCount} · 암묵 {c.implicitCount}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right text-sm text-muted-foreground tabular-nums">
                      {formatDateOnly(c.lastSeen)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
