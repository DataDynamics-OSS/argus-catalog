"use client"

import { useCallback, useEffect, useState } from "react"
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  Check,
  CheckCircle2,
  Code2,
  Columns3,
  DownloadCloud,
  GitBranch,
  Globe,
  Loader2,
} from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@workspace/ui/components/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@workspace/ui/components/tabs"
import { CodeViewer } from "@/components/code-viewer"
import { DatasetMetadataSummary } from "@/features/datasets/components/dataset-metadata-summary"
import type { DatasetDetail } from "@/features/datasets/data/schema"

import {
  datasetDetail,
  datasetLineage,
  datasetSample,
  importDataset,
  type FederatedDatasetDetail,
  type FederatedDatasetSchemaField,
  type FederatedLineageGraph,
  type FederatedSample,
} from "./api"
import { LineageBody, SourceBadge } from "./search-panel"

const MONO = "font-[family-name:var(--font-d2coding)]"

// 스키마 표 불리언 컬럼 — capability 키 / 헤더 / Check 색상
const SCHEMA_FLAG_COLS: {
  key: string
  label: string
  field: keyof FederatedDatasetSchemaField
  color: string
}[] = [
  { key: "schema.pk", label: "PK", field: "is_primary_key", color: "text-primary" },
  { key: "schema.unique", label: "고유", field: "is_unique", color: "text-primary" },
  { key: "schema.indexed", label: "인덱스", field: "is_indexed", color: "text-muted-foreground" },
  { key: "schema.partition", label: "파티션", field: "is_partition_key", color: "text-orange-500" },
  { key: "schema.distribution", label: "분산", field: "is_distribution_key", color: "text-blue-500" },
  { key: "schema.nullable", label: "Null", field: "nullable", color: "text-muted-foreground" },
]

/**
 * 페더레이션 상세(meta/extended)를 로컬 ``DatasetDetail`` 형태로 변환한다.
 * 이렇게 하면 로컬 데이터셋 상세와 **완전히 동일한** 메타데이터 표(라벨/값 한글화/
 * 상단 데이터셋명·요약설명·URN 행/툴팁)를 ``DatasetMetadataSummary`` 로 그대로 렌더할 수 있다.
 * 소비자 표시 선택(display_fields)으로 노출 불가한 필드는 비워(=표에서 자동 숨김) 거버넌스를 유지한다.
 */
function toLocalDetail(detail: FederatedDatasetDetail, urn: string): DatasetDetail {
  const meta = detail.metadata ?? {}
  const display = detail.display_fields
  const show = (k: string) => !display || display.includes(k)
  // 확장 메타데이터는 노출자 노출 ∩ 소비자 선택(extended)일 때만 채워진다.
  const ext: Record<string, unknown> =
    show("extended") && meta.extended ? meta.extended : {}

  const str = (v: unknown): string | null =>
    typeof v === "string" ? v : v == null ? null : String(v)
  const num = (v: unknown): number | null =>
    typeof v === "number" ? v : v == null || v === "" ? null : Number(v)
  const bool = (v: unknown): boolean | null =>
    typeof v === "boolean"
      ? v
      : v === "true"
        ? true
        : v === "false"
          ? false
          : null

  const owners = show("owners")
    ? (meta.owners ?? []).map((o, i) => ({
        id: i,
        dataset_id: 0,
        owner_name: o.name,
        owner_type: o.type,
        created_at: "",
      }))
    : []
  const tags = show("tags")
    ? (meta.tags ?? []).map((t, i) => ({
        id: i,
        name: t,
        color: "#64748b",
        created_at: "",
      }))
    : []
  const glossary = show("glossary")
    ? (meta.glossary ?? []).map((t, i) => ({ id: i, name: t, created_at: "" }))
    : []

  return {
    id: 0,
    urn: detail.federated_urn ?? meta.urn ?? urn,
    name: meta.name ?? "",
    // 논리명(예: 배우)은 확장 메타데이터에 담겨 옴. 없으면 표/제목에서 물리명으로 대체.
    display_name: str(ext.display_name),
    datasource: {
      id: 0,
      datasource_id: "",
      name: meta.datasource?.name ?? "",
      type: meta.datasource?.type ?? "",
      origin: meta.origin ?? "DEV",
      created_at: "",
    },
    summary: str(ext.summary),
    description: show("description") ? str(meta.description) : null,
    origin: show("origin") ? (meta.origin ?? "") : "",
    status: show("status") ? (meta.status ?? "") : "",
    is_synced: str(meta.is_synced) ?? "false",
    view_count: num(ext.view_count) ?? 0,
    query_count: num(ext.query_count) ?? 0,
    // 확장 메타데이터 (키가 로컬 DatasetDetail 과 1:1 일치)
    ingestion_frequency: str(ext.ingestion_frequency),
    ingestion_time: str(ext.ingestion_time),
    ingestion_day: str(ext.ingestion_day),
    ingestion_timezone: str(ext.ingestion_timezone),
    ingestion_cron: str(ext.ingestion_cron),
    ingestion_mode: str(ext.ingestion_mode),
    update_type: str(ext.update_type),
    freshness_sla: str(ext.freshness_sla),
    last_ingested_at: str(ext.last_ingested_at),
    retention_days: num(ext.retention_days),
    purge_days: num(ext.purge_days),
    data_category: str(ext.data_category),
    data_format: str(ext.data_format),
    compression: str(ext.compression),
    encoding: str(ext.encoding),
    row_count: num(ext.row_count),
    byte_size: num(ext.byte_size),
    file_count: num(ext.file_count),
    sensitivity: str(ext.sensitivity),
    contains_pii: bool(ext.contains_pii),
    pii_fields: str(ext.pii_fields),
    compliance_tags: str(ext.compliance_tags),
    tier: str(ext.tier),
    certification: str(ext.certification),
    quality_score: num(ext.quality_score),
    quality_status: str(ext.quality_status),
    note: str(ext.note),
    tags,
    owners,
    glossary_terms: glossary,
    schema_fields: [],
    // federation 메타에 등록/수정 일시가 없으면 표에서 "—"(숨김)
    created_at: undefined as unknown as Date,
    updated_at: undefined as unknown as Date,
    // 형태 변환 브리지 — DatasetMetadataSummary 가 읽는 필드는 모두 null-가드되어 있어
    // 일부 중첩 스키마(term_type 등)를 생략해도 런타임 안전하다.
  } as unknown as DatasetDetail
}

