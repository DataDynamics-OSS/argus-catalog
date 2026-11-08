"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { Link2, RefreshCw, Unlink } from "lucide-react"

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
import {
  addCatalogConnection,
  createCatalog,
  discoverEngineCatalogs,
  fetchCatalogs,
  fetchDatasourceCatalogs,
  removeCatalogConnection,
  type Catalog,
  type CatalogConnectionRole,
  type EngineCatalogs,
} from "@/features/catalogs/api"
import { datasourceRole } from "@/features/datasources/datasource-configs"
import { errorMessage, toast } from "@/lib/notify"

// 카탈로그에 QUERY 로 연결할 수 있는 유형 — 백엔드 datasource_metadata.QUERY_CAPABLE_TYPES 와 맞춘다
const QUERY_CAPABLE = new Set(["impala", "trino", "starrocks"])
// 엔진 카탈로그 탐색을 지원하는 유형
const DISCOVERABLE = new Set(["trino"])
// Trino 커넥터 → 새 카탈로그를 만들 때의 kind
const CONNECTOR_KIND: Record<string, string> = {
  hive: "hive",
  iceberg: "iceberg-rest",
  kudu: "kudu",
  postgresql: "postgresql",
  mysql: "mysql",
  mariadb: "mysql",
  oracle: "oracle",
  sqlserver: "mssql",
}
const NEW_SYNC = "__new_sync__"

const ROLE_LABEL: Record<CatalogConnectionRole, string> = { SYNC: "수집", QUERY: "조회" }

/**
 * 데이터 소스 ↔ 카탈로그 연결 관리.
 * - 엔진(Impala·Trino): 등록된 카탈로그에 조회(QUERY)로 연결. 엔진으로만 닿는 카탈로그는 수집(SYNC)으로 연결.
 * - 카탈로그·DB: 자기 카탈로그(수집)가 보이고, StarRocks 는 외부 카탈로그를 조회로 더 연결할 수 있다.
 */
