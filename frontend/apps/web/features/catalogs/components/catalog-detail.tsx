"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Database, Link2, Pencil, Plus, Trash2, Unlink } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { useConfirm } from "@/components/confirm-provider"
import { DetailSection, LINE_TABS_LIST, LINE_TABS_TRIGGER } from "@/components/detail-section"
import {
  addCatalogConnection,
  deleteCatalog,
  removeCatalogConnection,
  syncConnection,
  visibleConnections,
  type Catalog,
} from "@/features/catalogs/api"
import { SourceMapBadge } from "@/features/catalogs/components/source-map-badge"
import { fetchDatasets, type PaginatedDatasets } from "@/features/datasets/api"
import { DatasetCreateDialog } from "@/features/datasets/components/datasets-add-dialog"
import { datasetHref } from "@/features/datasets/links"
import { fetchDatasources, type DatasourceRow } from "@/features/datasources/api"
import { errorMessage, notifyError, notifySuccess } from "@/lib/notify"
import { Tip } from "@/components/tip"

// 카탈로그에 조회(QUERY)로 연결할 수 있는 엔진 — 백엔드 datasource_metadata.QUERY_CAPABLE_TYPES 와 맞춘다
const QUERY_CAPABLE = new Set(["impala", "trino", "starrocks"])
const PAGE_SIZE = 20

export const CATALOG_TABS = ["overview", "datasets", "connections"] as const
export type CatalogTab = (typeof CATALOG_TABS)[number]

export function CatalogSourceBadge({ catalog }: { catalog: Catalog }) {
  if (catalog.source_type === "ARGUS") return <Badge variant="info">직접 등록</Badge>
  return <Badge variant="muted">{catalog.auto_created ? "데이터 소스와 함께 생성" : "외부"}</Badge>
}

