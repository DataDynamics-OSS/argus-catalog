"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import dynamic from "next/dynamic"
import {
  ArrowLeft, BookOpen, Box, Clock, Code2, Download, Eye, Globe, Grid3X3, Import, List, Loader2,
  Search, User, X,
} from "lucide-react"

import {
  ReactFlow, Controls,
  type Node, type Edge, Position, Handle,
  MarkerType, useNodesState, useEdgesState,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism"

import { Badge } from "@workspace/ui/components/badge"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Label } from "@workspace/ui/components/label"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Input } from "@workspace/ui/components/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { DashboardHeader } from "@/components/dashboard-header"
import { CommentSection } from "@/components/comments"
import {
  FileViewerDialog,
  isViewableFile,
} from "@/components/local-filesystem-browser/file-viewer-dialog"
import type { FilesystemFile } from "@/components/local-filesystem-browser/types"
import { useAuth } from "@/features/auth"

import { OciHubDashboard, SOURCE_LABELS } from "@/features/oci-hub/oci-hub-dashboard"
import { CodeViewer } from "@/components/code-viewer"
import {
  fetchOciModels, fetchOciModel, fetchVersions, updateReadme, importFromHuggingFace,
  type OciModelSummary, type OciModelDetail, type OciModelVersion,
} from "@/features/oci-hub/api"

const MonacoEditor = dynamic(() => import("@monaco-editor/react").then((m) => m.default), {
  ssr: false,
  loading: () => <div className="flex items-center justify-center h-[200px]"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>,
})

function formatSize(bytes: number): string {
  if (!bytes) return "-"
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`
}

// ISO → ``yyyy-MM-dd HH:mm:ss`` (로컬 timezone). 파일 브라우저 및 모델 상세의
// 날짜 표기와 동일한 형식.
function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, "0")
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const days = Math.floor(diff / 86400000)
  if (days === 0) return "오늘"
  if (days === 1) return "1일 전"
  if (days < 30) return `${days}일 전`
  return `${Math.floor(days / 30)}개월 전`
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    draft: "bg-zinc-400", review: "bg-amber-500", approved: "bg-blue-500",
    production: "bg-green-500", deprecated: "bg-zinc-600", archived: "bg-zinc-800",
  }
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold text-white ${colors[status] || "bg-zinc-400"}`}>
      {status.toUpperCase()}
    </span>
  )
}

