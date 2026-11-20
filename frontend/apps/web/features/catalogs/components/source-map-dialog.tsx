"use client"

import { useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { AlertTriangle, Ban, CheckCircle2, Loader2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
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
  checkSourceMap,
  saveSourceMap,
  type SourceMapPreview,
} from "@/features/catalogs/api"
import { SourceMapBadge } from "@/features/catalogs/components/source-map-badge"
import type { DatasetSourceMap, SchemaColumnDiff } from "@/features/datasets/data/schema"
import { datasetHref } from "@/features/datasets/links"
import { fetchDatasources, type DatasourceRow } from "@/features/datasources/api"
import { datasourceRole } from "@/features/datasources/datasource-configs"
import { errorMessage, notifySuccess } from "@/lib/notify"

const CHANGE_LABEL: Record<SchemaColumnDiff["change"], string> = {
  ONLY_SOURCE: "원천에만 있음",
  ONLY_MANUAL: "이 데이터셋에만 있음",
  TYPE_DIFF: "타입 다름",
}

/** 원천 확인 결과의 컬럼 차이 표 — 대화상자와 상세 섹션이 같이 쓴다. */
export function SchemaDiffTable({ diffs }: { diffs: SchemaColumnDiff[] }) {
  if (diffs.length === 0) return null
  return (
    <div className="max-h-56 overflow-auto rounded-md border">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-muted/60 text-left text-xs">
          <tr>
            <th className="px-2 py-1 font-medium">컬럼</th>
            <th className="px-2 py-1 font-medium">차이</th>
            <th className="px-2 py-1 font-medium">이 데이터셋</th>
            <th className="px-2 py-1 font-medium">원천</th>
          </tr>
        </thead>
        <tbody>
          {diffs.map((d) => (
            <tr key={`${d.change}-${d.name}`} className="border-t">
              <td className="px-2 py-1 font-mono text-xs">{d.name}</td>
              <td className="px-2 py-1 text-xs">{CHANGE_LABEL[d.change]}</td>
              <td className="px-2 py-1 font-mono text-xs">{d.manual_type ?? "—"}</td>
              <td className="px-2 py-1 font-mono text-xs">{d.source_type ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** 데이터셋 qualified_name(`{데이터 소스 식별자}.{경로}`)에서 경로만 — 매핑 경로의 기본값. */
export function defaultRemotePath(qualifiedName: string | null | undefined, datasourceKey: string): string {
  const q = (qualifiedName ?? "").trim()
  return q.startsWith(`${datasourceKey}.`) ? q.slice(datasourceKey.length + 1) : q
}

/**
 * 수동 데이터셋을 실제 데이터 소스의 테이블에 매핑한다(TODO-CAT-001, 설계 5장).
 * 데이터 소스 · 경로 → [확인]으로 ① 중복(막음) ② 존재 ③ 스키마(경고만) → [매핑 저장].
 */
export function SourceMapDialog({
  open,
  onOpenChange,
  dataset,
  current,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  dataset: { id: number; name: string; qualified_name?: string | null; datasourceKey: string }
  current: DatasetSourceMap | null | undefined
  onSaved: (map: DatasetSourceMap) => void
}) {
  const [datasources, setDatasources] = useState<DatasourceRow[]>([])
  const [datasourceId, setDatasourceId] = useState(current ? String(current.datasource_id) : "")
  const [path, setPath] = useState(current?.remote_path ?? defaultRemotePath(dataset.qualified_name, dataset.datasourceKey))
  const [preview, setPreview] = useState<SourceMapPreview | null>(null)
  const [checking, setChecking] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    // 쿼리 엔진은 원천이 아니라 매핑 대상에서 뺀다(수동 등록 데이터 소스는 목록 API 가 이미 뺀다)
    fetchDatasources()
      .then((rows) => setDatasources(rows.filter((d) => datasourceRole(d.type) !== "ENGINE")))
      .catch((e) => setError(errorMessage(e) ?? "데이터 소스 목록을 불러오지 못했습니다."))
  }, [open])

  // 입력이 바뀌면 이전 확인 결과는 무효
  const previewMatches = useMemo(
    () => preview !== null && String(preview.datasource_id) === datasourceId && preview.remote_path === path.trim().replace(/^\.+|\.+$/g, ""),
    [preview, datasourceId, path],
  )
  const canCheck = Boolean(datasourceId) && path.trim().length > 0 && !checking && !saving
  const canSave = previewMatches && !preview?.conflict && !saving

  const runCheck = async () => {
    setChecking(true)
    setError(null)
    try {
      setPreview(await checkSourceMap(dataset.id, { datasource_id: Number(datasourceId), remote_path: path }))
    } catch (e) {
      setPreview(null)
      setError(errorMessage(e) ?? "원천을 확인하지 못했습니다.")
    } finally {
      setChecking(false)
    }
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const saved = await saveSourceMap(dataset.id, { datasource_id: Number(datasourceId), remote_path: path })
      notifySuccess(`'${dataset.name}' 을(를) ${saved.datasource_name} · ${saved.remote_path} 에 매핑했습니다.`)
      onSaved(saved)
      onOpenChange(false)
    } catch (e) {
      setError(errorMessage(e) ?? "원천 매핑을 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  const check = preview?.check

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>원천 매핑</DialogTitle>
          <DialogDescription>
            이 데이터셋이 가리키는 실제 테이블을 고릅니다. 매핑된 테이블은 메타데이터 수집이 건너뛰고,
            품질 진단 · 쿼리 리니지가 이 테이블을 이 데이터셋으로 봅니다.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="map-ds">데이터 소스</Label>
            <Select value={datasourceId} onValueChange={setDatasourceId}>
              <SelectTrigger id="map-ds">
                <SelectValue placeholder="데이터 소스 선택" />
              </SelectTrigger>
              <SelectContent>
                {datasources.map((d) => (
                  <SelectItem key={d.id} value={String(d.id)}>
                    {d.name} <span className="ml-1 text-xs text-muted-foreground">{d.type}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="map-path">
              원천 경로 <span className="text-xs">(예: sales.orders · db.schema.table)</span>
            </Label>
            <Input id="map-path" value={path} onChange={(e) => setPath(e.target.value)} maxLength={500}
              className="font-mono" onKeyDown={(e) => { if (e.key === "Enter" && canCheck) void runCheck() }} />
          </div>

          {previewMatches && preview && (
            <div className="grid gap-2 rounded-md border p-3 text-sm">
              {preview.conflict ? (
                <p className="flex items-start gap-2 text-destructive">
                  <Ban className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>
                    {preview.conflict.kind === "SYNCED_DATASET"
                      ? "이 테이블은 이미 수집된 데이터셋이 있어 매핑할 수 없습니다: "
                      : "다른 수동 데이터셋이 이미 이 테이블에 매핑되어 있습니다: "}
                    <Link className="underline" href={datasetHref(preview.conflict.dataset_id)} target="_blank">
                      {preview.conflict.dataset_name}
                    </Link>
                  </span>
                </p>
              ) : (
                <p className="flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-success" /> 다른 데이터셋과 겹치지 않습니다.
                </p>
              )}
              {check && (
                <>
                  <p className="flex flex-wrap items-center gap-2">
                    {check.status === "MATCH" ? <CheckCircle2 className="h-4 w-4 text-success" />
                      : <AlertTriangle className="h-4 w-4 text-warning" />}
                    원천 확인: <SourceMapBadge status={check.status} />
                    {check.source_column_count != null && (
                      <span className="text-xs text-muted-foreground">원천 컬럼 {check.source_column_count}개</span>
                    )}
                  </p>
                  {check.message && <p className="text-xs text-muted-foreground">{check.message}</p>}
                  <SchemaDiffTable diffs={check.diffs ?? []} />
                  {check.status !== "MATCH" && !preview.conflict && (
                    <p className="text-xs text-muted-foreground">
                      확인 결과는 참고용입니다 — 그대로 저장할 수 있고, 저장 뒤 [원천 스키마 가져오기]로 컬럼을 맞출 수 있습니다.
                    </p>
                  )}
                </>
              )}
            </div>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>취소</Button>
          <Button variant="outline" onClick={() => void runCheck()} disabled={!canCheck}>
            {checking && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
            확인
          </Button>
          <Button onClick={() => void save()} disabled={!canSave}>
            {saving ? "저장 중…" : "매핑 저장"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