export function CatalogBindings({
  datasource,
  canManage,
}: {
  datasource: { id: number; name: string; type: string }
  canManage: boolean
}) {
  const role = datasourceRole(datasource.type)
  const canQuery = QUERY_CAPABLE.has(datasource.type)
  const [linked, setLinked] = useState<Catalog[]>([])
  const [all, setAll] = useState<Catalog[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  // 수동 연결 입력
  const [pickCatalog, setPickCatalog] = useState<string>("")
  const [pickRole, setPickRole] = useState<CatalogConnectionRole>("QUERY")
  const [pickRemote, setPickRemote] = useState("")
  // 엔진 탐색 결과와 행별 선택
  const [discovered, setDiscovered] = useState<EngineCatalogs | null>(null)
  const [discovering, setDiscovering] = useState(false)
  const [choice, setChoice] = useState<Record<string, string>>({})

  const reload = useCallback(async () => {
    try {
      const [l, a] = await Promise.all([fetchDatasourceCatalogs(datasource.id), fetchCatalogs()])
      setLinked(l)
      setAll(a)
    } catch (err) {
      toast.error(errorMessage(err) ?? "카탈로그 연결을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [datasource.id])

  useEffect(() => {
    void reload()
  }, [reload])

  const linkedIds = useMemo(() => new Set(linked.map((c) => c.id)), [linked])
  const candidates = useMemo(() => all.filter((c) => !linkedIds.has(c.id)), [all, linkedIds])
  const catalogById = useMemo(() => new Map(all.map((c) => [c.id, c])), [all])
  const myConnection = (c: Catalog) => c.connections.find((x) => x.datasource_id === datasource.id)

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true)
    try {
      await fn()
      toast.success(ok)
      await reload()
      return true
    } catch (err) {
      toast.error(errorMessage(err) ?? "요청을 처리하지 못했습니다.")
      return false
    } finally {
      setBusy(false)
    }
  }

  const connect = async (catalogId: number, r: CatalogConnectionRole, remote: string | null) =>
    run(
      () => addCatalogConnection(catalogId, { datasource_id: datasource.id, role: r, remote_name: remote }),
      `카탈로그 "${catalogById.get(catalogId)?.name ?? catalogId}" 에 연결했습니다.`,
    )

  const connectNewSync = async (remote: string, connector: string | null) =>
    run(async () => {
      const cat = await createCatalog({
        name: `${datasource.name} · ${remote}`,
        kind: CONNECTOR_KIND[(connector ?? "").toLowerCase()] ?? connector,
      })
      await addCatalogConnection(cat.id, { datasource_id: datasource.id, role: "SYNC", remote_name: remote })
    }, `카탈로그 "${remote}" 를 만들고 이 엔진으로 수집하도록 연결했습니다.`)

  const handleManualConnect = async () => {
    if (!pickCatalog) return
    const ok = await connect(Number(pickCatalog), pickRole, pickRemote.trim() || null)
    if (ok) {
      setPickCatalog("")
      setPickRemote("")
    }
  }

  const handleDiscover = async () => {
    setDiscovering(true)
    try {
      const res = await discoverEngineCatalogs(datasource.id)
      setDiscovered(res)
      // 연결 후보가 하나면 미리 골라 둔다
      setChoice(
        Object.fromEntries(
          res.catalogs
            .filter((e) => e.connected_catalog_id == null && e.suggested_catalog_ids.length === 1)
            .map((e) => [e.remote_name, String(e.suggested_catalog_ids[0])]),
        ),
      )
      if (res.message) toast.warning(res.message)
    } catch (err) {
      toast.error(errorMessage(err) ?? "엔진 카탈로그를 불러오지 못했습니다.")
    } finally {
      setDiscovering(false)
    }
  }

  return (
    <div className="grid gap-3">
      <div>
        <h3 className="text-sm font-semibold">카탈로그 연결</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {role === "ENGINE"
            ? "이 엔진이 조회하는 카탈로그를 연결하세요. 쿼리 리니지가 연결된 카탈로그의 데이터셋으로 이어집니다. 엔진으로만 닿는 카탈로그는 ‘수집’으로 연결하면 이 엔진을 통해 메타데이터를 수집합니다."
            : "이 데이터 소스가 메타데이터를 수집하는 카탈로그입니다."}
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : linked.length === 0 ? (
        <p className="rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground">
          연결된 카탈로그가 없습니다.
        </p>
      ) : (
        <ul className="divide-y rounded-md border">
          {linked.map((c) => {
            const conn = myConnection(c)
            const locked = conn?.role === "SYNC" && c.auto_created
            return (
              <li key={c.id} className="flex items-center gap-2 px-3 py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate font-medium">{c.name}</span>
                    {c.kind && <span className="font-mono text-xs text-muted-foreground">{c.kind}</span>}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    데이터셋 {c.dataset_count}개
                    {conn?.remote_name && (
                      <>
                        {" · "}엔진 이름 <span className="font-mono">{conn.remote_name}</span>
                      </>
                    )}
                  </div>
                </div>
                {conn && (
                  <Badge variant={conn.role === "SYNC" ? "default" : "secondary"} className="shrink-0">
                    {ROLE_LABEL[conn.role]}
                  </Badge>
                )}
                {canManage && !locked && (
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-7 w-7 shrink-0"
                    title="연결 해제"
                    disabled={busy}
                    onClick={() => void run(() => removeCatalogConnection(c.id, datasource.id), `카탈로그 "${c.name}" 연결을 해제했습니다.`)}
                  >
                    <Unlink className="h-3.5 w-3.5" />
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {canManage && canQuery && (
        <div className="grid gap-2 rounded-md border p-3">
          <p className="text-xs font-medium">카탈로그 연결 추가</p>
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <Select value={pickCatalog} onValueChange={setPickCatalog}>
              <SelectTrigger className="h-8 text-sm">
                <SelectValue placeholder={candidates.length ? "카탈로그 선택" : "연결할 수 있는 카탈로그가 없습니다"} />
              </SelectTrigger>
              <SelectContent>
                {candidates.map((c) => (
                  <SelectItem key={c.id} value={String(c.id)} className="text-sm">
                    {c.name} <span className="text-muted-foreground">· {c.kind ?? "-"}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={pickRole} onValueChange={(v) => setPickRole(v as CatalogConnectionRole)}>
              <SelectTrigger className="h-8 w-[110px] text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="QUERY" className="text-sm">조회</SelectItem>
                <SelectItem value="SYNC" className="text-sm">수집</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <Input
              value={pickRemote}
              onChange={(e) => setPickRemote(e.target.value)}
              placeholder={datasource.type === "trino" ? "엔진 쪽 카탈로그 이름 (예: hive, iceberg)" : "엔진 쪽 이름 (선택)"}
              className="h-8 text-sm"
            />
            <Button size="sm" className="h-8" disabled={busy || !pickCatalog} onClick={() => void handleManualConnect()}>
              <Link2 className="mr-1 h-3.5 w-3.5" />
              연결
            </Button>
          </div>
        </div>
      )}

      {canManage && DISCOVERABLE.has(datasource.type) && (
        <div className="grid gap-2">
          <Button size="sm" variant="outline" className="justify-self-start" disabled={discovering} onClick={() => void handleDiscover()}>
            <RefreshCw className={`mr-1 h-3.5 w-3.5 ${discovering ? "animate-spin" : ""}`} />
            {discovering ? "불러오는 중..." : "Trino 에서 카탈로그 불러오기"}
          </Button>
          {discovered && discovered.catalogs.length > 0 && (
            <ul className="divide-y rounded-md border">
              {discovered.catalogs.map((e) => {
                const connected = e.connected_catalog_id != null ? catalogById.get(e.connected_catalog_id) : null
                const suggested = e.suggested_catalog_ids.filter((id) => !linkedIds.has(id))
                const others = candidates.filter((c) => !suggested.includes(c.id))
                const value = choice[e.remote_name] ?? ""
                return (
                  <li key={e.remote_name} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] items-center gap-2 px-3 py-2 text-sm">
                    <div className="min-w-0">
                      <div className="truncate font-mono">{e.remote_name}</div>
                      <div className="text-xs text-muted-foreground">{e.connector ?? "-"}</div>
                    </div>
                    {connected ? (
                      <span className="col-span-2 text-xs text-muted-foreground">
                        연결됨 → {connected.name}
                      </span>
                    ) : (
                      <>
                        <Select value={value} onValueChange={(v) => setChoice((p) => ({ ...p, [e.remote_name]: v }))}>
                          <SelectTrigger className="h-8 text-sm">
                            <SelectValue placeholder="연결할 카탈로그" />
                          </SelectTrigger>
                          <SelectContent>
                            {suggested.map((id) => (
                              <SelectItem key={id} value={String(id)} className="text-sm">
                                {catalogById.get(id)?.name ?? id} <span className="text-muted-foreground">· 추천</span>
                              </SelectItem>
                            ))}
                            {others.map((c) => (
                              <SelectItem key={c.id} value={String(c.id)} className="text-sm">
                                {c.name} <span className="text-muted-foreground">· {c.kind ?? "-"}</span>
                              </SelectItem>
                            ))}
                            <SelectItem value={NEW_SYNC} className="text-sm">
                              새 카탈로그 — 이 엔진으로 수집
                            </SelectItem>
                          </SelectContent>
                        </Select>
                        <Button
                          size="sm"
                          className="h-8"
                          disabled={busy || !value}
                          onClick={() =>
                            void (value === NEW_SYNC
                              ? connectNewSync(e.remote_name, e.connector)
                              : connect(Number(value), "QUERY", e.remote_name)
                            ).then((ok) => ok && void handleDiscover())
                          }
                        >
                          연결
                        </Button>
                      </>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
          {discovered && discovered.catalogs.length === 0 && !discovered.message && (
            <p className="text-xs text-muted-foreground">연결할 Trino 카탈로그가 없습니다 (내부 카탈로그 제외).</p>
          )}
        </div>
      )}
    </div>
  )
}
