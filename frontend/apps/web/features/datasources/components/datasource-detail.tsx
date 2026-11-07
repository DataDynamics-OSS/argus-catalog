"use client"

import { useEffect, useId, useMemo, useState } from "react"
import { Server } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { useAuth } from "@/features/auth"
import { usePermissions } from "@/features/permissions/use-permissions"
import {
  fetchDatasourceConfig,
  testDatasourceConnection,
  type DatasourceRow,
} from "@/features/datasources/api"
import { DATASOURCE_CONFIGS, getDefaultConfig } from "@/features/datasources/datasource-configs"
import {
  CONNECTION_TEST_TYPES,
  changedTargetKeys,
  hasRequiredFields,
  isDatasourceFormDirty,
  toastTestResult,
  type FlatSystem,
} from "@/features/datasources/utils"
import { ConfigForm } from "@/features/datasources/components/config-form"
import { InfoTable } from "@/features/datasources/components/detail-tables"
import { errorMessage } from "@/lib/notify"

export type SaveDatasourceHandler = (
  id: number,
  name: string,
  origin: string,
  systemId: number | null,
  // null = 연결 설정은 변경 없음(또는 조회 권한 없음) → 저장하지 않는다
  config: Record<string, unknown> | null,
) => Promise<boolean>

// 데이터 소스 인라인 편집 폼 (우측 상세). key={datasource.id} 로 선택 변경 시 폼/설정이 리셋된다.
export function DatasourceDetail({
  datasource,
  flatSystems,
  onSave,
  canManage,
}: {
  datasource: DatasourceRow
  flatSystems: FlatSystem[]
  canManage: boolean
  onSave: SaveDatasourceHandler
}) {
  const { user } = useAuth()
  const { isFeatureGranted } = usePermissions()
  // 연결 설정은 명시 허용형 권한(미설정이면 관리자 전용)
  const canViewConnection = !!user?.is_admin || isFeatureGranted("datasources.connection")
  const [name, setName] = useState(datasource.name)
  const [origin, setOrigin] = useState(datasource.origin ?? "DEV")
  const [systemId, setSystemId] = useState<number | null>(datasource.system_id ?? null)
  const [config, setConfig] = useState<Record<string, unknown>>({})
  // 불러온 원본(변경 여부 판단용)과 저장된 비밀값 키
  const [loadedConfig, setLoadedConfig] = useState<string>("{}")
  const [storedSecrets, setStoredSecrets] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  // 환경 변경은 소속 데이터셋 전체에 반영되므로 저장 전에 확인한다
  const [confirmOrigin, setConfirmOrigin] = useState(false)
  const idPrefix = useId()

  useEffect(() => {
    if (!canViewConnection) {
      setLoading(false)
      return
    }
    let active = true
    setLoading(true)
    setLoadError(null)
    fetchDatasourceConfig(datasource.id)
      .then((c) => {
        if (!active) return
        const initial = c?.config ?? getDefaultConfig(datasource.type)
        setConfig(initial)
        setLoadedConfig(c ? JSON.stringify(c.config) : "")
        setStoredSecrets(c?.secretFields ?? [])
      })
      .catch((e) => {
        // 기본값 폼으로 대체하지 않는다 — 그 상태로 저장하면 기존 설정을 덮어쓴다
        if (active) setLoadError(errorMessage(e) ?? String(e))
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [datasource.id, datasource.type, canViewConnection])

  const fields = DATASOURCE_CONFIGS[datasource.type] ?? []
  const configDirty = canViewConnection && !loadError && JSON.stringify(config) !== loadedConfig
  // 접속 대상(host·port 등)을 바꾸면 저장된 비밀값은 쓸 수 없다(서버가 거부) — 다시 입력받는다
  const targetChanged = useMemo(() => {
    if (!storedSecrets.length || !loadedConfig) return false
    try {
      return changedTargetKeys(config, JSON.parse(loadedConfig) as Record<string, unknown>).length > 0
    } catch {
      return false
    }
  }, [config, loadedConfig, storedSecrets])
  const usableSecrets = targetChanged ? [] : storedSecrets
  const configValid = !configDirty || fields.length === 0 || hasRequiredFields(fields, config, usableSecrets)
  const dirty = isDatasourceFormDirty({ name, origin, systemId, configDirty }, datasource)

  const save = async (originConfirmed = false) => {
    if (!dirty || !name.trim() || !configValid) return
    if (!originConfirmed && origin !== (datasource.origin ?? "DEV")) {
      setConfirmOrigin(true)
      return
    }
    setConfirmOrigin(false)
    setSaving(true)
    try {
      const ok = await onSave(datasource.id, name.trim(), origin, systemId, configDirty ? config : null)
      if (ok && configDirty) {
        setLoadedConfig(JSON.stringify(config))
        // 새로 입력한 비밀값은 저장됨 상태로 전환하고 입력란은 비운다
        const typed = Object.entries(config)
          .filter(([k, v]) => fields.some((f) => f.key === k && f.type === "password") && v)
          .map(([k]) => k)
        if (typed.length) {
          setStoredSecrets((prev) => Array.from(new Set([...prev, ...typed])))
          const cleared = { ...config }
          for (const k of typed) cleared[k] = ""
          setConfig(cleared)
          setLoadedConfig(JSON.stringify(cleared))
        }
      }
    } finally {
      setSaving(false)
    }
  }

  const runTest = async () => {
    setTesting(true)
    try {
      toastTestResult(await testDatasourceConnection(datasource.type, config, datasource.id))
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="grid gap-4 max-w-xl">
      <div className="flex items-center gap-2">
        <Server className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-base font-semibold">{datasource.name}</h2>
      </div>
      <InfoTable
        rows={[
          { label: "유형", value: datasource.type },
          { label: "데이터 소스 ID", value: <span className="font-mono text-xs">{datasource.datasource_id}</span> },
        ]}
      />
      <fieldset disabled={!canManage} className="contents">
      <div className="grid gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor={`${idPrefix}-name`} className="text-sm">표시 이름 <span className="text-destructive">*</span></Label>
          <Input id={`${idPrefix}-name`} value={name} onChange={(e) => setName(e.target.value)} className="h-9 text-sm" />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`${idPrefix}-origin`} className="text-sm">환경</Label>
          <Select value={origin} onValueChange={setOrigin}>
            <SelectTrigger id={`${idPrefix}-origin`} className="h-9 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="DEV" className="text-sm">DEV</SelectItem>
              <SelectItem value="STAGING" className="text-sm">STAGING</SelectItem>
              <SelectItem value="PROD" className="text-sm">PROD</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">변경 시 이 데이터 소스의 모든 데이터셋 환경도 함께 변경됩니다. (URN은 불변)</p>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`${idPrefix}-system`} className="text-sm">소속 시스템</Label>
          <Select value={systemId ? String(systemId) : "none"} onValueChange={(v) => setSystemId(v === "none" ? null : Number(v))}>
            <SelectTrigger id={`${idPrefix}-system`} className="h-9 text-sm">
              <SelectValue placeholder="미분류" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none" className="text-sm">미분류</SelectItem>
              {flatSystems.map((s) => (
                <SelectItem key={s.id} value={String(s.id)} className="text-sm">
                  {s.name} <span className="text-muted-foreground">· {s.orgName}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {/* 연결 설정 — 명시 허용형 기능 권한(datasources.connection). 비밀값은 항상 마스킹 */}
        {!canViewConnection ? (
          <p className="border-t pt-3 text-sm text-muted-foreground">
            연결 설정을 볼 수 있는 권한이 없습니다.
          </p>
        ) : loading ? (
          <p className="py-2 text-sm text-muted-foreground">연결 설정 불러오는 중...</p>
        ) : loadError ? (
          <p className="border-t pt-3 text-sm text-destructive">
            연결 설정을 불러오지 못했습니다: {loadError} (이름·환경·시스템만 저장됩니다)
          </p>
        ) : fields.length > 0 ? (
          <div className="border-t pt-3">
            <p className="mb-3 text-sm font-medium">연결 설정</p>
            {targetChanged && (
              <p className="mb-3 text-xs text-amber-600 dark:text-amber-500">
                접속 대상이 바뀌어 저장된 비밀값을 쓸 수 없습니다. 비밀번호 등 비밀값을 다시 입력하세요.
              </p>
            )}
            <ConfigForm
              fields={fields}
              values={config}
              storedSecrets={usableSecrets}
              onChange={(k, v) => setConfig((prev) => ({ ...prev, [k]: v }))}
            />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">이 데이터 소스 유형에 대한 설정 템플릿이 없습니다.</p>
        )}
        <div className="flex justify-end gap-2">
          {canManage && canViewConnection && !loadError && CONNECTION_TEST_TYPES.has(datasource.type) && (
            <Button size="sm" variant="outline" onClick={runTest} disabled={testing || loading}>
              {testing ? "테스트 중..." : "연결 테스트"}
            </Button>
          )}
          {canManage && (
            <Button size="sm" onClick={() => void save()} disabled={saving || loading || !dirty || !name.trim() || !configValid}>
              {saving ? "저장 중..." : "저장"}
            </Button>
          )}
        </div>
        <ConfirmDialog
          open={confirmOrigin}
          onOpenChange={setConfirmOrigin}
          title="환경 변경"
          desc={
            <p>
              환경을 <span className="font-semibold">{datasource.origin ?? "DEV"}</span> 에서{" "}
              <span className="font-semibold">{origin}</span> 로 바꾸면 이 데이터 소스의 모든 데이터셋
              환경도 함께 바뀝니다. 계속할까요?
            </p>
          }
          confirmText="변경하고 저장"
          isLoading={saving}
          handleConfirm={() => void save(true)}
        />
      </div>
      </fieldset>
    </div>
  )
}
