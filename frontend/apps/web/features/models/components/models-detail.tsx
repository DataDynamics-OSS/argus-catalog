"use client"

import { useCallback, useEffect, useState } from "react"
import {
  ArrowLeft, User, Clock, GitBranch, Loader2, Activity, BarChart3, Link2,
} from "lucide-react"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Textarea } from "@workspace/ui/components/textarea"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@workspace/ui/components/table"
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer,
} from "recharts"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"

import { CodeViewer } from "@/components/code-viewer"
import { CommentSection } from "@/components/comments"
import { authFetch } from "@/features/auth/auth-fetch"
import {
  fetchModelDetail,
  fetchModelVersions,
  fetchModelDownloadStats,
  type ModelDetail,
  type ModelVersionItem,
  type ModelDownloadStats,
} from "../api"

function formatSize(bytes: number | null): string {
  if (!bytes) return "-"
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ko-KR", {
    year: "numeric", month: "short", day: "numeric",
  })
}

// ISO 문자열을 ``yyyy-MM-dd HH:mm:ss`` (로컬 timezone) 로 변환.
// 파일 브라우저·OCI 파일 탭·모델 상세 카드의 모든 날짜 표시에 동일 적용.
function formatDateTime(iso: string | null | undefined): string {
  return _formatDateTime(iso, false)
}

// 같은 형식이지만 UTC timezone 기준으로 표시 — MLmodel YAML 의 ``utc_time_created``
// 같이 UTC 원본을 그대로 보여줄 때 사용.
function formatDateTimeUtc(iso: string | null | undefined): string {
  return _formatDateTime(iso, true)
}

function _formatDateTime(iso: string | null | undefined, utc: boolean): string {
  if (!iso) return ""
  // 일부 MLflow ``utc_time_created`` 값은 "yyyy-MM-dd HH:mm:ss.ffffff" 형식의 naive UTC.
  // ``Z`` suffix 가 없으므로 브라우저가 로컬로 해석하는 것을 막기 위해 UTC 로 명시 변환.
  const normalized = /\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?$/.test(iso)
    ? iso.replace(" ", "T") + "Z"
    : iso
  const d = new Date(normalized)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, "0")
  if (utc) {
    return (
      `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
      `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
    )
  }
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const days = Math.floor(diff / (1000 * 60 * 60 * 24))
  if (days === 0) return "오늘"
  if (days === 1) return "1일 전"
  if (days < 30) return `${days}일 전`
  if (days < 365) return `${Math.floor(days / 30)}개월 전`
  return `${Math.floor(days / 365)}년 전`
}

