"use client"

import { useId, useState, type ComponentType } from "react"
import { Database, Layers, ListFilter, Server, Snowflake as IcebergIcon } from "lucide-react"
import { toast, notifyError } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import { DisabledReason } from "@/components/tip"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import {
  createDatasource,
  fetchSyncSettings,
  saveDatasourceConfig,
  startSyncRun,
  testDatasourceConnection,
  updateDatasource,
  type SyncSettings,
} from "@/features/datasources/api"
import {
  DATASOURCE_CONFIGS,
  DATASOURCE_ROLE_LABELS,
  DATASOURCE_TYPES,
  datasourceRole,
  getDefaultConfig,
  type DatasourceRole,
} from "@/features/datasources/datasource-configs"
import { CONNECTION_TEST_TYPES, hasRequiredFields, toastTestResult } from "@/features/datasources/utils"
import { ConfigForm } from "@/features/datasources/components/config-form"
import { assignDatasourceSystem } from "@/features/topology/api"
import { CatalogBindings } from "@/features/catalogs/components/catalog-bindings"
import { SyncScopeDialog } from "@/features/datasources/components/sync-scope-dialog"
import { SYNC_PERMISSION_MISSING_MESSAGE } from "@/features/datasources/sync-format"

const ROLE_ORDER: Exclude<DatasourceRole, "MANUAL">[] = ["CATALOG", "DATABASE", "ENGINE"]
const ROLE_ICON = { CATALOG: Layers, DATABASE: Database, ENGINE: Server } as const
// Iceberg 는 테이블 포맷 — 카탈로그 구현에 따라 실제 데이터 소스 유형이 달라진다
// Apache Iceberg 카드 안에서 고르는 유형 — 유형 목록에는 따로 보이지 않는다
const ICEBERG_TYPES = new Set(["iceberg-rest", "iceberg-hadoop"])

const ICEBERG_CATALOGS: { key: string; type: string | null; title: string; desc: string }[] = [
  {
    key: "rest",
    type: "iceberg-rest",
    title: "REST Catalog",
    desc: "Polaris · Gravitino · Nessie · Tabular 등 Iceberg REST 규격 카탈로그",
  },
  {
    key: "hms",
    type: "hive",
    title: "Hive Metastore (HiveCatalog)",
    desc: "Iceberg 테이블이 HMS 에 등록된 경우 — HMS 카탈로그가 Hive·Iceberg 테이블을 함께 수집합니다.",
  },
  {
    key: "hadoop",
    type: "iceberg-hadoop",
    title: "Hadoop Catalog",
    desc: "메타스토어 없이 웨어하우스 경로(S3·HDFS)에 만든 테이블 — Spark type=hadoop 등",
  },
]

/**
 * 데이터 소스 추가 다이얼로그 (유형 선택 → [Iceberg 카탈로그 선택] → 연결 설정 → [엔진: 카탈로그 연결 | 수집 범위 선택]).
 * 열 때마다 부모가 key 를 바꿔 다시 마운트하므로 입력 상태는 항상 초기값에서 시작한다.
 */
