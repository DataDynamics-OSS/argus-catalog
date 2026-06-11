"use client"

// Dataset 상세 페이지의 "리니지" 탭 — React Flow 기반 upstream/downstream 그래프를 그리고,
// 엣지 클릭 시 컬럼 매핑/JOIN key 상세 패널을 노출. LineageAddDialog 를 호스팅한다.

import { useEffect, useMemo, useRef, useState, useCallback } from "react"
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  type EdgeMouseHandler,
  Position,
  Handle,
  useNodesState,
  useEdgesState,
  MarkerType,
  BackgroundVariant,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Separator } from "@workspace/ui/components/separator"
import { Database, ArrowRight, X, Link2, Plus, Trash2, GitBranch } from "lucide-react"
import Link from "next/link"
import { authFetch } from "@/features/auth/auth-fetch"
import { LineageAddDialog } from "./lineage-add-dialog"

const BASE = "/api/v1/catalog"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type LineageNode = {
  id: number
  name: string
  urn: string
  datasourceType: string
  qualityStatus?: string | null
  datasourceName: string
  isCurrent: boolean
}

type LineageEdge = {
  source: number
  target: number
  sourceTable: string
  targetTable: string
  lineageSource?: string   // QUERY_AGGREGATED | MANUAL | PIPELINE
  lineageId?: number | null
  relationType?: string
  description?: string
}

type ColumnLineageItem = {
  sourceDatasetId: number
  targetDatasetId: number
  sourceColumn: string
  targetColumn: string
  transformType: string
  /** 어느 lineage_source 의 column mapping 인지. 다중 출처 edge 에서 source 별 그룹화에 사용. */
  lineageSource?: string
}

type LineageData = {
  datasetId: number
  nodes: LineageNode[]
  edges: LineageEdge[]
  columnLineage: ColumnLineageItem[]
}

/** 같은 (source, target) 쌍의 한 lineage_source 에 해당하는 한 row 메타 + 매핑. */
type EdgeSourceBucket = {
  lineageSource: string
  lineageId?: number | null
  relationType?: string
  description?: string
  columns: ColumnLineageItem[]
  joinKeys: ColumnLineageItem[]
}

type SelectedEdgeInfo = {
  sourceId: number
  targetId: number
  sourceName: string
  targetName: string
  /** 한 edge 가 여러 lineage_source 를 동시에 가질 수 있어 출처별 bucket 으로 보관. */
  buckets: EdgeSourceBucket[]
}

// ---------------------------------------------------------------------------
// Datasource color palette
// ---------------------------------------------------------------------------

