"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { ReactFlow, Background, Controls, MarkerType, Position, Handle, useEdgesState, useNodesState, type Edge, type Node } from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { Copy, Key, Link2, Table2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import { authFetch } from "@/features/auth/auth-fetch"

// ---------------------------------------------------------------------------
// Types — GET /catalog/datasets/{id}/erd 응답
// ---------------------------------------------------------------------------

type ErdColumn = {
  name: string; type: string; nullable: boolean
  is_pk: boolean; is_unique: boolean; fk_to: string | null
}
type ErdTable = {
  dataset_id: number; name: string; full_name: string
  quality_status: string | null; is_center: boolean; columns: ErdColumn[]
}
type ErdRelation = {
  source_dataset_id: number; source_columns: string[]
  target_dataset_id: number; target_columns: string[]
  constraint_name: string | null; cardinality: string; origin: string
}
type ErdData = { tables: ErdTable[]; relations: ErdRelation[] }

const MAX_VISIBLE_COLUMNS = 10

// ---------------------------------------------------------------------------
// 테이블 노드 — PK 🔑 / FK 🔗 / nullable ? 표기, 컬럼 많으면 접기
// ---------------------------------------------------------------------------

function TableNode({ data }: { data: ErdTable & { expanded: boolean; onToggle: () => void } }) {
  // PK → FK → 일반 순으로 정렬해 키 컬럼이 항상 위에 보이도록
  const sorted = [...data.columns].sort((a, b) => {
    const rank = (c: ErdColumn) => (c.is_pk ? 0 : c.fk_to ? 1 : 2)
    return rank(a) - rank(b)
  })
  const visible = data.expanded ? sorted : sorted.slice(0, MAX_VISIBLE_COLUMNS)
  const hidden = sorted.length - visible.length

  return (
    <div
      className={`min-w-[220px] rounded-lg border-2 bg-card text-sm shadow-sm ${
        data.is_center ? "border-primary ring-2 ring-primary/40 shadow-md" : "border-border"
      }`}
    >
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground !w-2 !h-2" />
      {/* 헤더: 테이블명 + 품질 상태 */}
      <div className="flex items-center gap-2 rounded-t-md border-b bg-muted/60 px-3 py-2">
        <Table2 className="h-4 w-4 text-muted-foreground" />
        <Link href={`/dashboard/datasets/${data.dataset_id}`} className="font-semibold hover:underline">
          {data.name}
        </Link>
        {data.is_center && (
          <span className="rounded-full bg-primary px-1.5 py-0.5 text-[10px] font-semibold text-primary-foreground">현재</span>
        )}
        {data.quality_status && data.quality_status !== "GOOD" && (
          <span className={`ml-auto h-2.5 w-2.5 rounded-full ${
            data.quality_status === "BAD" ? "bg-red-500" : "bg-amber-500"
          }`} title={`품질 ${data.quality_status}`} />
        )}
      </div>
      {/* 컬럼 목록 */}
      <div className="px-1 py-1">
        {visible.map((c) => (
          <div key={c.name} className="flex items-center gap-1.5 rounded px-2 py-0.5 font-mono text-xs hover:bg-muted/40">
            <span className="w-3.5 flex-shrink-0">
              {c.is_pk ? <Key className="h-3 w-3 text-amber-600" /> :
               c.fk_to ? <Link2 className="h-3 w-3 text-sky-600" /> : null}
            </span>
            <span className={c.is_pk ? "font-semibold" : ""}>{c.name}</span>
            {c.nullable && <span className="text-muted-foreground">?</span>}
            <span className="ml-auto pl-3 text-muted-foreground">{c.type}</span>
          </div>
        ))}
        {hidden > 0 && (
          <button type="button" onClick={data.onToggle}
            className="w-full rounded px-2 py-0.5 text-left text-xs text-primary hover:underline">
            … {hidden}개 더보기
          </button>
        )}
        {data.expanded && sorted.length > MAX_VISIBLE_COLUMNS && (
          <button type="button" onClick={data.onToggle}
            className="w-full rounded px-2 py-0.5 text-left text-xs text-muted-foreground hover:underline">
            접기
          </button>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-muted-foreground !w-2 !h-2" />
    </div>
  )
}

const nodeTypes = { erdTable: TableNode }

// ---------------------------------------------------------------------------
// 레이아웃 — 자식(이 테이블을 참조) 왼쪽 / 중심 가운데 / 부모(참조 대상) 오른쪽
// ---------------------------------------------------------------------------

/** 부모 방향 레벨링 — level(t) = 1 + max(level(참조하는 부모들)), 사이클은 방문 중 차단. */
function levelByParents(data: ErdData): Map<number, number> {
  const parents = new Map<number, number[]>()
  for (const r of data.relations) {
    if (r.source_dataset_id === r.target_dataset_id) continue
    const list = parents.get(r.source_dataset_id) ?? []
    list.push(r.target_dataset_id)
    parents.set(r.source_dataset_id, list)
  }
  const levels = new Map<number, number>()
  const visiting = new Set<number>()
  const visit = (id: number): number => {
    const cached = levels.get(id)
    if (cached != null) return cached
    if (visiting.has(id)) return 0 // 사이클(staff↔store) 보호
    visiting.add(id)
    const ps = parents.get(id) ?? []
    const lv = ps.length === 0 ? 0 : 1 + Math.max(...ps.map(visit))
    visiting.delete(id)
    levels.set(id, lv)
    return lv
  }
  for (const t of data.tables) visit(t.dataset_id)
  return levels
}

function layoutErd(data: ErdData, expandedIds: Set<number>): { nodes: Node[]; edges: Edge[] } {
  const centerId = data.tables.find((t) => t.is_center)?.dataset_id
  const inIds: number[] = []   // 중심을 참조하는 자식들 (FK 소유)
  const outIds: number[] = []  // 중심이 참조하는 부모들
  for (const r of data.relations) {
    if (r.target_dataset_id === centerId && r.source_dataset_id !== centerId
        && !inIds.includes(r.source_dataset_id)) inIds.push(r.source_dataset_id)
    if (r.source_dataset_id === centerId && r.target_dataset_id !== centerId
        && !outIds.includes(r.target_dataset_id)) outIds.push(r.target_dataset_id)
  }
  // 양방향(상호 참조)이면 왼쪽(자식) 우선 배치
  const outOnly = outIds.filter((id) => !inIds.includes(id))

  const nodeHeight = (t: ErdTable) =>
    64 + Math.min(t.columns.length, expandedIds.has(t.dataset_id) ? t.columns.length : MAX_VISIBLE_COLUMNS) * 22

  const colX: Record<string, number> = { left: 0, center: 380, right: 760 }
  const yCursor: Record<string, number> = { left: 0, center: 0, right: 0 }
  const nodes: Node[] = []

  const place = (t: ErdTable, col: string) => {
    nodes.push({
      id: String(t.dataset_id),
      type: "erdTable",
      position: { x: colX[col]!, y: yCursor[col]! },
      data: t as unknown as Record<string, unknown>,
      draggable: true,
    })
    yCursor[col]! += nodeHeight(t) + 32
  }

  const byId = new Map(data.tables.map((t) => [t.dataset_id, t]))
  if (centerId != null) {
    // 데이터셋 중심 모드: 자식 왼쪽 / 중심 / 부모 오른쪽
    for (const id of inIds) { const t = byId.get(id); if (t) place(t, "left") }
    const center = byId.get(centerId)
    if (center) place(center, "center")
    for (const id of outOnly) { const t = byId.get(id); if (t) place(t, "right") }
  } else {
    // 데이터 소스 전체 모드: 부모 방향 레벨링 — 자식(깊은 레벨) 왼쪽, 루트 부모 오른쪽
    const levels = levelByParents(data)
    const maxLv = Math.max(0, ...levels.values())
    const yByLv = new Map<number, number>()
    // 레벨 안에서는 컬럼 수가 적은(코드성) 테이블이 위로 오도록 정렬해 균형 잡기
    const sorted = [...data.tables].sort((a, b) => {
      const lv = (levels.get(b.dataset_id) ?? 0) - (levels.get(a.dataset_id) ?? 0)
      return lv !== 0 ? lv : a.columns.length - b.columns.length
    })
    for (const t of sorted) {
      const lv = levels.get(t.dataset_id) ?? 0
      const x = (maxLv - lv) * 340
      const y = yByLv.get(lv) ?? 0
      nodes.push({
        id: String(t.dataset_id), type: "erdTable",
        position: { x, y },
        data: t as unknown as Record<string, unknown>, draggable: true,
      })
      yByLv.set(lv, y + nodeHeight(t) + 28)
    }
  }

  const edges: Edge[] = data.relations.map((r, i) => ({
    id: `fk-${i}`,
    source: String(r.source_dataset_id),
    target: String(r.target_dataset_id),
    label: `${r.source_columns.join(",") || "—"} ${r.cardinality}`,
    style: { stroke: r.origin === "DDL" ? "#0ea5e9" : "#9ca3af", strokeWidth: 1.5,
             strokeDasharray: r.origin === "DDL" ? undefined : "5 4" },
    markerEnd: { type: MarkerType.ArrowClosed, color: r.origin === "DDL" ? "#0ea5e9" : "#9ca3af" },
    labelStyle: { fontSize: 10, fill: "#0369a1", fontFamily: "monospace" },
    labelBgPadding: [4, 2] as [number, number],
    labelBgBorderRadius: 4,
    labelBgStyle: { fill: "white", stroke: "#e0f2fe" },
  }))

  return { nodes, edges }
}

// ---------------------------------------------------------------------------
// Mermaid erDiagram 소스 생성 — 문서화/공유용
// ---------------------------------------------------------------------------

function toMermaid(data: ErdData): string {
  const lines = ["erDiagram"]
  const byId = new Map(data.tables.map((t) => [t.dataset_id, t]))
  for (const r of data.relations) {
    const src = byId.get(r.source_dataset_id)?.name
    const tgt = byId.get(r.target_dataset_id)?.name
    if (!src || !tgt) continue
    // 자식 N — 부모 1 : Mermaid 표기는 부모 ||--o{ 자식
    const conn = r.cardinality === "1:1" ? "||--||" : "||--o{"
    const label = r.source_columns.join(",") || "ref"
    lines.push(`  ${tgt} ${conn} ${src} : "${label}"`)
  }
  for (const t of data.tables) {
    lines.push(`  ${t.name} {`)
    for (const c of t.columns) {
      const type = c.type.replace(/\s+/g, "_").replace(/[(),]/g, "_")
      const keys = [c.is_pk ? "PK" : null, c.fk_to ? "FK" : null].filter(Boolean).join(",")
      lines.push(`    ${type} ${c.name}${keys ? ` ${keys}` : ""}`)
    }
    lines.push("  }")
  }
  return lines.join("\n")
}

// ---------------------------------------------------------------------------
// ErdTab
// ---------------------------------------------------------------------------

export function ErdTab({ datasetId, datasourceId }: { datasetId?: number; datasourceId?: number }) {
  const [data, setData] = useState<ErdData | null>(null)
  const [loading, setLoading] = useState(true)
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const url = datasourceId != null
      ? `/api/v1/catalog/datasources/${datasourceId}/erd`
      : `/api/v1/catalog/datasets/${datasetId}/erd`
    authFetch(url)
      .then((r: Response) => (r.ok ? r.json() : null))
      .then((d: ErdData | null) => { if (!cancelled) setData(d) })
      .catch((err: unknown) => console.error("Failed to load ERD", { datasetId, err }))
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [datasetId, datasourceId])

  const toggleExpand = useCallback((id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }, [])

  const layouted = useMemo(() => {
    if (!data) return { nodes: [] as Node[], edges: [] as Edge[] }
    const enriched: ErdData = {
      ...data,
      tables: data.tables.map((t) => ({
        ...t,
        expanded: expandedIds.has(t.dataset_id),
        onToggle: () => toggleExpand(t.dataset_id),
      })) as ErdTable[],
    }
    return layoutErd(enriched, expandedIds)
  }, [data, expandedIds, toggleExpand])

  // 드래그를 지원하려면 노드가 state 여야 한다 (controlled 직접 전달 시 위치 변경이 무시됨).
  // 레이아웃 재계산 시(데이터 로드/컬럼 접기) 드래그로 옮긴 위치는 유지하고 data 만 갱신한다.
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  useEffect(() => {
    setNodes((prev) => {
      const prevPos = new Map(prev.map((n) => [n.id, n.position]))
      return layouted.nodes.map((n) => ({ ...n, position: prevPos.get(n.id) ?? n.position }))
    })
    setEdges(layouted.edges)
  }, [layouted, setNodes, setEdges])

  const copyMermaid = () => {
    if (!data) return
    navigator.clipboard.writeText(toMermaid(data))
    toast.success("Mermaid erDiagram 소스를 복사했습니다")
  }

  if (loading) {
    return <p className="py-12 text-center text-sm text-muted-foreground">ER 다이어그램 구성 중...</p>
  }
  if (!data || data.tables.length === 0) {
    return (
      <Card><CardContent className="py-12 text-center text-sm text-muted-foreground">
        ERD 를 구성할 수 없습니다 — DDL 또는 스키마 정보가 필요합니다.
      </CardContent></Card>
    )
  }

  return (
    <Card>
      <CardContent className="p-3">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1"><Key className="h-3 w-3 text-amber-600" /> PK</span>
            <span className="inline-flex items-center gap-1"><Link2 className="h-3 w-3 text-sky-600" /> FK</span>
            <span><span className="font-mono">?</span> = NULL 허용</span>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-0.5 w-5 bg-sky-500" /> DDL 제약
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-0.5 w-5"
                style={{ backgroundImage: "repeating-linear-gradient(90deg, #9ca3af 0 5px, transparent 5px 9px)" }} />
              리니지 추정
            </span>
            <span>엣지 라벨: FK 컬럼 + 카디널리티 (자식 N:1 부모)</span>
          </div>
          <Button variant="outline" size="sm" onClick={copyMermaid} className="gap-1.5">
            <Copy className="h-3.5 w-3.5" /> Mermaid 복사
          </Button>
        </div>
        <div style={{ height: datasourceId != null ? 720 : 560 }} className="rounded border">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
            proOptions={{ hideAttribution: true }}
            nodesConnectable={false}
          >
            <Background gap={16} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      </CardContent>
    </Card>
  )
}
