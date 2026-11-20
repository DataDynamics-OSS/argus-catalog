"use client"

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
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
import { Textarea } from "@workspace/ui/components/textarea"
import { errorMessage, notifySuccess } from "@/lib/notify"
import { createCatalog, fetchCatalogs, syncConnection, type Catalog } from "@/features/catalogs/api"
import { usePermissions } from "@/features/permissions/use-permissions"
import { createDataset, fetchDatasources } from "../api"
import type { DatasetDetail, Datasource } from "../data/schema"
import { useDatasets } from "./datasets-provider"
import { datasourceRole, isDatasourceTypeImplemented } from "@/features/datasources/datasource-configs"

const ORIGINS = ["PROD", "STAGING", "DEV"] as const
// 카탈로그 선택지의 "새 카탈로그 만들기" (직접 등록 카탈로그를 그 자리에서 만든다)
const NEW_CATALOG = "__new__"

// 데이터 소스 카드 legend 와 동일한 색상 매핑: PROD=emerald, STAGING=orange, DEV=sky.
const originColor = (origin: string | undefined) =>
  origin === "PROD"
    ? "text-emerald-600"
    : origin === "STAGING"
      ? "text-orange-500"
      : "text-sky-500"

/**
 * 데이터셋을 담을 수 있는 카탈로그(TODO-CAT-001 설계 9.3):
 * - 직접 등록한(ARGUS) 카탈로그 → 수동 데이터셋
 * - 수집 카탈로그 중 수집(SYNC) 데이터 소스가 구현된 원천(엔진이 아닌)인 것 — 자동 카탈로그는 데이터 소스와 1:1 이라 예전 흐름과 같다
 */
export function canHoldDatasets(c: Catalog, datasources: Datasource[]): boolean {
  if (c.source_type === "ARGUS") return true
  const sync = syncConnection(c)
  if (!sync) return false
  const ds = datasources.find((d) => d.id === sync.datasource_id)
  return Boolean(ds) && isDatasourceTypeImplemented(ds!.type) && datasourceRole(ds!.type) !== "ENGINE"
}

/**
 * 데이터셋 추가 — 카탈로그를 고른다. presetCatalogId 가 있으면(카탈로그 상세에서 열 때) 그 카탈로그로 고정.
 * manualOnly 면 데이터 소스 없는 수동 데이터셋만 — 직접 등록한(ARGUS) 카탈로그만 고르고, 카탈로그 관리 권한이 있으면
 * 새 카탈로그를 그 자리에서 만들 수 있다(분류 체계 화면, TODO-CAT-001).
 */