export function AddDatasourceDialog({
  open,
  onOpenChange,
  targetSystemId,
  onReload,
  onCreated,
  canSync = false,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 컨텍스트 메뉴로 지정한 시스템 — null 이면 미분류로 생성 */
  targetSystemId: number | null
  onReload: () => Promise<void>
  /**
   * 생성한 데이터 소스로 끝났을 때(닫기·나중에·범위 저장·"저장하고 지금 수집") — 부모가 새 항목을 선택한다(TODO-074).
   * "지금 수집" 이면 수집 요청이 끝난 뒤 불러, 선택된 상세의 수집 패널이 진행 중인 실행을 바로 보여 준다.
   */
  onCreated?: (id: number) => void
  /** 수집 권한(datasources.sync) — 있으면 생성 뒤 "수집 범위 선택" 단계로 이어진다 */
  canSync?: boolean
}) {
  const [addStep, setAddStep] = useState<"type" | "iceberg" | "config" | "bindings" | "scope">("type")
  // 생성 뒤 수집 범위 단계 — 화면 수집을 지원하는 유형만(설계 §4.1). "나중에"면 범위 없이 둔다(수집 차단)
  const [scopeSettings, setScopeSettings] = useState<SyncSettings | null>(null)
  const [scopeOpen, setScopeOpen] = useState(false)
  // Iceberg 카드로 들어온 경우 — 설정 화면에 안내 문구와 '이전' 대상이 달라진다
  const [viaIceberg, setViaIceberg] = useState(false)
  const [selectedType, setSelectedType] = useState<string>("")
  const [addDisplayName, setAddDisplayName] = useState("")
  const [addOrigin, setAddOrigin] = useState<string>("DEV")
  const [addConfig, setAddConfig] = useState<Record<string, unknown>>({})
  const [addSaving, setAddSaving] = useState(false)
  const [addTesting, setAddTesting] = useState(false)
  // 생성 후 다음 단계(설정 저장 등)가 실패한 경우의 생성된 id — 재시도 시 다시 만들지 않는다
  const [addCreatedId, setAddCreatedId] = useState<number | null>(null)
  const idPrefix = useId()

  // 생성한 데이터 소스로 마무리 — 다이얼로그를 닫고 부모가 새 항목을 선택하게 한다
  const finishCreated = (id: number | null) => {
    setAddCreatedId(null)
    onOpenChange(false)
    if (id != null) onCreated?.(id)
  }

  const handleSelectType = (typeName: string, displayName?: string) => {
    setSelectedType(typeName)
    const pt = DATASOURCE_TYPES.find((t) => t.name === typeName)
    setAddDisplayName(displayName ?? pt?.display ?? typeName)
    setAddConfig(getDefaultConfig(typeName))
    setAddStep("config")
  }

  const handleAddSave = async () => {
    if (!selectedType || !addDisplayName.trim()) return
    setAddSaving(true)
    const name = addDisplayName.trim()
    let createdId = addCreatedId
    try {
      if (createdId == null) {
        const created = await createDatasource({ name, type: selectedType, origin: addOrigin })
        createdId = created.id
        setAddCreatedId(created.id)
      } else {
        // 이전 시도에서 이미 생성됨 — 다시 만들지 않고 이름/환경만 맞춘다
        await updateDatasource(createdId, { name, origin: addOrigin })
      }
      await saveDatasourceConfig(createdId, addConfig)
      // 컨텍스트 메뉴로 지정한 시스템에만 배정(미분류/루트에서 추가하면 미분류 유지)
      const targetSys = targetSystemId
      let assignFailed = false
      if (targetSys != null) {
        await assignDatasourceSystem(createdId, targetSys).catch(() => {
          assignFailed = true
        })
      }
      if (assignFailed) {
        toast.warning(`데이터 소스 "${name}" 을(를) 생성했지만 시스템 배정에 실패해 미분류로 두었습니다.`)
      } else {
        toast.success(`데이터 소스 "${name}" 을(를) 생성했습니다.`)
      }
      if (datasourceRole(selectedType) === "ENGINE") {
        // 엔진은 자기 카탈로그가 없다 — 이어서 조회할 카탈로그를 연결한다
        setAddStep("bindings")
      } else {
        await continueToScope(createdId)
      }
    } catch (err) {
      if (createdId != null) {
        notifyError(err, "데이터 소스는 추가했지만 다음 단계를 마치지 못했습니다. 다시 \"추가\"를 누르면 이어서 저장합니다.")
      } else {
        notifyError(err, "데이터 소스를 생성하지 못했습니다.")
      }
    } finally {
      setAddSaving(false)
      if (createdId != null) await onReload()
    }
  }

  /**
   * 생성(엔진은 카탈로그 연결) 뒤 수집 범위 단계로 잇는다. 화면 수집을 지원하지 않거나,
   * 엔진에 '수집' 연결이 없으면(DEF-068 — 수집하면 409) 범위 단계 없이 닫는다.
   * 수집 권한이 없으면 범위 단계 대신 권한 안내를 보여 준다(DEF-069).
   */
  const continueToScope = async (createdId: number) => {
    const sync = await fetchSyncSettings(createdId).catch(() => null)
    if (sync?.executor && !sync.sync_catalog_missing) {
      setScopeSettings(sync)
      setAddStep("scope")
    } else {
      finishCreated(createdId)
    }
  }

  const handleAddTest = async () => {
    if (!selectedType) return
    setAddTesting(true)
    try {
      toastTestResult(await testDatasourceConnection(selectedType, addConfig, addCreatedId ?? undefined))
    } finally {
      setAddTesting(false)
    }
  }

  const addFields = DATASOURCE_CONFIGS[selectedType] ?? []
  const addConfigValid = addFields.length === 0 || hasRequiredFields(addFields, addConfig)

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (addSaving) return
        // 이미 만든 뒤(범위·카탈로그 연결 단계 등)에 X·바깥 클릭으로 닫아도 새 항목을 선택한다
        if (!o && addCreatedId != null) finishCreated(addCreatedId)
        else onOpenChange(o)
      }}
    >
      <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {addStep === "type"
              ? "무엇을 추가하나요?"
              : addStep === "iceberg"
                ? "Iceberg 카탈로그 선택"
                : addStep === "bindings"
                  ? `${addDisplayName} — 카탈로그 연결`
                  : addStep === "scope"
                    ? `${addDisplayName} — 수집 범위`
                    : `${addDisplayName} 추가`}
          </DialogTitle>
          {addStep === "type" && (
            <DialogDescription>
              카탈로그·데이터베이스는 메타데이터를 수집하고, 쿼리 엔진은 등록된 카탈로그에 연결해 쿼리 이력과 리니지를 수집합니다.
            </DialogDescription>
          )}
          {addStep === "iceberg" && (
            <DialogDescription>
              Iceberg 는 테이블 포맷입니다. 테이블이 등록된 카탈로그를 고르세요. Impala·Trino·StarRocks 는 추가한 뒤 이 카탈로그에 연결합니다.
            </DialogDescription>
          )}
          {addStep === "bindings" && (
            <DialogDescription>
              데이터 소스를 생성했습니다. 이 엔진이 조회하는 카탈로그를 연결하세요. 나중에 데이터 소스 상세에서도 바꿀 수 있습니다.
            </DialogDescription>
          )}
        </DialogHeader>

        {addStep === "type" ? (
          <div className="grid gap-4 py-2">
            {ROLE_ORDER.map((role) => {
              const types = DATASOURCE_TYPES.filter(
                (pt) => pt.role === role && !pt.hiddenFromAddDialog && !ICEBERG_TYPES.has(pt.name),
              )
              const Icon = ROLE_ICON[role]
              return (
                <section key={role} className="grid gap-2">
                  <div>
                    <p className="text-sm font-semibold">{DATASOURCE_ROLE_LABELS[role].title}</p>
                    <p className="text-xs">{DATASOURCE_ROLE_LABELS[role].desc}</p>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {role === "CATALOG" && (
                      <TypeCard
                        icon={IcebergIcon}
                        title="Apache Iceberg"
                        subtitle="REST · HMS · Hadoop"
                        onClick={() => setAddStep("iceberg")}
                      />
                    )}
                    {types.map((pt) => (
                      <TypeCard
                        key={pt.name}
                        icon={Icon}
                        title={pt.display}
                        subtitle={pt.name}
                        disabled={!pt.implemented}
                        onClick={() => {
                          setViaIceberg(false)
                          handleSelectType(pt.name)
                        }}
                      />
                    ))}
                  </div>
                </section>
              )
            })}
          </div>
        ) : addStep === "iceberg" ? (
          <div className="grid gap-2 py-2">
            {ICEBERG_CATALOGS.map((c) => (
              <TypeCard
                key={c.key}
                icon={IcebergIcon}
                title={c.title}
                subtitle={c.desc}
                disabled={c.type == null}
                onClick={() => {
                  if (!c.type) return
                  setViaIceberg(true)
                  handleSelectType(c.type, c.type === "hive" ? "Hive Metastore (Iceberg)" : undefined)
                }}
              />
            ))}
            <div className="flex justify-start pt-1">
              <Button variant="ghost" size="sm" onClick={() => setAddStep("type")}>
                이전
              </Button>
            </div>
          </div>
        ) : addStep === "scope" && addCreatedId != null && scopeSettings && !canSync ? (
          // 수집 권한(datasources.sync) 없이 데이터 소스만 만든 경우 — 범위를 고를 수 없음을 알린다(DEF-069)
          <div className="grid gap-4 py-2">
            <p className="text-sm">
              데이터 소스를 생성했습니다. {SYNC_PERMISSION_MISSING_MESSAGE}
            </p>
            <div className="flex justify-end gap-2 border-t pt-3">
              <Button
                onClick={() => finishCreated(addCreatedId)}
              >
                닫기
              </Button>
            </div>
          </div>
        ) : addStep === "scope" && addCreatedId != null && scopeSettings ? (
          <div className="grid gap-4 py-2">
            <p className="text-sm">
              데이터 소스를 생성했습니다. 메타데이터를 가져올 {scopeSettings.container_label}를 고르세요.
              범위를 정하기 전에는 수집할 수 없습니다 — 실수로 전체를 가져오지 않도록. 나중에 데이터 소스 상세의 “수집”에서도 고를 수 있습니다.
            </p>
            <div className="flex justify-end gap-2 border-t pt-3">
              <Button
                variant="outline"
                onClick={() => finishCreated(addCreatedId)}
              >
                나중에
              </Button>
              <Button onClick={() => setScopeOpen(true)}>
                <ListFilter className="mr-1 size-4" />
                범위 선택…
              </Button>
            </div>
            <SyncScopeDialog
              open={scopeOpen}
              onOpenChange={setScopeOpen}
              datasource={{ id: addCreatedId, name: addDisplayName.trim(), type: selectedType }}
              settings={scopeSettings}
              onSaved={(_saved, runNow) => {
                const id = addCreatedId
                if (!runNow) {
                  finishCreated(id)
                  return
                }
                setAddCreatedId(null)
                onOpenChange(false)
                // 수집 요청이 끝난 뒤 선택 — 새로 열린 수집 패널이 실행 중인 수집을 바로 보여 준다
                startSyncRun(id)
                  .then(() => toast.success("수집을 시작했습니다. 오른쪽 \"수집\"에서 진행을 볼 수 있습니다."))
                  .catch((e) => notifyError(e, "수집을 시작하지 못했습니다."))
                  .finally(() => onCreated?.(id))
              }}
            />
          </div>
        ) : addStep === "bindings" && addCreatedId != null ? (
          <div className="grid gap-4 py-2">
            <CatalogBindings
              datasource={{ id: addCreatedId, name: addDisplayName.trim(), type: selectedType }}
              canManage
            />
            <div className="flex justify-end gap-2 border-t pt-3">
              {/* '수집' 으로 연결했으면 범위 단계로 잇고, 조회(QUERY)만 연결했으면 닫는다(DEF-068) */}
              <Button onClick={() => void continueToScope(addCreatedId)}>
                완료
              </Button>
            </div>
          </div>
        ) : (
          <div className="grid gap-4 py-2">
            {viaIceberg && selectedType === "hive" && (
              <p className="rounded-md border bg-muted/40 px-3 py-2 text-xs">
                Hive Metastore 를 카탈로그로 등록합니다. 이 HMS 에 있는 Hive·Iceberg 테이블을 함께 수집하며,
                같은 HMS 를 쓰는 Impala·Trino 는 엔진으로 추가해 이 카탈로그에 연결하세요.
              </p>
            )}
            <div className="grid gap-1.5">
              <Label htmlFor={`${idPrefix}-name`} className="text-sm">
                표시 이름 <span className="text-destructive">*</span>
              </Label>
              <Input
                id={`${idPrefix}-name`}
                value={addDisplayName}
                onChange={(e) => setAddDisplayName(e.target.value)}
                placeholder="예: 운영 PostgreSQL"
                className="h-9 text-sm"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={`${idPrefix}-origin`} className="text-sm">
                환경 <span className="text-destructive">*</span>
              </Label>
              <Select value={addOrigin} onValueChange={setAddOrigin}>
                <SelectTrigger id={`${idPrefix}-origin`} className="h-9 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="DEV" className="text-sm">DEV</SelectItem>
                  <SelectItem value="STAGING" className="text-sm">STAGING</SelectItem>
                  <SelectItem value="PROD" className="text-sm">PROD</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {addFields.length > 0 && (
              <div className="border-t pt-3">
                <p className="text-sm font-medium mb-3">연결 설정</p>
                <ConfigForm
                  fields={addFields}
                  values={addConfig}
                  onChange={(k, v) => setAddConfig((prev) => ({ ...prev, [k]: v }))}
                />
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2">
              {addCreatedId == null && (
                <Button
                  variant="ghost"
                  className="mr-auto"
                  disabled={addSaving}
                  onClick={() => setAddStep(viaIceberg ? "iceberg" : "type")}
                >
                  이전
                </Button>
              )}
              <Button variant="outline" onClick={() => onOpenChange(false)} disabled={addSaving}>
                취소
              </Button>
              {CONNECTION_TEST_TYPES.has(selectedType) && (
                <Button variant="outline" onClick={handleAddTest} disabled={addTesting || addSaving}>
                  {addTesting ? "테스트 중…" : "연결 테스트"}
                </Button>
              )}
              <Button onClick={handleAddSave} disabled={addSaving || !addDisplayName.trim() || !addConfigValid}>
                {addSaving ? "추가 중…" : "추가"}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function TypeCard({
  icon: Icon,
  title,
  subtitle,
  disabled,
  onClick,
}: {
  icon: ComponentType<{ className?: string }>
  title: string
  subtitle: string
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <DisabledReason className="grid" reason={disabled ? "준비 중입니다" : undefined}><Button
      variant="outline"
      className="h-auto justify-start whitespace-normal px-3 py-2.5 disabled:opacity-50"
      disabled={disabled}
      onClick={onClick}
    >
      <Icon className="mr-2 h-4 w-4 shrink-0 self-start text-muted-foreground" />
      <div className="flex-1 text-left">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          {title}
          {disabled && (
            <span className="inline-flex items-center rounded border px-1 py-0 text-[11px] font-normal">
              준비 중
            </span>
          )}
        </div>
        <div className="text-xs font-normal">{subtitle}</div>
      </div>
    </Button></DisabledReason>
  )
}