/** 페더레이션(원격) 데이터셋 상세 — 로컬 데이터셋 상세와 동일한 모양(읽기 전용). */
export function FederationDatasetDetailView({ urn }: { urn: string }) {
  const router = useRouter()
  const [detail, setDetail] = useState<FederatedDatasetDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [lineage, setLineage] = useState<FederatedLineageGraph | null>(null)
  const [lineageLoading, setLineageLoading] = useState(false)
  const [lineageError, setLineageError] = useState<string | null>(null)

  const [sample, setSample] = useState<FederatedSample | null>(null)
  const [sampleLoading, setSampleLoading] = useState(false)
  const [sampleError, setSampleError] = useState<string | null>(null)

  // 로컬 승격(import) 상태
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<
    { ok: boolean; text: string; id?: number } | null
  >(null)

  const doImport = useCallback(async () => {
    setImporting(true)
    setImportResult(null)
    try {
      const r = await importDataset(urn)
      setImportResult({
        ok: true,
        text: `'${r.name}' 을(를) 로컬 데이터셋으로 가져왔습니다.`,
        id: r.id,
      })
    } catch (e) {
      setImportResult({
        ok: false,
        text: e instanceof Error ? e.message : String(e),
      })
    } finally {
      setImporting(false)
    }
  }, [urn])

  useEffect(() => {
    let alive = true
    if (!urn) {
      setError("데이터셋 URN 이 지정되지 않았습니다.")
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    datasetDetail(urn)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [urn])

  const loadLineage = useCallback(() => {
    if (lineage || lineageLoading) return
    setLineageLoading(true)
    setLineageError(null)
    datasetLineage(urn, 2)
      .then(setLineage)
      .catch((e) => setLineageError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLineageLoading(false))
  }, [urn, lineage, lineageLoading])

  const loadSample = useCallback(() => {
    if (sample || sampleLoading) return
    setSampleLoading(true)
    setSampleError(null)
    datasetSample(urn, 100)
      .then(setSample)
      .catch((e) => setSampleError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSampleLoading(false))
  }, [urn, sample, sampleLoading])

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-16 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> 불러오는 중...
      </div>
    )
  }

  if (error || !detail || detail._error) {
    return (
      <div className="flex flex-col gap-3">
        <BackButton onClick={() => router.back()} />
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" /> {error || detail?._error}
        </div>
      </div>
    )
  }

  const meta = detail.metadata ?? {}
  // 소비자 표시 선택(null=전부). 노출자 노출 ∩ 이 선택만 화면에 노출.
  const display = detail.display_fields
  const show = (key: string) => !display || display.includes(key)

  const schema = meta.schema ?? []

  // 로컬 데이터셋 상세와 동일한 메타데이터 표를 그대로 쓰기 위한 변환본.
  const local = toLocalDetail(detail, urn)
  // 상세 설명 — 노출 시에만 children 으로 전달(undefined 면 섹션 자체 숨김).
  const descNode =
    show("description") && meta.description ? (
      <p className="text-sm whitespace-pre-wrap text-muted-foreground">
        {meta.description}
      </p>
    ) : undefined

  const flagCols = SCHEMA_FLAG_COLS.filter((c) => show(c.key))
  const showType = show("schema.type")
  const showPii = show("schema.pii")
  const showColDesc = show("schema.description")
  const showSchema = show("schema") && schema.length > 0
  const showSample = show("sample")
  const showLineage = show("lineage")
  const showDdl = show("ddl") && !!meta.ddl

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <BackButton onClick={() => router.back()} />

      {/* 헤더 카드 — 로컬 상세와 동일 구조 */}
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1 space-y-1">
              <CardTitle className="flex items-center gap-2 text-xl uppercase">
                {local.display_name || local.name || "(이름 없음)"}
              </CardTitle>
              {local.summary && <p className="text-sm">{local.summary}</p>}
              <p className={`text-sm text-muted-foreground ${MONO} break-all`}>
                {local.urn}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <SourceBadge
                instanceKey={detail.source_instance_key ?? null}
                instanceName={detail.source_instance_name}
              />
              {show("origin") && meta.origin && (
                <Badge variant="secondary">{meta.origin}</Badge>
              )}
              {show("status") && meta.status && (
                <Badge variant="outline">{meta.status}</Badge>
              )}
              {/* 로컬 승격 — 전체 메타/스키마/샘플을 로컬 카탈로그로 가져온다(admin) */}
              <Button
                size="sm"
                variant="outline"
                onClick={doImport}
                disabled={importing}
                title="이 데이터셋을 로컬 카탈로그로 가져옵니다"
              >
                {importing ? (
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <DownloadCloud className="mr-1 h-3.5 w-3.5" />
                )}
                로컬로 가져오기
              </Button>
            </div>
          </div>
        </CardHeader>
      </Card>

      {/* 로컬 승격 결과 — 성공 시 로컬 데이터셋 링크, 실패 시 사유 */}
      {importResult && (
        <div
          className={`flex items-center gap-2 rounded-md border px-3 py-2 text-sm ${
            importResult.ok
              ? "border-primary/30 bg-primary/5 text-foreground"
              : "border-destructive/40 bg-destructive/10 text-destructive"
          }`}
        >
          {importResult.ok ? (
            <CheckCircle2 className="h-4 w-4 shrink-0 text-primary" />
          ) : (
            <AlertTriangle className="h-4 w-4 shrink-0" />
          )}
          <span className="min-w-0">{importResult.text}</span>
          {importResult.ok && importResult.id != null && (
            <Link
              href={`/dashboard/datasets/${importResult.id}`}
              className="ml-auto shrink-0 font-medium underline"
            >
              로컬 데이터셋 열기 →
            </Link>
          )}
        </div>
      )}

      {/* 탭 — 로컬 상세와 동일(아이콘 + gap-1.5) */}
      <Tabs
        defaultValue="overview"
        className="flex min-h-0 flex-1 flex-col"
        onValueChange={(v) => {
          if (v === "lineage") loadLineage()
          if (v === "sample") loadSample()
        }}
      >
        <TabsList>
          <TabsTrigger value="overview" className="gap-1.5">
            <BookOpen className="h-4 w-4" /> 개요
          </TabsTrigger>
          {showSchema && (
            <TabsTrigger value="schema" className="gap-1.5">
              <Columns3 className="h-4 w-4" /> 스키마 ({schema.length})
            </TabsTrigger>
          )}
          {showSample && (
            <TabsTrigger value="sample" className="gap-1.5">
              <Globe className="h-4 w-4" /> 샘플
            </TabsTrigger>
          )}
          {showDdl && (
            <TabsTrigger value="ddl" className="gap-1.5">
              <Code2 className="h-4 w-4" /> DDL
            </TabsTrigger>
          )}
          {showLineage && (
            <TabsTrigger value="lineage" className="gap-1.5">
              <GitBranch className="h-4 w-4" /> 리니지
            </TabsTrigger>
          )}
        </TabsList>

        {/* 개요 — 로컬 데이터셋 상세와 동일한 메타데이터 표(컴포넌트 재사용) */}
        <TabsContent value="overview" className="mt-4 overflow-auto">
          <DatasetMetadataSummary dataset={local}>{descNode}</DatasetMetadataSummary>
        </TabsContent>

        {/* 스키마 — 로컬 상세와 동일(Check 아이콘 / Badge 타입) */}
        {showSchema && (
          <TabsContent value="schema" className="mt-4 overflow-auto">
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[50px]">#</TableHead>
                    <TableHead>필드</TableHead>
                    <TableHead>논리명</TableHead>
                    {showType && <TableHead>타입</TableHead>}
                    {showType && <TableHead>네이티브 타입</TableHead>}
                    {flagCols.map((c) => (
                      <TableHead key={c.key} className="w-[50px] text-center">
                        {c.label}
                      </TableHead>
                    ))}
                    {showPii && <TableHead>PII</TableHead>}
                    {showColDesc && <TableHead>설명</TableHead>}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {schema.map((f, idx) => (
                    <TableRow key={`${f.field_path}-${idx}`}>
                      <TableCell className="text-muted-foreground">{idx + 1}</TableCell>
                      <TableCell className={`text-sm ${MONO}`}>{f.field_path}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {f.display_name || "-"}
                      </TableCell>
                      {showType && (
                        <TableCell>
                          {f.field_type ? (
                            <Badge variant="secondary" className="font-mono text-xs">
                              {f.field_type}
                            </Badge>
                          ) : (
                            "-"
                          )}
                        </TableCell>
                      )}
                      {showType && (
                        <TableCell className={`text-sm uppercase ${MONO}`}>
                          {f.native_type || "-"}
                        </TableCell>
                      )}
                      {flagCols.map((c) => (
                        <TableCell key={c.key} className="text-center">
                          {f[c.field] === "true" && (
                            <Check className={`mx-auto h-4 w-4 ${c.color}`} />
                          )}
                        </TableCell>
                      ))}
                      {showPii && (
                        <TableCell className="text-xs">
                          {f.pii_type ? (
                            <Badge variant="outline" className="text-[10px]">
                              {f.pii_type}
                            </Badge>
                          ) : (
                            ""
                          )}
                        </TableCell>
                      )}
                      {showColDesc && (
                        <TableCell className="max-w-[500px] min-w-[200px] text-sm">
                          <span className="whitespace-pre-wrap break-words">
                            {f.description || "-"}
                          </span>
                        </TableCell>
                      )}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </TabsContent>
        )}

        {/* 샘플 */}
        {showSample && (
          <TabsContent value="sample" className="mt-4 overflow-auto">
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Globe className="h-4 w-4" /> 샘플 데이터
                </CardTitle>
              </CardHeader>
              <CardContent>
                {sampleLoading ? (
                  <Loading text="샘플 불러오는 중..." />
                ) : sampleError ? (
                  <ErrorBox msg={sampleError} />
                ) : sample ? (
                  sample.columns.length === 0 ? (
                    <Empty text="샘플 데이터가 없습니다." />
                  ) : (
                    <div className="rounded-md border">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            {sample.columns.map((c) => (
                              <TableHead key={c} className={`text-xs ${MONO}`}>
                                {c}
                              </TableHead>
                            ))}
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {sample.rows.map((row, ri) => (
                            <TableRow key={ri}>
                              {row.map((cell, ci) => (
                                <TableCell key={ci} className="text-xs">
                                  {cell ?? "—"}
                                </TableCell>
                              ))}
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )
                ) : (
                  <Empty text="이 탭을 선택하면 샘플을 불러옵니다." />
                )}
              </CardContent>
            </Card>
          </TabsContent>
        )}

        {/* DDL — 로컬과 동일 CodeViewer */}
        {showDdl && (
          <TabsContent value="ddl" className="mt-4 overflow-auto">
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Code2 className="h-4 w-4" /> CREATE TABLE DDL
                </CardTitle>
              </CardHeader>
              <CardContent>
                <CodeViewer
                  code={meta.ddl ?? ""}
                  language="sql"
                  height={420}
                  copyLabel="DDL"
                />
              </CardContent>
            </Card>
          </TabsContent>
        )}

        {/* 리니지 */}
        {showLineage && (
          <TabsContent value="lineage" className="mt-4 overflow-auto">
            {lineageLoading ? (
              <Loading text="리니지 불러오는 중..." />
            ) : lineageError ? (
              <ErrorBox msg={lineageError} />
            ) : lineage ? (
              <LineageBody graph={lineage} />
            ) : (
              <Empty text="리니지를 불러오려면 이 탭을 선택하세요." />
            )}
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}

function BackButton({ onClick }: { onClick: () => void }) {
  return (
    <Button
      variant="ghost"
      size="sm"
      className="-ml-2 h-7 self-start px-2 text-muted-foreground hover:text-foreground"
      onClick={onClick}
    >
      <ArrowLeft className="mr-1 h-4 w-4" /> 뒤로
    </Button>
  )
}

function Loading({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 py-8 text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" /> {text}
    </div>
  )
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      <AlertTriangle className="h-4 w-4" /> {msg}
    </div>
  )
}

function Empty({ text }: { text: string }) {
  return <p className="py-8 text-center text-sm text-muted-foreground">{text}</p>
}
