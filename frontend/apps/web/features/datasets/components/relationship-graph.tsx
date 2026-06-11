"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  ConnectionMode,
  EdgeLabelRenderer,
  Handle,
  Position,
  getBezierPath,
  useEdgesState,
  useInternalNode,
  useNodesState,
  type InternalNode,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { Database, Link2, Loader2, X } from "lucide-react"
import Link from "next/link"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"

import { authFetch } from "@/features/auth/auth-fetch"
import { getDatasourceColor } from "./lineage-tab"

const BASE = "/api/v1/catalog"

type GraphNode = {
  id: number
  name: string
  datasourceName: string
  datasourceType: string
  urn: string
  depth: number
  isFocus: boolean
}
type ColumnLink = {
  sourceDatasetId: number
  sourceField: string
  targetDatasetId: number
  targetField: string
  joinCount: number
  explicitCount: number
  implicitCount: number
}
type GraphEdge = {
  source: number
  target: number
  joinCount: number
  explicitCount: number
  implicitCount: number
  columns: ColumnLink[]
}
type GraphData = { nodes: GraphNode[]; edges: GraphEdge[] }

// ---------------------------------------------------------------------------
// Custom node — 데이터셋 카드 (focus 강조, datasource 색)
// ---------------------------------------------------------------------------

function RelNode({ data }: { data: GraphNode }) {
  const pc = getDatasourceColor(data.datasourceType)
  // 노드 비율은 리니지 탭(DatasetNode)과 동일하게 맞춘다.
  const emphasis = data.isFocus
    ? "border-primary ring-2 ring-primary/40 bg-card shadow-md"
    : `${pc.border} bg-card shadow-sm`
  return (
    <div className={`relative px-4 py-3 rounded-lg border-2 min-w-[200px] ${emphasis}`}>
      {data.isFocus && (
        <span className="absolute -top-2.5 right-2 rounded-full bg-primary px-2 py-0.5 text-[10px] font-semibold text-primary-foreground shadow">
          현재
        </span>
      )}
      {/* floating edge 용 hidden handles (연결 유효성 확보) */}
      <Handle type="source" position={Position.Top} className="!opacity-0" />
      <Handle type="target" position={Position.Top} className="!opacity-0" />
      <div className="flex items-center gap-2 mb-1">
        <Database className={`h-4 w-4 flex-shrink-0 ${pc.icon}`} />
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
    </div>
  )
}

const nodeTypes = { rel: RelNode }

// ---------------------------------------------------------------------------
// Floating bezier edge — 노드 경계를 잇는 곡선(@xyflow floating edge 패턴)
// ---------------------------------------------------------------------------

function nodeIntersection(node: InternalNode, other: InternalNode) {
  const w = (node.measured.width ?? 200) / 2
  const h = (node.measured.height ?? 64) / 2
  const x2 = node.internals.positionAbsolute.x + w
  const y2 = node.internals.positionAbsolute.y + h
  const x1 = other.internals.positionAbsolute.x + (other.measured.width ?? 200) / 2
  const y1 = other.internals.positionAbsolute.y + (other.measured.height ?? 64) / 2
  const xx1 = (x1 - x2) / (2 * w) - (y1 - y2) / (2 * h)
  const yy1 = (x1 - x2) / (2 * w) + (y1 - y2) / (2 * h)
  const a = 1 / (Math.abs(xx1) + Math.abs(yy1) || 1)
  const xx3 = a * xx1
  const yy3 = a * yy1
  return { x: w * (xx3 + yy3) + x2, y: h * (-xx3 + yy3) + y2 }
}

function edgeSide(node: InternalNode, p: { x: number; y: number }): Position {
  const nx = Math.round(node.internals.positionAbsolute.x)
  const ny = Math.round(node.internals.positionAbsolute.y)
  const w = node.measured.width ?? 200
  const h = node.measured.height ?? 64
  const px = Math.round(p.x)
  const py = Math.round(p.y)
  if (px <= nx + 1) return Position.Left
  if (px >= nx + w - 1) return Position.Right
  if (py <= ny + 1) return Position.Top
  if (py >= ny + h - 1) return Position.Bottom
  return Position.Top
}

