"use client"

// Dataset 상세 페이지 — 헤더(요약/상태/origin), 좌측 사이드바(데이터 소스·날짜·소유자·태그·용어집 요약),
// 우측 Tabs(개요/스키마/태그/소유자/용어집/표준 용어/품질/샘플/댓글, 더보기 드롭다운에 리니지·ERD·Avro·이관 코드·이력)
// 를 한 화면에 묶어 제공한다. 인라인 편집, AI 자동 생성, 삭제 확인 다이얼로그를 모두 호스팅하는 페이지 컨테이너.

import { useCallback, useEffect, useMemo, useState } from "react"
import { useParams, useRouter } from "next/navigation"
import dynamic from "next/dynamic"
import { toast } from "sonner"
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  Check,
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Circle,
  Code2,
  Columns3,
  FlaskConical,
  Globe,
  History,
  Loader2,
  Pencil,
  FolderOpen,
  Rocket,
  Search,
  Server,
  Settings2,
  Shield,
  Sparkles,
  Tags,
  Trash2,
  X,
} from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@workspace/ui/components/command"
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
// Textarea removed — replaced by Tiptap MarkdownEditor
import { Popover, PopoverContent, PopoverTrigger } from "@workspace/ui/components/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { Separator } from "@workspace/ui/components/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { DashboardHeader } from "@/components/dashboard-header"
import {
  addDatasetGlossaryTerm,
  addDatasetOwner,
  addDatasetTag,
  deleteDataset,
  fetchDataset,
  fetchDatasourceMetadata,
  type DatasourceMetadata,
  removeDatasetGlossaryTerm,
  removeDatasetOwner,
  removeDatasetTag,
  updateDataset,
  updateDatasetProperties,
  updateDatasetSchema,
} from "@/features/datasets/api"
import { fetchTags } from "@/features/tags/api"
import { fetchGlossaryTerms } from "@/features/glossary/api"
import { fetchUsers } from "@/features/users/api"
import type { User } from "@/features/users/data/schema"
import type { DatasetDetail, GlossaryTerm, SchemaField, Tag } from "@/features/datasets/data/schema"
import { useAuth } from "@/features/auth"
import { SampleDataTab } from "@/features/datasets/components/sample-data-tab"
import { SchemaHistoryTab } from "@/features/datasets/components/schema-history-tab"
import { DatasourceSpecificCard } from "@/features/datasets/components/datasource-specific-card"
import { pathFromUrn } from "@/features/datasets/components/datasets-columns"
import { CommentEditor } from "@/components/comments/comment-editor"
import { RichTextViewer } from "@/components/comments/rich-text-viewer"
import { CodeViewer } from "@/components/code-viewer"
import { SchemaEditGrid, type EditableField } from "@/features/datasets/components/schema-edit-grid"
import { LineageTab } from "@/features/datasets/components/lineage-tab"
import { RelationshipsTab } from "@/features/datasets/components/relationships-tab"
import { ErdTab } from "@/features/datasets/components/erd-tab"
import { usePermissions } from "@/features/permissions/use-permissions"
import { QualityTab } from "@/features/datasets/components/quality-tab"
import { TermsTab } from "@/features/datasets/components/terms-tab"
import { PySparkTab } from "@/features/datasets/components/pyspark-tab"
import { KestraFlowTab } from "@/features/datasets/components/kestra-flow-tab"
import { AirflowDagTab } from "@/features/datasets/components/airflow-dag-tab"
import { DatasetMetadataTab } from "@/features/datasets/components/dataset-metadata-tab"
import { DatasetMetadataSummary } from "@/features/datasets/components/dataset-metadata-summary"
import { fetchDatasetCategories } from "@/features/taxonomy/api"
import {
  generateDescription, generateSummary, generateColumns, suggestTags, detectPII,
  fetchAiStatus, applySuggestion, applySuggestions,
  type DescriptionResult, type ColumnsResult, type TagSuggestionResult, type PIIResult,
} from "@/features/datasets/ai-api"
import { ClipboardCheck, Flame, GitBranch,
  Network, MessageSquare, MoreHorizontal, Wind, Workflow } from "lucide-react"
import { cn } from "@workspace/ui/lib/utils"
import { CommentSection } from "@/components/comments"
import { DatasetChangeRequests } from "@/features/change-mgmt/dataset-change-requests"


// ---------------------------------------------------------------------------
// Schema field helpers for editing
// ---------------------------------------------------------------------------

let _idCounter = 0
function genId(): string {
  return `f-${Date.now()}-${++_idCounter}-${Math.random().toString(36).slice(2, 8)}`
}

function newField(ordinal: number): EditableField {
  return {
    key: genId(),
    field_path: "",
    display_name: "",
    field_type: "STRING",
    native_type: "",
    description: "",
    nullable: "true",
    is_primary_key: "false",
    is_unique: "false",
    is_indexed: "false",
    is_partition_key: "false",
    ordinal,
  }
}

function toEditable(f: SchemaField): EditableField {
  return {
    key: genId(),
    field_path: f.field_path,
    display_name: f.display_name ?? "",
    field_type: f.field_type,
    native_type: f.native_type ?? "",
    description: f.description ?? "",
    nullable: f.nullable,
    is_primary_key: f.is_primary_key ?? "false",
    is_unique: f.is_unique ?? "false",
    is_indexed: f.is_indexed ?? "false",
    is_partition_key: f.is_partition_key ?? "false",
    ordinal: f.ordinal,
  }
}

// ---------------------------------------------------------------------------
// Avro schema generator
// ---------------------------------------------------------------------------
function fieldTypeToAvro(fieldType: string): unknown {
  const t = fieldType.toUpperCase()
  switch (t) {
    case "BOOLEAN":
    case "BOOL":
      return "boolean"
    case "TINYINT":
    case "SMALLINT":
    case "INT":
    case "INT8":
    case "INT16":
    case "INT32":
    case "INTEGER":
    case "MEDIUMINT":
    case "SERIAL":
      return "int"
    case "BIGINT":
    case "INT64":
    case "BIGSERIAL":
    case "LARGEINT":
      return "long"
    case "FLOAT":
    case "FLOAT32":
    case "REAL":
      return "float"
    case "DOUBLE":
    case "DOUBLE PRECISION":
    case "FLOAT64":
      return "double"
    case "DECIMAL":
    case "NUMERIC":
    case "NUMBER":
    case "MONEY":
    case "DECIMAL128":
      return { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }
    case "DATE":
      return { type: "int", logicalType: "date" }
    case "TIME":
      return { type: "long", logicalType: "time-millis" }
    case "TIMESTAMP":
    case "TIMESTAMPTZ":
    case "TIMESTAMP_NTZ":
    case "TIMESTAMP_LTZ":
    case "TIMESTAMP_TZ":
    case "DATETIME":
    case "UNIXTIME_MICROS":
      return { type: "long", logicalType: "timestamp-millis" }
    case "BINARY":
    case "BYTEA":
    case "VARBINARY":
    case "BYTES":
    case "BINDATA":
    case "BLOB":
      return "bytes"
    case "UUID":
      return { type: "string", logicalType: "uuid" }
    case "JSON":
    case "JSONB":
    case "VARIANT":
    case "SUPER":
      return "string"
    case "ARRAY":
      return { type: "array", items: "string" }
    case "MAP":
      return { type: "map", values: "string" }
    case "STRUCT":
    case "ROW":
    case "OBJECT":
      return { type: "record", name: "nested", fields: [] }
    default:
      return "string"
  }
}

