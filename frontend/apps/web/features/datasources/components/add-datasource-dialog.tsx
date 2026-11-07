"use client"

import { useId, useState } from "react"
import { Server } from "lucide-react"
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
  DATASOURCE_TYPES,
  getDefaultConfig,
} from "@/features/datasources/datasource-configs"
import { CONNECTION_TEST_TYPES, hasRequiredFields, toastTestResult } from "@/features/datasources/utils"
import { ConfigForm } from "@/features/datasources/components/config-form"
import { assignDatasourceSystem } from "@/features/topology/api"

/**
 * 데이터 소스 추가 다이얼로그 (유형 선택 → 연결 설정).
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
  const [addStep, setAddStep] = useState<"type" | "config">("type")
  const [selectedType, setSelectedType] = useState<string>("")
  const [addDisplayName, setAddDisplayName] = useState("")
  const [addOrigin, setAddOrigin] = useState<string>("DEV")
  const [addConfig, setAddConfig] = useState<Record<string, unknown>>({})
  const [addSaving, setAddSaving] = useState(false)
  const [addTesting, setAddTesting] = useState(false)
  // 생성 후 다음 단계(설정 저장 등)가 실패한 경우의 생성된 id — 재시도 시 다시 만들지 않는다
  const [addCreatedId, setAddCreatedId] = useState<number | null>(null)
  const idPrefix = useId()

  const handleSelectType = (typeName: string) => {
    setSelectedType(typeName)
    const pt = DATASOURCE_TYPES.find((t) => t.name === typeName)
    setAddDisplayName(pt?.display ?? typeName)
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
      setAddCreatedId(null)
      onOpenChange(false)
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
          <DialogTitle>{addStep === "type" ? "데이터 소스 유형 선택" : `${addDisplayName} 추가`}</DialogTitle>
          {addStep === "type" && (
            <DialogDescription>추가할 데이터 소스 유형을 선택하세요.</DialogDescription>
          )}
        </DialogHeader>

        {addStep === "type" ? (
          <div className="grid grid-cols-2 gap-2 py-2">
            {DATASOURCE_TYPES.filter((pt) => !pt.hiddenFromAddDialog).map((pt) => (
              <Button
                key={pt.name}
                variant="outline"
                className="justify-start h-auto py-2.5 px-3 disabled:opacity-50"
                disabled={!pt.implemented}
                title={pt.implemented ? undefined : "준비 중입니다"}
                onClick={() => handleSelectType(pt.name)}
              >
                <Server className="h-4 w-4 mr-2 shrink-0 text-muted-foreground" />
                <div className="text-left flex-1">
                  <div className="text-sm font-medium flex items-center gap-1.5">
                    {pt.display}
                    {!pt.implemented && (
                      <span className="inline-flex items-center rounded border px-1 py-0 text-[10px] font-normal text-muted-foreground">
                        준비 중
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground font-mono">{pt.name}</div>
                </div>
              </Button>
            ))}
          </div>
        ) : (
          <div className="grid gap-4 py-2">
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