function FloatingEdge({ id, source, target, style, selected, data }: {
  id: string; source: string; target: string
  style?: React.CSSProperties; selected?: boolean; data?: { label?: string }
}) {
  const s = useInternalNode(source)
  const t = useInternalNode(target)
  if (!s || !t) return null
  const sp = nodeIntersection(s, t)
  const tp = nodeIntersection(t, s)
  const [path, labelX, labelY] = getBezierPath({
    sourceX: sp.x, sourceY: sp.y, sourcePosition: edgeSide(s, sp),
    targetX: tp.x, targetY: tp.y, targetPosition: edgeSide(t, tp),
  })
  return (
    <>
      <path id={id} className="react-flow__edge-path" d={path} style={style} fill="none" />
      {data?.label && (
        <EdgeLabelRenderer>
          <div
            style={{ transform: `translate(-50%,-50%) translate(${labelX}px,${labelY}px)` }}
            className={`absolute rounded-full border px-1.5 py-0 text-[10px] font-semibold tabular-nums pointer-events-none ${
              selected ? "bg-primary text-primary-foreground border-primary" : "bg-background text-muted-foreground"
            }`}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

const edgeTypes = { floating: FloatingEdge }

// ---------------------------------------------------------------------------
// Radial layout — focus 중심, 1홉 안쪽 링, 2+홉은 부모(가장 강한 1홉 이웃) 근처로 클러스터
// ---------------------------------------------------------------------------

function radialLayout(nodes: GraphNode[], edges: GraphEdge[]) {
  const cx = 600, cy = 380
  const pos = new Map<number, { x: number; y: number }>()
  const d1 = nodes.filter(n => n.depth === 1)
  const dOuter = nodes.filter(n => n.depth >= 2)

  // 노드 수에 맞춰 반지름 — 1홉이 많으면 더 넓게(겹침 방지).
  const R1 = Math.max(280, (d1.length * 215) / (2 * Math.PI))
  const R2 = R1 + 320

  nodes.filter(n => n.depth === 0).forEach(n => pos.set(n.id, { x: cx, y: cy }))

  const angle1 = new Map<number, number>()
  d1.forEach((n, i) => {
    const a = d1.length === 1 ? -Math.PI / 2 : (2 * Math.PI * i) / d1.length - Math.PI / 2
    angle1.set(n.id, a)
    pos.set(n.id, { x: cx + R1 * Math.cos(a), y: cy + R1 * Math.sin(a) })
  })

  // 2+홉을 "가장 강하게 연결된 1홉 부모" 그룹으로 묶어 그 각도 근처에 배치.
  const groups = new Map<number | string, GraphNode[]>()
  dOuter.forEach(n => {
    let parent: number | null = null
    let best = -1
    edges.forEach(e => {
      const other = e.source === n.id ? e.target : e.target === n.id ? e.source : null
      if (other != null && angle1.has(other) && e.joinCount > best) {
        best = e.joinCount
        parent = other
      }
    })
    const key: number | string = parent ?? "__orphan"
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(n)
  })
  groups.forEach((kids, key) => {
    const base = key === "__orphan" ? -Math.PI / 2 : angle1.get(key as number) ?? -Math.PI / 2
    const spread = Math.min(0.9, 0.22 * kids.length)
    kids.forEach((n, i) => {
      const a = kids.length === 1 ? base : base - spread / 2 + (spread * i) / (kids.length - 1)
      pos.set(n.id, { x: cx + R2 * Math.cos(a), y: cy + R2 * Math.sin(a) })
    })
  })
  return pos
}

// 빈도(join_count) → 선 굵기/투명도 — confidence 가 높을수록 진하고 굵게.
function edgeWeight(joinCount: number) {
  return { width: Math.min(1.2 + joinCount * 0.5, 4.5), opacity: Math.min(0.4 + joinCount * 0.12, 0.95) }
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const DEPTHS = [1, 2, 3] as const

export function RelationshipGraph({ datasetId }: { datasetId: number }) {
  const [depth, setDepth] = useState(1)
  const [data, setData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<{ edge: GraphEdge; aName: string; bName: string } | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    authFetch(`${BASE}/datasets/${datasetId}/relationships/graph?depth=${depth}`)
      .then(r => (r.ok ? r.json() : { nodes: [], edges: [] }))
      .then(d => { if (alive) { setData(d); setLoading(false) } })
      .catch(() => { if (alive) { setData({ nodes: [], edges: [] }); setLoading(false) } })
    return () => { alive = false }
  }, [datasetId, depth])

  const nameOf = useMemo(() => {
    const m = new Map<number, string>()
    data?.nodes.forEach(n => m.set(n.id, n.name))
    return m
  }, [data])

  const { initialNodes, initialEdges } = useMemo(() => {
    if (!data) return { initialNodes: [], initialEdges: [] }
    const pos = radialLayout(data.nodes, data.edges)
    const fNodes = data.nodes.map(n => ({
      id: String(n.id),
      type: "rel",
      position: pos.get(n.id) ?? { x: 0, y: 0 },
      data: n,
    }))
    const fEdges = data.edges.map(e => {
      const w = edgeWeight(e.joinCount)
      return {
        id: `${e.source}-${e.target}`,
        source: String(e.source),
        target: String(e.target),
        type: "floating",
        data: { label: `${e.joinCount}`, edge: e },
        style: { stroke: "#6366f1", strokeWidth: w.width, opacity: w.opacity },
      }
    })
    return { initialNodes: fNodes, initialEdges: fEdges }
  }, [data])

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges)
  useEffect(() => { setNodes(initialNodes) }, [initialNodes, setNodes])
  useEffect(() => { setEdges(initialEdges) }, [initialEdges, setEdges])

  const onEdgeClick = useCallback((_: unknown, edge: { data?: { edge?: GraphEdge } }) => {
    const e = edge.data?.edge
    if (!e) return
    setSelected({ edge: e, aName: nameOf.get(e.source) ?? String(e.source), bName: nameOf.get(e.target) ?? String(e.target) })
    setEdges(es => es.map(x => ({ ...x, selected: x.id === `${e.source}-${e.target}` })))
  }, [nameOf, setEdges])

  const hopControl = (
    <div className="inline-flex items-center gap-0.5 rounded-md border bg-background/95 p-0.5 shadow-sm backdrop-blur-sm">
      {DEPTHS.map(d => (
        <Button
          key={d}
          variant={depth === d ? "default" : "ghost"}
          size="sm"
          className="h-6 px-2 text-xs"
          onClick={() => setDepth(d)}
        >
          {d}홉
        </Button>
      ))}
    </div>
  )

  if (loading && !data) {
    return <div className="flex h-[560px] items-center justify-center text-sm text-muted-foreground">관계 그래프 로딩 중…</div>
  }
  if (data && data.edges.length === 0) {
    return (
      <div className="relative" style={{ height: 560 }}>
        <div className="absolute left-3 top-3 z-10">{hopControl}</div>
        <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
          <Link2 className="h-8 w-8 opacity-40" />
          <p className="text-sm">이 데이터셋과 함께 조인된 쿼리가 없습니다.</p>
        </div>
      </div>
    )
  }

  return (
    <div style={{ height: 560 }} className="relative">
      {/* 홉 조절 컨트롤 */}
      <div className="absolute left-3 top-3 z-10 flex items-center gap-2">
        {hopControl}
        {loading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
      </div>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onEdgeClick={onEdgeClick}
        onPaneClick={() => { setSelected(null); setEdges(es => es.map(x => ({ ...x, selected: false }))) }}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        connectionMode={ConnectionMode.Loose}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.2}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>

      {selected && (
        <div className="absolute right-3 top-3 z-10 w-72 rounded-lg border bg-background/95 backdrop-blur-sm shadow-lg">
          <div className="flex items-center justify-between border-b px-3 py-2">
            <span className="text-sm font-semibold truncate">{selected.aName} ↔ {selected.bName}</span>
            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setSelected(null)}>
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
          <div className="max-h-72 space-y-2 overflow-auto p-3">
            <p className="text-xs text-muted-foreground">
              총 {selected.edge.joinCount}회 함께 조인 (명시 {selected.edge.explicitCount} · 암묵 {selected.edge.implicitCount})
            </p>
            {selected.edge.columns
              .slice()
              .sort((a, b) => b.joinCount - a.joinCount)
              .map((c, i) => (
                <div key={i} className="rounded border px-2 py-1.5 text-xs">
                  <div className="font-mono">
                    {nameOf.get(c.sourceDatasetId)?.split(".").pop()}.{c.sourceField}
                    <span className="mx-1 text-muted-foreground">↔</span>
                    {nameOf.get(c.targetDatasetId)?.split(".").pop()}.{c.targetField}
                  </div>
                  <div className="mt-0.5 text-[10px] text-muted-foreground tabular-nums">
                    {c.joinCount}회 · 명시 {c.explicitCount} · 암묵 {c.implicitCount}
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}

      <div className="absolute bottom-3 left-3 z-10 rounded border bg-background/90 px-2 py-1 text-[10px] text-muted-foreground backdrop-blur-sm">
        선 굵기 = 함께 조인된 빈도 · 엣지 클릭 시 컬럼 관계 표시
      </div>
    </div>
  )
}