export function DatasetCreateDialog({
  open,
  onOpenChange,
  presetCatalogId,
  manualOnly = false,
  title = "데이터셋 추가",
  intro,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  presetCatalogId?: number
  manualOnly?: boolean
  title?: string
  /** 대화상자 맨 위 안내 한 줄 */
  intro?: ReactNode
  onCreated: (created: DatasetDetail) => void | Promise<void>
}) {
  const { canUseFeature } = usePermissions()
  const canCreateCatalog = canUseFeature("catalogs.manage") || canUseFeature("datasources.manage")
  const [newCatalogName, setNewCatalogName] = useState("")
  const [catalogs, setCatalogs] = useState<Catalog[]>([])
  const [datasources, setDatasources] = useState<Datasource[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [name, setName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [catalogId, setCatalogId] = useState(presetCatalogId ? String(presetCatalogId) : "")
  const [manualOrigin, setManualOrigin] = useState<string>("PROD")
  const [qualifiedPath, setQualifiedPath] = useState("")
  const [summary, setSummary] = useState("")
  const [description, setDescription] = useState("")

  useEffect(() => {
    if (!open) return
    Promise.all([fetchCatalogs(), fetchDatasources()])
      .then(([c, d]) => { setCatalogs(c); setDatasources(d) })
      .catch((e) => setError(errorMessage(e) ?? "카탈로그 목록을 불러오지 못했습니다."))
  }, [open])

  const availableCatalogs = useMemo(
    () => catalogs.filter((c) => (manualOnly ? c.source_type === "ARGUS" : canHoldDatasets(c, datasources))),
    [catalogs, datasources, manualOnly],
  )
  const creatingCatalog = catalogId === NEW_CATALOG
  // 수동 전용인데 직접 등록한 카탈로그가 하나도 없으면 새로 만들기를 기본으로
  useEffect(() => {
    if (manualOnly && canCreateCatalog && catalogs.length > 0 && availableCatalogs.length === 0 && !catalogId) {
      setCatalogId(NEW_CATALOG)
    }
  }, [manualOnly, canCreateCatalog, catalogs.length, availableCatalogs.length, catalogId])
  const selectedCatalog = useMemo(
    () => catalogs.find((c) => String(c.id) === catalogId),
    [catalogs, catalogId],
  )
  const isManual = creatingCatalog || selectedCatalog?.source_type === "ARGUS"
  // 수집 카탈로그는 수집 데이터 소스의 환경을 그대로(데이터 소스가 SoT), 직접 등록한 카탈로그는 고른다
  const syncDatasource = useMemo(() => {
    const sync = selectedCatalog ? syncConnection(selectedCatalog) : undefined
    return sync ? datasources.find((d) => d.id === sync.datasource_id) : undefined
  }, [selectedCatalog, datasources])
  const effectiveOrigin = isManual ? manualOrigin : (syncDatasource?.origin ?? "DEV")

  const reset = () => {
    setName("")
    setDisplayName("")
    setCatalogId(presetCatalogId ? String(presetCatalogId) : "")
    setQualifiedPath("")
    setSummary("")
    setDescription("")
    setNewCatalogName("")
  }

  const handleSubmit = useCallback(async () => {
    if (!name.trim() || !catalogId || (creatingCatalog && !newCatalogName.trim())) return
    setSaving(true)
    setError(null)
    try {
      let targetCatalogId = Number(catalogId)
      if (creatingCatalog) {
        const cat = await createCatalog({ name: newCatalogName.trim(), source_type: "ARGUS" })
        setCatalogs((rows) => [...rows, cat])
        setCatalogId(String(cat.id))
        targetCatalogId = cat.id
      }
      const created = await createDataset({
        name: name.trim(),
        display_name: displayName.trim() || undefined,
        catalog_id: targetCatalogId,
        qualified_name: qualifiedPath.trim() || undefined,
        summary: summary.trim() || undefined,
        description: description.trim() || undefined,
        origin: effectiveOrigin,
      })
      reset()
      onOpenChange(false)
      notifySuccess(`데이터셋 '${created?.name ?? name.trim()}' 을(를) 추가했습니다.`)
      await onCreated(created)
    } catch (e) {
      setError(errorMessage(e) ?? "데이터셋을 생성하지 못했습니다.")
    } finally {
      setSaving(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, displayName, catalogId, creatingCatalog, newCatalogName, qualifiedPath, summary, description, effectiveOrigin, onOpenChange, onCreated])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          {intro && <p className="text-sm">{intro}</p>}
          <div className="grid gap-2">
            <Label htmlFor="ds-name">
              물리명 <span className="text-xs">(테이블명)</span>
            </Label>
            <Input
              id="ds-name"
              placeholder="예: user_events"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ds-display-name">
              논리명 <span className="text-xs">(선택, 표시용)</span>
            </Label>
            <Input
              id="ds-display-name"
              placeholder="예: 사용자 이벤트"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              maxLength={255}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ds-catalog">카탈로그</Label>
            <Select value={catalogId} onValueChange={setCatalogId} disabled={presetCatalogId !== undefined}>
              <SelectTrigger id="ds-catalog">
                <SelectValue placeholder="카탈로그 선택" />
              </SelectTrigger>
              <SelectContent>
                {manualOnly && canCreateCatalog && (
                  <SelectItem value={NEW_CATALOG}>+ 새 카탈로그 만들기</SelectItem>
                )}
                {availableCatalogs.map((c) => (
                  <SelectItem key={c.id} value={String(c.id)}>
                    <span className="inline-flex items-center gap-2">
                      <span>{c.name}</span>
                      <span className="text-[11px] text-muted-foreground">
                        {c.source_type === "ARGUS" ? "직접 등록" : (syncConnection(c)?.datasource_type ?? c.kind ?? "")}
                      </span>
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {creatingCatalog && (
              <Input
                aria-label="새 카탈로그 이름"
                placeholder="새 카탈로그 이름 (예: 엑셀 관리 자료)"
                value={newCatalogName}
                onChange={(e) => setNewCatalogName(e.target.value)}
                maxLength={200}
              />
            )}
            {manualOnly && !canCreateCatalog && catalogs.length > 0 && availableCatalogs.length === 0 && (
              <p className="text-xs text-destructive">
                직접 등록한 카탈로그가 없습니다. 카탈로그 관리 권한이 있는 사람이 데이터 카탈로그 › 카탈로그에서 먼저 만들어야 합니다.
              </p>
            )}
            {(selectedCatalog || creatingCatalog) && isManual && (
              <p className="text-xs">
                직접 등록한 카탈로그입니다 — 메타데이터를 수집하지 않는 <b>수동 데이터셋</b>이 되고, 나중에 실제 테이블에 매핑할 수 있습니다.
              </p>
            )}
            {selectedCatalog && !isManual && syncDatasource && (
              <p className="text-xs">
                데이터 소스 <b>{syncDatasource.name}</b> 에 등록합니다. 환경은 데이터 소스의 값
                <span className={`ml-1 font-mono font-semibold ${originColor(effectiveOrigin)}`}>
                  {effectiveOrigin}
                </span>
                {" "}을 따릅니다.
              </p>
            )}
          </div>
          {isManual && (
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-2">
                <Label htmlFor="ds-origin">환경</Label>
                <Select value={manualOrigin} onValueChange={setManualOrigin}>
                  <SelectTrigger id="ds-origin"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {ORIGINS.map((o) => (
                      <SelectItem key={o} value={o}>
                        <span className={`font-mono font-semibold ${originColor(o)}`}>{o}</span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="ds-path">
                  경로 <span className="text-xs">(선택)</span>
                </Label>
                <Input
                  id="ds-path"
                  className="font-mono"
                  placeholder={name.trim() || "예: sales.orders"}
                  value={qualifiedPath}
                  onChange={(e) => setQualifiedPath(e.target.value)}
                  maxLength={500}
                />
              </div>
            </div>
          )}
          <div className="grid gap-2">
            <Label htmlFor="ds-summary">
              요약 <span className="text-xs">(한 줄, 최대 200자)</span>
            </Label>
            <Input
              id="ds-summary"
              placeholder="예: 매장 주문 트랜잭션"
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              maxLength={200}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ds-desc">상세 설명</Label>
            <Textarea
              id="ds-desc"
              placeholder="이 데이터셋의 내용을 자세히 설명하세요"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={saving}
            >
              취소
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={saving || !name.trim() || !catalogId || (creatingCatalog && !newCatalogName.trim())}
            >
              {saving ? "추가 중…" : "추가"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/** 데이터셋 목록 화면의 [데이터셋 추가] — 목록 provider 와 연결한다. */
export function DatasetsAddDialog() {
  const { open, setOpen, refreshDatasets } = useDatasets()
  return (
    <DatasetCreateDialog
      open={open === "add"}
      onOpenChange={(o) => setOpen(o ? "add" : null)}
      onCreated={() => refreshDatasets()}
    />
  )
}