function generateAvroSchema(datasetName: string, namespace: string, fields: SchemaField[]): string {
  const avroFields = fields.map((f) => {
    const avroType = fieldTypeToAvro(f.field_type)
    const fieldDef: Record<string, unknown> = {
      name: f.field_path.replace(/[^a-zA-Z0-9_]/g, "_"),
      type: f.nullable === "true" ? ["null", avroType] : avroType,
    }
    if (f.nullable === "true") {
      fieldDef.default = null
    }
    if (f.description) {
      fieldDef.doc = f.description
    }
    return fieldDef
  })

  const schema = {
    type: "record",
    name: datasetName.replace(/[^a-zA-Z0-9_]/g, "_"),
    namespace,
    doc: `Avro schema for ${datasetName}`,
    fields: avroFields,
  }

  return JSON.stringify(schema, null, 2)
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------
export default function DatasetDetailPage() {
  const { user } = useAuth()
  const params = useParams()
  const router = useRouter()
  const datasetId = Number(params.id)
  const [dataset, setDataset] = useState<DatasetDetail | null>(null)
  // 소유권 게이팅 — admin 이거나 데이터셋 생성자(소유자)면 수정/삭제 가능.
  // 서버가 동일 규칙을 강제하므로 UI 는 발견성 용도.
  const canManage = !!user?.is_admin || (!!dataset?.created_by && dataset.created_by === user?.username)
  const [categoryPaths, setCategoryPaths] = useState<string[]>([])  // 매핑된 분류 체계 경로
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // All tags / glossary terms for pickers
  const [allTags, setAllTags] = useState<Tag[]>([])
  const [allGlossary, setAllGlossary] = useState<GlossaryTerm[]>([])
  // owner_name 의 괄호 안 username → 사용자 정보(소속/부서/이메일) 매핑. dataset 로드 후 lookup.
  const [ownerInfo, setOwnerInfo] = useState<
    Record<string, { email?: string; organization?: string; department?: string }>
  >({})

  // Datasource metadata
  const [datasourceMeta, setDatasourceMeta] = useState<DatasourceMetadata | null>(null)

  // Users for owner picker (loaded on search)
  const [ownerSearchUsers, setOwnerSearchUsers] = useState<User[]>([])
  const [ownerSearchQuery, setOwnerSearchQuery] = useState("")
  const [ownerSearching, setOwnerSearching] = useState(false)

  // Popover states
  const [tagPopoverOpen, setTagPopoverOpen] = useState(false)
  const [glossaryPopoverOpen, setGlossaryPopoverOpen] = useState(false)
  const [ownerPopoverOpen, setOwnerPopoverOpen] = useState(false)
  const [ownerType, setOwnerType] = useState("TECHNICAL_OWNER")

  // 활성 탭 — 핵심 6개는 항상 노출, 나머지는 "더보기" 드롭다운에서 선택.
  // 드롭다운 항목을 선택하면 ``setActiveTab`` 으로 ``Tabs`` 의 value 가 갱신된다.
  const [activeTab, setActiveTab] = useState("overview")
  const { isFeatureAllowed } = usePermissions()

  // Schema inline edit state
  const [schemaEditing, setSchemaEditing] = useState(false)
  const [editFields, setEditFields] = useState<EditableField[]>([])
  const [schemaSaving, setSchemaSaving] = useState(false)

  // Status toggling
  const [statusUpdating, setStatusUpdating] = useState(false)

  // Description inline editing — 본문 편집기는 ``CommentEditor`` 가 자체 state 를 가지므로
  // 부모는 편집 모드 진입/저장 중 플래그만 유지한다.
  const [descEditing, setDescEditing] = useState(false)
  const [, setDescSaving] = useState(false)

  // Summary inline editing — 한 줄 요약. 단일 input 이므로 부모가 값을 직접 보관.
  const [summaryEditing, setSummaryEditing] = useState(false)
  const [summaryDraft, setSummaryDraft] = useState("")
  const [summarySaving, setSummarySaving] = useState(false)

  // 논리명(display_name) 인라인 편집 — summary 와 동일 패턴. 빈 값 저장 시 물리명으로 fallback.
  const [logicalEditing, setLogicalEditing] = useState(false)
  const [logicalDraft, setLogicalDraft] = useState("")
  const [logicalSaving, setLogicalSaving] = useState(false)

  const saveLogicalName = async () => {
    try {
      setLogicalSaving(true)
      // 빈 값으로 저장하면 논리명 제거 → 물리명으로 fallback
      const updated = await updateDataset(datasetId, { display_name: logicalDraft.trim() })
      setDataset(updated)
      setLogicalEditing(false)
    } catch (err) {
      console.error("Failed to save dataset logical name", { datasetId, err })
    } finally {
      setLogicalSaving(false)
    }
  }

  const saveSummary = async () => {
    try {
      setSummarySaving(true)
      const updated = await updateDataset(datasetId, { summary: summaryDraft.trim() })
      setDataset(updated)
      setSummaryEditing(false)
    } catch (err) {
      console.error("Failed to save dataset summary", { datasetId, err })
    } finally {
      setSummarySaving(false)
    }
  }

  // Delete confirmation — "삭제" 단어를 정확히 입력해야 destructive 버튼 활성.
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState("")
  const [deleting, setDeleting] = useState(false)

  // type-to-confirm 패턴 — "삭제" 단어를 정확히 입력해야 destructive 호출이 이뤄진다.
  const handleDelete = useCallback(async () => {
    if (!dataset) return
    if (deleteConfirm.trim() !== "삭제") return
    try {
      setDeleting(true)
      await deleteDataset(datasetId)
      console.info("Dataset deleted", { datasetId, name: dataset.name })
      toast.success(`데이터셋 "${dataset.name}" 을 삭제했습니다.`)
      router.push("/dashboard/datasets")
    } catch (e) {
      console.error("Failed to delete dataset", { datasetId, err: e })
      toast.error(e instanceof Error ? e.message : "삭제에 실패했습니다.")
    } finally {
      setDeleting(false)
    }
  }, [dataset, deleteConfirm, datasetId, router])

  // AI generation
  const [aiGenerating, setAiGenerating] = useState(false)
  // 생성 진행 모달에 표시할 작업 종류 — 생성 중에만 값이 있고, 끝나면 null.
  const [aiGenType, setAiGenType] = useState<"description" | "summary" | "columns" | "tags" | "pii" | null>(null)
  // LLM 제공자 활성화 여부 — 꺼져 있으면 AI 자동 생성 메뉴를 비활성화(표시는 유지)
  const [aiEnabled, setAiEnabled] = useState(false)
  useEffect(() => {
    fetchAiStatus().then((st) => setAiEnabled(st.enabled)).catch(() => setAiEnabled(false))
  }, [])
  const [aiDescResult, setAiDescResult] = useState<DescriptionResult | null>(null)
  const [aiSummaryResult, setAiSummaryResult] = useState<{ summary: string; confidence: number } | null>(null)
  const [aiColsResult, setAiColsResult] = useState<ColumnsResult | null>(null)
  const [aiTagsResult, setAiTagsResult] = useState<TagSuggestionResult | null>(null)
  const [aiPiiResult, setAiPiiResult] = useState<PIIResult | null>(null)
  const [aiDialogOpen, setAiDialogOpen] = useState(false)
  const [aiDialogType, setAiDialogType] = useState<"description" | "summary" | "columns" | "tags" | "pii" | null>(null)

  // showLoading=false 로 호출하면 갱신 중 깜빡임 없이 silently refresh — 태그 추가 등 부분 변경 후 사용.
  const load = useCallback(async (showLoading = true) => {
    try {
      if (showLoading) setIsLoading(true)
      const data = await fetchDataset(datasetId)
      setDataset(data)
      // 분류 체계 매핑 경로 — 개요 메타데이터 표 상단에 노출. 실패해도 무시.
      fetchDatasetCategories(datasetId)
        .then((refs) => setCategoryPaths(refs.map((r) => r.path)))
        .catch(() => setCategoryPaths([]))
      // 데이터 소스 메타데이터는 부수 정보 — 실패해도 페이지 자체는 계속 동작해야 한다.
      fetchDatasourceMetadata(data.datasource.id).then(setDatasourceMeta).catch((err) => {
        console.error("Failed to load datasource metadata", { datasourceId: data.datasource.id, err })
      })
    } catch (e) {
      console.error("Failed to load dataset", { datasetId, err: e })
      setError(e instanceof Error ? e.message : "Failed to load dataset")
    } finally {
      if (showLoading) setIsLoading(false)
    }
  }, [datasetId])

  useEffect(() => {
    load()
    fetchTags().then(setAllTags).catch((err) => {
      console.error("Failed to load tags catalog", err)
    })
    fetchGlossaryTerms().then(setAllGlossary).catch((err) => {
      console.error("Failed to load glossary terms catalog", err)
    })
  }, [load])

  // 소유자 tooltip 에 표시할 사용자 정보(소속/부서/이메일)를 username 별로 lookup.
  // owner_name 패턴은 "성+이름 (username)" — 괄호 안 식별자로 fetchUsers 검색.
  useEffect(() => {
    if (!dataset?.owners?.length) return
    const usernames = Array.from(new Set(
      dataset.owners
        .map((o) => o.owner_name.match(/\(([^()]+)\)\s*$/)?.[1]?.trim())
        .filter((u): u is string => Boolean(u))
    ))
    if (usernames.length === 0) return
    const missing = usernames.filter((u) => !(u in ownerInfo))
    if (missing.length === 0) return
    Promise.all(missing.map((u) =>
      fetchUsers({ search: u, pageSize: 0 })
        .then((r) => {
          const exact = r.items.find((it) => it.username === u)
          return exact
            ? [u, { email: exact.email, organization: exact.organization, department: exact.department }] as const
            : null
        })
        .catch(() => null),
    )).then((pairs) => {
      const next: Record<string, { email?: string; organization?: string; department?: string }> = {}
      for (const p of pairs) if (p) next[p[0]] = p[1]
      if (Object.keys(next).length > 0) {
        setOwnerInfo((prev) => ({ ...prev, ...next }))
      }
    })
  }, [dataset?.owners, ownerInfo])

  // 소유자 검색은 300ms debounce — 매 키 입력마다 API 호출되지 않도록 timer 로 묶고
  // cleanup 에서 clearTimeout 으로 이전 예약을 취소한다. (race condition 방지)
  useEffect(() => {
    const trimmed = ownerSearchQuery.trim()
    if (!trimmed) {
      setOwnerSearchUsers([])
      return
    }
    setOwnerSearching(true)
    const timer = setTimeout(() => {
      fetchUsers({ search: trimmed, pageSize: 0 })
        .then((r) => setOwnerSearchUsers(r.items))
        .catch((err) => {
          console.error("Failed to search users for owner picker", { query: trimmed, err })
          setOwnerSearchUsers([])
        })
        .finally(() => setOwnerSearching(false))
    }, 300)
    return () => clearTimeout(timer)
  }, [ownerSearchQuery])

  // -------------------------------------------------------------------------
  // Status change
  // -------------------------------------------------------------------------
  const handleStatusChange = async (newStatus: string) => {
    if (!dataset || statusUpdating || dataset.status === newStatus) return
    try {
      setStatusUpdating(true)
      const updated = await updateDataset(datasetId, { status: newStatus })
      setDataset(updated)
    } catch (err) {
      console.error("Failed to update dataset status", { datasetId, newStatus, err })
    } finally {
      setStatusUpdating(false)
    }
  }

  const statusConfig: { [key: string]: { label: string; icon: React.ReactNode; className: string } } = {
    active: {
      label: "활성",
      icon: <Check className="mr-1.5 h-3.5 w-3.5" />,
      className: "bg-primary text-primary-foreground hover:bg-primary/90",
    },
    inactive: {
      label: "비활성",
      icon: <Circle className="mr-1.5 h-3.5 w-3.5" />,
      className: "bg-amber-500 text-white hover:bg-amber-500/90",
    },
    deprecated: {
      label: "사용 중단",
      icon: <AlertTriangle className="mr-1.5 h-3.5 w-3.5" />,
      className: "bg-zinc-600 text-white hover:bg-zinc-600/90",
    },
  }

  const allStatuses = ["active", "inactive", "deprecated"] as const
  const currentStatusConfig = statusConfig[dataset?.status ?? "active"] ?? statusConfig["active"]

  // Origin (environment) config & handler
  const [originUpdating, setOriginUpdating] = useState(false)

  const originConfig: { [key: string]: { label: string; icon: React.ReactNode; className: string } } = {
    PROD: {
      label: "PROD",
      icon: <Rocket className="mr-1.5 h-3.5 w-3.5" />,
      className: "bg-emerald-600 text-white hover:bg-emerald-600/90",
    },
    STAGING: {
      label: "STAGING",
      icon: <FlaskConical className="mr-1.5 h-3.5 w-3.5" />,
      className: "bg-orange-500 text-white hover:bg-orange-500/90",
    },
    DEV: {
      label: "DEV",
      icon: <Server className="mr-1.5 h-3.5 w-3.5" />,
      className: "bg-sky-500 text-white hover:bg-sky-500/90",
    },
  }

  const allOrigins = ["PROD", "STAGING", "DEV"] as const
  const currentOriginConfig = originConfig[dataset?.origin ?? "PROD"] ?? originConfig["PROD"]

  const handleOriginChange = async (newOrigin: string) => {
    if (!dataset || originUpdating || dataset.origin === newOrigin) return
    try {
      setOriginUpdating(true)
      const updated = await updateDataset(datasetId, { origin: newOrigin })
      setDataset(updated)
    } catch (err) {
      console.error("Failed to update dataset origin", { datasetId, newOrigin, err })
    } finally {
      setOriginUpdating(false)
    }
  }

  // -------------------------------------------------------------------------
  // Description inline editing — 저장 로직은 CommentEditor 의 onSubmit 콜백에 인라인.
  // -------------------------------------------------------------------------
  const startDescEdit = () => setDescEditing(true)
  const cancelDescEdit = () => setDescEditing(false)

  // -------------------------------------------------------------------------
  // AI metadata generation
  // -------------------------------------------------------------------------
  // 단일 진입점에서 type 별 분기. tags/pii 는 즉시 apply(서버에 반영), description/summary/columns
  // 는 우선 미리보기 다이얼로그로 결과를 노출하고 사용자가 "적용" 을 눌러야 반영.
  const handleAiGenerate = async (type: "description" | "summary" | "columns" | "tags" | "pii") => {
    if (!dataset) return
    setAiGenType(type)
    setAiGenerating(true)
    try {
      if (type === "description") {
        const result = await generateDescription(datasetId, { apply: false })
        if (result.skipped) {
          toast.info("이미 설명이 존재합니다.")
        } else {
          setAiDescResult(result)
          setAiDialogType("description")
          setAiDialogOpen(true)
        }
      } else if (type === "summary") {
        // force=true 로 항상 새로 생성 — 사용자가 메뉴를 눌렀다는 것은 재생성 의도.
        const result = await generateSummary(datasetId, { apply: false, force: true })
        if (!result.summary) {
          toast.info("요약을 생성하지 못했습니다.")
        } else {
          setAiSummaryResult({ summary: result.summary, confidence: result.confidence })
          setAiDialogType("summary")
          setAiDialogOpen(true)
        }
      } else if (type === "columns") {
        const result = await generateColumns(datasetId, { apply: false })
        if (result.skipped) {
          toast.info("모든 컬럼에 이미 설명이 있습니다.")
        } else {
          setAiColsResult(result)
          setAiDialogType("columns")
          setAiDialogOpen(true)
        }
      } else if (type === "tags") {
        // apply=false 로 후보만 받아 미리보기 → 사용자가 "적용" 을 눌러야 반영.
        const result = await suggestTags(datasetId, { apply: false })
        if (result.suggested_tags.length === 0 && result.new_tags.length === 0) {
          toast.info("추천할 태그를 찾지 못했습니다.")
        } else {
          setAiTagsResult(result)
          setAiDialogType("tags")
          setAiDialogOpen(true)
        }
      } else if (type === "pii") {
        // apply=false 로 후보만 받아 미리보기 → 사용자가 "적용" 을 눌러야 반영.
        const result = await detectPII(datasetId, { apply: false })
        if (result.pii_columns.length === 0) {
          toast.info("PII 컬럼을 찾지 못했습니다.")
        } else {
          setAiPiiResult(result)
          setAiDialogType("pii")
          setAiDialogOpen(true)
        }
      }
    } catch (e) {
      console.error("AI generation failed", { datasetId, type, err: e })
      toast.error(e instanceof Error ? e.message : "AI 생성에 실패했습니다.")
    } finally {
      setAiGenerating(false)
      setAiGenType(null)
    }
  }

  const handleAiApplyDescription = async () => {
    if (!aiDescResult?.log_id || !dataset) return
    try {
      // applySuggestion 으로 미리 본 결과(log_id)를 그대로 적용 — LLM 재호출 없음.
      await applySuggestion(aiDescResult.log_id)
      setDataset({ ...dataset, description: aiDescResult.description })
      setAiDialogOpen(false)
      setAiDescResult(null)
      toast.success("설명을 적용했습니다.")
    } catch (e) {
      console.error("Failed to apply AI description", { datasetId, err: e })
      toast.error(e instanceof Error ? e.message : "적용에 실패했습니다.")
    }
  }

  const handleAiApplySummary = async () => {
    if (!aiSummaryResult || !dataset) return
    try {
      // updateDataset 으로 직접 적용 — 한 번 더 LLM 을 부르지 않기 위해.
      const updated = await updateDataset(datasetId, { summary: aiSummaryResult.summary })
      setDataset(updated)
      setAiDialogOpen(false)
      setAiSummaryResult(null)
      toast.success("요약을 적용했습니다.")
    } catch (e) {
      console.error("Failed to apply AI summary", { datasetId, err: e })
      toast.error(e instanceof Error ? e.message : "적용에 실패했습니다.")
    }
  }

  const handleAiApplyColumns = async () => {
    if (!aiColsResult) return
    try {
      // applySuggestions 로 미리 본 컬럼 설명(log_id 목록)을 한 번에 적용 — LLM 재호출 없음.
      const logIds = aiColsResult.columns.map((c) => c.log_id).filter((id): id is number => typeof id === "number")
      if (logIds.length === 0) {
        toast.error("적용할 컬럼 설명이 없습니다.")
        return
      }
      const res = await applySuggestions(logIds)
      setAiDialogOpen(false)
      setAiColsResult(null)
      toast.success(`컬럼 설명 ${res.count}개를 적용했습니다.`)
      load(false)
    } catch (e) {
      console.error("Failed to apply AI column descriptions", { datasetId, err: e })
      toast.error(e instanceof Error ? e.message : "적용에 실패했습니다.")
    }
  }

  const handleAiApplyTags = async () => {
    if (!aiTagsResult?.log_id) return
    try {
      // applySuggestion 으로 미리 본 후보(log_id)를 그대로 적용 — LLM 재호출 없음.
      await applySuggestion(aiTagsResult.log_id)
      setAiDialogOpen(false)
      const total = aiTagsResult.suggested_tags.length + aiTagsResult.new_tags.length
      setAiTagsResult(null)
      toast.success(`태그 ${total}개를 적용했습니다.`)
      load(false)
    } catch (e) {
      console.error("Failed to apply AI tags", { datasetId, err: e })
      toast.error(e instanceof Error ? e.message : "적용에 실패했습니다.")
    }
  }

  const handleAiApplyPii = async () => {
    if (!aiPiiResult?.log_id) return
    try {
      // applySuggestion 으로 미리 본 후보(log_id)를 그대로 적용 — LLM 재호출 없음.
      await applySuggestion(aiPiiResult.log_id)
      setAiDialogOpen(false)
      const count = aiPiiResult.pii_columns.length
      setAiPiiResult(null)
      toast.success(`PII 컬럼 ${count}개를 적용했습니다.`)
      load(false)
    } catch (e) {
      console.error("Failed to apply AI PII", { datasetId, err: e })
      toast.error(e instanceof Error ? e.message : "적용에 실패했습니다.")
    }
  }

  // -------------------------------------------------------------------------
  // Schema inline editing
  // -------------------------------------------------------------------------
  const startSchemaEdit = () => {
    if (!dataset) return
    setEditFields(
      dataset.schema_fields.length > 0
        ? dataset.schema_fields.map(toEditable)
        : [newField(0)]
    )
    setSchemaEditing(true)
  }

  const cancelSchemaEdit = () => {
    setSchemaEditing(false)
    setEditFields([])
  }

  // 빈 필드 path/type 행은 저장에서 제외. ordinal 은 클라이언트 표시 순서를 그대로 부여한다.
  const saveSchema = async () => {
    const valid = editFields.filter((f) => f.field_path.trim() && f.field_type.trim())
    const payload = valid.map((f, idx) => ({
      field_path: f.field_path.trim(),
      display_name: f.display_name.trim() || undefined,
      field_type: f.field_type.trim(),
      native_type: f.native_type.trim() || undefined,
      description: f.description.trim() || undefined,
      nullable: f.nullable,
      is_primary_key: f.is_primary_key,
      is_unique: f.is_unique,
      is_indexed: f.is_indexed,
      is_partition_key: f.is_partition_key,
      ordinal: idx,
    }))
    try {
      setSchemaSaving(true)
      await updateDatasetSchema(datasetId, payload)
      setSchemaEditing(false)
      await load(false)
    } catch (err) {
      // 오류 시에는 편집 모드를 유지해 사용자가 입력을 잃지 않도록 한다.
      console.error("Failed to save schema", { datasetId, fields: payload.length, err })
    } finally {
      setSchemaSaving(false)
    }
  }

  // Datasource data type options for the Type dropdown
  const dataTypeOptions = datasourceMeta?.data_types ?? []
  const featuresMeta = datasourceMeta?.features ?? []

  // -------------------------------------------------------------------------
  // Tag management
  // -------------------------------------------------------------------------
  const handleAddTag = async (tagId: number) => {
    try {
      await addDatasetTag(datasetId, tagId)
      setTagPopoverOpen(false)
      await load(false)
    } catch (err) {
      console.error("Failed to add tag to dataset", { datasetId, tagId, err })
    }
  }

  const handleRemoveTag = async (tagId: number) => {
    try {
      await removeDatasetTag(datasetId, tagId)
      await load(false)
    } catch (err) {
      console.error("Failed to remove tag from dataset", { datasetId, tagId, err })
    }
  }

  // -------------------------------------------------------------------------
  // Glossary management
  // -------------------------------------------------------------------------
  const handleAddGlossary = async (termId: number) => {
    try {
      await addDatasetGlossaryTerm(datasetId, termId)
      setGlossaryPopoverOpen(false)
      await load(false)
    } catch (err) {
      console.error("Failed to add glossary term to dataset", { datasetId, termId, err })
    }
  }

  const handleRemoveGlossary = async (termId: number) => {
    try {
      await removeDatasetGlossaryTerm(datasetId, termId)
      await load(false)
    } catch (err) {
      console.error("Failed to remove glossary term from dataset", { datasetId, termId, err })
    }
  }

  // -------------------------------------------------------------------------
  // Owner management
  // -------------------------------------------------------------------------
  const handleAddOwner = async (user: User) => {
    try {
      // 표시 일관성: "성+이름 (USERNAME)" 형식으로 저장.
      const fullName = `${user.lastName ?? ""}${user.firstName ?? ""}`.trim()
      const ownerName = fullName
        ? `${fullName} (${user.username})`
        : user.username
      await addDatasetOwner(datasetId, {
        owner_name: ownerName,
        owner_type: ownerType,
      })
      setOwnerPopoverOpen(false)
      setOwnerSearchQuery("")
      setOwnerSearchUsers([])
      await load(false)
    } catch (err) {
      console.error("Failed to add owner to dataset", { datasetId, username: user.username, err })
    }
  }

  const handleRemoveOwner = async (ownerId: number) => {
    try {
      await removeDatasetOwner(datasetId, ownerId)
      await load(false)
    } catch (err) {
      console.error("Failed to remove owner from dataset", { datasetId, ownerId, err })
    }
  }

  // -------------------------------------------------------------------------
  // Derived data
  // -------------------------------------------------------------------------
  const attachedTagIds = new Set(dataset?.tags.map((t) => t.id) ?? [])
  const availableTags = allTags.filter((t) => !attachedTagIds.has(t.id))

  const attachedTermIds = new Set(dataset?.glossary_terms.map((t) => t.id) ?? [])
  const availableGlossary = allGlossary.filter((t) => !attachedTermIds.has(t.id))
  const availableTermsOnly = availableGlossary.filter((t) => (t.term_type ?? "TERM") === "TERM")

  // 검색 결과는 그대로 노출하되, 이미 추가된 사용자는 disabled 처리.
  // owner_name 저장 포맷이 시점별로 다르므로(옛: "firstName lastName",
  // 새: "lastName+firstName (username)") 가능한 모든 형태와 비교.
  const isOwnerAttached = (u: User): boolean => {
    const newKor = `${u.lastName ?? ""}${u.firstName ?? ""}`.trim()
    const oldEn = `${u.firstName ?? ""} ${u.lastName ?? ""}`.trim()
    const newFmt = newKor ? `${newKor} (${u.username})` : u.username
    return (dataset?.owners ?? []).some((o) => {
      const n = o.owner_name
      if (!n) return false
      if (n.includes(`(${u.username})`)) return true
      return n === u.username || n === newKor || n === oldEn || n === newFmt
    })
  }
  const availableUsers = ownerSearchUsers

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  if (isLoading) {
    return (
      <>
        <DashboardHeader title="데이터셋" />
        <div className="flex items-center justify-center p-8">
          <p className="text-muted-foreground">데이터셋을 불러오는 중...</p>
        </div>
      </>
    )
  }

  if (error || !dataset) {
    return (
      <>
        <DashboardHeader title="데이터셋" />
        <div className="flex flex-col items-center justify-center gap-4 p-8">
          <p className="text-destructive">{error || "데이터셋을 찾을 수 없습니다."}</p>
          <Button variant="outline" onClick={() => router.back()}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            데이터셋 목록으로
          </Button>
        </div>
      </>
    )
  }

  return (
    <>
      {/* logical name 이 있으면 헤더는 logical 그대로, 없으면 정규화 경로(name=database.schema.table)를 대문자로. */}
      <DashboardHeader
        title={dataset.display_name || dataset.name.toUpperCase()}
      />
      <div className="flex flex-1 flex-col gap-4 p-4">
        {/* Back link — 모델 상세와 동일한 패턴으로 데이터셋 목록 복귀 명시. */}
        <Button
          variant="ghost"
          size="sm"
          className="h-7 -ml-2 self-start px-2 text-muted-foreground hover:text-foreground"
          onClick={() => router.back()}
        >
          <ArrowLeft className="h-4 w-4 mr-1" />
          데이터셋 목록으로
        </Button>

        {/* Dataset header info */}
        <Card>
          <CardHeader>
            <div className="flex items-start justify-between">
              <div className="space-y-1 min-w-0 flex-1">
                {/* 논리명(display_name) — 클릭하면 인라인 편집. 논리명이 있으면 "논리명 (물리명)" 형식으로 노출.
                    없으면 물리명을 대문자로 노출(클릭 시 논리명 입력). */}
                {logicalEditing ? (
                  <div className="flex items-center gap-2">
                    <Input
                      className="h-9 w-1/2 text-lg font-semibold"
                      value={logicalDraft}
                      maxLength={255}
                      autoFocus
                      placeholder={`논리명 (예: ${dataset.name})`}
                      onChange={(e) => setLogicalDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") {
                          e.preventDefault()
                          setLogicalEditing(false) // 원상태 복귀(저장 안 함)
                        } else if (e.key === "Enter") {
                          e.preventDefault()
                          void saveLogicalName()
                        }
                      }}
                      disabled={logicalSaving}
                    />
                    <Button size="sm" variant="outline" className="h-8" onClick={() => void saveLogicalName()} disabled={logicalSaving}>
                      저장
                    </Button>
                    <Button size="sm" variant="outline" className="h-8" onClick={() => setLogicalEditing(false)} disabled={logicalSaving}>
                      취소
                    </Button>
                  </div>
                ) : (
                  <CardTitle
                    className={`group/title flex items-center gap-2 text-xl cursor-pointer rounded-md px-2 py-0.5 -mx-2 hover:bg-muted transition-colors ${dataset.display_name ? "" : "uppercase"}`}
                    title="클릭하여 논리명 편집"
                    onClick={() => {
                      setLogicalDraft(dataset.display_name ?? "")
                      setLogicalEditing(true)
                    }}
                  >
                    {dataset.display_name ? (
                      <span>
                        {dataset.display_name}
                        <span className="ml-1.5 text-base font-normal uppercase text-muted-foreground" title="물리명 (physical name)">
                          ({dataset.name})
                        </span>
                      </span>
                    ) : (
                      dataset.name
                    )}
                    {dataset.is_synced === "true" && (
                      <span className="inline-flex items-center rounded-full border border-orange-400 px-2 py-0.5 text-[10px] font-semibold text-orange-500">
                        동기화됨
                      </span>
                    )}
                    <Pencil className="h-3.5 w-3.5 shrink-0 opacity-0 transition-opacity group-hover/title:opacity-60" />
                  </CardTitle>
                )}
                {/* 한 줄 요약 — 클릭하면 인라인 편집. 빈 값이면 placeholder 형태로 노출. */}
                {summaryEditing ? (
                  <div className="flex items-center gap-2">
                    <Input
                      className="h-7 w-full text-sm"
                      value={summaryDraft}
                      maxLength={200}
                      autoFocus
                      placeholder="예: 매장 주문 트랜잭션"
                      onChange={(e) => setSummaryDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") {
                          e.preventDefault()
                          setSummaryEditing(false) // 원상태 복귀(저장 안 함)
                        } else if (e.key === "Enter") {
                          e.preventDefault()
                          void saveSummary()
                        }
                      }}
                      disabled={summarySaving}
                    />
                    <span className="text-[11px] text-muted-foreground whitespace-nowrap">
                      {summaryDraft.length}/200
                    </span>
                    <Button size="sm" variant="outline" className="h-7" onClick={() => void saveSummary()} disabled={summarySaving}>
                      저장
                    </Button>
                    <Button size="sm" variant="outline" className="h-7" onClick={() => setSummaryEditing(false)} disabled={summarySaving}>
                      취소
                    </Button>
                  </div>
                ) : dataset.summary ? (
                  <p
                    className="text-sm cursor-pointer rounded-md px-2 py-0.5 -mx-2 hover:bg-muted transition-colors"
                    title="클릭하여 편집"
                    onClick={() => {
                      setSummaryDraft(dataset.summary ?? "")
                      setSummaryEditing(true)
                    }}
                  >
                    {dataset.summary}
                  </p>
                ) : (
                  <p
                    className="text-sm italic text-muted-foreground cursor-pointer rounded-md px-2 py-0.5 -mx-2 hover:bg-muted transition-colors"
                    title="클릭하여 요약 작성"
                    onClick={() => {
                      setSummaryDraft("")
                      setSummaryEditing(true)
                    }}
                  >
                    한 줄 요약을 추가하세요
                  </p>
                )}
                <p className="text-sm text-muted-foreground font-mono">
                  {dataset.urn}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {/* 읽기 전용 chip — 항상 노출. admin 도 동일하게 본다. 변경은 우측 ⋮ 메뉴. */}
                <Badge className={currentStatusConfig?.className}>{currentStatusConfig?.label}</Badge>
                <Badge className={currentOriginConfig?.className}>{currentOriginConfig?.label}</Badge>
                {dataset.is_synced === "true" && (
                  <span className="inline-flex items-center rounded-full border border-orange-400 px-2 py-0.5 text-[10px] font-semibold text-orange-500">
                    동기화됨
                  </span>
                )}
                {canManage && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button size="sm" variant="outline" className="h-8 w-8 p-0" aria-label="작업">
                        <MoreHorizontal className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-56">
                      {/* AI 자동 생성 — LLM 제공자 비활성 시 표시는 하되 클릭 불가 */}
                      <DropdownMenuLabel className="text-xs text-muted-foreground">
                        AI 자동 생성{!aiEnabled && " (LLM 비활성 — 설정에서 활성화)"}
                      </DropdownMenuLabel>
                      <DropdownMenuItem onClick={() => handleAiGenerate("summary")} disabled={aiGenerating || !aiEnabled}>
                        <Sparkles className="h-4 w-4 mr-2" /> 요약 생성
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleAiGenerate("description")} disabled={aiGenerating || !aiEnabled}>
                        <Sparkles className="h-4 w-4 mr-2" /> 설명 생성
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleAiGenerate("columns")} disabled={aiGenerating || !aiEnabled}>
                        <Columns3 className="h-4 w-4 mr-2" /> 컬럼 설명 생성
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleAiGenerate("tags")} disabled={aiGenerating || !aiEnabled}>
                        <Tags className="h-4 w-4 mr-2" /> 태그 추천
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleAiGenerate("pii")} disabled={aiGenerating || !aiEnabled}>
                        <Shield className="h-4 w-4 mr-2" /> PII 감지
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      {/* 상태 변경 */}
                      <DropdownMenuLabel className="text-xs text-muted-foreground">상태 변경</DropdownMenuLabel>
                      {allStatuses.filter((s) => s !== dataset.status).map((s) => {
                        const cfg = statusConfig[s]
                        return (
                          <DropdownMenuItem key={s} onClick={() => handleStatusChange(s)} disabled={statusUpdating}>
                            {cfg?.icon}
                            <span>{cfg?.label}</span>
                          </DropdownMenuItem>
                        )
                      })}
                      <DropdownMenuSeparator />
                      {/* 환경 변경 */}
                      <DropdownMenuLabel className="text-xs text-muted-foreground">환경 변경</DropdownMenuLabel>
                      {allOrigins.filter((o) => o !== dataset.origin).map((o) => {
                        const cfg = originConfig[o]
                        return (
                          <DropdownMenuItem key={o} onClick={() => handleOriginChange(o)} disabled={originUpdating}>
                            {cfg?.icon}
                            <span>{cfg?.label}</span>
                          </DropdownMenuItem>
                        )
                      })}
                      <DropdownMenuSeparator />
                      {/* 삭제 */}
                      <DropdownMenuItem
                        onClick={() => { setDeleteConfirm(""); setDeleteOpen(true) }}
                        className="text-destructive focus:text-destructive"
                      >
                        <Trash2 className="h-4 w-4 mr-2" /> 데이터셋 삭제
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </div>
            </div>
          </CardHeader>
          {/* 본문 영역은 "개요" 탭 으로 이동했다. 헤더는 title row 만 유지. */}
        </Card>

        {/* 본문 — 탭 콘텐츠 (메타 정보는 개요 탭 메타데이터 표로 통합) */}
        <div>
          {/* 사이드바(데이터 소스·날짜·소유자·태그·용어집) 제거 — 개요 탭 메타데이터 표로 통합 */}

          {/* ----- Main: Tabs ----- */}
          <div className="min-w-0">
        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1">
          <TabsList>
            <TabsTrigger value="overview" className="gap-1.5">
              <BookOpen className="h-4 w-4" />
              개요
            </TabsTrigger>
            <TabsTrigger value="metadata" className="gap-1.5">
              <Settings2 className="h-4 w-4" />
              메타데이터
            </TabsTrigger>
            {/* DDL 컬럼이 채워져 있을 때만 노출 — 어댑터마다 fetch 지원이 달라 모든
                dataset 에 있지는 않다. */}
            {dataset.ddl && (
              <TabsTrigger value="ddl" className="gap-1.5">
                <Code2 className="h-4 w-4" />
                DDL
              </TabsTrigger>
            )}
            <TabsTrigger value="schema" className="gap-1.5">
              <Columns3 className="h-4 w-4" />
              스키마 ({dataset.schema_fields.length})
            </TabsTrigger>
            {/* 태그·소유자·용어집 탭은 "메타데이터" 탭의 분류·소유 영역으로 통합됨 */}
            <TabsTrigger value="terms" className="gap-1.5">
              <Shield className="h-4 w-4" />
              표준 용어
            </TabsTrigger>
            <TabsTrigger value="quality" className="gap-1.5">
              <CheckCircle className="h-4 w-4" />
              품질
            </TabsTrigger>
            {/* 샘플 데이터는 실데이터 노출 — 기능 권한(datasets.sample-view)으로 통제 */}
            {isFeatureAllowed("datasets.sample-view") && (
              <TabsTrigger value="sample" className="gap-1.5">
                <Globe className="h-4 w-4" />
                샘플
              </TabsTrigger>
            )}
            <TabsTrigger value="comments" className="gap-1.5">
              <MessageSquare className="h-4 w-4" />
              댓글
            </TabsTrigger>

            {/* 더보기 드롭다운 — 리니지 / ER 다이어그램 / Avro / PySpark / Kestra / Airflow / 변경 이력.
               ER 다이어그램·PySpark·Kestra·Airflow 탭은 RDBMS 데이터 소스에서만 노출한다
               (ERD 는 DDL FK 파싱 기반, 이관 코드는 JDBC 접속 가정 — Kafka/S3 등에서는 misleading). */}
            {(() => {
              const RDBMS_TYPES = ["mysql", "mariadb", "postgresql", "greenplum", "oracle", "mssql", "sqlserver", "starrocks", "tibero"]
              const isRdbms = RDBMS_TYPES.includes(dataset.datasource.type)
              const overflowTabs: { value: string; label: string; icon: React.ReactNode }[] = [
                { value: "lineage", label: "리니지",   icon: <GitBranch className="h-4 w-4" /> },
                { value: "relationships", label: "관계", icon: <Network className="h-4 w-4" /> },
                ...(isRdbms ? [
                  { value: "erd", label: "ER 다이어그램", icon: <Network className="h-4 w-4" /> },
                ] : []),
                { value: "avro",    label: "Avro",     icon: <Code2 className="h-4 w-4" /> },
                ...(isRdbms && isFeatureAllowed("datasets.transfer-code") ? [
                  { value: "pyspark", label: "PySpark", icon: <Flame className="h-4 w-4" /> },
                  { value: "kestra",  label: "Kestra",  icon: <Workflow className="h-4 w-4" /> },
                  { value: "airflow", label: "Airflow", icon: <Wind className="h-4 w-4" /> },
                ] : []),
                { value: "history", label: "변경 이력", icon: <History className="h-4 w-4" /> },
                { value: "changes", label: "변경 요청", icon: <ClipboardCheck className="h-4 w-4" /> },
              ]
              const activeOverflow = overflowTabs.find((t) => t.value === activeTab)
              const isOverflowActive = !!activeOverflow
              return (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button
                      type="button"
                      data-active={isOverflowActive || undefined}
                      className="relative inline-flex h-[calc(100%-1px)] items-center justify-center gap-1.5 rounded-md border border-transparent px-2 py-1 text-sm font-medium whitespace-nowrap text-foreground/60 transition-all hover:text-foreground data-active:bg-background data-active:text-foreground data-active:shadow-sm focus-visible:outline-1 focus-visible:outline-ring"
                    >
                      {activeOverflow ? (
                        <>
                          {activeOverflow.icon}
                          {activeOverflow.label}
                        </>
                      ) : (
                        <>
                          <MoreHorizontal className="h-4 w-4" />
                          더보기
                        </>
                      )}
                      <ChevronDown className="h-3 w-3 opacity-60" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="w-44">
                    {overflowTabs.map((t) => (
                      <DropdownMenuItem
                        key={t.value}
                        onClick={() => setActiveTab(t.value)}
                        className={cn(
                          "gap-2",
                          activeTab === t.value && "bg-muted font-medium",
                        )}
                      >
                        {t.icon}
                        {t.label}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              )
            })()}
          </TabsList>

          {/* =============== Overview tab — 메타데이터 요약 + description 인라인 편집기 =============== */}
          <TabsContent value="overview" className="mt-4">
            <DatasetMetadataSummary dataset={dataset} categoryPaths={categoryPaths} ownerInfo={ownerInfo}>
              {descEditing ? (
                <CommentEditor
                  key={`desc-edit-${datasetId}-${dataset.updated_at}`}
                  initialContent={dataset.description ?? ""}
                  showCategory={false}
                  placeholder="설명을 작성하세요..."
                  submitLabel="저장"
                  autoFocus
                  onCancel={cancelDescEdit}
                  onSubmit={async (html) => {
                    try {
                      setDescSaving(true)
                      const trimmed = html.trim()
                      const updated = await updateDataset(datasetId, {
                        description: trimmed || undefined,
                      })
                      setDataset(updated)
                      setDescEditing(false)
                    } catch (err) {
                      console.error("Failed to save dataset description", { datasetId, err })
                    } finally {
                      setDescSaving(false)
                    }
                  }}
                />
              ) : dataset.description ? (
                <div
                  className="cursor-pointer rounded-md px-2 py-1 -mx-2 hover:bg-muted transition-colors"
                  onClick={startDescEdit}
                  title="클릭하여 편집"
                >
                  <RichTextViewer html={dataset.description} />
                </div>
              ) : (
                <p
                  className="text-sm cursor-pointer rounded-md px-2 py-1 -mx-2 hover:bg-muted transition-colors text-muted-foreground italic"
                  onClick={startDescEdit}
                  title="클릭하여 편집"
                >
                  클릭해서 설명을 추가할 수 있습니다.
                </p>
              )}
            </DatasetMetadataSummary>
          </TabsContent>

          {/* =============== Metadata tab — 확장 메타데이터 편집 =============== */}
          <TabsContent value="metadata" className="mt-4">
            <DatasetMetadataTab
              dataset={dataset}
              onSaved={setDataset}
              extraGroup={{
                name: "분류 · 소유",
                rows: [
                  {
                    label: "소유자",
                    desc: "데이터 소유자(기술/업무 책임자·스튜어드). 즉시 반영됩니다.",
                    control: (
                      <div className="flex flex-col gap-2">
                        <div className="flex flex-wrap gap-1.5">
                          {dataset.owners.length > 0 ? (
                            dataset.owners.map((o) => (
                              <Badge key={o.id} variant="secondary" className="gap-1 text-xs">
                                {o.owner_name}
                                <span className="text-[10px] text-muted-foreground">· {o.owner_type.replaceAll("_", " ")}</span>
                                <button className="ml-0.5 hover:opacity-70" onClick={() => handleRemoveOwner(o.id)} aria-label="소유자 제거">
                                  <X className="h-3 w-3" />
                                </button>
                              </Badge>
                            ))
                          ) : (
                            <span className="text-xs text-muted-foreground">없음</span>
                          )}
                        </div>
                        <div className="flex items-center gap-1.5">
                          <Select value={ownerType} onValueChange={setOwnerType}>
                            <SelectTrigger size="sm" className="h-7 w-[140px] text-xs"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="TECHNICAL_OWNER">기술 소유자</SelectItem>
                              <SelectItem value="BUSINESS_OWNER">비즈니스 소유자</SelectItem>
                              <SelectItem value="DATA_STEWARD">데이터 스튜어드</SelectItem>
                            </SelectContent>
                          </Select>
                          <Popover
                            open={ownerPopoverOpen}
                            onOpenChange={(open) => { setOwnerPopoverOpen(open); if (!open) { setOwnerSearchQuery(""); setOwnerSearchUsers([]) } }}
                          >
                            <PopoverTrigger asChild>
                              <Button size="sm" variant="outline" className="h-7 w-[45px] px-0">추가</Button>
                            </PopoverTrigger>
                            <PopoverContent className="p-0" align="start">
                              <Command shouldFilter={false}>
                                <CommandInput placeholder="사용자 검색..." value={ownerSearchQuery} onValueChange={setOwnerSearchQuery} />
                                <CommandList>
                                  {ownerSearchQuery.trim() === "" ? (
                                    <div className="p-4 text-center text-sm text-muted-foreground">이름을 입력해 사용자를 검색하세요</div>
                                  ) : ownerSearching ? (
                                    <div className="p-4 text-center text-sm text-muted-foreground">검색 중...</div>
                                  ) : availableUsers.length === 0 ? (
                                    <CommandEmpty>사용자를 찾을 수 없습니다</CommandEmpty>
                                  ) : (
                                    <CommandGroup>
                                      {availableUsers.map((u) => {
                                        const fullName = `${u.lastName ?? ""}${u.firstName ?? ""}`.trim() || u.username
                                        const attached = isOwnerAttached(u)
                                        return (
                                          <CommandItem key={u.id} value={u.id} disabled={attached} onSelect={() => { if (!attached) handleAddOwner(u) }}>
                                            <span className="font-medium">{fullName} ({u.username})</span>
                                            {attached && <span className="ml-auto text-[10px] text-muted-foreground">이미 추가됨</span>}
                                          </CommandItem>
                                        )
                                      })}
                                    </CommandGroup>
                                  )}
                                </CommandList>
                              </Command>
                            </PopoverContent>
                          </Popover>
                        </div>
                      </div>
                    ),
                  },
                  {
                    label: "태그",
                    desc: "분류·검색용 태그. 즉시 반영됩니다.",
                    control: (
                      <div className="flex flex-col gap-2">
                        <div className="flex flex-wrap gap-1.5">
                          {dataset.tags.length > 0 ? (
                            dataset.tags.map((t) => (
                              <Badge key={t.id} style={{ backgroundColor: t.color, color: "#fff" }} className="gap-1 text-xs">
                                {t.name}
                                <button className="ml-0.5 hover:opacity-70" onClick={() => handleRemoveTag(t.id)} aria-label="태그 제거">
                                  <X className="h-3 w-3" />
                                </button>
                              </Badge>
                            ))
                          ) : (
                            <span className="text-xs text-muted-foreground">없음</span>
                          )}
                        </div>
                        <Popover open={tagPopoverOpen} onOpenChange={setTagPopoverOpen}>
                          <PopoverTrigger asChild>
                            <Button size="sm" variant="outline" className="h-7 w-[45px] px-0" disabled={availableTags.length === 0}>추가</Button>
                          </PopoverTrigger>
                          <PopoverContent className="p-0" align="start">
                            <Command>
                              <CommandInput placeholder="태그 검색..." />
                              <CommandList>
                                <CommandEmpty>태그가 없습니다</CommandEmpty>
                                <CommandGroup>
                                  {availableTags.map((tag) => (
                                    <CommandItem key={tag.id} value={tag.name} onSelect={() => handleAddTag(tag.id)}>
                                      <span className="mr-2 inline-block h-3 w-3 shrink-0 rounded-full" style={{ backgroundColor: tag.color }} />
                                      {tag.name}
                                    </CommandItem>
                                  ))}
                                </CommandGroup>
                              </CommandList>
                            </Command>
                          </PopoverContent>
                        </Popover>
                      </div>
                    ),
                  },
                  {
                    label: "용어집",
                    desc: "연결된 비즈니스 용어. 즉시 반영됩니다.",
                    control: (
                      <div className="flex flex-col gap-2">
                        <div className="flex flex-wrap gap-1.5">
                          {dataset.glossary_terms.length > 0 ? (
                            dataset.glossary_terms.map((g) => (
                              <Badge key={g.id} variant="secondary" className="gap-1 text-xs">
                                {g.name}
                                <button className="ml-0.5 hover:opacity-70" onClick={() => handleRemoveGlossary(g.id)} aria-label="용어 제거">
                                  <X className="h-3 w-3" />
                                </button>
                              </Badge>
                            ))
                          ) : (
                            <span className="text-xs text-muted-foreground">없음</span>
                          )}
                        </div>
                        <GlossaryTermPicker
                          open={glossaryPopoverOpen}
                          onOpenChange={setGlossaryPopoverOpen}
                          allGlossary={allGlossary}
                          availableTerms={availableTermsOnly}
                          onSelect={handleAddGlossary}
                        />
                      </div>
                    ),
                  },
                ],
              }}
            />
          </TabsContent>

          {/* =============== Schema tab =============== */}
          {/* =============== DDL tab =============== */}
          {dataset.ddl && (
            <TabsContent value="ddl" className="mt-4">
              <Card>
                <CardHeader className="py-3">
                  <CardTitle className="text-base flex items-center gap-2">
                    <Code2 className="h-4 w-4" />
                    CREATE TABLE DDL
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {/* 줄 수에 맞춰 높이만 잡고 최대 600px 까지. read-only + SQL syntax. */}
                  <CodeViewer
                    code={dataset.ddl}
                    language="sql"
                    height={Math.min(dataset.ddl.split("\n").length * 20 + 40, 600)}
                    copyLabel="DDL"
                  />
                </CardContent>
              </Card>
            </TabsContent>
          )}

          <TabsContent value="schema" className="mt-4">
            <Card>
              <div className="flex justify-end px-4 pt-3">
                {schemaEditing ? (
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={cancelSchemaEdit}
                      disabled={schemaSaving}
                    >
                      Cancel
                    </Button>
                    <Button
                      size="sm"
                      onClick={saveSchema}
                      disabled={schemaSaving}
                    >
                      {schemaSaving ? "Updating..." : "Update"}
                    </Button>
                  </div>
                ) : user?.is_admin ? (
                  <Button size="sm" variant="outline" onClick={startSchemaEdit}>
                    <Pencil className="mr-1 h-3.5 w-3.5" />
                    스키마 수정
                  </Button>
                ) : null}
              </div>
              <CardContent className="p-0">
                {schemaEditing ? (
                  /* ---------- AG Grid edit mode ---------- */
                  <div>
                    <SchemaEditGrid
                      fields={editFields}
                      onChange={setEditFields}
                      dataTypeOptions={dataTypeOptions.map(dt => dt.type_name)}
                    />

                    {/* Datasource features section */}
                    {featuresMeta.length > 0 && (
                      <>
                        <Separator className="my-4" />
                        <div className="space-y-3">
                          <p className="text-xs font-medium text-muted-foreground">
                            Datasource Features ({dataset.datasource.name})
                          </p>
                          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                            {featuresMeta.map((feat) => {
                              const existing = dataset.properties?.find(
                                (p) => p.property_key === feat.feature_key
                              )
                              return (
                                <div key={feat.feature_key} className="grid gap-1">
                                  <label className="text-xs font-medium flex items-center gap-1">
                                    {feat.display_name}
                                    {feat.is_required === "true" && (
                                      <span className="text-destructive">*</span>
                                    )}
                                  </label>
                                  {feat.description && (
                                    <p className="text-xs text-muted-foreground truncate">
                                      {feat.description}
                                    </p>
                                  )}
                                  <Input
                                    placeholder={
                                      feat.value_type === "number"
                                        ? "0"
                                        : feat.value_type === "column_list"
                                          ? "col1, col2"
                                          : feat.value_type === "boolean"
                                            ? "true / false"
                                            : ""
                                    }
                                    defaultValue={existing?.property_value ?? ""}
                                    className="h-8 text-sm"
                                    disabled
                                  />
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                ) : dataset.schema_fields.length > 0 ? (
                  /* ---------- Read-only view ---------- */
                  <>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-[50px]">#</TableHead>
                          <TableHead>필드</TableHead>
                          <TableHead>논리명</TableHead>
                          <TableHead>타입</TableHead>
                          <TableHead>네이티브 타입</TableHead>
                          <TableHead className="w-[50px] text-center">PK</TableHead>
                          <TableHead className="w-[50px] text-center">고유</TableHead>
                          <TableHead className="w-[50px] text-center">인덱스</TableHead>
                          <TableHead className="w-[50px] text-center">파티션</TableHead>
                          <TableHead className="w-[50px] text-center">분산</TableHead>
                          <TableHead className="w-[50px] text-center">Null</TableHead>
                          <TableHead>설명</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {dataset.schema_fields.map((field, idx) => (
                          <TableRow key={field.id}>
                            <TableCell className="text-muted-foreground">
                              {idx + 1}
                            </TableCell>
                            <TableCell className="text-sm font-[family-name:var(--font-d2coding)]">
                              {field.field_path}
                            </TableCell>
                            <TableCell className="text-sm text-muted-foreground">
                              {field.display_name || "-"}
                            </TableCell>
                            <TableCell>
                              <Badge variant="secondary" className="font-mono text-xs">
                                {field.field_type}
                              </Badge>
                            </TableCell>
                            {/* native_type 이 25 자를 초과하면 끝에 ``…`` 을 붙이고 전체 값을
                                title(tooltip) 로 노출. */}
                            <TableCell className="text-sm font-[family-name:var(--font-d2coding)] uppercase">
                              {field.native_type ? (
                                <span title={field.native_type}>
                                  {field.native_type.length > 25
                                    ? `${field.native_type.slice(0, 25)}…`
                                    : field.native_type}
                                </span>
                              ) : (
                                "-"
                              )}
                            </TableCell>
                            <TableCell className="text-center">
                              {field.is_primary_key === "true" && (
                                <Check className="h-4 w-4 text-primary mx-auto" />
                              )}
                            </TableCell>
                            <TableCell className="text-center">
                              {field.is_unique === "true" && (
                                <Check className="h-4 w-4 text-primary mx-auto" />
                              )}
                            </TableCell>
                            <TableCell className="text-center">
                              {field.is_indexed === "true" && (
                                <Check className="h-4 w-4 text-muted-foreground mx-auto" />
                              )}
                            </TableCell>
                            <TableCell className="text-center">
                              {field.is_partition_key === "true" && (
                                <Check className="h-4 w-4 text-orange-500 mx-auto" />
                              )}
                            </TableCell>
                            <TableCell className="text-center">
                              {field.is_distribution_key === "true" && (
                                <Check className="h-4 w-4 text-blue-500 mx-auto" />
                              )}
                            </TableCell>
                            <TableCell className="text-center">
                              {field.nullable === "true" && (
                                <Check className="h-4 w-4 text-muted-foreground mx-auto" />
                              )}
                            </TableCell>
                            <TableCell className="text-sm min-w-[200px] max-w-[500px]">
                              <span className="whitespace-pre-wrap break-words">
                                {field.description || "-"}
                              </span>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </>
                ) : (
                  <div className="flex items-center justify-center p-8">
                    <p className="text-muted-foreground">No schema fields defined.</p>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Datasource Specific — inside Schema tab */}
            {(dataset.datasource_properties || (dataset.properties && dataset.properties.length > 0)) && (
              <div className="mt-4"><DatasourceSpecificCard
                datasourceType={dataset.datasource.type}
                properties={dataset.datasource_properties || {}}
                datasetProperties={dataset.properties}
                datasetName={dataset.name}
              /></div>
            )}
          </TabsContent>

          {/* =============== Sample tab =============== */}
          <TabsContent value="sample" className="mt-4">
            <SampleDataTab datasetId={datasetId} isSynced={dataset.is_synced === "true"} isAdmin={!!user?.is_admin} />
          </TabsContent>

          {/* =============== Avro tab =============== */}
          <TabsContent value="avro" className="mt-4">
            <AvroSchemaCard dataset={dataset} onUpdated={setDataset} />
          </TabsContent>

          {/* =============== PySpark tab — RDBMS → S3 이관 코드 (RDBMS 한정) =============== */}
          <TabsContent value="pyspark" className="mt-4">
            <PySparkTab dataset={dataset} />
          </TabsContent>

          {/* =============== Kestra tab — 적재 워크플로우 YAML (RDBMS 한정) =============== */}
          <TabsContent value="kestra" className="mt-4">
            <KestraFlowTab dataset={dataset} />
          </TabsContent>

          {/* =============== Airflow tab — 적재 DAG (RDBMS 한정) =============== */}
          <TabsContent value="airflow" className="mt-4">
            <AirflowDagTab dataset={dataset} />
          </TabsContent>

          {/* =============== History tab =============== */}
          <TabsContent value="history" className="mt-4">
            <SchemaHistoryTab datasetId={datasetId} />
          </TabsContent>

          {/* =============== Lineage tab =============== */}
          {/* =============== Terms tab =============== */}
          <TabsContent value="terms" className="mt-4">
            <TermsTab datasetId={datasetId} />
          </TabsContent>

          {/* =============== Quality tab =============== */}
          <TabsContent value="quality" className="mt-4">
            <QualityTab datasetId={datasetId} columns={dataset.schema_fields.map((f) => f.field_path)} />
          </TabsContent>

          <TabsContent value="lineage" className="mt-4">
            <LineageTab datasetId={datasetId} datasetName={dataset.name} />
          </TabsContent>

          <TabsContent value="relationships" className="mt-4">
            <RelationshipsTab datasetId={datasetId} />
          </TabsContent>

          <TabsContent value="erd" className="mt-4">
            <ErdTab datasetId={datasetId} />
          </TabsContent>

          {/* =============== Comments tab =============== */}
          <TabsContent value="changes" className="mt-4">
            <DatasetChangeRequests datasetId={dataset.id} />
          </TabsContent>

          <TabsContent value="comments" className="mt-4">
            <CommentSection
              entityType="dataset"
              entityId={String(datasetId)}
            />
          </TabsContent>
        </Tabs>
          </div>
        </div>

      </div>

      {/* Delete Dataset Dialog — type-to-confirm */}
      <Dialog open={deleteOpen} onOpenChange={(o) => { if (!o) { setDeleteOpen(false); setDeleteConfirm("") } }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-destructive">
              <AlertTriangle className="h-5 w-5" />
              데이터셋 삭제
            </DialogTitle>
            <DialogDescription>
              이 작업은 되돌릴 수 없습니다. 데이터셋과 관련된 스키마/속성/태그/소유자/
              리니지 정보가 모두 함께 삭제됩니다.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-1">
            <div className="rounded-md border bg-muted/40 p-3 text-sm">
              <p className="font-medium">{dataset.name}</p>
              <p className="mt-0.5 text-xs text-muted-foreground font-mono break-all">
                {dataset.urn}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="dataset-delete-confirm" className="text-sm">
                계속하려면 <span className="font-mono font-semibold text-destructive">삭제</span> 를 입력하세요.
              </Label>
              <Input
                id="dataset-delete-confirm"
                value={deleteConfirm}
                onChange={(e) => setDeleteConfirm(e.target.value)}
                placeholder="삭제"
                autoComplete="off"
                autoFocus
              />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="outline" onClick={() => { setDeleteOpen(false); setDeleteConfirm("") }} disabled={deleting}>
                취소
              </Button>
              <Button variant="destructive" onClick={handleDelete} disabled={deleteConfirm.trim() !== "삭제" || deleting}>
                {deleting ? "삭제 중..." : "삭제"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* AI 생성 진행 모달 — 생성 중에만 표시. 닫기 불가(showCloseButton=false)이며,
          생성이 끝나면 aiGenerating=false 로 자동 닫히고 요약/설명/컬럼은 아래 미리보기로 이어진다. */}
      <Dialog open={aiGenerating}>
        <DialogContent className="max-w-sm" showCloseButton={false}>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4" />
              AI 생성 중
            </DialogTitle>
            <DialogDescription>
              {({ description: "설명", summary: "요약", columns: "컬럼 설명", tags: "태그", pii: "PII" }[aiGenType ?? "summary"])}
              을(를) 생성하고 있습니다. 잠시만 기다려 주세요.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-1">
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div className="ac-progress-indeterminate h-full w-2/5 rounded-full bg-primary" />
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              LLM 응답을 기다리는 중…
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* AI Summary Preview Dialog */}
      <Dialog open={aiDialogOpen && aiDialogType === "summary"} onOpenChange={(open) => { if (!open) { setAiDialogOpen(false); setAiSummaryResult(null) } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4" />
              AI 생성 요약
            </DialogTitle>
          </DialogHeader>
          {aiSummaryResult && (
            <div className="space-y-4">
              <div className="rounded-md border p-3 text-sm bg-muted/50">
                {aiSummaryResult.summary}
              </div>
              <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <span>신뢰도: {(aiSummaryResult.confidence * 100).toFixed(0)}%</span>
                <span>{aiSummaryResult.summary.length}자</span>
              </div>
              <div className="flex items-center gap-2 justify-end">
                <Button variant="outline" size="sm" onClick={() => { setAiDialogOpen(false); setAiSummaryResult(null) }}>
                  닫기
                </Button>
                <Button size="sm" onClick={handleAiApplySummary}>
                  <Check className="h-4 w-4 mr-1" />
                  적용
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* AI Description Preview Dialog */}
      <Dialog open={aiDialogOpen && aiDialogType === "description"} onOpenChange={(open) => { if (!open) { setAiDialogOpen(false); setAiDescResult(null) } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4" />
              AI 생성 설명
            </DialogTitle>
          </DialogHeader>
          {aiDescResult && (
            <div className="space-y-4">
              <div className="rounded-md border p-3 text-sm whitespace-pre-wrap bg-muted/50">
                {aiDescResult.description}
              </div>
              <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <span>신뢰도: {(aiDescResult.confidence * 100).toFixed(0)}%</span>
              </div>
              <div className="flex items-center gap-2 justify-end">
                <Button variant="outline" size="sm" onClick={() => { setAiDialogOpen(false); setAiDescResult(null) }}>
                  닫기
                </Button>
                <Button size="sm" onClick={handleAiApplyDescription}>
                  <Check className="h-4 w-4 mr-1" />
                  적용
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* AI Column Descriptions Preview Dialog */}
      <Dialog open={aiDialogOpen && aiDialogType === "columns"} onOpenChange={(open) => { if (!open) { setAiDialogOpen(false); setAiColsResult(null) } }}>
        <DialogContent className="max-w-2xl max-h-[70vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4" />
              AI 생성 컬럼 설명
            </DialogTitle>
          </DialogHeader>
          {aiColsResult && (
            <div className="space-y-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[150px]">컬럼</TableHead>
                    <TableHead>생성된 설명</TableHead>
                    <TableHead className="w-[60px] text-right">신뢰도</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {aiColsResult.columns.map((col) => (
                    <TableRow key={col.field_path}>
                      <TableCell className="font-mono text-xs">{col.field_path}</TableCell>
                      <TableCell className="text-sm">{col.description}</TableCell>
                      <TableCell className="text-right text-xs text-muted-foreground">{(col.confidence * 100).toFixed(0)}%</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <div className="flex items-center gap-2 justify-end">
                <Button variant="outline" size="sm" onClick={() => { setAiDialogOpen(false); setAiColsResult(null) }}>
                  닫기
                </Button>
                <Button size="sm" onClick={handleAiApplyColumns}>
                  <Check className="h-4 w-4 mr-1" />
                  전체 적용 ({aiColsResult.total_generated})
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* AI Tag Suggestion Preview Dialog */}
      <Dialog open={aiDialogOpen && aiDialogType === "tags"} onOpenChange={(open) => { if (!open) { setAiDialogOpen(false); setAiTagsResult(null) } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Tags className="h-4 w-4" />
              AI 추천 태그
            </DialogTitle>
            <DialogDescription>
              적용 시 기존 태그는 할당되고, 새 태그는 생성 후 할당됩니다.
            </DialogDescription>
          </DialogHeader>
          {aiTagsResult && (
            <div className="space-y-4">
              {aiTagsResult.suggested_tags.length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-xs font-medium text-muted-foreground">기존 태그 ({aiTagsResult.suggested_tags.length})</div>
                  <div className="flex flex-wrap gap-1.5">
                    {aiTagsResult.suggested_tags.map((t) => (
                      <Badge key={t} variant="secondary">{t}</Badge>
                    ))}
                  </div>
                </div>
              )}
              {aiTagsResult.new_tags.length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-xs font-medium text-muted-foreground">새 태그 ({aiTagsResult.new_tags.length})</div>
                  <div className="flex flex-col gap-1.5">
                    {aiTagsResult.new_tags.map((t) => (
                      <div key={t.name} className="flex items-center gap-2 text-sm">
                        <Badge>{t.name}</Badge>
                        {t.description && <span className="text-xs text-muted-foreground">{t.description}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex items-center gap-2 justify-end">
                <Button variant="outline" size="sm" onClick={() => { setAiDialogOpen(false); setAiTagsResult(null) }}>
                  닫기
                </Button>
                <Button size="sm" onClick={handleAiApplyTags}>
                  <Check className="h-4 w-4 mr-1" />
                  적용 ({aiTagsResult.suggested_tags.length + aiTagsResult.new_tags.length})
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* AI PII Detection Preview Dialog */}
      <Dialog open={aiDialogOpen && aiDialogType === "pii"} onOpenChange={(open) => { if (!open) { setAiDialogOpen(false); setAiPiiResult(null) } }}>
        <DialogContent className="max-w-2xl max-h-[70vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Shield className="h-4 w-4" />
              AI PII 감지 결과
            </DialogTitle>
            <DialogDescription>
              적용 시 해당 컬럼의 PII 유형이 설정됩니다.
            </DialogDescription>
          </DialogHeader>
          {aiPiiResult && (
            <div className="space-y-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[150px]">컬럼</TableHead>
                    <TableHead className="w-[120px]">PII 유형</TableHead>
                    <TableHead>근거</TableHead>
                    <TableHead className="w-[60px] text-right">신뢰도</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {aiPiiResult.pii_columns.map((col) => (
                    <TableRow key={col.name}>
                      <TableCell className="font-mono text-xs">{col.name}</TableCell>
                      <TableCell><Badge variant="secondary">{col.pii_type}</Badge></TableCell>
                      <TableCell className="text-sm">{col.reason}</TableCell>
                      <TableCell className="text-right text-xs text-muted-foreground">{(col.confidence * 100).toFixed(0)}%</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <div className="flex items-center gap-2 justify-end">
                <Button variant="outline" size="sm" onClick={() => { setAiDialogOpen(false); setAiPiiResult(null) }}>
                  닫기
                </Button>
                <Button size="sm" onClick={handleAiApplyPii}>
                  <Check className="h-4 w-4 mr-1" />
                  적용 ({aiPiiResult.pii_columns.length})
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

    </>
  )
}

// ---------------------------------------------------------------------------
// Avro Schema Card with line numbers
// ---------------------------------------------------------------------------
// 사용자 편집본을 보관할 키. dataset_properties 에 저장하므로 ``argus.`` prefix 로
// 다른 sync 어댑터(iceberg.*, hive.*) 의 키와 안전하게 격리.
const AVRO_OVERRIDE_KEY = "argus.avro_schema"

// 외부에서 Avro 스키마를 가져가는 방법 (curl / wget / Python). OCI 모델 허브의 사용법
// 다이얼로그와 동일한 형태로, dataset URN/ID 기반의 ``/api/v1/external/avro-schema``
// 라우트를 호출하는 코드를 제시한다.
function AvroFetchGuide({ urn, datasetId }: { urn: string; datasetId: number }) {
  const [open, setOpen] = useState(false)
  const [origin, setOrigin] = useState("http://localhost:4600")
  useEffect(() => {
    if (typeof window !== "undefined") setOrigin(window.location.origin)
  }, [])
  const encUrn = encodeURIComponent(urn)
  const byUrn = `${origin}/api/v1/external/avro-schema?urn=${encUrn}`
  const byId = `${origin}/api/v1/external/datasets/${datasetId}/avro-schema`

  const curlCode = [
    "# URN 으로 조회 (외부 시스템에 권장 — 데이터셋 식별자가 안정적)",
    `curl -s "${byUrn}" | jq .`,
    "",
    "# 내부 dataset_id 로도 동일하게 조회 가능",
    `curl -s "${byId}" | jq .`,
    "",
    "# 캐시 우회가 필요할 때(편집 직후 등)",
    `curl -s "${byUrn}&no_cache=true" | jq .`,
  ].join("\n")

  const wgetCode = [
    "# URN 으로 조회",
    `wget -qO- "${byUrn}"`,
    "",
    "# 파일로 저장",
    `wget -O dataset.avsc "${byUrn}"`,
  ].join("\n")

  const pythonCode = [
    "import json, urllib.parse, urllib.request",
    "",
    `BASE = "${origin}"`,
    `URN  = "${urn}"`,
    "",
    "# 1) URN 으로 Avro 스키마 가져오기",
    'url = f"{BASE}/api/v1/external/avro-schema?urn=" + urllib.parse.quote(URN)',
    "with urllib.request.urlopen(url, timeout=10) as resp:",
    "    avro = json.load(resp)",
    "print(json.dumps(avro, indent=2, ensure_ascii=False))",
    "",
    "# 2) 자동 캐싱됨 — 동일 URN 재호출 시 < 1ms",
    "#    캐시 우회: 같은 URL 에 ``&no_cache=true`` 추가",
  ].join("\n")

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" title="외부 시스템에서 가져오는 방법">
          <Code2 className="mr-1 h-3.5 w-3.5" />
          가져오기 가이드
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Code2 className="h-4 w-4" />
            Avro 스키마 가져오기
          </DialogTitle>
          <DialogDescription>
            외부 시스템(Kafka producer/consumer, Spark/Flink job 등) 에서
            <code className="mx-1 rounded bg-muted px-1 py-0.5 text-xs">/api/v1/external/avro-schema</code>
            라우트로 받아갈 수 있습니다. 캐시가 켜져 있어 첫 호출 이후엔 1ms 미만이 일반적입니다.
          </DialogDescription>
        </DialogHeader>
        <Tabs defaultValue="curl">
          <TabsList>
            <TabsTrigger value="curl">curl</TabsTrigger>
            <TabsTrigger value="wget">wget</TabsTrigger>
            <TabsTrigger value="python">Python</TabsTrigger>
          </TabsList>
          <TabsContent value="curl" className="mt-2">
            <CodeViewer code={curlCode} language="shell" height={240} copyLabel="curl" />
          </TabsContent>
          <TabsContent value="wget" className="mt-2">
            <CodeViewer code={wgetCode} language="shell" height={160} copyLabel="wget" />
          </TabsContent>
          <TabsContent value="python" className="mt-2">
            <CodeViewer code={pythonCode} language="python" height={280} copyLabel="Python" />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

const AvroMonacoEditor = dynamic(
  () => import("@monaco-editor/react").then((m) => m.default),
  { ssr: false, loading: () => <div className="p-8 text-sm text-muted-foreground">에디터 로딩…</div> },
)

function AvroSchemaCard({
  dataset,
  onUpdated,
}: {
  dataset: DatasetDetail
  onUpdated: (updated: DatasetDetail) => void
}) {
  // 1) 데이터셋 정의로부터 매번 새로 만드는 "원본" — 초기화 시 돌아갈 기준.
  // namespace 는 datasource_id 만, name 은 URN 의 path (``db.schema.table`` 등) 를 사용.
  // Avro spec 상 name 은 dot 을 허용하지 않으므로 generateAvroSchema 가 ``.`` 를
  // ``_`` 로 sanitize 한다 (예: ``argus_test.sales.orders`` → ``argus_test_sales_orders``).
  const generated = useMemo(() => {
    if (dataset.schema_fields.length === 0) return ""
    const path = pathFromUrn(dataset.urn) || dataset.name
    return generateAvroSchema(path, dataset.datasource.datasource_id, dataset.schema_fields)
  }, [dataset.urn, dataset.name, dataset.datasource.datasource_id, dataset.schema_fields])

  // 2) 사용자가 저장한 override 가 있으면 그것을 우선 표시.
  const override = useMemo(() => {
    const prop = (dataset.properties ?? []).find((p) => p.property_key === AVRO_OVERRIDE_KEY)
    return prop?.property_value ?? ""
  }, [dataset.properties])

  const effective = override || generated

  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<string>(effective)
  const [saving, setSaving] = useState(false)
  const [resetting, setResetting] = useState(false)

  // dataset 가 외부에서 갱신될 때마다 draft 도 동기화. 단, 편집 중에는 사용자 입력 보존.
  useEffect(() => {
    if (!editing) setDraft(effective)
  }, [effective, editing])

  const lineCount = useMemo(() => effective.split("\n").length, [effective])
  const isJsonValid = useMemo(() => {
    if (!editing) return true
    try { JSON.parse(draft); return true } catch { return false }
  }, [draft, editing])

  // ``argus.avro_schema`` 키만 새 값으로 두고 나머지 properties 는 그대로 유지하며 전체 교체.
  // 서버는 properties 를 전량 덮어쓰는 시맨틱이므로 mutator 패턴으로 기존 키들을 보존한다.
  const writeProperties = useCallback(
    async (mutator: (props: { key: string; value: string }[]) => { key: string; value: string }[]) => {
      const current = (dataset.properties ?? []).map((p) => ({
        key: p.property_key,
        value: p.property_value,
      }))
      const next = mutator(current)
      await updateDatasetProperties(dataset.id, next)
      const updated = await fetchDataset(dataset.id)
      onUpdated(updated)
    },
    [dataset.properties, dataset.id, onUpdated],
  )

  const handleSave = useCallback(async () => {
    if (!isJsonValid) {
      toast.error("유효한 JSON 이 아닙니다.")
      return
    }
    try {
      setSaving(true)
      await writeProperties((props) => {
        const filtered = props.filter((p) => p.key !== AVRO_OVERRIDE_KEY)
        return [...filtered, { key: AVRO_OVERRIDE_KEY, value: draft }]
      })
      setEditing(false)
      toast.success("Avro 스키마를 저장했습니다.")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "저장에 실패했습니다.")
    } finally {
      setSaving(false)
    }
  }, [draft, isJsonValid, writeProperties])

  const handleReset = useCallback(async () => {
    try {
      setResetting(true)
      await writeProperties((props) => props.filter((p) => p.key !== AVRO_OVERRIDE_KEY))
      setEditing(false)
      toast.success("자동 생성 스키마로 초기화했습니다.")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "초기화에 실패했습니다.")
    } finally {
      setResetting(false)
    }
  }, [writeProperties])

  if (!effective) {
    return (
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-base">Avro 스키마</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">
            스키마 필드를 먼저 정의하면 Avro 스키마가 자동 생성됩니다.
          </p>
        </CardContent>
      </Card>
    )
  }

  const editorHeight = Math.min(lineCount * 20 + 40, 600)

  return (
    <Card>
      <CardHeader className="py-3 flex flex-row items-center justify-between">
        <CardTitle className="text-base flex items-center gap-2">
          Avro 스키마
          {override && !editing && (
            <span className="rounded border px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
              사용자 편집본
            </span>
          )}
        </CardTitle>
        <div className="flex items-center gap-2">
          {!editing && (
            <AvroFetchGuide urn={dataset.urn} datasetId={dataset.id} />
          )}
          {editing ? (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => { setEditing(false); setDraft(effective) }}
                disabled={saving}
              >
                취소
              </Button>
              <Button
                size="sm"
                onClick={handleSave}
                disabled={saving || !isJsonValid || draft === effective}
              >
                {saving ? "저장 중..." : "저장"}
              </Button>
            </>
          ) : (
            <>
              {override && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleReset}
                  disabled={resetting}
                  title="자동 생성 스키마로 되돌립니다"
                >
                  {resetting ? "초기화 중..." : "초기화"}
                </Button>
              )}
              <Button size="sm" variant="outline" onClick={() => { setDraft(effective); setEditing(true) }}>
                <Pencil className="mr-1 h-3.5 w-3.5" />
                편집
              </Button>
            </>
          )}
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {editing ? (
          <>
            <AvroMonacoEditor
              height={editorHeight}
              language="json"
              theme="vs-dark"
              value={draft}
              onChange={(v) => setDraft(v ?? "")}
              options={{
                fontFamily: "var(--font-d2coding), 'D2Coding', Consolas, monospace",
                fontSize: 13,
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                wordWrap: "on",
              }}
            />
            {!isJsonValid && (
              <p className="border-t bg-destructive/10 px-3 py-1.5 text-xs text-destructive">
                유효하지 않은 JSON 입니다.
              </p>
            )}
          </>
        ) : (
          <CodeViewer
            code={effective}
            language="json"
            height={editorHeight}
            copyLabel="Avro Schema"
            className="border-t rounded-none"
          />
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Glossary Term Picker — tree-based folder navigation
// ---------------------------------------------------------------------------

type GlossaryTreeNode = GlossaryTerm & { children: GlossaryTreeNode[]; depth: number }

function buildGlossaryTree(terms: GlossaryTerm[]): GlossaryTreeNode[] {
  const map = new Map<number, GlossaryTreeNode>()
  const roots: GlossaryTreeNode[] = []
  for (const t of terms) map.set(t.id, { ...t, children: [], depth: 0 })
  for (const node of map.values()) {
    if (node.parent_id && map.has(node.parent_id)) {
      map.get(node.parent_id)!.children.push(node)
    } else {
      roots.push(node)
    }
  }
  const setDepth = (nodes: GlossaryTreeNode[], d: number) => {
    for (const n of nodes) { n.depth = d; setDepth(n.children, d + 1) }
  }
  setDepth(roots, 0)
  const sortNodes = (nodes: GlossaryTreeNode[]) => {
    nodes.sort((a, b) => a.name.localeCompare(b.name))
    for (const n of nodes) sortNodes(n.children)
  }
  sortNodes(roots)
  return roots
}

function collectGlossaryIds(node: GlossaryTreeNode): number[] {
  const ids = [node.id]
  for (const c of node.children) ids.push(...collectGlossaryIds(c))
  return ids
}

function GlossaryTermPicker({
  open,
  onOpenChange,
  allGlossary,
  availableTerms,
  onSelect,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  allGlossary: GlossaryTerm[]
  availableTerms: GlossaryTerm[]
  onSelect: (termId: number) => void
}) {
  const [selectedFolderId, setSelectedFolderId] = useState<number | null>(null)
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())
  const [search, setSearch] = useState("")

  const categories = allGlossary.filter(t => (t.term_type ?? "TERM") === "CATEGORY")
  const catTree = useMemo(() => buildGlossaryTree(categories), [categories])
  const catMap = useMemo(() => {
    const m = new Map<number, GlossaryTreeNode>()
    const walk = (nodes: GlossaryTreeNode[]) => { for (const n of nodes) { m.set(n.id, n); walk(n.children) } }
    walk(catTree)
    return m
  }, [catTree])

  const visibleCats = useMemo(() => {
    const result: GlossaryTreeNode[] = []
    const walk = (nodes: GlossaryTreeNode[]) => {
      for (const n of nodes) {
        if ((n.term_type ?? "TERM") !== "CATEGORY") continue
        result.push(n)
        if (expandedIds.has(n.id)) walk(n.children)
      }
    }
    walk(catTree)
    return result
  }, [catTree, expandedIds])

  const filteredTerms = useMemo(() => {
    let list = availableTerms
    if (selectedFolderId) {
      // Collect all CATEGORY ids under selected folder (including itself)
      const node = catMap.get(selectedFolderId)
      if (node) {
        const folderIds = new Set(collectGlossaryIds(node))
        // Filter terms whose parent_id is one of these folders
        list = list.filter(t => t.parent_id != null && folderIds.has(t.parent_id))
      }
    }
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(t => t.name.toLowerCase().includes(q) || (t.description ?? "").toLowerCase().includes(q))
    }
    return list
  }, [availableTerms, selectedFolderId, catMap, search])

  const toggleExpand = (id: number) => {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <Button size="sm" variant="outline" className="h-7 w-[45px] px-0" disabled={availableTerms.length === 0}>
          추가
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[520px] p-0" align="end">
        <div className="flex h-[340px]">
          {/* Left: folder tree */}
          <div className="w-[180px] border-r overflow-y-auto py-1">
            <button
              type="button"
              onClick={() => setSelectedFolderId(null)}
              className={`w-full text-left flex items-center gap-1.5 px-2 py-1.5 text-sm transition-colors ${
                selectedFolderId === null ? "bg-primary/10 text-primary font-medium" : "hover:bg-muted/50"
              }`}
            >
              <BookOpen className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
              All
            </button>
            {visibleCats.map(node => {
              const hasCatChildren = node.children.some(c => (c.term_type ?? "TERM") === "CATEGORY")
              const isExp = expandedIds.has(node.id)
              const isSel = selectedFolderId === node.id
              return (
                <div
                  key={node.id}
                  className={`flex items-center gap-1 pr-1 transition-colors ${isSel ? "bg-primary/10" : "hover:bg-muted/50"}`}
                  style={{ paddingLeft: `${node.depth * 14 + 6}px` }}
                >
                  <button
                    type="button"
                    className={`shrink-0 p-0.5 ${hasCatChildren ? "cursor-pointer" : "invisible"}`}
                    onClick={() => hasCatChildren && toggleExpand(node.id)}
                  >
                    <ChevronRight className={`h-3 w-3 transition-transform ${isExp ? "rotate-90" : ""}`} />
                  </button>
                  <button
                    type="button"
                    onClick={() => setSelectedFolderId(node.id)}
                    className={`flex-1 min-w-0 text-left truncate text-sm py-1 flex items-center gap-1 ${isSel ? "text-primary font-medium" : ""}`}
                  >
                    <FolderOpen className="h-3.5 w-3.5 text-amber-500 shrink-0" />
                    {node.name}
                  </button>
                </div>
              )
            })}
          </div>

          {/* Right: term list */}
          <div className="flex-1 flex flex-col min-h-0">
            <div className="px-2 py-1.5 border-b">
              <div className="relative">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                <Input
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  placeholder="용어 검색..."
                  className="h-7 pl-7 text-sm"
                />
              </div>
            </div>
            <div className="flex-1 overflow-y-auto">
              {filteredTerms.length === 0 ? (
                <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
                  사용 가능한 용어가 없습니다
                </div>
              ) : (
                filteredTerms.map(term => (
                  <button
                    key={term.id}
                    type="button"
                    onClick={() => { onSelect(term.id); onOpenChange(false) }}
                    className="w-full text-left px-3 py-2 hover:bg-muted/50 transition-colors border-b last:border-b-0"
                  >
                    <span className="text-sm font-medium">{term.name}</span>
                    {term.description && (
                      <p className="text-xs text-muted-foreground line-clamp-1 mt-0.5">{term.description}</p>
                    )}
                  </button>
                ))
              )}
            </div>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