const DATASOURCE_COLORS: Record<string, {
  border: string; bg: string; ring: string; icon: string; badge: string
}> = {
  PostgreSQL:  { border: "border-sky-500",     bg: "bg-sky-50 dark:bg-sky-950",         ring: "ring-sky-200",     icon: "text-sky-600",     badge: "bg-sky-100 text-sky-800 dark:bg-sky-900 dark:text-sky-200" },
  MySQL:       { border: "border-orange-500",  bg: "bg-orange-50 dark:bg-orange-950",   ring: "ring-orange-200",  icon: "text-orange-600",  badge: "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200" },
  MariaDB:     { border: "border-orange-500",  bg: "bg-orange-50 dark:bg-orange-950",   ring: "ring-orange-200",  icon: "text-orange-600",  badge: "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200" },
  Hive:        { border: "border-yellow-500",  bg: "bg-yellow-50 dark:bg-yellow-950",   ring: "ring-yellow-200",  icon: "text-yellow-600",  badge: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200" },
  Impala:      { border: "border-indigo-500",  bg: "bg-indigo-50 dark:bg-indigo-950",   ring: "ring-indigo-200",  icon: "text-indigo-600",  badge: "bg-indigo-100 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-200" },
  Trino:       { border: "border-pink-500",    bg: "bg-pink-50 dark:bg-pink-950",       ring: "ring-pink-200",    icon: "text-pink-600",    badge: "bg-pink-100 text-pink-800 dark:bg-pink-900 dark:text-pink-200" },
  StarRocks:   { border: "border-violet-500",  bg: "bg-violet-50 dark:bg-violet-950",   ring: "ring-violet-200",  icon: "text-violet-600",  badge: "bg-violet-100 text-violet-800 dark:bg-violet-900 dark:text-violet-200" },
  Kafka:       { border: "border-stone-600",   bg: "bg-stone-50 dark:bg-stone-950",     ring: "ring-stone-200",   icon: "text-stone-600",   badge: "bg-stone-100 text-stone-800 dark:bg-stone-900 dark:text-stone-200" },
  S3:          { border: "border-emerald-500", bg: "bg-emerald-50 dark:bg-emerald-950", ring: "ring-emerald-200", icon: "text-emerald-600", badge: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200" },
  Greenplum:   { border: "border-green-500",   bg: "bg-green-50 dark:bg-green-950",     ring: "ring-green-200",   icon: "text-green-600",   badge: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200" },
  Oracle:      { border: "border-red-500",     bg: "bg-red-50 dark:bg-red-950",         ring: "ring-red-200",     icon: "text-red-600",     badge: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200" },
  Kudu:        { border: "border-teal-500",    bg: "bg-teal-50 dark:bg-teal-950",       ring: "ring-teal-200",    icon: "text-teal-600",    badge: "bg-teal-100 text-teal-800 dark:bg-teal-900 dark:text-teal-200" },
  Spark:       { border: "border-amber-500",   bg: "bg-amber-50 dark:bg-amber-950",     ring: "ring-amber-200",   icon: "text-amber-600",   badge: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200" },
}

const DEFAULT_DATASOURCE_COLOR = {
  border: "border-gray-400", bg: "bg-gray-50 dark:bg-gray-950", ring: "ring-gray-200",
  icon: "text-gray-500", badge: "bg-gray-100 text-gray-800 dark:bg-gray-900 dark:text-gray-200",
}

export function getDatasourceColor(datasourceType: string) {
  return DATASOURCE_COLORS[datasourceType] ?? DEFAULT_DATASOURCE_COLOR
}

// ---------------------------------------------------------------------------
// Custom Node Component
// ---------------------------------------------------------------------------

function DatasetNode({ data }: { data: LineageNode }) {
  const pc = getDatasourceColor(data.datasourceType)

  // 현재(보고 있는) 데이터셋은 datasource 색이 아니라 테마 primary 로 강하게 구분한다.
  // 배경은 불투명(bg-card=흰색)으로 — 반투명 배경은 그래프 격자/엣지가 비쳐 보임.
  const emphasis = data.isCurrent
    ? "border-primary ring-2 ring-primary/40 bg-card shadow-md"
    : `${pc.border} bg-card shadow-sm`

  return (
    <div
      className={`relative px-4 py-3 rounded-lg border-2 min-w-[200px] ${emphasis}`}
    >
      {data.isCurrent && (
        <span className="absolute -top-2.5 right-2 rounded-full bg-primary px-2 py-0.5 text-[10px] font-semibold text-primary-foreground shadow">
          현재
        </span>
      )}
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground !w-2 !h-2" />

      <div className="flex items-center gap-2 mb-1">
        <Database className={`h-4 w-4 flex-shrink-0 ${pc.icon}`} />
        {/* 품질 상태 점 — 원천 품질 문제의 다운스트림 전파를 그래프에서 바로 인지 */}
        {data.qualityStatus && data.qualityStatus !== "GOOD" && (
          <span
            title={`품질 ${data.qualityStatus}`}
            className={`h-2.5 w-2.5 flex-shrink-0 rounded-full ${
              data.qualityStatus === "BAD" ? "bg-red-500" : "bg-amber-500"
            }`}
          />
        )}
        <Link
          href={`/dashboard/datasets/${data.id}`}
          className="text-sm font-semibold text-foreground hover:underline truncate"
        >
          {data.name}
        </Link>
      </div>
      <div className="flex items-center gap-1.5">
        <Badge className={`text-[10px] px-1.5 py-0 font-normal border-0 ${pc.badge}`}>
          {data.datasourceType}
        </Badge>
        <span className="text-xs text-muted-foreground truncate">
          {data.datasourceName}
        </span>
      </div>

      <Handle type="source" position={Position.Right} className="!bg-muted-foreground !w-2 !h-2" />
    </div>
  )
}

const nodeTypes = { dataset: DatasetNode }

// ---------------------------------------------------------------------------
// Transform type badge color
// ---------------------------------------------------------------------------

function transformBadge(type: string) {
  switch (type) {
    case "DIRECT":
      return <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-normal">{type}</Badge>
    case "AGGREGATION":
      return <Badge variant="secondary" className="text-[10px] px-1.5 py-0 font-normal bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200">{type}</Badge>
    case "JOIN_KEY":
      return <Badge variant="secondary" className="text-[10px] px-1.5 py-0 font-normal bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200">{type}</Badge>
    case "EXPRESSION":
      return <Badge variant="secondary" className="text-[10px] px-1.5 py-0 font-normal bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200">{type}</Badge>
    case "CAST":
      return <Badge variant="secondary" className="text-[10px] px-1.5 py-0 font-normal bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200">{type}</Badge>
    default:
      return <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-normal">{type}</Badge>
  }
}

// ---------------------------------------------------------------------------
// Lineage source badge
// ---------------------------------------------------------------------------

function lineageSourceBadge(source: string | undefined) {
  switch (source) {
    case "MANUAL":
      return (
        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 font-normal bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200">
          Manual
        </Badge>
      )
    case "PIPELINE":
      return (
        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 font-normal bg-indigo-100 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-200">
          Pipeline
        </Badge>
      )
    case "QUERY_AGGREGATED":
      return (
        <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-normal">
          Auto
        </Badge>
      )
    default:
      return null
  }
}

// ---------------------------------------------------------------------------
// Draggable + Resizable Lineage Detail Panel
// ---------------------------------------------------------------------------

function EdgeDetailPanel({
  info,
  onClose,
  onDelete,
}: {
  info: SelectedEdgeInfo
  onClose: () => void
  onDelete?: (lineageId: number) => void
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)
  const [size, setSize] = useState({ w: 420, h: 480 })
  const dragRef = useRef({ active: false, sx: 0, sy: 0, ox: 0, oy: 0 })
  const resizeRef = useRef({ active: false, sx: 0, sy: 0, ow: 0, oh: 0 })

  // 첫 마운트 시 부모 컨테이너의 너비를 측정해 우상단에 패널을 띄운다. pos 가 이미 잡혀 있으면
  // 사용자가 드래그해 옮긴 위치를 보존해야 하므로 재계산을 건너뛴다.
  useEffect(() => {
    if (pos) return
    const container = panelRef.current?.parentElement
    if (container) {
      containerRef.current = container as HTMLDivElement
      const rect = container.getBoundingClientRect()
      setPos({ left: rect.width - size.w - 16, top: 16 })
    } else {
      setPos({ left: 400, top: 16 })
    }
  }, [pos, size.w])

  // 드래그 시작 — mousedown 으로 시작점 캐싱 후 window 에 mousemove/mouseup 을 붙여
  // 패널 바깥까지 안전하게 추적. 종료 시 반드시 listener 를 제거.
  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    if (!pos) return
    dragRef.current = { active: true, sx: e.clientX, sy: e.clientY, ox: pos.left, oy: pos.top }
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current.active) return
      setPos({
        left: dragRef.current.ox + (ev.clientX - dragRef.current.sx),
        top: dragRef.current.oy + (ev.clientY - dragRef.current.sy),
      })
    }
    const onUp = () => {
      dragRef.current.active = false
      window.removeEventListener("mousemove", onMove)
      window.removeEventListener("mouseup", onUp)
    }
    window.addEventListener("mousemove", onMove)
    window.addEventListener("mouseup", onUp)
  }, [pos])

  // 리사이즈 핸들 — 드래그와 동일한 패턴이지만 최소 크기(320x200) 미만으로는 줄어들지 않는다.
  const onResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    resizeRef.current = { active: true, sx: e.clientX, sy: e.clientY, ow: size.w, oh: size.h }
    const onMove = (ev: MouseEvent) => {
      if (!resizeRef.current.active) return
      setSize({
        w: Math.max(320, resizeRef.current.ow + (ev.clientX - resizeRef.current.sx)),
        h: Math.max(200, resizeRef.current.oh + (ev.clientY - resizeRef.current.sy)),
      })
    }
    const onUp = () => {
      resizeRef.current.active = false
      window.removeEventListener("mousemove", onMove)
      window.removeEventListener("mouseup", onUp)
    }
    window.addEventListener("mousemove", onMove)
    window.addEventListener("mouseup", onUp)
  }, [size])

  if (!pos) return null

  return (
    <div
      ref={panelRef}
      className="absolute z-10"
      style={{ left: pos.left, top: pos.top, width: size.w, height: size.h }}
    >
      <Card className="h-full flex flex-col shadow-lg border-2">
        {/* Draggable header */}
        <CardHeader
          className="pb-3 cursor-move select-none flex-shrink-0"
          onMouseDown={onDragStart}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 flex-wrap">
              <CardTitle className="text-sm font-medium">리니지 상세</CardTitle>
              {info.buckets.map((b) => (
                <span key={b.lineageSource}>{lineageSourceBadge(b.lineageSource)}</span>
              ))}
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={onClose}
                onMouseDown={e => e.stopPropagation()}
                className="text-muted-foreground hover:text-foreground p-0.5"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="flex items-center gap-2 text-sm text-muted-foreground mt-1">
            <span className="font-medium text-foreground">{info.sourceName}</span>
            <ArrowRight className="h-3 w-3" />
            <span className="font-medium text-foreground">{info.targetName}</span>
          </div>
        </CardHeader>

        {/* Scrollable content — bucket(=lineage_source) 별로 섹션을 따로 렌더한다. */}
        <CardContent className="pt-0 space-y-4 overflow-auto flex-1 min-h-0">
          {info.buckets.length === 0 ? (
            <p className="text-sm text-muted-foreground">No lineage detail for this edge.</p>
          ) : (
            info.buckets.map((bucket, bi) => {
              const isManualBucket = bucket.lineageSource === "MANUAL" || bucket.lineageSource === "PIPELINE"
              const hasAnyDetail = bucket.joinKeys.length > 0 || bucket.columns.length > 0
              return (
                <div key={bucket.lineageSource} className="space-y-3">
                  {bi > 0 && <Separator />}
                  {/* bucket 헤더 — 출처 뱃지 + (MANUAL/PIPELINE 이면) 삭제 버튼 */}
                  <div className="flex items-center justify-between">
                    {lineageSourceBadge(bucket.lineageSource)}
                    {isManualBucket && bucket.lineageId && onDelete && (
                      <button
                        onClick={() => onDelete(bucket.lineageId!)}
                        onMouseDown={e => e.stopPropagation()}
                        className="text-muted-foreground hover:text-destructive p-0.5"
                        title="리니지 삭제"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>

                  {/* MANUAL/PIPELINE metadata */}
                  {isManualBucket && (
                    <div className="space-y-1.5 text-sm">
                      {bucket.relationType && (
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">관계</span>
                          <span className="font-medium">{bucket.relationType}</span>
                        </div>
                      )}
                      {bucket.description && (
                        <div>
                          <span className="text-muted-foreground">설명</span>
                          <p className="mt-0.5 text-foreground">{bucket.description}</p>
                        </div>
                      )}
                    </div>
                  )}

                  {/* JOIN Keys */}
                  {bucket.joinKeys.length > 0 && (
                    <div>
                      <div className="flex items-center gap-1.5 mb-2">
                        <Link2 className="h-3.5 w-3.5 text-purple-600" />
                        <span className="text-sm font-medium">JOIN 컬럼 ({bucket.joinKeys.length})</span>
                      </div>
                      <div className="space-y-1.5">
                        {bucket.joinKeys.map((jk, i) => (
                          <div
                            key={i}
                            className="flex items-center gap-2 text-sm bg-purple-50 dark:bg-purple-950 rounded px-2.5 py-1.5"
                          >
                            <code className="font-mono text-purple-700 dark:text-purple-300">
                              {jk.sourceColumn}
                            </code>
                            <span className="text-muted-foreground">=</span>
                            <code className="font-mono text-purple-700 dark:text-purple-300">
                              {jk.targetColumn}
                            </code>
                            {transformBadge("JOIN_KEY")}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Column Mappings */}
                  {bucket.columns.length > 0 && (
                    <div>
                      <div className="flex items-center gap-1.5 mb-2">
                        <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
                        <span className="text-sm font-medium">컬럼 매핑 ({bucket.columns.length})</span>
                      </div>
                      <div className="space-y-1">
                        {bucket.columns.map((cl, i) => (
                          <div
                            key={i}
                            className="flex items-center justify-between gap-2 text-sm bg-muted/50 rounded px-2.5 py-1.5"
                          >
                            <div className="flex items-center gap-1.5 min-w-0">
                              <code className="font-mono truncate text-foreground">
                                {cl.sourceColumn}
                              </code>
                              <ArrowRight className="h-3 w-3 flex-shrink-0 text-muted-foreground" />
                              <code className="font-mono truncate text-foreground">
                                {cl.targetColumn}
                              </code>
                            </div>
                            {transformBadge(cl.transformType)}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {!hasAnyDetail && !isManualBucket && (
                    <p className="text-sm text-muted-foreground">
                      No column-level detail available.
                    </p>
                  )}
                </div>
              )
            })
          )}
        </CardContent>

        {/* Resize handle */}
        <div
          className="absolute bottom-0 right-0 w-4 h-4 cursor-se-resize"
          onMouseDown={onResizeStart}
        >
          <svg
            width="16" height="16" viewBox="0 0 16 16"
            className="text-muted-foreground/40 hover:text-muted-foreground"
          >
            <path d="M14 14L8 14L14 8Z" fill="currentColor" />
            <path d="M14 14L11 14L14 11Z" fill="currentColor" opacity="0.5" />
          </svg>
        </div>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Layout Helper - simple layered layout
// ---------------------------------------------------------------------------

function layoutNodes(
  lineageNodes: LineageNode[],
  lineageEdges: LineageEdge[],
  currentId: number,
): Node[] {
  const downstream = new Map<number, Set<number>>()
  const upstream = new Map<number, Set<number>>()

  for (const e of lineageEdges) {
    if (!downstream.has(e.source)) downstream.set(e.source, new Set())
    downstream.get(e.source)!.add(e.target)
    if (!upstream.has(e.target)) upstream.set(e.target, new Set())
    upstream.get(e.target)!.add(e.source)
  }

  const layers = new Map<number, number>()
  layers.set(currentId, 0)

  const upQueue = [currentId]
  const upVisited = new Set([currentId])
  while (upQueue.length > 0) {
    const node = upQueue.shift()!
    const parents = upstream.get(node)
    if (parents) {
      for (const p of parents) {
        if (!upVisited.has(p)) {
          upVisited.add(p)
          layers.set(p, (layers.get(node) ?? 0) - 1)
          upQueue.push(p)
        }
      }
    }
  }

  const downQueue = [currentId]
  const downVisited = new Set([currentId])
  while (downQueue.length > 0) {
    const node = downQueue.shift()!
    const children = downstream.get(node)
    if (children) {
      for (const c of children) {
        if (!downVisited.has(c)) {
          downVisited.add(c)
          layers.set(c, (layers.get(node) ?? 0) + 1)
          downQueue.push(c)
        }
      }
    }
  }

  for (const n of lineageNodes) {
    if (!layers.has(n.id)) layers.set(n.id, 0)
  }

  const layerGroups = new Map<number, LineageNode[]>()
  for (const n of lineageNodes) {
    const layer = layers.get(n.id) ?? 0
    if (!layerGroups.has(layer)) layerGroups.set(layer, [])
    layerGroups.get(layer)!.push(n)
  }

  const X_GAP = 300
  const Y_GAP = 100
  const sortedLayers = [...layerGroups.keys()].sort((a, b) => a - b)
  const minLayer = sortedLayers[0] ?? 0

  const nodes: Node[] = []
  for (const layer of sortedLayers) {
    const group = layerGroups.get(layer)!
    const x = (layer - minLayer) * X_GAP
    const startY = -((group.length - 1) * Y_GAP) / 2

    for (let i = 0; i < group.length; i++) {
      const n = group[i]!
      nodes.push({
        id: String(n.id),
        type: "dataset",
        position: { x, y: startY + i * Y_GAP },
        data: { ...n },
      })
    }
  }

  return nodes
}

// ---------------------------------------------------------------------------
// Edge style helpers
// ---------------------------------------------------------------------------

// lineage_source 별 색·선 스타일. 자동 수집(QUERY_AGGREGATED)은 점선, 사용자/파이프라인
// 등록은 실선으로 구분한다. (FK 는 데이터 흐름이 아닌 구조적 관계라 lineage 에서 제외됨.)
const LINEAGE_SOURCE_STYLES: Record<string, { color: string; dashed: boolean; label: string }> = {
  QUERY_AGGREGATED: { color: "#3b82f6", dashed: true,  label: "QUERY" },
  MANUAL:           { color: "#f59e0b", dashed: false, label: "수동" },
  PIPELINE:         { color: "#10b981", dashed: false, label: "PIPELINE" },
}
const LINEAGE_SOURCE_DEFAULT = { color: "#6b7280", dashed: true, label: "기타" }

function lineageSourceStyle(source: string | undefined) {
  return (source && LINEAGE_SOURCE_STYLES[source]) || LINEAGE_SOURCE_DEFAULT
}

function edgeStyle(lineageSource: string | undefined, isSelected: boolean) {
  const style = lineageSourceStyle(lineageSource)
  return {
    stroke: isSelected ? "#8b5cf6" : style.color,
    strokeWidth: isSelected ? 3 : 2,
    strokeDasharray: style.dashed ? "6 3" : undefined,
    cursor: "pointer" as const,
  }
}

function edgeMarkerColor(lineageSource: string | undefined, isSelected: boolean): string {
  if (isSelected) return "#8b5cf6"
  return lineageSourceStyle(lineageSource).color
}

/** 그래프 상단의 작은 범례 — 각 lineage_source 별 색·선 스타일을 안내. */
function LineageLegend() {
  return (
    <div className="flex flex-wrap items-center gap-3 px-2 py-1 text-xs">
      {/* 데이터 흐름(파생/변환)만 표시 — FK 등 구조적 관계는 "관계" 탭에서 본다. */}
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block h-0.5 w-6 bg-foreground/70" />
        <span className="text-muted-foreground font-medium">데이터 흐름</span>
      </span>
      <span className="text-muted-foreground/50">|</span>
      {Object.entries(LINEAGE_SOURCE_STYLES).map(([key, s]) => (
        <span key={key} className="inline-flex items-center gap-1.5">
          <span
            className="inline-block h-0.5 w-6 align-middle"
            style={{
              background: s.color,
              backgroundImage: s.dashed
                ? `repeating-linear-gradient(90deg, ${s.color} 0 6px, transparent 6px 9px)`
                : undefined,
              backgroundColor: s.dashed ? "transparent" : s.color,
            }}
          />
          <span className="text-muted-foreground">{s.label}</span>
        </span>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// LineageTab Component
// ---------------------------------------------------------------------------

export function LineageTab({
  datasetId,
  datasetName,
}: {
  datasetId: number
  datasetName: string
}) {
  const [lineageData, setLineageData] = useState<LineageData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<SelectedEdgeInfo | null>(null)
  const [addDialogOpen, setAddDialogOpen] = useState(false)

  const loadLineage = useCallback(async () => {
    try {
      setLoading(true)
      const resp = await authFetch(`${BASE}/datasets/${datasetId}/lineage`)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const data = await resp.json()
      setLineageData(data)
    } catch (e: unknown) {
      console.error("Failed to load lineage", { datasetId, err: e })
      setError(e instanceof Error ? e.message : "리니지를 불러오지 못했습니다")
    } finally {
      setLoading(false)
    }
  }, [datasetId])

  useEffect(() => {
    loadLineage()
  }, [loadLineage])

  // (sourceId, targetId) → 같은 쌍의 모든 lineage row (MANUAL / PIPELINE / QUERY 다중).
  // 한 쌍에 여러 row 가 양립할 수 있어 edge click 시 list 로 다룬다.
  const edgeMetaMap = useMemo(() => {
    const m = new Map<string, LineageEdge[]>()
    if (lineageData) {
      for (const e of lineageData.edges) {
        const key = `${e.source}-${e.target}`
        const arr = m.get(key)
        if (arr) arr.push(e)
        else m.set(key, [e])
      }
    }
    return m
  }, [lineageData])

  // 같은 (source, target, lineage_source) 의 column mapping 만 가져오는 helper.
  // API 가 lineageSource 를 columnLineage 각 row 에 달아주기 시작했으므로 이걸 활용해
  // source 별로 정확히 분리한다.
  const filterColumnsFor = (
    data: LineageData, srcId: number, tgtId: number, lineageSource: string,
  ): ColumnLineageItem[] => data.columnLineage.filter(
    cl => cl.sourceDatasetId === srcId
      && cl.targetDatasetId === tgtId
      && (cl.lineageSource ?? "") === lineageSource,
  )

  const nodeNameMap = useMemo(() => {
    const m = new Map<number, string>()
    if (lineageData) {
      for (const n of lineageData.nodes) m.set(n.id, n.name)
    }
    return m
  }, [lineageData])

  // 엣지 클릭 핸들러 — JOIN_KEY 와 일반 컬럼을 분리해 상세 패널에 별도 섹션으로 표시.
  // 같은 (source, target) 쌍에 여러 lineage_source 가 있으면 출처별 bucket 으로 묶어
  // EdgeDetailPanel 이 각각 별도 섹션으로 렌더한다.
  const onEdgeClick: EdgeMouseHandler = (event, edge) => {
    if (!lineageData) return
    const srcId = Number(edge.source)
    const tgtId = Number(edge.target)

    const metas = edgeMetaMap.get(`${srcId}-${tgtId}`) ?? []
    const buckets: EdgeSourceBucket[] = metas
      .map((m) => {
        const ls = m.lineageSource || "UNKNOWN"
        const allCols = filterColumnsFor(lineageData, srcId, tgtId, ls)
        return {
          lineageSource: ls,
          lineageId: m.lineageId,
          relationType: m.relationType,
          description: m.description,
          joinKeys: allCols.filter(cl => cl.transformType === "JOIN_KEY"),
          columns: allCols.filter(cl => cl.transformType !== "JOIN_KEY"),
        }
      })
      // 표시 priority: MANUAL > PIPELINE > QUERY_AGGREGATED > 기타.
      .sort((a, b) => {
        const order: Record<string, number> = {
          MANUAL: 0, PIPELINE: 1, QUERY_AGGREGATED: 2,
        }
        return (order[a.lineageSource] ?? 99) - (order[b.lineageSource] ?? 99)
      })

    setSelectedEdge({
      sourceId: srcId,
      targetId: tgtId,
      sourceName: nodeNameMap.get(srcId) ?? String(srcId),
      targetName: nodeNameMap.get(tgtId) ?? String(tgtId),
      buckets,
    })
  }

  const handleDeleteLineage = async (lineageId: number) => {
    try {
      const resp = await authFetch(`${BASE}/lineage/${lineageId}`, { method: "DELETE" })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      console.info("Lineage deleted", { lineageId })
      setSelectedEdge(null)
      loadLineage()
    } catch (err) {
      console.error("Failed to delete lineage", { lineageId, err })
    }
  }

  // React Flow 가 요구하는 node/edge 모델로 변환. layoutNodes 가 layered DAG 좌표를 계산하고
  // 중복 (source,target) 쌍은 seen 으로 1회만 그린다 — 동일 데이터셋 쌍에 다중 컬럼 매핑이 있어도
  // 그래프 상에서는 하나의 엣지로 합쳐 표현한다.
  const { flowNodes, flowEdges } = useMemo(() => {
    if (!lineageData || lineageData.nodes.length === 0) {
      return { flowNodes: [], flowEdges: [] }
    }

    const nodes = layoutNodes(lineageData.nodes, lineageData.edges, datasetId)

    // 같은 (source, target) 쌍의 lineage_source 들을 모은다. multi-source 면 priority 가
    // 가장 높은 source 의 색을, 라벨은 모든 source 를 "·" 로 연결해 표시.
    const PRIORITY: Record<string, number> = {
      MANUAL: 0, PIPELINE: 1, QUERY_AGGREGATED: 2,
    }
    type Grouped = { sources: string[]; primary: string }
    const grouped = new Map<string, Grouped>()
    for (const e of lineageData.edges) {
      const key = `${e.source}-${e.target}`
      const ls = e.lineageSource || "UNKNOWN"
      const g = grouped.get(key)
      if (g) {
        if (!g.sources.includes(ls)) g.sources.push(ls)
        if ((PRIORITY[ls] ?? 99) < (PRIORITY[g.primary] ?? 99)) g.primary = ls
      } else {
        grouped.set(key, { sources: [ls], primary: ls })
      }
    }

    const edges: Edge[] = []
    for (const e of lineageData.edges) {
      const key = `${e.source}-${e.target}`
      const g = grouped.get(key)
      if (!g) continue
      // primary 출처 행만 render 후보로 잡고 나머지는 skip — multi-source 정보는 g.sources 로 표시.
      if ((e.lineageSource || "UNKNOWN") !== g.primary) continue
      grouped.delete(key)  // 한 쌍당 한 번만 push

      const primaryLs = lineageSourceStyle(g.primary)
      const isManualPrimary = g.primary === "MANUAL" || g.primary === "PIPELINE"
      const labelColor = primaryLs.color
      // 라벨: lineage_source 표기 (다중이면 "·" 연결).
      const label = g.sources.map((s) => lineageSourceStyle(s).label).join(" · ")

      edges.push({
        id: `e-${e.source}-${e.target}`,
        source: String(e.source),
        target: String(e.target),
        animated: !isManualPrimary,
        style: edgeStyle(g.primary, false),
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: labelColor,
        },
        label,
        labelBgPadding: [4, 2],
        labelBgBorderRadius: 4,
        labelBgStyle: { fill: "white", stroke: labelColor, strokeWidth: 1 },
        labelStyle: { fill: labelColor, fontSize: 10, fontWeight: 600 },
        interactionWidth: 20,
        data: { lineageSource: g.primary, lineageSources: g.sources, relationType: e.relationType },
      })
    }

    return { flowNodes: nodes, flowEdges: edges }
  }, [lineageData, datasetId])

  const [nodes, setNodes, onNodesChange] = useNodesState(flowNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(flowEdges)

  // 선택된 엣지에만 보라색 강조 스타일을 다시 입혀 React Flow state 에 반영.
  // flowEdges (메모 결과) 와 selectedEdge 가 함께 의존성에 들어가야 두 입력의 교차 변화를 반영한다.
  useEffect(() => {
    if (!flowEdges.length) return

    const updated = flowEdges.map(e => {
      const srcId = Number(e.source)
      const tgtId = Number(e.target)
      const isSelected = selectedEdge
        && srcId === selectedEdge.sourceId
        && tgtId === selectedEdge.targetId

      const ls = e.data?.lineageSource as string | undefined

      return {
        ...e,
        style: edgeStyle(ls, !!isSelected),
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: edgeMarkerColor(ls, !!isSelected),
        },
      }
    })
    setEdges(updated)
  }, [selectedEdge, flowEdges, setEdges])

  useEffect(() => {
    setNodes(flowNodes)
  }, [flowNodes, setNodes])

  // -- Add Lineage button (always visible) --
  const addButton = (
    <Button
      variant="outline"
      size="sm"
      onClick={() => setAddDialogOpen(true)}
      className="gap-1.5"
    >
      <Plus className="h-3.5 w-3.5" />
      리니지 추가
    </Button>
  )

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <p className="text-sm text-muted-foreground">리니지를 불러오는 중...</p>
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

  if (!lineageData || lineageData.nodes.length <= 1) {
    return (
      <>
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12 gap-4">
            <GitBranch className="h-10 w-10 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              이 데이터셋에 사용 가능한 리니지 정보가 없습니다.
            </p>
            {addButton}
          </CardContent>
        </Card>
        <LineageAddDialog
          open={addDialogOpen}
          onOpenChange={setAddDialogOpen}
          datasetId={datasetId}
          datasetName={datasetName}
          onCreated={loadLineage}
        />
      </>
    )
  }

  return (
    <>
      <Card>
        <CardContent className="p-0">
          <div style={{ height: 600 }} className="relative">
            {/* Floating toolbar */}
            <div className="absolute top-3 left-3 z-10 flex items-center gap-2">
              {addButton}
            </div>

            {/* Floating legend — lineage_source 별 색·선 안내. */}
            <div className="absolute top-3 right-3 z-10 rounded border bg-background/90 backdrop-blur-sm">
              <LineageLegend />
            </div>

            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onEdgeClick={onEdgeClick}
              onPaneClick={() => setSelectedEdge(null)}
              nodeTypes={nodeTypes}
              defaultViewport={{ x: 50, y: 150, zoom: 1 }}
              minZoom={0.3}
              maxZoom={2}
              proOptions={{ hideAttribution: true }}
            >
              <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
              <Controls showInteractive={false} />
            </ReactFlow>

            {selectedEdge && (
              <EdgeDetailPanel
                info={selectedEdge}
                onClose={() => setSelectedEdge(null)}
                onDelete={handleDeleteLineage}
              />
            )}
          </div>

          <div className="px-4 py-2 border-t text-xs text-muted-foreground flex items-center gap-4">
            <span>엣지를 클릭하면 컬럼 매핑이 표시됩니다.</span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block w-5 border-t-2 border-gray-400" style={{ borderStyle: "dashed" }} />
              자동 (쿼리 기반)
            </span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block w-5 border-t-2 border-emerald-500" />
              수동 / 파이프라인
            </span>
          </div>
        </CardContent>
      </Card>

      <LineageAddDialog
        open={addDialogOpen}
        onOpenChange={setAddDialogOpen}
        datasetId={datasetId}
        datasetName={datasetName}
        onCreated={loadLineage}
      />
    </>
  )
}
