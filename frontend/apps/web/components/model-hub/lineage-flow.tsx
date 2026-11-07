"use client"

import { useMemo } from "react"
import { useRouter } from "next/navigation"
import {
  ReactFlow, Controls,
  type Node, type Edge, Position, Handle,
  MarkerType, useNodesState, useEdgesState,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"

/** 리니지 노드 종류 — 색·아이콘·라벨이 정해져 있다. */
export type FlowKind = "dataset" | "mlflow" | "oci" | "model" | "external"

export type FlowNode = {
  id: string
  label: string
  kind: FlowKind
  relation: string
  description?: string | null
  href?: string | null
}

const KIND_STYLE: Record<FlowKind, { bg: string; border: string; icon: string; label: string }> = {
  dataset: { bg: "bg-blue-50", border: "border-blue-300", icon: "📊", label: "데이터셋" },
  mlflow: { bg: "bg-violet-50", border: "border-violet-300", icon: "🧪", label: "MLflow 모델" },
  oci: { bg: "bg-amber-50", border: "border-amber-300", icon: "📦", label: "OCI 모델" },
  model: { bg: "bg-purple-50", border: "border-purple-300", icon: "🤖", label: "외부 모델" },
  external: { bg: "bg-green-50", border: "border-green-300", icon: "🌐", label: "외부 자원" },
}

type NodeData = { label: string; kind: FlowKind; isCurrent: boolean; description?: string | null; href?: string | null }

function FlowNodeView({ data }: { data: NodeData }) {
  const s = KIND_STYLE[data.kind] ?? KIND_STYLE.external
  return (
    <div
      className={`min-w-[170px] max-w-[240px] rounded-lg border-2 px-4 py-3 text-center shadow-sm ${
        data.isCurrent ? "border-primary bg-primary/5 ring-2 ring-primary/20" : `${s.border} ${s.bg}`
      } ${data.href ? "cursor-pointer hover:shadow-md" : ""}`}
      title={data.href ? "눌러서 열기" : undefined}
    >
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !bg-muted-foreground" />
      <div className="mb-1 text-xs text-muted-foreground">{data.isCurrent ? "현재 모델" : `${s.icon} ${s.label}`}</div>
      <div className="break-all text-sm font-medium">{data.label}</div>
      {data.description && <div className="mt-1 text-xs text-muted-foreground">{data.description}</div>}
      {data.href && <div className="mt-1 text-[11px] text-primary">열기 ↗</div>}
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !bg-muted-foreground" />
    </div>
  )
}

const nodeTypes = { flow: FlowNodeView }

function build(center: { label: string; kind: FlowKind }, inputs: FlowNode[], outputs: FlowNode[]) {
  const nodes: Node[] = []
  const edges: Edge[] = []
  const spacing = 120
  const centerY = Math.max(inputs.length, outputs.length, 1) * 55
  nodes.push({
    id: "__current", type: "flow", position: { x: 380, y: centerY },
    data: { label: center.label, kind: center.kind, isCurrent: true },
  })
  const place = (list: FlowNode[], x: number, side: "in" | "out") => {
    const startY = centerY - ((list.length - 1) * spacing) / 2
    list.forEach((n, i) => {
      const id = `${side}-${n.id}`
      const color = side === "in" ? "#6366f1" : "#10b981"
      nodes.push({
        id, type: "flow", position: { x, y: startY + i * spacing },
        data: { label: n.label, kind: n.kind, isCurrent: false, description: n.description, href: n.href },
      })
      edges.push({
        id: `e-${id}`,
        source: side === "in" ? id : "__current",
        target: side === "in" ? "__current" : id,
        label: n.relation,
        animated: true,
        style: { stroke: color }, labelStyle: { fontSize: 11, fill: color },
        markerEnd: { type: MarkerType.ArrowClosed, color },
      })
    })
  }
  place(inputs, 20, "in")
  place(outputs, 740, "out")
  return { nodes, edges }
}

/** 입력(왼쪽) → 현재 모델 → 출력(오른쪽) 리니지 그래프. 링크가 있는 노드는 눌러서 이동한다. */
export function LineageFlow({
  center, inputs, outputs, height = 380,
}: {
  center: { label: string; kind: FlowKind }
  inputs: FlowNode[]
  outputs: FlowNode[]
  height?: number
}) {
  const router = useRouter()
  const init = useMemo(() => build(center, inputs, outputs), [center, inputs, outputs])
  const [nodes, , onNodesChange] = useNodesState(init.nodes)
  const [edges, , onEdgesChange] = useEdgesState(init.edges)
  return (
    <div className="rounded-lg border" style={{ height }}>
      <ReactFlow
        nodes={nodes} edges={edges}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
        onNodeClick={(_, n) => { const h = (n.data as NodeData).href; if (h) router.push(h) }}
        proOptions={{ hideAttribution: true }}
      >
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}
