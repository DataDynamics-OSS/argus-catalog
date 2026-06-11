"use client"

import { useMemo } from "react"
import {
  ReactFlow, Background, BackgroundVariant, Controls, MarkerType, Position,
  type Node, type Edge,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"

import type { ApiLineage } from "../api"

const TARGET_TYPE_LABEL: Record<string, string> = { api: "API", dataset: "데이터셋", model: "모델", agent: "AI 에이전트", system: "시스템" }
const RELATION_LABEL: Record<string, string> = { provides: "제공", consumes: "소비", depends_on: "의존" }
const RELATION_COLOR: Record<string, string> = { provides: "#059669", consumes: "#0284c7", depends_on: "#d97706" }

const GAP = 96
const COL_X = { left: 20, center: 320, right: 640 }

function colY(i: number, len: number, centerY: number) {
  return centerY - ((len - 1) * GAP) / 2 + i * GAP
}

export function ApisLineageGraph({ apiName, lineage }: { apiName: string; lineage: ApiLineage[] }) {
  const { nodes, edges } = useMemo(() => {
    // 왼쪽(업스트림): 소비·의존 / 오른쪽(다운스트림): 제공
    const left = lineage.filter((e) => e.relation === "consumes" || e.relation === "depends_on")
    const right = lineage.filter((e) => e.relation === "provides")
    const centerY = (Math.max(left.length, right.length, 1) - 1) * GAP / 2

    const nodes: Node[] = []
    const edges: Edge[] = []

    nodes.push({
      id: "__self__",
      position: { x: COL_X.center, y: centerY },
      data: { label: <div className="text-xs font-semibold text-white">{apiName}</div> },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      style: { background: "#1e293b", border: "1px solid #0f172a", borderRadius: 8, padding: 10, width: 190 },
    })

    const targetNode = (e: ApiLineage, x: number, y: number): Node => ({
      id: String(e.id),
      position: { x, y },
      data: {
        label: (
          <div className="text-xs">
            <div className="font-medium text-foreground">{e.target_label || e.target_ref}</div>
            <div className="text-[10px] text-muted-foreground">{TARGET_TYPE_LABEL[e.target_type] ?? e.target_type}</div>
          </div>
        ),
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      style: { background: "#ffffff", border: "1px solid #cbd5e1", borderRadius: 8, padding: 8, width: 180 },
    })

    const mkEdge = (e: ApiLineage, source: string, target: string): Edge => ({
      id: `e-${e.id}`,
      source, target,
      label: RELATION_LABEL[e.relation] ?? e.relation,
      labelStyle: { fill: RELATION_COLOR[e.relation], fontSize: 11, fontWeight: 600 },
      labelBgStyle: { fill: "#ffffff" },
      style: { stroke: RELATION_COLOR[e.relation], strokeWidth: 1.5 },
      markerEnd: { type: MarkerType.ArrowClosed, color: RELATION_COLOR[e.relation] },
    })

    // 왼쪽: 대상 → 이 API (업스트림 유입)
    left.forEach((e, i) => {
      nodes.push(targetNode(e, COL_X.left, colY(i, left.length, centerY)))
      edges.push(mkEdge(e, String(e.id), "__self__"))
    })
    // 오른쪽: 이 API → 대상 (다운스트림 제공)
    right.forEach((e, i) => {
      nodes.push(targetNode(e, COL_X.right, colY(i, right.length, centerY)))
      edges.push(mkEdge(e, "__self__", String(e.id)))
    })

    return { nodes, edges }
  }, [apiName, lineage])

  if (lineage.length === 0) {
    return <p className="text-sm text-muted-foreground">등록된 리니지 관계가 없습니다. 표 보기의 “관계 추가”로 등록하세요.</p>
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1"><span className="inline-block h-2 w-4 rounded" style={{ background: RELATION_COLOR.consumes }} /> 소비</span>
        <span className="inline-flex items-center gap-1"><span className="inline-block h-2 w-4 rounded" style={{ background: RELATION_COLOR.depends_on }} /> 의존</span>
        <span className="inline-flex items-center gap-1"><span className="inline-block h-2 w-4 rounded" style={{ background: RELATION_COLOR.provides }} /> 제공</span>
        <span className="ml-2">왼쪽=업스트림(소비·의존) · 가운데=이 API · 오른쪽=다운스트림(제공)</span>
      </div>
      <div className="h-[480px] rounded-md border bg-muted/10">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          nodesConnectable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  )
}
