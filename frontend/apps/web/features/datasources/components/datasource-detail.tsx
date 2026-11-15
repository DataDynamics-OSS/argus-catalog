"use client"

import { useEffect, useId, useMemo, useState } from "react"
import { CheckCircle2, XCircle } from "lucide-react"

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
import { cn } from "@workspace/ui/lib/utils"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { DetailSection } from "@/components/detail-section"
import { usePermissions } from "@/features/permissions/use-permissions"
import {
  fetchDatasourceConfig,
  testDatasourceConnection,
  type ConnectionTestResult,
  type DatasourceRow,
} from "@/features/datasources/api"
import { DATASOURCE_CONFIGS, getDefaultConfig, type DatasourceFieldDef } from "@/features/datasources/datasource-configs"
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
import { ORIGINS, originLabel } from "@/features/datasets/origin"
import { OriginText } from "@/features/datasets/components/origin-text"
import { errorMessage } from "@/lib/notify"
import { decodeList } from "@/lib/text-list"

export type SaveDatasourceHandler = (
  id: number,
  name: string,
  origin: string,
  systemId: number | null,
  // null = 연결 설정은 변경 없음(또는 조회 권한 없음) → 저장하지 않는다
  config: Record<string, unknown> | null,
) => Promise<boolean>

/** 연결 설정 값을 읽기 전용으로 — 비밀값은 저장 여부만 */
function configValue(f: DatasourceFieldDef, v: unknown, stored: string[]): string {
  if (f.type === "password") return stored.includes(f.key) ? "저장됨 (가려짐)" : "-"
  if (f.type === "toggle") return v ? "켬" : "끔"
  if (f.type === "select") return f.options?.find((o) => o.value === v)?.label ?? (v ? String(v) : "-")
  if (f.type === "list") {
    const items = Array.isArray(v) ? decodeList(v.map(String)) : decodeList(typeof v === "string" ? v : null)
    return items.length ? items.join(" · ") : "-"
  }
  return v === undefined || v === null || v === "" ? "-" : String(v)
}

type TestState = { result: ConnectionTestResult; at: Date }

/**
 * 데이터 소스 기본 정보(이름 · 환경 · 소속 시스템 · 연결 설정). key={datasource.id} 로 선택이 바뀌면 다시 만든다.
 * - 관리 권한이 없으면 키·값 표(읽기 전용)로 보인다.
 * - 고친 내용이 있으면 아래 고정 줄에 "저장 안 된 변경" · 취소 · 저장이 나오고, ``onDirtyChange`` 로 알린다
 *   (다른 노드로 옮길 때 확인하는 데 쓴다).
 */
