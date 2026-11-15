"use client"

/**
 * AI 어시스턴트 도구 결과의 미니 그래프 — 리니지(상류 → 대상 → 하류)와 ER 관계 (TODO-065).
 *
 * 채팅 패널 안에 들어가므로 작게(높이 220px) 그리고, 휠은 대화 스크롤에 양보한다(확대는 버튼·핀치).
 * 노드를 누르면 데이터셋 상세로 이동한다 — Next 라우터 이동이라 패널·대화가 유지된다.
 */

import { useMemo, type CSSProperties } from "react"
import { useRouter } from "next/navigation"
import {
  ReactFlow, Controls, MarkerType, Position,
  type Edge, type Node, type NodeMouseHandler,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"

const datasetHref = (id: number | string) => `/dashboard/datasets/${id}`

export type LineageResult = {
  dataset?: { id?: number; name?: string }
  upstream?: { id?: number; name?: string; relation?: string }[]
  downstream?: { id?: number; name?: string; relation?: string }[]
  quality_warnings?: { name?: string; status?: string }[]
}

export type ErdResult = {
  tables?: { dataset_id?: number; name?: string }[]
  relations?: { from_table?: string; from_columns?: unknown; to_table?: string; to_columns?: unknown; cardinality?: string }[]
}

const NODE_W = 150
const GAP_Y = 56
const BASE_STYLE = { borderRadius: 8, padding: 6, width: NODE_W, fontSize: 11 }
const NORMAL = { ...BASE_STYLE, background: "#ffffff", border: "1px solid #cbd5e1" }
const FOCUS = { ...BASE_STYLE, background: "#1e293b", border: "1px solid #0f172a", color: "#ffffff", fontWeight: 600 }
const WARN = { ...BASE_STYLE, background: "#fffbeb", border: "1px solid #f59e0b" }

function colY(i: number, len: number) {
  return (i - (len - 1) / 2) * GAP_Y
}

function useOpenDataset(): NodeMouseHandler {
  const router = useRouter()
  return (_e, node) => {
    const id = (node.data as { datasetId?: number }).datasetId
    if (id != null) router.push(datasetHref(id))
  }
}

function MiniFlow({ nodes, edges, label }: { nodes: Node[]; edges: Edge[]; label: string }) {
  const onNodeClick = useOpenDataset()
  return (
    <div className="h-[220px] w-full overflow-hidden rounded-md border border-border bg-background" role="img" aria-label={label}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodeClick={onNodeClick}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        nodesDraggable={false}
        nodesConnectable={false}
        zoomOnScroll={false}
        preventScrolling={false}
        minZoom={0.3}
        proOptions={{ hideAttribution: true }}
      >
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  )
}

const nodeLabel = (name: string, clickable: boolean) => (
  <div className={`truncate ${clickable ? "cursor-pointer" : ""}`} title={name}>{name}</div>
)

export function LineageMiniGraph({ result }: { result: LineageResult }) {
  const { nodes, edges } = useMemo(() => {
    const warn = new Set((result.quality_warnings ?? []).map((w) => w.name))
    const up = result.upstream ?? []
    const down = result.downstream ?? []
    const self = result.dataset
    const nodes: Node[] = [{
      id: "self",
      position: { x: 200, y: 0 },
      data: { label: nodeLabel(self?.name || "대상", self?.id != null), datasetId: self?.id },
      sourcePosition: Position.Right, targetPosition: Position.Left,
      style: FOCUS,
    }]
    const edges: Edge[] = []
    const add = (side: "up" | "down", d: { id?: number; name?: string; relation?: string }, i: number, len: number) => {
      const nid = `${side}-${i}`
      const name = d.name || "(이름 없음)"
      nodes.push({
        id: nid,
        position: { x: side === "up" ? 0 : 400, y: colY(i, len) },
        data: { label: nodeLabel(name, d.id != null), datasetId: d.id },
        sourcePosition: Position.Right, targetPosition: Position.Left,
        style: warn.has(d.name) ? WARN : NORMAL,
      })
      edges.push({
        id: `e-${nid}`,
        source: side === "up" ? nid : "self",
        target: side === "up" ? "self" : nid,
        label: d.relation || undefined,
        labelStyle: { fontSize: 10, fill: "#475569" },
        labelBgStyle: { fill: "#ffffff" },
        style: { stroke: "#64748b", strokeWidth: 1.2 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#64748b" },
      })
    }
    up.forEach((d, i) => add("up", d, i, up.length))
    down.forEach((d, i) => add("down", d, i, down.length))
    return { nodes, edges }
  }, [result])
  return <MiniFlow nodes={nodes} edges={edges} label={`${result.dataset?.name ?? "데이터셋"} 리니지: 상류 ${result.upstream?.length ?? 0}개, 하류 ${result.downstream?.length ?? 0}개`} />
}

const cols = (v: unknown) => (Array.isArray(v) ? v.join(", ") : typeof v === "string" ? v : "")

export function ErdMiniGraph({ result, focusId }: { result: ErdResult; focusId: number | null }) {
  const { nodes, edges } = useMemo(() => {
    const tables = (result.tables ?? []).filter((t) => t.name)
    const focus = tables.find((t) => t.dataset_id === focusId) ?? tables[0]
    const others = tables.filter((t) => t !== focus)
    const byName = new Map<string, string>()
    const nodes: Node[] = []
    // 대상 테이블을 가운데, 나머지는 원으로 둘러 배치
    const place = (t: { dataset_id?: number; name?: string }, x: number, y: number, style: CSSProperties) => {
      const nid = `t-${nodes.length}`
      byName.set(t.name!, nid)
      nodes.push({
        id: nid, position: { x, y },
        data: { label: nodeLabel(t.name!, t.dataset_id != null), datasetId: t.dataset_id },
        sourcePosition: Position.Right, targetPosition: Position.Left, style,
      })
    }
    if (focus) place(focus, 0, 0, FOCUS)
    const radius = Math.max(170, others.length * 32)
    others.forEach((t, i) => {
      const a = (2 * Math.PI * i) / Math.max(others.length, 1) - Math.PI / 2
      place(t, Math.cos(a) * radius, Math.sin(a) * radius * 0.6, NORMAL)
    })
    const edges: Edge[] = []
    ;(result.relations ?? []).forEach((r, i) => {
      const s = r.from_table && byName.get(r.from_table)
      const t = r.to_table && byName.get(r.to_table)
      if (!s || !t) return
      const label = [cols(r.from_columns), cols(r.to_columns)].filter(Boolean).join(" → ")
      edges.push({
        id: `r-${i}`, source: s, target: t, label: label || r.cardinality || undefined,
        labelStyle: { fontSize: 10, fill: "#475569" }, labelBgStyle: { fill: "#ffffff" },
        style: { stroke: "#0284c7", strokeWidth: 1.2 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#0284c7" },
      })
    })
    return { nodes, edges }
  }, [result, focusId])
  return <MiniFlow nodes={nodes} edges={edges} label={`ER 관계: 테이블 ${result.tables?.length ?? 0}개, 관계 ${result.relations?.length ?? 0}개`} />
}
