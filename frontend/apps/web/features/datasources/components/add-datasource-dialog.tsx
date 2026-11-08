"use client"

import { useId, useState, type ComponentType } from "react"
import { ArrowLeft, Database, Layers, Server, Snowflake as IcebergIcon } from "lucide-react"
import { errorMessage, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
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
  saveDatasourceConfig,
  testDatasourceConnection,
  updateDatasource,
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

const ROLE_ORDER: DatasourceRole[] = ["CATALOG", "DATABASE", "ENGINE"]
const ROLE_ICON = { CATALOG: Layers, DATABASE: Database, ENGINE: Server } as const
// Iceberg 는 테이블 포맷 — 카탈로그 구현에 따라 실제 데이터 소스 유형이 달라진다
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
  { key: "hadoop", type: null, title: "Hadoop Catalog", desc: "파일 시스템 경로 기반 카탈로그 — 준비 중" },
]

/**
 * 데이터 소스 추가 다이얼로그 (유형 선택 → [Iceberg 카탈로그 선택] → 연결 설정 → [엔진: 카탈로그 연결]).
 * 열 때마다 부모가 key 를 바꿔 다시 마운트하므로 입력 상태는 항상 초기값에서 시작한다.
 */
export function AddDatasourceDialog({
  open,
  onOpenChange,
  targetSystemId,
  onReload,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 컨텍스트 메뉴로 지정한 시스템 — null 이면 미분류로 생성 */
  targetSystemId: number | null
  onReload: () => Promise<void>
}) {
  const [addStep, setAddStep] = useState<"type" | "iceberg" | "config" | "bindings">("type")
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
        setAddCreatedId(null)
        onOpenChange(false)
      }
    } catch (err) {
      const msg = errorMessage(err) ?? "데이터 소스를 생성하지 못했습니다."
      toast.error(
        createdId != null
          ? `데이터 소스는 생성됐지만 다음 단계에 실패했습니다: ${msg} — 다시 "생성"을 누르면 이어서 저장합니다.`
          : msg,
      )
    } finally {
      setAddSaving(false)
      if (createdId != null) await onReload()
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
    <Dialog open={open} onOpenChange={(o) => !addSaving && onOpenChange(o)}>
      <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {addStep === "type"
              ? "무엇을 추가하나요?"
              : addStep === "iceberg"
                ? "Iceberg 카탈로그 선택"
                : addStep === "bindings"
                  ? `${addDisplayName} — 카탈로그 연결`
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
                (pt) => pt.role === role && !pt.hiddenFromAddDialog && pt.name !== "iceberg-rest",
              )
              const Icon = ROLE_ICON[role]
              return (
                <section key={role} className="grid gap-2">
                  <div>
                    <p className="text-sm font-semibold">{DATASOURCE_ROLE_LABELS[role].title}</p>
                    <p className="text-xs text-muted-foreground">{DATASOURCE_ROLE_LABELS[role].desc}</p>
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
                  handleSelectType(c.type, c.type === "hive" ? "Hive Metastore (Iceberg)" : "Iceberg REST Catalog")
                }}
              />
            ))}
            <div className="flex justify-start pt-1">
              <Button variant="ghost" size="sm" onClick={() => setAddStep("type")}>
                <ArrowLeft className="mr-1 h-3.5 w-3.5" />
                이전
              </Button>
            </div>
          </div>
        ) : addStep === "bindings" && addCreatedId != null ? (
          <div className="grid gap-4 py-2">
            <CatalogBindings
              datasource={{ id: addCreatedId, name: addDisplayName.trim(), type: selectedType }}
              canManage
            />
            <div className="flex justify-end gap-2 border-t pt-3">
              <Button
                onClick={() => {
                  setAddCreatedId(null)
                  onOpenChange(false)
                }}
              >
                완료
              </Button>
            </div>
          </div>
        ) : (
          <div className="grid gap-4 py-2">
            {viaIceberg && selectedType === "hive" && (
              <p className="rounded-md border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
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
                  <ArrowLeft className="mr-1 h-3.5 w-3.5" />
                  이전
                </Button>
              )}
              <Button variant="outline" onClick={() => onOpenChange(false)} disabled={addSaving}>
                취소
              </Button>
              {CONNECTION_TEST_TYPES.has(selectedType) && (
                <Button variant="outline" onClick={handleAddTest} disabled={addTesting || addSaving}>
                  {addTesting ? "테스트 중..." : "연결 테스트"}
                </Button>
              )}
              <Button onClick={handleAddSave} disabled={addSaving || !addDisplayName.trim() || !addConfigValid}>
                {addSaving ? "생성 중..." : "생성"}
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
    <Button
      variant="outline"
      className="h-auto justify-start whitespace-normal px-3 py-2.5 disabled:opacity-50"
      disabled={disabled}
      title={disabled ? "준비 중입니다" : undefined}
      onClick={onClick}
    >
      <Icon className="mr-2 h-4 w-4 shrink-0 self-start text-muted-foreground" />
      <div className="flex-1 text-left">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          {title}
          {disabled && (
            <span className="inline-flex items-center rounded border px-1 py-0 text-[10px] font-normal text-muted-foreground">
              준비 중
            </span>
          )}
        </div>
        <div className="text-xs font-normal text-muted-foreground">{subtitle}</div>
      </div>
    </Button>
  )
}