export function DatasourceDetail({
  datasource,
  flatSystems,
  onSave,
  canManage,
  onDirtyChange,
}: {
  datasource: DatasourceRow
  flatSystems: FlatSystem[]
  canManage: boolean
  onSave: SaveDatasourceHandler
  onDirtyChange?: (dirty: boolean) => void
}) {
  const { canUseFeature } = usePermissions()
  // 연결 설정은 명시 허용형 권한(미설정이면 관리자 전용)
  const canViewConnection = canUseFeature("datasources.connection")
  const [name, setName] = useState(datasource.name)
  const [origin, setOrigin] = useState(datasource.origin ?? "DEV")
  const [systemId, setSystemId] = useState<number | null>(datasource.system_id ?? null)
  const [config, setConfig] = useState<Record<string, unknown>>({})
  // 불러온 원본(변경 여부 판단 · 취소용)과 저장된 비밀값 키
  const [loadedConfig, setLoadedConfig] = useState<string>("{}")
  const [storedSecrets, setStoredSecrets] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [lastTest, setLastTest] = useState<TestState | null>(null)
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
  const configDirty = canViewConnection && !loadError && !loading && JSON.stringify(config) !== loadedConfig
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
  const dirty = canManage && isDatasourceFormDirty({ name, origin, systemId, configDirty }, datasource)

  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])
  // 다른 노드를 고르거나 화면을 떠나면 이 폼은 사라진다 — 변경 없음으로 되돌려 둔다
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange])

  const reset = () => {
    setName(datasource.name)
    setOrigin(datasource.origin ?? "DEV")
    setSystemId(datasource.system_id ?? null)
    if (loadedConfig) {
      try { setConfig(JSON.parse(loadedConfig) as Record<string, unknown>) } catch { /* 원본이 없으면 그대로 */ }
    }
  }

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
      const result = await testDatasourceConnection(datasource.type, config, datasource.id)
      setLastTest({ result, at: new Date() })
      toastTestResult(result)
    } finally {
      setTesting(false)
    }
  }

  const systemName = (id: number | null) => {
    if (id == null) return "미분류"
    const s = flatSystems.find((x) => x.id === id)
    return s ? `${s.name} · ${s.orgName}` : String(id)
  }

  const connectionState = !canViewConnection ? (
    <p className="text-sm text-muted-foreground">연결 설정을 볼 수 있는 권한이 없습니다.</p>
  ) : loading ? (
    <p className="text-sm text-muted-foreground">연결 설정 불러오는 중…</p>
  ) : loadError ? (
    <p className="text-sm text-destructive">
      연결 설정을 불러오지 못했습니다: {loadError}{canManage ? " (이름 · 환경 · 시스템만 저장됩니다)" : ""}
    </p>
  ) : fields.length === 0 ? (
    <p className="text-sm text-muted-foreground">이 데이터 소스 유형에 대한 설정 템플릿이 없습니다.</p>
  ) : null

  // 읽기 전용 — 키·값 표
  if (!canManage) {
    const shown = fields.filter((f) => !f.showWhen || config[f.showWhen.field] === f.showWhen.value)
    return (
      <div className="grid max-w-2xl gap-8">
        <DetailSection title="기본 정보">
          <InfoTable rows={[
            { label: "표시 이름", value: datasource.name },
            { label: "환경", value: <OriginText origin={datasource.origin} /> },
            { label: "소속 시스템", value: systemName(datasource.system_id ?? null) },
          ]} />
        </DetailSection>
        <DetailSection title="연결 설정">
          {connectionState ?? (
            <InfoTable rows={shown.map((f) => ({ label: f.label, value: configValue(f, config[f.key], storedSecrets) }))} />
          )}
        </DetailSection>
      </div>
    )
  }

  const canTest = canViewConnection && !loadError && CONNECTION_TEST_TYPES.has(datasource.type)

  return (
    <div className="grid max-w-2xl gap-8">
      <DetailSection title="기본 정보">
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor={`${idPrefix}-name`} className="text-sm">표시 이름 <span className="text-destructive">*</span></Label>
            <Input id={`${idPrefix}-name`} value={name} onChange={(e) => setName(e.target.value)} className="h-9 text-sm" />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor={`${idPrefix}-origin`} className="text-sm">환경</Label>
              <Select value={origin} onValueChange={setOrigin}>
                <SelectTrigger id={`${idPrefix}-origin`} className="h-9 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ORIGINS.map((o) => (
                    <SelectItem key={o} value={o} className="text-sm"><OriginText origin={o} /></SelectItem>
                  ))}
                </SelectContent>
              </Select>
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
                      {s.name} <span >· {s.orgName}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <p className="text-xs">환경을 바꾸면 이 데이터 소스의 모든 데이터셋 환경도 함께 바뀝니다. (URN 은 그대로)</p>
        </div>
      </DetailSection>

      {/* 연결 설정 — 명시 허용형 기능 권한(datasources.connection). 비밀값은 항상 마스킹 */}
      <DetailSection
        title="연결 설정"
        actions={canTest ? (
          <Button size="sm" variant="outline" onClick={runTest} disabled={testing || loading}>
            {testing ? "테스트 중…" : "연결 테스트"}
          </Button>
        ) : undefined}
      >
        {/* 마지막 테스트 결과는 토스트가 사라져도 남긴다 */}
        {lastTest && (
          <p className={cn("flex items-center gap-1.5 text-xs",
            lastTest.result.ok ? "text-emerald-700" : lastTest.result.unsupported ? "text-muted-foreground" : "text-destructive")}>
            {lastTest.result.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
            {lastTest.result.ok
              ? `연결 성공${lastTest.result.latency_ms != null ? ` (${lastTest.result.latency_ms}ms)` : ""}`
              : lastTest.result.unsupported ? lastTest.result.message : `연결 실패: ${lastTest.result.message}`}
            <span >
              · {lastTest.at.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })} 테스트
              {configDirty ? " (그 뒤에 설정을 고침)" : ""}
            </span>
          </p>
        )}
        {connectionState ?? (
          <>
            {targetChanged && (
              <p className="text-xs text-amber-600 dark:text-amber-500">
                접속 대상이 바뀌어 저장된 비밀값을 쓸 수 없습니다. 비밀번호 등 비밀값을 다시 입력하세요.
              </p>
            )}
            <ConfigForm
              fields={fields}
              values={config}
              storedSecrets={usableSecrets}
              onChange={(k, v) => setConfig((prev) => ({ ...prev, [k]: v }))}
            />
          </>
        )}
      </DetailSection>

      {/* 저장 줄 — 고친 내용이 있을 때 화면 아래에 붙는다(이름만 고쳐도 끝까지 내려가지 않게) */}
      {dirty && (
        <div className="sticky bottom-0 z-10 -mx-1 flex items-center gap-2 border-t bg-background/95 px-1 py-2 backdrop-blur">
          <span className="text-sm text-amber-700 dark:text-amber-400">저장 안 된 변경이 있습니다</span>
          {!configValid && <span className="text-xs text-destructive">필수 연결 설정을 채우세요</span>}
          <span className="ml-auto flex gap-2">
            <Button size="sm" variant="ghost" onClick={reset} disabled={saving}>취소</Button>
            <Button size="sm" onClick={() => void save()} disabled={saving || loading || !name.trim() || !configValid}>
              {saving ? "저장 중…" : "저장"}
            </Button>
          </span>
        </div>
      )}

      <ConfirmDialog
        open={confirmOrigin}
        onOpenChange={setConfirmOrigin}
        title="환경 변경"
        desc={
          <p>
            환경을 <span className="font-semibold">{originLabel(datasource.origin ?? "DEV")}</span> 에서{" "}
            <span className="font-semibold">{originLabel(origin)}</span> 로 바꾸면 이 데이터 소스의 모든 데이터셋
            환경도 함께 바뀝니다. 계속할까요?
          </p>
        }
        confirmText="변경하고 저장"
        isLoading={saving}
        handleConfirm={() => void save(true)}
      />
    </div>
  )
}