function CatalogDatasets({ catalog, canCreate }: { catalog: Catalog; canCreate: boolean }) {
  const [page, setPage] = useState(1)
  const [data, setData] = useState<PaginatedDatasets | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState({ open: false, session: 0 })
  const isManual = catalog.source_type === "ARGUS"

  const load = useCallback(async () => {
    try {
      setData(await fetchDatasets({ catalogId: catalog.id, page, pageSize: PAGE_SIZE }))
      setError(null)
    } catch (e) {
      setError(errorMessage(e) ?? "데이터셋을 불러오지 못했습니다.")
    }
  }, [catalog.id, page])

  useEffect(() => { void load() }, [load])

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1

  return (
    <DetailSection
      title={`데이터셋${data ? ` ${data.total.toLocaleString()}개` : ""}`}
      icon={<Database />}
      description={isManual
        ? "직접 등록한 데이터셋입니다 — 메타데이터를 수집하지 않습니다. 데이터셋 상세의 [원천 매핑]으로 실제 테이블에 연결합니다."
        : "데이터 소스에서 수집한 데이터셋과 이 카탈로그에 직접 추가한 데이터셋입니다."}
      actions={canCreate ? (
        <Button size="sm" onClick={() => setAdding((a) => ({ open: true, session: a.session + 1 }))}>
          <Plus className="mr-1 h-3.5 w-3.5" /> 데이터셋 추가
        </Button>
      ) : undefined}
    >
      {error && <p className="text-sm text-destructive">{error}</p>}
      {data && data.items.length === 0 && <p className="text-sm text-muted-foreground">데이터셋이 없습니다.</p>}
      {data && data.items.length > 0 && (
        <div className="overflow-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs">
              <tr>
                <th className="px-3 py-2 font-medium">이름</th>
                <th className="px-3 py-2 font-medium">요약</th>
                {isManual && <th className="px-3 py-2 font-medium">원천 매핑</th>}
                <th className="px-3 py-2 font-medium">컬럼</th>
                <th className="px-3 py-2 font-medium">변경일</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((d) => (
                <tr key={d.id} className="border-t">
                  <td className="px-3 py-2">
                    <Link className="font-medium hover:underline" href={datasetHref(d.id)}>{d.display_name || d.name}</Link>
                    {d.display_name && <span className="ml-2 font-mono text-xs text-muted-foreground">{d.name}</span>}
                  </td>
                  <td className="px-3 py-2">{d.summary ?? ""}</td>
                  {isManual && <td className="px-3 py-2"><SourceMapBadge status={d.source_map_status} /></td>}
                  <td className="px-3 py-2 tabular-nums">{d.schema_field_count}</td>
                  <td className="px-3 py-2 text-xs">{new Date(d.updated_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <div className="flex items-center justify-end gap-2 text-sm">
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>이전</Button>
          <span className="tabular-nums">{page} / {pages}</span>
          <Button size="sm" variant="outline" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>다음</Button>
        </div>
      )}
      <DatasetCreateDialog
        key={adding.session}
        open={adding.open}
        onOpenChange={(open) => setAdding((a) => ({ ...a, open }))}
        presetCatalogId={catalog.id}
        onCreated={() => load()}
      />
    </DetailSection>
  )
}

function CatalogConnections({
  catalog,
  canManage,
  onChanged,
}: {
  catalog: Catalog
  canManage: boolean
  onChanged: (c: Catalog) => void
}) {
  const [engines, setEngines] = useState<DatasourceRow[]>([])
  const [pick, setPick] = useState("")
  const [remote, setRemote] = useState("")
  const [busy, setBusy] = useState(false)
  const conns = visibleConnections(catalog)

  useEffect(() => {
    if (!canManage) return
    fetchDatasources().then((rows) => setEngines(rows.filter((d) => QUERY_CAPABLE.has(d.type)))).catch(() => {})
  }, [canManage])

  const connected = new Set(conns.map((c) => c.datasource_id))
  const candidates = engines.filter((e) => !connected.has(e.id))

  const add = async () => {
    setBusy(true)
    try {
      onChanged(await addCatalogConnection(catalog.id, {
        datasource_id: Number(pick), role: "QUERY", remote_name: remote.trim() || null,
      }))
      setPick("")
      setRemote("")
      notifySuccess("엔진을 연결했습니다.")
    } catch (e) {
      notifyError(e, "카탈로그에 연결하지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  const remove = async (datasourceId: number) => {
    setBusy(true)
    try {
      onChanged(await removeCatalogConnection(catalog.id, datasourceId))
      notifySuccess("연결을 해제했습니다.")
    } catch (e) {
      notifyError(e, "카탈로그 연결을 해제하지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <DetailSection
      title="데이터 소스 연결"
      icon={<Link2 />}
      description={catalog.source_type === "ARGUS"
        ? "직접 등록한 카탈로그는 수집하지 않습니다 — 이 카탈로그를 조회하는 쿼리 엔진만 연결합니다."
        : "수집(SYNC)은 메타데이터를 가져오는 데이터 소스, 조회(QUERY)는 이 카탈로그를 읽는 쿼리 엔진입니다."}
    >
      {conns.length === 0 ? (
        <p className="text-sm text-muted-foreground">연결된 데이터 소스가 없습니다.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {conns.map((c) => (
            <li key={c.datasource_id} className="flex items-center gap-3 px-3 py-2 text-sm">
              <Badge variant={c.role === "SYNC" ? "success" : "outline"}>{c.role === "SYNC" ? "수집" : "조회"}</Badge>
              <Link className="font-medium hover:underline" href={`/dashboard/datasources?datasource=${encodeURIComponent(c.datasource_key)}`}>
                {c.datasource_name}
              </Link>
              <span className="text-xs text-muted-foreground">{c.datasource_type}</span>
              {c.remote_name && <span className="font-mono text-xs">엔진 안 이름: {c.remote_name}</span>}
              {canManage && !(c.role === "SYNC" && catalog.auto_created) && (
                <Tip label="연결 해제"><Button size="sm" variant="ghost" className="ml-auto h-7" disabled={busy}
                  onClick={() => void remove(c.datasource_id)} aria-label={`${c.datasource_name} 연결 해제`}>
                  <Unlink className="h-3.5 w-3.5" />
                </Button></Tip>
              )}
            </li>
          ))}
        </ul>
      )}
      {canManage && (
        <div className="flex flex-wrap items-end gap-2">
          <Select value={pick} onValueChange={setPick}>
            <SelectTrigger className="h-8 w-56" aria-label="연결할 쿼리 엔진">
              <SelectValue placeholder={candidates.length ? "쿼리 엔진 선택" : "연결할 쿼리 엔진 없음"} />
            </SelectTrigger>
            <SelectContent>
              {candidates.map((e) => (
                <SelectItem key={e.id} value={String(e.id)}>{e.name} <span className="ml-1 text-xs text-muted-foreground">{e.type}</span></SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input className="h-8 w-48" placeholder="엔진 안 카탈로그 이름(선택)" value={remote}
            onChange={(e) => setRemote(e.target.value)} maxLength={255} />
          <Button size="sm" variant="outline" disabled={!pick || busy} onClick={() => void add()}>조회 엔진 연결</Button>
        </div>
      )}
    </DetailSection>
  )
}

export function CatalogDetail({
  catalog,
  tab,
  onTabChange,
  canManage,
  canCreateDataset,
  onEdit,
  onChanged,
  onDeleted,
}: {
  catalog: Catalog
  tab: CatalogTab
  onTabChange: (t: CatalogTab) => void
  canManage: boolean
  canCreateDataset: boolean
  onEdit: () => void
  onChanged: (c: Catalog) => void
  onDeleted: () => void
}) {
  const askConfirm = useConfirm()
  const remove = async () => {
    const ok = await askConfirm({
      title: "카탈로그 삭제",
      description: `'${catalog.name}' 을(를) 삭제합니다. 데이터셋이 남아 있으면 삭제할 수 없습니다.`,
      confirmText: "삭제",
      destructive: true,
    })
    if (!ok) return
    try {
      await deleteCatalog(catalog.id)
      notifySuccess("카탈로그를 삭제했습니다.")
      onDeleted()
    } catch (e) {
      notifyError(e, "카탈로그를 삭제하지 못했습니다.")
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <h2 className="flex flex-wrap items-center gap-2 text-xl font-semibold">
            {catalog.name}
            <CatalogSourceBadge catalog={catalog} />
            {catalog.kind && <Badge variant="outline">{catalog.kind}</Badge>}
          </h2>
          {catalog.summary && <p className="text-sm">{catalog.summary}</p>}
          <p className="font-mono text-xs text-muted-foreground">{catalog.catalog_key}</p>
        </div>
        {canManage && (
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={onEdit}><Pencil className="mr-1 h-3.5 w-3.5" /> 편집</Button>
            {!catalog.auto_created && (
              <Button size="sm" variant="outline" onClick={() => void remove()}>
                <Trash2 className="mr-1 h-3.5 w-3.5" /> 삭제
              </Button>
            )}
          </div>
        )}
      </div>

      <Tabs value={tab} onValueChange={(v) => onTabChange(v as CatalogTab)}>
        <TabsList variant="line" className={LINE_TABS_LIST}>
          <TabsTrigger value="overview" className={LINE_TABS_TRIGGER}>개요</TabsTrigger>
          <TabsTrigger value="datasets" className={LINE_TABS_TRIGGER}>데이터셋</TabsTrigger>
          <TabsTrigger value="connections" className={LINE_TABS_TRIGGER}>데이터 소스 연결</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="mt-4">
          <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground">원천</dt>
            <dd>{catalog.source_type === "ARGUS" ? "Argus 에 직접 등록 — 메타데이터를 수집하지 않습니다" : "외부 카탈로그 — 데이터 소스에서 수집합니다"}</dd>
            <dt className="text-muted-foreground">데이터셋</dt>
            <dd className="tabular-nums">{catalog.dataset_count.toLocaleString()}개</dd>
            <dt className="text-muted-foreground">소유자</dt>
            <dd>{catalog.owner || "—"}</dd>
            {catalog.source_type === "ARGUS" && (<>
              <dt className="text-muted-foreground">시스템</dt>
              <dd>
                {catalog.system_name
                  ? <>{catalog.system_name}{catalog.org_name ? ` · 소관기관 ${catalog.org_name}` : " · 조직 미지정"}</>
                  : "지정 안 함 — 편집에서 시스템을 지정하면 그 조직이 이 카탈로그 데이터셋의 소관기관이 됩니다"}
              </dd>
            </>)}
            <dt className="text-muted-foreground">설명</dt>
            <dd className="whitespace-pre-wrap">{catalog.description || "—"}</dd>
            {catalog.created_at && (<>
              <dt className="text-muted-foreground">등록</dt>
              <dd>{new Date(catalog.created_at).toLocaleString()}</dd>
            </>)}
          </dl>
        </TabsContent>
        <TabsContent value="datasets" className="mt-4">
          {/* 수집 데이터 소스가 없는(엔진으로만 닿는) 카탈로그에는 데이터셋을 넣을 곳이 없다 */}
          <CatalogDatasets catalog={catalog}
            canCreate={canCreateDataset && (catalog.source_type === "ARGUS" || Boolean(syncConnection(catalog)))} />
        </TabsContent>
        <TabsContent value="connections" className="mt-4">
          <CatalogConnections catalog={catalog} canManage={canManage} onChanged={onChanged} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