function SourceBadge({ sourceType }: { sourceType: string | null }) {
  if (!sourceType) return null
  // SOURCE_LABELS (대시보드 공유) 와 동일한 한글 명칭. 색상은 출처별 톤 구분용.
  const config: Record<string, { label: string; color: string }> = {
    huggingface: { label: "HuggingFace", color: "text-blue-600 bg-blue-50 border-blue-200" },
    my: { label: "내 모델", color: "text-orange-600 bg-orange-50 border-orange-200" },
    file: { label: "파일", color: "text-green-600 bg-green-50 border-green-200" },
    local: { label: "로컬", color: "text-green-600 bg-green-50 border-green-200" },
  }
  const c = config[sourceType] || { label: sourceType, color: "text-zinc-600 bg-zinc-50 border-zinc-200" }
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${c.color}`}>
      {c.label}
    </span>
  )
}

// ─── Model Card (card in grid) ───

function ModelCard({
  model, onClick,
}: { model: OciModelSummary; onClick: () => void }) {
  return (
    <Card className="cursor-pointer hover:bg-muted/50 transition-colors" onClick={onClick}>
      <CardContent className="pt-2 pb-2">
        <div className="flex items-center gap-2 mb-1.5">
          <Box className="h-5 w-5 text-primary shrink-0" />
          <h3 className="font-semibold text-base truncate" title={model.display_name || model.name}>{model.display_name || model.name}</h3>
        </div>
        {model.description && (
          <p className="text-sm text-muted-foreground line-clamp-2 mb-2">{model.description}</p>
        )}
        <div className="flex flex-wrap gap-1 mb-3">
          <SourceBadge sourceType={model.source_type} />
          {model.task && <Badge variant="secondary" className="text-xs">{model.task}</Badge>}
          {model.framework && <Badge variant="secondary" className="text-xs">{model.framework}</Badge>}
          {model.language && <Badge variant="outline" className="text-xs">{model.language}</Badge>}
          {model.tags?.map((t) => (
            <Badge key={t.id} variant="outline" className="text-xs" style={{ borderColor: t.color, color: t.color }}>
              {t.name}
            </Badge>
          ))}
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1"><Download className="h-3 w-3" />{model.download_count}</span>
          <span>{formatSize(model.total_size)}</span>
          <span>v{model.version_count}</span>
          <span className="ml-auto">{timeAgo(model.updated_at)}</span>
        </div>
      </CardContent>
    </Card>
  )
}

// ─── Lineage Graph ───

type LineageEntry = {
  id: number
  source_type: string
  source_id: string
  source_name: string | null
  relation_type: string
  description: string | null
}

const SOURCE_TYPE_COLORS: Record<string, { bg: string; border: string; icon: string }> = {
  dataset: { bg: "bg-blue-50", border: "border-blue-300", icon: "📊" },
  model: { bg: "bg-purple-50", border: "border-purple-300", icon: "🤖" },
  external: { bg: "bg-green-50", border: "border-green-300", icon: "🌐" },
}

const RELATION_LABELS: Record<string, string> = {
  trained_on: "trained on",
  fine_tuned_from: "fine-tuned from",
  distilled_from: "distilled from",
  derived_from: "derived from",
  produces: "produces",
}

function LineageNode({ data }: { data: { label: string; sourceType: string; isCurrent: boolean; description?: string } }) {
  const colors = SOURCE_TYPE_COLORS[data.sourceType] || SOURCE_TYPE_COLORS.external!
  return (
    <div className={`px-4 py-3 rounded-lg border-2 shadow-sm min-w-[160px] text-center ${
      data.isCurrent ? "border-primary bg-primary/5 ring-2 ring-primary/20" : `${colors.border} ${colors.bg}`
    }`}>
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground !w-2 !h-2" />
      <div className="text-xs text-muted-foreground mb-1">
        {data.isCurrent ? "🤖 current model" : `${colors.icon} ${data.sourceType}`}
      </div>
      <div className="font-medium text-sm">{data.label}</div>
      {data.description && <div className="text-xs text-muted-foreground mt-1">{data.description}</div>}
      <Handle type="source" position={Position.Right} className="!bg-muted-foreground !w-2 !h-2" />
    </div>
  )
}

const lineageNodeTypes = { lineageNode: LineageNode }

function buildLineageNodesEdges(modelName: string, lineage: LineageEntry[]) {
  const inputs = lineage.filter((l) => l.relation_type !== "produces")
  const outputs = lineage.filter((l) => l.relation_type === "produces")

  const initNodes: Node[] = []
  const initEdges: Edge[] = []

  const centerX = 400
  const centerY = Math.max(inputs.length, outputs.length, 1) * 50

  initNodes.push({
    id: "current",
    type: "lineageNode",
    position: { x: centerX, y: centerY },
    draggable: true,
    data: { label: modelName, sourceType: "model", isCurrent: true },
  })

  inputs.forEach((l, i) => {
    const nodeId = `input-${l.id}`
    const spacing = 110
    const startY = centerY - ((inputs.length - 1) * spacing) / 2
    initNodes.push({
      id: nodeId, type: "lineageNode", draggable: true,
      position: { x: 50, y: startY + i * spacing },
      data: { label: l.source_name || l.source_id, sourceType: l.source_type, isCurrent: false, description: l.description },
    })
    initEdges.push({
      id: `edge-${l.id}`, source: nodeId, target: "current",
      label: RELATION_LABELS[l.relation_type] || l.relation_type,
      type: "default", animated: true,
      style: { stroke: "#6366f1" }, labelStyle: { fontSize: 11, fill: "#6366f1" },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#6366f1" },
    })
  })

  outputs.forEach((l, i) => {
    const nodeId = `output-${l.id}`
    const spacing = 110
    const startY = centerY - ((outputs.length - 1) * spacing) / 2
    initNodes.push({
      id: nodeId, type: "lineageNode", draggable: true,
      position: { x: 750, y: startY + i * spacing },
      data: { label: l.source_name || l.source_id, sourceType: l.source_type, isCurrent: false, description: l.description },
    })
    initEdges.push({
      id: `edge-${l.id}`, source: "current", target: nodeId,
      label: RELATION_LABELS[l.relation_type] || l.relation_type,
      type: "default", animated: true,
      style: { stroke: "#10b981" }, labelStyle: { fontSize: 11, fill: "#10b981" },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#10b981" },
    })
  })

  return { initNodes, initEdges }
}

function LineageGraph({ modelName, lineage }: { modelName: string; lineage: LineageEntry[] }) {
  const { initNodes, initEdges } = useMemo(
    () => buildLineageNodesEdges(modelName, lineage),
    [modelName, lineage],
  )
  const [nodes, , onNodesChange] = useNodesState(initNodes)
  const [edges, , onEdgesChange] = useEdgesState(initEdges)

  if (lineage.length === 0) {
    return <p className="text-sm text-muted-foreground text-center py-8">No lineage entries yet.</p>
  }

  return (
    <div className="border rounded-lg" style={{ height: "450px" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={lineageNodeTypes}
        defaultViewport={{ x: 50, y: 150, zoom: 1 }}
        proOptions={{ hideAttribution: true }}
      >
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}

// ─── Files Tab ───

type S3File = { key: string; name: string; size: number; last_modified: string }
type S3Folder = { key: string; name: string }

// FileViewerDialog 가 처리 가능한 확장 중 server-side preview 가 필수인 것 — OCI 의
// model-store browse API 는 server preview 를 제공하지 않으므로 미리보기 버튼을
// 숨긴다 (열어도 dialog 가 빈 상태로 떴다 닫혀 사용자에게 혼란).
const OCI_SERVER_PREVIEW_ONLY = new Set(["xlsx", "xls", "docx", "pptx", "parquet"])

function ociCanPreview(name: string): boolean {
  if (!isViewableFile(name)) return false
  const ext = name.split(".").pop()?.toLowerCase() ?? ""
  return !OCI_SERVER_PREVIEW_ONLY.has(ext)
}

function FilesTab({ modelName, latestVersion }: { modelName: string; latestVersion: number }) {
  const [, setPath] = useState(`/${modelName}/v${latestVersion}`)
  const [folders, setFolders] = useState<S3Folder[]>([])
  const [files, setFiles] = useState<S3File[]>([])
  const [loading, setLoading] = useState(true)
  const [currentPath, setCurrentPath] = useState("")
  // 미리보기 dialog 에 전달할 선택된 파일. null 이면 닫힘.
  const [previewEntry, setPreviewEntry] = useState<FilesystemFile | null>(null)

  const load = useCallback(async (p: string) => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ path: p })
      const res = await fetch(`/api/v1/model-store/browse/list?${params}`)
      if (!res.ok) throw new Error(`${res.status}`)
      const data = await res.json()
      setFolders(data.folders || [])
      setFiles(data.files || [])
      setCurrentPath(data.current_path || p)
      setPath(p)
    } catch (err) {
      console.error("Failed to load files:", err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load(`/${modelName}/v${latestVersion}`)
  }, [load, modelName, latestVersion])

  // S3 객체 경로(``filePath``) 로부터 presigned URL 을 가져온다 — download 와 preview
  // 양쪽에서 재사용. preview 측은 FileViewerDialog 의 ``getDownloadUrl`` 콜백 시그니처
  // (``Promise<string>``) 에 맞춰져 있다.
  const getPreviewUrl = useCallback(async (filePath: string): Promise<string> => {
    const params = new URLSearchParams({ path: filePath })
    const res = await fetch(`/api/v1/model-store/browse/download?${params}`)
    if (!res.ok) throw new Error(`Failed to get download URL (${res.status})`)
    const data = await res.json()
    return data.url as string
  }, [])

  const handleDownload = useCallback(async (filePath: string) => {
    try {
      const url = await getPreviewUrl(filePath)
      window.open(url, "_blank")
    } catch (err) {
      console.error("Download error:", err)
    }
  }, [getPreviewUrl])

  // 미리보기 버튼 클릭 — 파일을 FilesystemFile 형태로 변환해 dialog 에 전달.
  const handlePreview = useCallback((f: S3File) => {
    setPreviewEntry({
      kind: "file",
      key: f.key,
      name: f.name,
      size: f.size,
      lastModified: f.last_modified,
    })
  }, [])

  // Breadcrumb segments
  const segments = currentPath.split("/").filter(Boolean)
  const basePath = `/${modelName}/v${latestVersion}`

  const totalSize = files.reduce((acc, f) => acc + f.size, 0)

  if (loading) return <p className="text-sm text-muted-foreground text-center py-8">Loading files...</p>

  return (
    <div className="space-y-3">
      {/* Summary */}
      <p className="text-sm">
        {folders.length > 0 && `폴더 ${folders.length}개, `}
        파일 {files.length}개
        {totalSize > 0 && ` · 총 ${formatSize(totalSize)}`}
      </p>

      {/* File table */}
      <div className="border rounded-md overflow-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/60 sticky top-0">
            <tr>
              <th className="px-3 py-2 text-left font-medium">이름</th>
              <th className="px-3 py-2 text-right font-medium w-28">크기</th>
              <th className="px-3 py-2 text-left font-medium w-44">수정</th>
              <th className="px-3 py-2 text-center font-medium w-24">작업</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {/* Parent directory */}
            {currentPath !== basePath && (
              <tr className="hover:bg-muted/30 cursor-pointer" onClick={() => {
                const parent = "/" + segments.slice(0, -1).join("/")
                load(parent)
              }}>
                <td className="px-3 py-2 text-primary" colSpan={4}>📁 ..</td>
              </tr>
            )}
            {/* Folders */}
            {folders.map((f) => (
              <tr key={f.key} className="hover:bg-muted/30 cursor-pointer" onClick={() => load(f.key.replace(/\/$/, ""))}>
                <td className="px-3 py-2 text-primary flex items-center gap-2">📁 {f.name}</td>
                <td className="px-3 py-2 text-right text-muted-foreground">-</td>
                <td className="px-3 py-2 text-muted-foreground">-</td>
                <td className="px-3 py-2 text-center">-</td>
              </tr>
            ))}
            {/* Files */}
            {files.map((f) => (
              <tr key={f.key} className="hover:bg-muted/30">
                <td className="px-3 py-2 flex items-center gap-2">📄 {f.name}</td>
                <td className="px-3 py-2 text-right text-muted-foreground">{formatSize(f.size)}</td>
                <td className="px-3 py-2 text-muted-foreground text-xs">
                  {f.last_modified ? formatDateTime(f.last_modified) : "-"}
                </td>
                <td className="px-3 py-2 text-center">
                  <div className="inline-flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 text-xs"
                      onClick={() => handleDownload(f.key)}
                    >
                      <Download className="h-3 w-3 mr-1" />다운로드
                    </Button>
                    {ociCanPreview(f.name) && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 text-xs"
                        onClick={() => handlePreview(f)}
                      >
                        <Eye className="h-3 w-3 mr-1" />미리보기
                      </Button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 파일 미리보기 dialog — model-store 의 server preview API 가 없어
          ``previewFile`` 은 제공하지 않으며, server preview 가 필수인 형식은 ``ociCanPreview``
          가 미리 거른다. */}
      <FileViewerDialog
        open={previewEntry !== null}
        onOpenChange={(o) => { if (!o) setPreviewEntry(null) }}
        entry={previewEntry}
        getDownloadUrl={getPreviewUrl}
      />
    </div>
  )
}

// ─── Model Detail (inline) ───

function ModelDetail({
  name, onBack,
}: { name: string; onBack: () => void }) {
  const { user } = useAuth()
  const [detail, setDetail] = useState<OciModelDetail | null>(null)
  const [versions, setVersions] = useState<OciModelVersion[]>([])
  const [loading, setLoading] = useState(true)
  const [readmeEdit, setReadmeEdit] = useState(false)
  const [readmeText, setReadmeText] = useState("")
  const [savingReadme, setSavingReadme] = useState(false)

  useEffect(() => {
    setLoading(true)
    Promise.all([fetchOciModel(name), fetchVersions(name)])
      .then(([d, v]) => { setDetail(d); setVersions(v); setReadmeText(d.readme || "") })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [name])

  const handleSaveReadme = useCallback(async () => {
    setSavingReadme(true)
    try {
      await updateReadme(name, readmeText)
      setDetail((prev) => prev ? { ...prev, readme: readmeText } : prev)
      setReadmeEdit(false)
    } finally {
      setSavingReadme(false)
    }
  }, [name, readmeText])

  // 사용법 탭에 표시할 Python SDK 샘플. 한글 주석으로 작성.
  const usageCode = useMemo(
    () => `from argus_catalog_sdk import ModelClient

client = ModelClient("http://<argus-catalog-server>:<argus-catalog-server-port>")

# 모델을 로컬 디렉토리로 다운로드
client.pull("${detail?.name ?? ""}", version=${detail?.version_count || 1}, dest="/tmp/${detail?.name ?? ""}")

# 또는 presigned 다운로드 URL 만 받아서 직접 처리
urls = client.get_download_urls("${detail?.name ?? ""}", version=${detail?.version_count || 1})
for filename, url in urls["files"].items():
    print(f"{filename}: {url}")
`,
    [detail?.name, detail?.version_count],
  )

  if (loading || !detail) {
    return <div className="flex items-center justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
  }

  return (
    <div className="space-y-4">
      {/* Back link — 모델명 라인에서 분리해 헤더 위 별도 줄에 둔다.
          ml-9 들여쓰기를 강제하던 부작용도 함께 제거. */}
      <Button
        variant="ghost"
        size="sm"
        className="h-7 -ml-2 self-start px-2 text-muted-foreground hover:text-foreground"
        onClick={onBack}
      >
        <ArrowLeft className="h-4 w-4 mr-1" />
        모델 목록으로
      </Button>

      {/* Header */}
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Globe className="h-5 w-5 text-primary" />
          <h1 className="text-xl font-bold">{detail.display_name || detail.name}</h1>
          <SourceBadge sourceType={detail.source_type} />
        </div>
        {detail.description && <p className="text-sm text-muted-foreground">{detail.description}</p>}
        <div className="flex items-center gap-4 text-sm text-muted-foreground">
          {detail.owner && <span className="flex items-center gap-1"><User className="h-3.5 w-3.5" />{detail.owner}</span>}
          <span className="flex items-center gap-1"><Clock className="h-3.5 w-3.5" />{timeAgo(detail.created_at)}</span>
          <span className="flex items-center gap-1"><Download className="h-3.5 w-3.5" />다운로드 {detail.download_count}회</span>
          {detail.source_type && detail.source_type === "huggingface" && detail.source_id ? (
            <a
              href={`https://huggingface.co/${detail.source_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-primary hover:underline"
            >
              출처: HuggingFace:{detail.source_id} ↗
            </a>
          ) : detail.source_type ? (
            <span>출처: {SOURCE_LABELS[detail.source_type] ?? detail.source_type}{detail.source_id ? `:${detail.source_id}` : ""}</span>
          ) : null}
        </div>
        {(detail.tags?.length > 0 || detail.task || detail.framework) && (
          <div className="flex flex-wrap gap-1 mt-1">
            {detail.task && <Badge variant="secondary" className="text-xs">{detail.task}</Badge>}
            {detail.framework && <Badge variant="secondary" className="text-xs">{detail.framework}</Badge>}
            {detail.language && <Badge variant="outline" className="text-xs">{detail.language}</Badge>}
            {detail.license && <Badge variant="outline" className="text-xs">{detail.license}</Badge>}
            {detail.tags?.map((t) => (
              <Badge key={t.id} variant="outline" className="text-xs" style={{ borderColor: t.color, color: t.color }}>{t.name}</Badge>
            ))}
          </div>
        )}
      </div>

      {/* Tabs */}
      <Tabs defaultValue="info">
        <TabsList variant="line">
          <TabsTrigger value="info" className="text-base">정보</TabsTrigger>
          <TabsTrigger value="model-card" className="text-base">모델 카드</TabsTrigger>
          <TabsTrigger value="files" className="text-base">파일</TabsTrigger>
          <TabsTrigger value="versions" className="text-base">버전</TabsTrigger>
          <TabsTrigger value="lineage" className="text-base">리니지</TabsTrigger>
          <TabsTrigger value="usage" className="text-base">사용법</TabsTrigger>
          <TabsTrigger value="comments" className="text-base">댓글</TabsTrigger>
        </TabsList>

        {/* Info Tab (README + Edit) */}
        <TabsContent value="info" className="mt-4">
          <div className="flex justify-end mb-2">
            {user?.is_admin && (
              readmeEdit ? (
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => { setReadmeEdit(false); setReadmeText(detail.readme || "") }} disabled={savingReadme}>취소</Button>
                  <Button size="sm" onClick={handleSaveReadme} disabled={savingReadme}>{savingReadme ? "저장 중..." : "저장"}</Button>
                </div>
              ) : (
                <Button variant="outline" size="sm" onClick={() => setReadmeEdit(true)}>수정</Button>
              )
            )}
          </div>
          {readmeEdit ? (
            <div className="overflow-hidden rounded" style={{ height: "calc(100vh - 320px)" }}>
              <MonacoEditor
                height="100%" language="markdown" value={readmeText}
                onChange={(v) => setReadmeText(v || "")}
                theme="light"
                options={{
                  minimap: { enabled: false }, wordWrap: "on", fontSize: 13,
                  fontFamily: "D2Coding, monospace",
                  lineNumbers: "on", renderLineHighlight: "none",
                  overviewRulerBorder: false, hideCursorInOverviewRuler: true,
                }}
              />
            </div>
          ) : detail.readme ? (
            <div className="prose prose-base max-w-none prose-headings:text-foreground prose-p:text-foreground prose-li:text-foreground prose-strong:text-foreground prose-code:text-foreground prose-code:bg-zinc-100 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:font-[D2Coding,monospace] [&_table]:border-collapse [&_table]:text-sm [&_th]:border [&_th]:px-3 [&_th]:py-1.5 [&_th]:bg-muted/60 [&_th]:text-foreground [&_td]:border [&_td]:px-3 [&_td]:py-1.5 [&_td]:text-foreground [&_code]:font-[D2Coding,monospace]">
              <Markdown
                remarkPlugins={[remarkGfm]}
                components={{
                  code({ className, children, ...props }) {
                    const match = /language-(\w+)/.exec(className || "")
                    const codeStr = String(children).replace(/\n$/, "")
                    if (match) {
                      return (
                        <SyntaxHighlighter
                          style={oneDark}
                          language={match[1]}
                          PreTag="div"
                          customStyle={{ borderRadius: "0.5rem", fontSize: "13px", fontFamily: "D2Coding, monospace" }}
                        >
                          {codeStr}
                        </SyntaxHighlighter>
                      )
                    }
                    return <code className={className} {...props}>{children}</code>
                  },
                }}
              >
                {detail.readme}
              </Markdown>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground text-center py-12">아직 README 가 없습니다. <strong>수정</strong> 을 눌러 작성하세요.</p>
          )}
        </TabsContent>

        {/* Model Card (metadata from config.json) */}
        <TabsContent value="model-card" className="mt-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader><CardTitle className="text-sm font-medium">모델 정보</CardTitle></CardHeader>
              <CardContent className="text-sm space-y-1.5">
                <div className="flex gap-2"><span className="text-muted-foreground w-32 shrink-0">이름</span><span>{detail.name}</span></div>
                {detail.source_id && <div className="flex gap-2"><span className="text-muted-foreground w-32 shrink-0">출처</span><span>{SOURCE_LABELS[detail.source_type ?? "unknown"] ?? detail.source_type ?? ""}: {detail.source_id}</span></div>}
                {detail.task && <div className="flex gap-2"><span className="text-muted-foreground w-32 shrink-0">태스크</span><span>{detail.task}</span></div>}
                {detail.framework && <div className="flex gap-2"><span className="text-muted-foreground w-32 shrink-0">프레임워크</span><span>{detail.framework}</span></div>}
                {detail.language && <div className="flex gap-2"><span className="text-muted-foreground w-32 shrink-0">언어</span><span>{detail.language}</span></div>}
                {detail.license && <div className="flex gap-2"><span className="text-muted-foreground w-32 shrink-0">라이선스</span><span>{detail.license}</span></div>}
                <div className="flex gap-2"><span className="text-muted-foreground w-32 shrink-0">크기</span><span>{formatSize(detail.total_size)}</span></div>
                <div className="flex gap-2"><span className="text-muted-foreground w-32 shrink-0">버전 수</span><span>{detail.version_count}</span></div>
                <div className="flex gap-2"><span className="text-muted-foreground w-32 shrink-0">상태</span><StatusBadge status={detail.status} /></div>
              </CardContent>
            </Card>

            {versions.length > 0 && versions[0]?.extra_metadata && (
              <Card>
                <CardHeader><CardTitle className="text-sm font-medium">아키텍처</CardTitle></CardHeader>
                <CardContent className="text-sm space-y-1.5">
                  {Object.entries(versions[0]!.extra_metadata as Record<string, unknown>)
                    .filter(([, v]) => v != null)
                    .map(([k, v]) => (
                      <div key={k} className="flex gap-2">
                        <span className="text-muted-foreground w-40 shrink-0">{k.replace(/_/g, " ")}</span>
                        <span className="font-mono text-xs">{Array.isArray(v) ? (v as string[]).join(", ") : String(v)}</span>
                      </div>
                    ))
                  }
                </CardContent>
              </Card>
            )}
          </div>
        </TabsContent>

        {/* Files */}
        <TabsContent value="files" className="mt-4">
          <FilesTab modelName={detail.name} latestVersion={detail.version_count} />
        </TabsContent>

        {/* Versions */}
        <TabsContent value="versions" className="mt-4">
          {versions.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">아직 버전이 없습니다.</p>
          ) : (
            <div className="border rounded-md overflow-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted/60 sticky top-0">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium w-20">버전</th>
                    <th className="px-3 py-2 text-center font-medium w-20">파일</th>
                    <th className="px-3 py-2 text-center font-medium w-24">크기</th>
                    <th className="px-3 py-2 text-center font-medium w-24">상태</th>
                    <th className="px-3 py-2 text-left font-medium">생성일</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {versions.map((v) => (
                    <tr key={v.id} className="hover:bg-muted/30">
                      <td className="px-3 py-2">v{v.version}</td>
                      <td className="px-3 py-2 text-center">{v.file_count}</td>
                      <td className="px-3 py-2 text-center">{formatSize(v.total_size)}</td>
                      <td className="px-3 py-2 text-center"><StatusBadge status={v.status} /></td>
                      <td className="px-3 py-2">{timeAgo(v.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </TabsContent>

        {/* Lineage */}
        <TabsContent value="lineage" className="mt-4">
          <LineageGraph modelName={detail.name} lineage={detail.lineage} />
        </TabsContent>

        {/* Usage — viewport 하단까지 차도록 동적 높이 (Back link · 헤더 · 탭 트리거 +
            페이지 padding 약 280px 를 제하고 360px floor). */}
        <TabsContent value="usage" className="mt-4">
          <CodeViewer
            code={usageCode}
            language="python"
            height="max(360px, calc(100vh - 280px))"
            copyLabel="Python SDK"
          />
        </TabsContent>

        {/* Comments */}
        <TabsContent value="comments" className="mt-4">
          <CommentSection entityType="oci-model" entityId={detail.name} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

// ─── Main Page ───

// ─── HuggingFace URL validation ───

const HF_URL_REGEX = /^https?:\/\/huggingface\.co\/([a-zA-Z0-9_-]+(?:\/[a-zA-Z0-9_.-]+)?)(?:\/.*)?$/

function extractHfModelId(url: string): string | null {
  // Accept both URL and direct model ID
  const urlMatch = HF_URL_REGEX.exec(url.trim())
  if (urlMatch) return urlMatch[1] ?? null
  // Direct model ID: org/model or model
  const directMatch = /^[a-zA-Z0-9_-]+(?:\/[a-zA-Z0-9_.-]+)?$/.exec(url.trim())
  if (directMatch) return directMatch[0]
  return null
}

// ─── Main Page ───

export default function OciHubPage() {
  const { user } = useAuth()
  const [models, setModels] = useState<OciModelSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState("")
  const [selectedModel, setSelectedModel] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState("dashboard")
  const [viewMode, setViewMode] = useState<"card" | "list">("card")
  const [guideOpen, setGuideOpen] = useState(false)
  const [guideTab, setGuideTab] = useState<"cli" | "sdk" | "api">("cli")
  const [useGuideOpen, setUseGuideOpen] = useState(false)
  const [useGuideTab, setUseGuideTab] = useState<"cli" | "sdk" | "api">("cli")
  const [serverHost, setServerHost] = useState("")
  const [serverPort, setServerPort] = useState(4600)
  const pageSize = 12
  const totalPages = Math.ceil(total / pageSize)

  // 목록 스크롤 위치 보관 + 상세 복귀 시 복원
  const listScrollRef = useRef(0)
  const pendingScrollRestore = useRef(false)
  // 목록 보기 중 스크롤 위치 추적
  useEffect(() => {
    if (selectedModel) return
    const onScroll = () => {
      listScrollRef.current = window.scrollY
    }
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => window.removeEventListener("scroll", onScroll)
  }, [selectedModel])
  // 상세에서 복귀(재조회 완료) 시 저장된 위치로 복원
  useEffect(() => {
    if (selectedModel || loading || !pendingScrollRestore.current) return
    pendingScrollRestore.current = false
    let raf2 = 0
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => window.scrollTo(0, listScrollRef.current))
    })
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
    }
  }, [selectedModel, loading])

  // Import dialog state
  const [importOpen, setImportOpen] = useState(false)
  const [importUrl, setImportUrl] = useState("")
  const [importing, setImporting] = useState(false)
  const [importProgress, setImportProgress] = useState(0)
  const [importError, setImportError] = useState<string | null>(null)
  const [importSuccess, setImportSuccess] = useState<string | null>(null)

  const extractedId = extractHfModelId(importUrl)
  const isValidUrl = !!extractedId

  // Fetch server info for guide code examples
  useEffect(() => {
    if ((guideOpen || useGuideOpen) && !serverHost) {
      fetch("/api/v1/oci-models/server-info")
        .then((r) => r.json())
        .then((d) => { setServerHost(d.host); setServerPort(d.port) })
        .catch(() => { setServerHost("localhost"); setServerPort(4600) })
    }
  }, [guideOpen, useGuideOpen, serverHost])

  const serverUrl = `http://${serverHost || "localhost"}:${serverPort}`

  const load = useCallback(async (p: number, s: string) => {
    setLoading(true)
    try {
      const data = await fetchOciModels({ search: s || undefined, page: p, pageSize })
      setModels(data.items)
      setTotal(data.total)
      setPage(p)
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load(1, "") }, [load])

  const handleImport = useCallback(async () => {
    if (!extractedId) return
    setImporting(true)
    setImportError(null)
    setImportSuccess(null)
    setImportProgress(10)

    try {
      // Simulate progress (actual import is a single request)
      const progressInterval = setInterval(() => {
        setImportProgress((prev) => Math.min(prev + 5, 85))
      }, 500)

      const result = await importFromHuggingFace({
        hf_model_id: extractedId,
        name: extractedId.replace(/\//g, "-"),
      })

      clearInterval(progressInterval)
      setImportProgress(100)
      setImportSuccess(
        `Imported ${result.name} v${result.version}: ${result.file_count} files, ${formatSize(result.total_size)}`
      )

      // Reload list after short delay
      setTimeout(() => {
        setImportOpen(false)
        setImportUrl("")
        setImportProgress(0)
        setImportSuccess(null)
        load(1, search)
      }, 2000)
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Import failed")
      setImportProgress(0)
    } finally {
      setImporting(false)
    }
  }, [extractedId, load, search])

  function handleImportClose(open: boolean) {
    if (!importing) {
      setImportOpen(open)
      if (!open) {
        setImportUrl("")
        setImportError(null)
        setImportSuccess(null)
        setImportProgress(0)
      }
    }
  }

  return (
    <>
      <DashboardHeader title="OCI 모델 허브" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <Tabs value={selectedModel ? "models" : activeTab} onValueChange={(v) => { setActiveTab(v); setSelectedModel(null) }}>
          <TabsList variant="line">
            <TabsTrigger value="dashboard" className="text-base">대시보드</TabsTrigger>
            <TabsTrigger value="models" className="text-base">모델</TabsTrigger>
          </TabsList>

          <TabsContent value="dashboard" className="mt-4">
            <OciHubDashboard />
          </TabsContent>

          <TabsContent value="models" className="mt-4">
          {selectedModel ? (
            <ModelDetail name={selectedModel} onBack={() => { pendingScrollRestore.current = true; setSelectedModel(null); load(page, search) }} />
          ) : (
        <div className="space-y-4">
        {/* Toolbar */}
        <div className="flex items-center gap-2">
          <div className="relative flex-1 max-w-xs">
            <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="모델 검색..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && load(1, search)}
              className="pl-8 h-9"
            />
          </div>
          {search && (
            <Button variant="ghost" size="sm" onClick={() => { setSearch(""); load(1, "") }}>
              <X className="h-3.5 w-3.5 mr-1" />초기화
            </Button>
          )}
          <div className="flex items-center gap-2 ml-auto">
            {/* 보기 전환 */}
            <div className="flex items-center border rounded-md">
              <Button
                variant={viewMode === "card" ? "secondary" : "ghost"}
                size="icon-sm"
                onClick={() => setViewMode("card")}
                aria-label="카드 보기"
              >
                <Grid3X3 className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant={viewMode === "list" ? "secondary" : "ghost"}
                size="icon-sm"
                onClick={() => setViewMode("list")}
                aria-label="목록 보기"
              >
                <List className="h-3.5 w-3.5" />
              </Button>
            </div>
            {user?.is_admin && (
              <Button size="sm" onClick={() => setImportOpen(true)}>
                <Import className="h-4 w-4 mr-1.5" />
                HuggingFace 에서 가져오기
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={() => setGuideOpen(true)}>
              <BookOpen className="h-4 w-4 mr-1.5" />
              가져오기 가이드
            </Button>
            <Button variant="outline" size="sm" onClick={() => setUseGuideOpen(true)}>
              <Code2 className="h-4 w-4 mr-1.5" />
              사용 가이드
            </Button>
          </div>
        </div>

        {/* Import Dialog */}
        <Dialog open={importOpen} onOpenChange={handleImportClose}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>HuggingFace 에서 가져오기</DialogTitle>
            </DialogHeader>
            <div className="grid gap-4 py-2">
              <div className="grid gap-2">
                <Label>HuggingFace URL 또는 모델 ID</Label>
                <Input
                  value={importUrl}
                  onChange={(e) => { setImportUrl(e.target.value); setImportError(null) }}
                  placeholder="https://huggingface.co/bert-base-uncased"
                  disabled={importing}
                />
                <div className="text-xs text-muted-foreground space-y-1">
                  <p>지원 형식:</p>
                  <ul className="list-disc list-inside space-y-0.5">
                    <li><code>https://huggingface.co/bert-base-uncased</code></li>
                    <li><code>https://huggingface.co/meta-llama/Llama-3.1-8B</code></li>
                    <li><code>bert-base-uncased</code> (모델 ID 직접 입력)</li>
                    <li><code>meta-llama/Llama-3.1-8B</code> (org/model 형식)</li>
                  </ul>
                </div>
                {extractedId && (
                  <p className="text-sm text-primary">
                    모델 ID: <strong>{extractedId}</strong>
                  </p>
                )}
              </div>

              {/* 진행 상태 */}
              {(importing || importProgress > 0) && (
                <div className="space-y-1">
                  <div className="h-2 bg-muted rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-300 ${
                        importSuccess ? "bg-green-500" : "bg-primary"
                      }`}
                      style={{ width: `${importProgress}%` }}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {importing ? "다운로드 후 S3 에 업로드 중..." : importProgress === 100 ? "완료!" : ""}
                  </p>
                </div>
              )}

              {importError && <p className="text-sm text-destructive">{importError}</p>}
              {importSuccess && <p className="text-sm text-green-600">{importSuccess}</p>}

              <div className="flex justify-end gap-2 pt-2">
                <Button variant="outline" onClick={() => handleImportClose(false)} disabled={importing}>
                  취소
                </Button>
                <Button onClick={handleImport} disabled={!isValidUrl || importing}>
                  {importing ? (
                    <><Loader2 className="h-4 w-4 animate-spin mr-1.5" />가져오는 중...</>
                  ) : (
                    "가져오기"
                  )}
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>

        {/* How to Import Guide Dialog */}
        <Dialog open={guideOpen} onOpenChange={setGuideOpen}>
          <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>모델 가져오기 방법</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              {/* Tab buttons */}
              <div className="flex items-center border rounded-md w-fit">
                {(["cli", "sdk", "api"] as const).map((t) => (
                  <Button
                    key={t}
                    variant={guideTab === t ? "secondary" : "ghost"}
                    size="sm"
                    onClick={() => setGuideTab(t)}
                    className="rounded-none first:rounded-l-md last:rounded-r-md"
                  >
                    {t === "cli" ? "CLI" : t === "sdk" ? "Python SDK" : "REST API"}
                  </Button>
                ))}
              </div>

              {/* CLI tab */}
              {guideTab === "cli" && (
                <CodeViewer
                  language="shell"
                  height={520}
                  copyLabel="CLI 가져오기"
                  code={`# ─── CLI 설치 ───
pip install argus-catalog-sdk

# ─── HuggingFace 에서 가져오기 ───
argus-model import-hf bert-base-uncased my-bert \\
  --server ${serverUrl} \\
  --description "BERT 기본 모델"

# ─── 로컬 디렉토리에서 가져오기 (에어갭 환경) ───
# 1. 모델 파일을 USB/SCP 등으로 서버에 전송
# 2. 서버 로컬 경로에서 import
argus-model import-local /data/transferred/bert my-bert \\
  --server ${serverUrl}

# ─── presigned URL 로 로컬 모델 push ───
# (클라이언트에서 직접 업로드 — 서버 파일시스템 접근 불필요)
argus-model push /path/to/my-model my-custom-model \\
  --server ${serverUrl} \\
  --description "직접 학습한 커스텀 모델"

# ─── 가져온 모델 목록 ───
argus-model list --server ${serverUrl}

# ─── 모델을 로컬로 다운로드 ───
argus-model pull my-bert 1 /tmp/my-bert --server ${serverUrl}`}
                />
              )}

              {/* Python SDK tab */}
              {guideTab === "sdk" && (
                <CodeViewer
                  language="python"
                  height={520}
                  copyLabel="Python SDK 가져오기"
                  code={`from argus_catalog_sdk import ModelClient

client = ModelClient("${serverUrl}")

# ─── HuggingFace 에서 가져오기 ───
result = client.import_huggingface(
    "bert-base-uncased",
    "my-bert",
    description="BERT 기본 모델",
    owner="ml-team",
)
print(f"가져온 버전 v{result['version']}: 파일 {result['file_count']}개")

# ─── 로컬 디렉토리에서 가져오기 (에어갭 환경) ───
# 디렉토리는 카탈로그 서버에서 접근 가능해야 함
result = client.import_local(
    "/data/transferred/bert",
    "my-bert",
    description="BERT (에어갭 가져오기)",
)

# ─── presigned URL 로 로컬 모델 push ───
# 클라이언트에서 직접 업로드 — 서버 파일시스템 접근 불필요
result = client.push(
    "/path/to/my-model",
    "my-custom-model",
    description="직접 학습한 커스텀 모델",
)

# ─── 모델 목록 ───
models = client.list_models()
for m in models["items"]:
    print(f"{m['name']} - v{m['max_version_number']}")

# ─── 모델을 로컬로 다운로드 ───
files = client.pull("my-bert", version=1, dest="/tmp/my-bert")
print(f"다운로드 완료: 파일 {len(files)}개")`}
                />
              )}

              {/* REST API tab */}
              {guideTab === "api" && (
                <CodeViewer
                  language="shell"
                  height={520}
                  copyLabel="REST API 가져오기"
                  code={`# ─── HuggingFace 에서 가져오기 ───
curl -X POST "${serverUrl}/api/v1/oci-models/import/huggingface" \\
  -H "Content-Type: application/json" \\
  -d '{
    "hf_model_id": "bert-base-uncased",
    "name": "my-bert",
    "description": "BERT 기본 모델",
    "owner": "ml-team",
    "task": "fill-mask",
    "framework": "pytorch"
  }'

# ─── 모델을 직접 생성 ───
curl -X POST "${serverUrl}/api/v1/oci-models" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "my-custom-model",
    "description": "직접 만든 커스텀 모델",
    "task": "classification",
    "framework": "sklearn"
  }'

# ─── 모델 목록 조회 ───
curl "${serverUrl}/api/v1/oci-models?page=1&page_size=10"

# ─── 모델 상세 조회 ───
curl "${serverUrl}/api/v1/oci-models/my-bert"

# ─── README 갱신 ───
curl -X PUT "${serverUrl}/api/v1/oci-models/my-bert/readme" \\
  -H "Content-Type: application/json" \\
  -d '{"readme": "# my-bert 모델\\n\\n이 모델은 BERT 기반 분류 모델입니다."}'`}
                />
              )}
            </div>
          </DialogContent>
        </Dialog>

        {/* How to Use Guide Dialog */}
        <Dialog open={useGuideOpen} onOpenChange={setUseGuideOpen}>
          <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>모델 사용 방법</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="flex items-center border rounded-md w-fit">
                {(["cli", "sdk", "api"] as const).map((t) => (
                  <Button
                    key={t}
                    variant={useGuideTab === t ? "secondary" : "ghost"}
                    size="sm"
                    onClick={() => setUseGuideTab(t)}
                    className="rounded-none first:rounded-l-md last:rounded-r-md"
                  >
                    {t === "cli" ? "CLI" : t === "sdk" ? "Python SDK" : "REST API"}
                  </Button>
                ))}
              </div>

              {useGuideTab === "cli" && (
                <CodeViewer
                  language="shell"
                  height={520}
                  copyLabel="CLI 사용법"
                  code={`# ─── CLI 설치 ───
pip install argus-catalog-sdk

# ─── 사용 가능한 모델 목록 ───
argus-model list --server ${serverUrl}

# ─── 모델을 로컬 디렉토리로 다운로드 ───
argus-model pull my-bert 1 /tmp/my-bert --server ${serverUrl}

# 다음 파일들이 다운로드됨:
#   /tmp/my-bert/config.json
#   /tmp/my-bert/model.safetensors
#   /tmp/my-bert/tokenizer.json
#   ...

# ─── 모델 버전의 파일 목록 ───
argus-model files my-bert 1 --server ${serverUrl}

# ─── OCI manifest 보기 ───
argus-model manifest my-bert 1 --server ${serverUrl}

# ─── 다운로드한 모델 사용 ───
# pull 후, 사용하는 프레임워크로 로드:
python -c "
from transformers import AutoModel, AutoTokenizer
model = AutoModel.from_pretrained('/tmp/my-bert')
tokenizer = AutoTokenizer.from_pretrained('/tmp/my-bert')
inputs = tokenizer('Hello world', return_tensors='pt')
outputs = model(**inputs)
print('Output shape:', outputs.last_hidden_state.shape)
"`}
                />
              )}

              {useGuideTab === "sdk" && (
                <CodeViewer
                  language="python"
                  height={520}
                  copyLabel="Python SDK 사용법"
                  code={`from argus_catalog_sdk import ModelClient

client = ModelClient("${serverUrl}")

# ─── 사용 가능한 모델 목록 ───
models = client.list_models()
for m in models["items"]:
    print(f"{m['name']} v{m['max_version_number']} - {m['status']}")

# ─── 모델을 로컬 디렉토리로 다운로드 ───
files = client.pull("my-bert", version=1, dest="/tmp/my-bert")
print(f"파일 {len(files)}개를 /tmp/my-bert 로 다운로드 완료")

# ─── presigned 다운로드 URL 가져오기 ───
# (선택 다운로드나 다른 도구와의 연동에 사용)
urls = client.get_download_urls("my-bert", version=1)
for filename, url in urls["files"].items():
    print(f"{filename}: {url}")

# ─── HuggingFace Transformers 와 함께 사용 ───
from transformers import AutoModel, AutoTokenizer

model = AutoModel.from_pretrained("/tmp/my-bert")
tokenizer = AutoTokenizer.from_pretrained("/tmp/my-bert")

inputs = tokenizer("Hello world", return_tensors="pt")
outputs = model(**inputs)
print("출력 shape:", outputs.last_hidden_state.shape)

# ─── PyTorch 로 직접 사용 ───
import torch
state_dict = torch.load("/tmp/my-bert/pytorch_model.bin", weights_only=True)
print(f"파라미터 텐서 {len(state_dict)}개 로드")

# ─── ONNX Runtime 으로 사용 ───
import onnxruntime as ort
session = ort.InferenceSession("/tmp/my-bert/onnx/model.onnx")
print("ONNX 모델 로드 완료, 입력:", [i.name for i in session.get_inputs()])`}
                />
              )}

              {useGuideTab === "api" && (
                <CodeViewer
                  language="shell"
                  height={520}
                  copyLabel="REST API 사용법"
                  code={`# ─── 사용 가능한 모델 목록 ───
curl "${serverUrl}/api/v1/oci-models?page=1&page_size=10"

# ─── 모델 상세 조회 ───
curl "${serverUrl}/api/v1/oci-models/my-bert"

# ─── 모델 버전 목록 조회 ───
curl "${serverUrl}/api/v1/oci-models/my-bert/versions"

# ─── 모든 파일의 presigned 다운로드 URL 가져오기 ───
curl "${serverUrl}/api/v1/model-store/my-bert/versions/1/download-urls"
# 응답: {"files": {"config.json": "https://minio/...", "model.safetensors": "https://..."}}

# ─── 특정 파일의 presigned URL 가져오기 ───
curl "${serverUrl}/api/v1/model-store/my-bert/versions/1/download-url?filename=config.json"
# 응답: {"url": "https://minio/...presigned...", "key": "...", "expires_in": 3600}

# 그 URL 로 파일 직접 다운로드:
curl -o config.json "<위에서 받은 presigned URL>"

# ─── S3 버킷 탐색 ───
curl "${serverUrl}/api/v1/model-store/browse/list?path=/my-bert/v1"

# ─── OCI manifest 조회 ───
curl "${serverUrl}/api/v1/model-store/my-bert/versions/1/manifest"`}
                />
              )}
            </div>
          </DialogContent>
        </Dialog>

        {/* Content */}
        {loading ? (
          <div className="text-center py-12 text-muted-foreground">모델을 불러오는 중...</div>
        ) : models.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            모델이 없습니다.{" "}
            <span className="text-primary">HuggingFace 에서 가져오기</span> 또는 SDK 로 푸시하세요.
          </div>
        ) : viewMode === "card" ? (
          /* Card View (3 columns) */
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {models.map((m) => (
              <ModelCard key={m.id} model={m} onClick={() => setSelectedModel(m.name)} />
            ))}
          </div>
        ) : (
          /* List View (table) — 이름 500px / 태스크·프레임워크 150px 고정,
             설명은 word-wrap 으로 자동 줄바꿈 + 행 높이 자동 확장. */
          <div className="border rounded-md overflow-auto">
            <table className="w-full text-sm table-fixed">
              <thead className="bg-muted/60 sticky top-0">
                <tr>
                  <th className="px-3 py-2 text-left font-medium w-[350px]">이름</th>
                  <th className="px-3 py-2 text-left font-medium">설명</th>
                  <th className="px-3 py-2 text-center font-medium w-[150px]">태스크</th>
                  <th className="px-3 py-2 text-center font-medium w-[150px]">프레임워크</th>
                  <th className="px-3 py-2 text-center font-medium w-24">크기</th>
                  <th className="px-3 py-2 text-center font-medium w-16">버전</th>
                  <th className="px-3 py-2 text-center font-medium w-24">출처</th>
                  <th className="px-3 py-2 text-center font-medium w-24">수정</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {models.map((m) => (
                  <tr
                    key={m.id}
                    className="hover:bg-muted/30 cursor-pointer"
                    onClick={() => setSelectedModel(m.name)}
                  >
                    <td className="px-3 py-2 align-top">
                      <div className="flex items-center gap-2">
                        <Box className="h-4 w-4 text-primary shrink-0" />
                        <span className="font-medium truncate" title={m.display_name || m.name}>
                          {m.display_name || m.name}
                        </span>
                      </div>
                    </td>
                    <td className="px-3 py-2 align-top text-muted-foreground whitespace-normal break-words">
                      {m.description || "-"}
                    </td>
                    <td className="px-3 py-2 align-top text-center">{m.task || "-"}</td>
                    <td className="px-3 py-2 align-top text-center">{m.framework || "-"}</td>
                    <td className="px-3 py-2 align-top text-center">{formatSize(m.total_size)}</td>
                    <td className="px-3 py-2 align-top text-center">v{m.version_count}</td>
                    <td className="px-3 py-2 align-top text-center"><SourceBadge sourceType={m.source_type} /></td>
                    <td className="px-3 py-2 align-top text-center text-muted-foreground">{timeAgo(m.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-1 pt-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => load(page - 1, search)}>이전</Button>
            {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
              <Button key={p} variant={p === page ? "default" : "outline"} size="sm" className="w-8 h-8 p-0" onClick={() => load(p, search)}>{p}</Button>
            ))}
            <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => load(page + 1, search)}>다음</Button>
          </div>
        )}
        </div>
          )}
          </TabsContent>
        </Tabs>
      </div>
    </>
  )
}