function StatusBadge({ status }: { status: string | null }) {
  let bg = "bg-orange-500"
  let label = "N/A"
  if (status === "READY") { bg = "bg-blue-500"; label = "READY" }
  else if (status === "PENDING_REGISTRATION") { bg = "bg-zinc-400"; label = "PENDING" }
  else if (status === "FAILED_REGISTRATION") { bg = "bg-red-500"; label = "FAILED" }
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[10px] font-semibold text-white ${bg}`}>
      {label}
    </span>
  )
}

function InfoRow({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex items-start gap-3 py-1.5 border-b last:border-b-0">
      <span className="text-sm text-muted-foreground w-36 shrink-0">{label}</span>
      <span className="text-sm break-all">{value || "-"}</span>
    </div>
  )
}

// ─── Overview Tab ───

function OverviewTab({ detail }: { detail: ModelDetail }) {
  const c = detail.catalog
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-sm font-medium">모델 정보</CardTitle></CardHeader>
          <CardContent>
            <InfoRow label="이름" value={detail.name} />
            <InfoRow label="URN" value={detail.urn} />
            <InfoRow label="소유자" value={detail.owner} />
            <InfoRow label="저장소 유형" value={detail.storage_type} />
            <InfoRow label="생성일" value={formatDateTime(detail.created_at)} />
            <InfoRow label="수정일" value={formatDateTime(detail.updated_at)} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-sm font-medium">최신 버전 (v{detail.max_version_number})</CardTitle></CardHeader>
          <CardContent>
            {c ? (
              <>
                <InfoRow label="상태" value={detail.latest_version_status} />
                <InfoRow label="predict_fn" value={c.predict_fn} />
                <InfoRow label="Python" value={c.python_version} />
                <InfoRow label="sklearn" value={c.sklearn_version} />
                <InfoRow label="MLflow" value={c.mlflow_version} />
                <InfoRow label="직렬화 형식" value={c.serialization_format} />
                <InfoRow label="모델 크기" value={formatSize(c.model_size_bytes)} />
                <InfoRow label="모델 ID" value={c.mlflow_model_id} />
                <InfoRow label="생성일 (UTC)" value={formatDateTimeUtc(c.utc_time_created)} />
                <InfoRow label="생성일 (로컬)" value={formatDateTime(c.utc_time_created)} />
              </>
            ) : (
              <p className="text-sm text-muted-foreground py-4">메타데이터 없음</p>
            )}
          </CardContent>
        </Card>
      </div>

      {c && (c.requirements || c.conda) && (
        <div className="grid gap-4 lg:grid-cols-2">
          {c.requirements && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium">Requirements</CardTitle>
              </CardHeader>
              <CardContent>
                <CodeViewer
                  code={c.requirements}
                  language="plaintext"
                  height={200}
                  copyLabel="requirements.txt"
                />
              </CardContent>
            </Card>
          )}
          {c.conda && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium">Conda</CardTitle>
              </CardHeader>
              <CardContent>
                <CodeViewer
                  code={c.conda}
                  language="yaml"
                  height={200}
                  copyLabel="conda.yaml"
                />
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Usage Tab ───

function UsageTab({ detail }: { detail: ModelDetail }) {
  const name = detail.name
  const ver = detail.max_version_number
  const code = `import mlflow
import pandas as pd

# ─── 환경 설정 ───
# Argus Catalog Server 를 MLflow 모델 레지스트리로 지정합니다.
mlflow.set_registry_uri("uc:http://<argus-catalog-server>:<argus-catalog-server-port>")


# ─── 1. 모델 로드 및 예측 ───

# 특정 버전을 로드합니다.
model = mlflow.pyfunc.load_model("models:/${name}/${ver}")

# 입력 데이터를 준비합니다.
X_new = pd.DataFrame(
    # TODO: 실제 입력 데이터로 교체하세요.
    [[...], [...]],
    columns=["feature_1", "feature_2", ...],
)

# 추론을 실행합니다.
predictions = model.predict(X_new)
print("Predictions:", predictions)


# ─── 2. 최신 버전 로드 ───

latest_model = mlflow.pyfunc.load_model("models:/${name}/latest")
print("Latest predictions:", latest_model.predict(X_new))


# ─── 3. 새 버전 등록 ───

# 모델을 학습시킵니다.
# model = YourModel()
# model.fit(X_train, y_train)

# 학습한 모델을 기록하고 등록합니다(새 버전이 자동으로 생성됩니다).
# mlflow.sklearn.log_model(
#     model,
#     "model",
#     registered_model_name="${name}",
# )
`

  // viewport 하단까지 차도록 동적 높이. 상단 고정 영역(Back link · 헤더 · 탭 트리거 ·
  // 페이지 padding 합) 약 280px 를 제하고, viewport 가 작은 환경을 위해 360px floor.
  return (
    <CodeViewer
      code={code}
      language="python"
      height="max(360px, calc(100vh - 280px))"
      copyLabel="usage.py"
    />
  )
}

// ─── Versions Tab ───

function VersionsTab({ modelName }: { modelName: string }) {
  const [versions, setVersions] = useState<ModelVersionItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchModelVersions(modelName, 1, 100)
      .then((data) => setVersions(data.items))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [modelName])

  if (loading) return <div className="text-center py-12 text-muted-foreground">버전을 불러오는 중...</div>

  return (
    <div className="border rounded-md overflow-auto">
      <table className="w-full text-sm">
        <thead className="bg-muted/60 sticky top-0">
          <tr>
            <th className="px-3 py-2 text-left font-medium w-20">버전</th>
            <th className="px-3 py-2 text-center font-medium w-24">상태</th>
            <th className="px-3 py-2 text-center font-medium w-28">스테이지</th>
            <th className="px-3 py-2 text-center font-medium w-20">파일</th>
            <th className="px-3 py-2 text-center font-medium w-24">크기</th>
            <th className="px-3 py-2 text-center font-medium w-28">완료</th>
            <th className="px-3 py-2 text-left font-medium">Run ID</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {versions.map((v) => (
            <tr key={v.id} className="hover:bg-muted/30">
              <td className="px-3 py-2">v{v.version}</td>
              <td className="px-3 py-2 text-center"><StatusBadge status={v.status} /></td>
              <td className="px-3 py-2 text-center">
                <select
                  className="text-sm border rounded px-1 py-0.5 bg-background"
                  value={(v as Record<string, unknown>).stage as string || "NONE"}
                  onChange={async (e) => {
                    await authFetch(`/api/v1/models/${encodeURIComponent(modelName)}/versions/${v.version}/stage`, {
                      method: "PUT",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ stage: e.target.value }),
                    })
                    fetchModelVersions(modelName, 1, 100).then(d => setVersions(d.items))
                  }}
                >
                  <option value="NONE">없음</option>
                  <option value="STAGING">스테이징</option>
                  <option value="PRODUCTION">운영</option>
                  <option value="ARCHIVED">보관</option>
                </select>
              </td>
              <td className="px-3 py-2 text-center">{v.artifact_count}</td>
              <td className="px-3 py-2 text-center">{formatSize(v.artifact_size)}</td>
              <td className="px-3 py-2 text-center" title={v.finished_at || ""}>
                {v.finished_at ? timeAgo(v.finished_at) : "-"}
              </td>
              <td className="px-3 py-2 font-mono truncate max-w-[200px]">
                {v.run_id || "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ─── Download Tab ───

function DownloadTab({ modelName }: { modelName: string }) {
  const [stats, setStats] = useState<ModelDownloadStats | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchModelDownloadStats(modelName)
      .then(setStats)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [modelName])

  if (loading || !stats) return <div className="text-center py-12 text-muted-foreground">다운로드 데이터를 불러오는 중...</div>

  const chartData = stats.daily_download.map((d) => ({
    date: d.date.slice(5),
    fullDate: d.date,
    count: d.count,
  }))

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">일별 다운로드 (30일)</CardTitle>
        </CardHeader>
        <CardContent>
          {chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={chartData} margin={{ left: 0, right: 10, top: 5, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" fontSize={10} />
                <YAxis allowDecimals={false} fontSize={11} />
                <Tooltip
                  labelFormatter={(_, p) => p?.[0]?.payload?.fullDate || ""}
                  itemStyle={{ color: "#666" }}
                />
                <Line type="monotone" dataKey="count" name="다운로드 수" stroke="#10b981" strokeWidth={2} dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-muted-foreground text-center py-8">아직 다운로드 데이터가 없습니다</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">최근 다운로드 로그</CardTitle>
        </CardHeader>
        <CardContent>
          {stats.recent_logs.length > 0 ? (
            <div className="border rounded-md overflow-auto max-h-[400px]">
              <table className="w-full text-sm">
                <thead className="bg-muted/60 sticky top-0">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium w-[13.2rem]">시간</th>
                    <th className="px-3 py-2 text-center font-medium w-16">버전</th>
                    <th className="px-3 py-2 text-center font-medium w-24">유형</th>
                    <th className="px-3 py-2 text-left font-medium w-32">클라이언트 IP</th>
                    <th className="px-3 py-2 text-left font-medium">User Agent</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {stats.recent_logs.map((log, i) => (
                    <tr key={i} className="hover:bg-muted/30">
                      <td className="px-3 py-1.5">{formatDateTime(log.downloaded_at)}</td>
                      <td className="px-3 py-1.5 text-center">v{log.version}</td>
                      <td className="px-3 py-1.5 text-center">
                        <Badge variant="outline" className="text-sm">{log.download_type}</Badge>
                      </td>
                      <td className="px-3 py-1.5 font-mono">{log.client_ip || "-"}</td>
                      <td className="px-3 py-1.5 truncate max-w-[300px]">{log.user_agent || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground text-center py-4">아직 다운로드 로그가 없습니다</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

// ─── Main Detail Component ───

type ModelsDetailProps = {
  modelName: string
  onBack: () => void
}

export function ModelsDetail({ modelName, onBack }: ModelsDetailProps) {
  const [detail, setDetail] = useState<ModelDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetchModelDetail(modelName)
      .then(setDetail)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [modelName])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error || !detail) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-3">
        <p className="text-sm text-destructive">{error || "모델을 찾을 수 없습니다."}</p>
        <Button variant="outline" size="sm" onClick={onBack}>
          <ArrowLeft className="h-4 w-4 mr-1" />
          모델 목록으로
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Back link — 모델명/메타 라인과 시각적으로 분리되도록 헤더 위 별도 줄에 둔다.
          ghost 버튼 inline 패턴에서 ``ml-9`` 들여쓰기를 강제하던 부작용을 함께 제거. */}
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
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold">{detail.name}</h1>
            <StatusBadge status={detail.latest_version_status} />
          </div>
          {detail.description && (
            <p className="text-sm text-muted-foreground">{detail.description}</p>
          )}
          <div className="flex items-center gap-4 text-sm text-muted-foreground">
            {detail.owner && (
              <span className="flex items-center gap-1">
                <User className="h-3.5 w-3.5" /> {detail.owner}
              </span>
            )}
            <span className="flex items-center gap-1">
              <Clock className="h-3.5 w-3.5" /> {formatDate(detail.created_at)}
            </span>
            <span className="flex items-center gap-1">
              <GitBranch className="h-3.5 w-3.5" /> 버전 {detail.max_version_number}개
            </span>
            <span className="flex items-center gap-1">
              <Activity className="h-3.5 w-3.5" /> 다운로드 {detail.download_count}회
            </span>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <Tabs defaultValue="overview">
        <TabsList variant="line">
          <TabsTrigger value="overview" className="text-base">개요</TabsTrigger>
          <TabsTrigger value="usage" className="text-base">사용법</TabsTrigger>
          <TabsTrigger value="versions" className="text-base">버전</TabsTrigger>
          <TabsTrigger value="metrics" className="text-base">메트릭</TabsTrigger>
          <TabsTrigger value="lineage" className="text-base">리니지</TabsTrigger>
          <TabsTrigger value="card" className="text-base">모델 카드</TabsTrigger>
          <TabsTrigger value="download" className="text-base">다운로드</TabsTrigger>
          <TabsTrigger value="comments" className="text-base">댓글</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="mt-4">
          <OverviewTab detail={detail} />
        </TabsContent>
        <TabsContent value="usage" className="mt-4">
          <UsageTab detail={detail} />
        </TabsContent>
        <TabsContent value="versions" className="mt-4">
          <VersionsTab modelName={detail.name} />
        </TabsContent>
        <TabsContent value="metrics" className="mt-4">
          <MetricsTab modelName={detail.name} />
        </TabsContent>
        <TabsContent value="lineage" className="mt-4">
          <ModelLineageTab modelName={detail.name} />
        </TabsContent>
        <TabsContent value="card" className="mt-4">
          <ModelCardTab modelName={detail.name} />
        </TabsContent>
        <TabsContent value="download" className="mt-4">
          <DownloadTab modelName={detail.name} />
        </TabsContent>
        <TabsContent value="comments" className="mt-4">
          <CommentSection entityType="model" entityId={detail.name} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Metrics Tab — compare metrics across versions
// ---------------------------------------------------------------------------

function MetricsTab({ modelName }: { modelName: string }) {
  const [data, setData] = useState<{ version: number; metrics: Record<string, number> }[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    authFetch(`/api/v1/models/${encodeURIComponent(modelName)}/metrics`)
      .then(r => r.json()).then(setData).catch(() => {})
      .finally(() => setLoading(false))
  }, [modelName])

  if (loading) return <p className="text-sm text-muted-foreground text-center py-8">메트릭을 불러오는 중...</p>
  if (data.length === 0) return (
    <Card><CardContent className="text-center py-12 text-sm text-muted-foreground">
      <BarChart3 className="h-10 w-10 mx-auto mb-2 text-muted-foreground/40" />
      기록된 메트릭이 없습니다. API 로 각 버전의 메트릭을 기록할 수 있습니다.
    </CardContent></Card>
  )

  const allKeys = Array.from(new Set(data.flatMap(d => Object.keys(d.metrics)))).sort()

  return (
    <Card><CardContent className="p-0">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-20">버전</TableHead>
            {allKeys.map(k => <TableHead key={k}>{k}</TableHead>)}
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.map(d => (
            <TableRow key={d.version}>
              <TableCell className="font-medium">v{d.version}</TableCell>
              {allKeys.map(k => (
                <TableCell key={k} className="font-mono text-sm">
                  {d.metrics[k] !== undefined ? d.metrics[k].toFixed(4) : "—"}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </CardContent></Card>
  )
}

// ---------------------------------------------------------------------------
// Model Lineage Tab — training data links
// ---------------------------------------------------------------------------

function ModelLineageTab({ modelName }: { modelName: string }) {
  const [lineages, setLineages] = useState<{
    id: number; dataset_id: number; dataset_name: string | null; datasource_type: string | null
    model_version: number | null; relation_type: string; description: string | null
  }[]>([])
  const [loading, setLoading] = useState(true)

  const fetch = useCallback(() => {
    setLoading(true)
    authFetch(`/api/v1/models/${encodeURIComponent(modelName)}/lineage`)
      .then(r => r.json()).then(setLineages).catch(() => {})
      .finally(() => setLoading(false))
  }, [modelName])

  useEffect(() => { fetch() }, [fetch])

  const remove = async (id: number) => {
    await authFetch(`/api/v1/models/${encodeURIComponent(modelName)}/lineage/${id}`, { method: "DELETE" })
    fetch()
  }

  if (loading) return <p className="text-sm text-muted-foreground text-center py-8">리니지를 불러오는 중...</p>
  if (lineages.length === 0) return (
    <Card><CardContent className="text-center py-12 text-sm text-muted-foreground">
      <Link2 className="h-10 w-10 mx-auto mb-2 text-muted-foreground/40" />
      연결된 데이터셋 리니지가 없습니다. API 로 학습 데이터셋을 연결할 수 있습니다.
    </CardContent></Card>
  )

  return (
    <Card><CardContent className="p-0">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>데이터셋</TableHead>
            <TableHead className="w-28">데이터 소스</TableHead>
            <TableHead className="w-20">버전</TableHead>
            <TableHead className="w-36">관계</TableHead>
            <TableHead>설명</TableHead>
            <TableHead className="w-16" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {lineages.map(l => (
            <TableRow key={l.id}>
              <TableCell className="font-medium">{l.dataset_name || `ID: ${l.dataset_id}`}</TableCell>
              <TableCell><Badge variant="outline">{l.datasource_type}</Badge></TableCell>
              <TableCell>{l.model_version ? `v${l.model_version}` : "전체"}</TableCell>
              <TableCell><Badge variant="secondary">{l.relation_type}</Badge></TableCell>
              <TableCell className="text-muted-foreground">{l.description || "—"}</TableCell>
              <TableCell>
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => remove(l.id)}>
                  <span className="text-muted-foreground hover:text-destructive">×</span>
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </CardContent></Card>
  )
}

// ---------------------------------------------------------------------------
// Model Card Tab — structured governance info
// ---------------------------------------------------------------------------

function ModelCardTab({ modelName }: { modelName: string }) {
  const [card, setCard] = useState<Record<string, string | null>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setLoading(true)
    authFetch(`/api/v1/models/${encodeURIComponent(modelName)}/card`)
      .then(r => r.json()).then(setCard).catch(() => {})
      .finally(() => setLoading(false))
  }, [modelName])

  const save = async () => {
    setSaving(true)
    await authFetch(`/api/v1/models/${encodeURIComponent(modelName)}/card`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(card),
    })
    setSaving(false)
  }

  const update = (key: string, value: string) => setCard(prev => ({ ...prev, [key]: value || null }))

  if (loading) return <p className="text-sm text-muted-foreground text-center py-8">모델 카드를 불러오는 중...</p>

  const fields = [
    { key: "purpose", label: "용도", placeholder: "이 모델이 어떤 목적으로 쓰이나요?", rows: 3 },
    { key: "performance", label: "성능", placeholder: "AUC, F1, accuracy, latency 등", rows: 2 },
    { key: "limitations", label: "제약사항", placeholder: "알려진 한계, 편향, 엣지 케이스 등", rows: 3 },
    { key: "training_data", label: "학습 데이터", placeholder: "데이터셋, 기간, 행 수 등", rows: 2 },
    { key: "framework", label: "프레임워크", placeholder: "scikit-learn 1.3.0 / Python 3.11", rows: 1 },
    { key: "license", label: "라이선스", placeholder: "Apache 2.0, Internal Use Only 등", rows: 1 },
    { key: "contact", label: "연락처", placeholder: "ml-team@company.com", rows: 1 },
  ]

  return (
    <Card>
      <CardContent className="p-6 space-y-4">
        {fields.map(f => (
          <div key={f.key} className="space-y-1.5">
            <Label className="text-sm font-medium">{f.label}</Label>
            {f.rows > 1 ? (
              <Textarea
                value={card[f.key] || ""}
                onChange={e => update(f.key, e.target.value)}
                placeholder={f.placeholder}
                rows={f.rows}
                className="text-sm"
              />
            ) : (
              <Input
                value={card[f.key] || ""}
                onChange={e => update(f.key, e.target.value)}
                placeholder={f.placeholder}
                className="h-9 text-sm"
              />
            )}
          </div>
        ))}
        <div className="flex justify-end pt-2">
          <Button onClick={save} disabled={saving}>
            {saving ? "저장 중..." : "모델 카드 저장"}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
