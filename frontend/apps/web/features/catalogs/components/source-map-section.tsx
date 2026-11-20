"use client"

import { useState } from "react"
import { Download, Link2, RefreshCw, Unlink } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { useConfirm } from "@/components/confirm-provider"
import { DetailSection } from "@/components/detail-section"
import { deleteSourceMap, importSourceSchema, recheckSourceMap } from "@/features/catalogs/api"
import { SourceMapBadge } from "@/features/catalogs/components/source-map-badge"
import { SchemaDiffTable, SourceMapDialog } from "@/features/catalogs/components/source-map-dialog"
import type { DatasetDetail, DatasetSourceMap } from "@/features/datasets/data/schema"
import { usePermissions } from "@/features/permissions/use-permissions"
import { notifyError, notifySuccess } from "@/lib/notify"

/**
 * 수동 데이터셋(직접 등록한 카탈로그의 데이터셋)의 원천 매핑 — 데이터셋 상세 개요 탭(TODO-CAT-001).
 * 매핑 · 변경 · 해제 · 다시 확인 · 원천 스키마 가져오기는 catalogs.manage(또는 datasources.manage).
 */
export function SourceMapSection({
  dataset,
  onChanged,
}: {
  dataset: DatasetDetail
  /** 매핑 · 스키마가 바뀌었을 때 — 상세를 다시 불러온다 */
  onChanged: () => void
}) {
  const { canUseFeature } = usePermissions()
  const canManage = canUseFeature("catalogs.manage") || canUseFeature("datasources.manage")
  const askConfirm = useConfirm()
  const [dialog, setDialog] = useState({ open: false, session: 0 })
  const [busy, setBusy] = useState(false)
  const map: DatasetSourceMap | null = dataset.source_map ?? null

  const run = async (fn: () => Promise<void>, fallback: string) => {
    setBusy(true)
    try {
      await fn()
      onChanged()
    } catch (e) {
      notifyError(e, fallback)
    } finally {
      setBusy(false)
    }
  }

  const recheck = () => run(async () => {
    const m = await recheckSourceMap(dataset.id)
    notifySuccess(`원천을 다시 확인했습니다: ${m.check_status ?? "결과 없음"}`)
  }, "원천을 다시 확인하지 못했습니다.")

  const importSchema = async () => {
    const ok = await askConfirm({
      title: "원천 스키마 가져오기",
      description: "원천 테이블의 컬럼을 이 데이터셋에 더하고 타입을 원천에 맞춥니다. 이 데이터셋에만 있는 컬럼과 설명 · 표시 이름은 그대로 둡니다.",
      confirmText: "가져오기",
    })
    if (!ok) return
    await run(async () => {
      const r = await importSourceSchema(dataset.id)
      notifySuccess(`원천 스키마를 가져왔습니다 — 추가 ${r.added}개, 갱신 ${r.updated}개`)
    }, "원천 스키마를 가져오지 못했습니다.")
  }

  const unmap = async () => {
    const ok = await askConfirm({
      title: "원천 매핑 해제",
      description: "매핑을 해제하면 다음 메타데이터 수집부터 이 테이블이 별도의 수집 데이터셋으로 등록될 수 있습니다.",
      confirmText: "해제",
      destructive: true,
    })
    if (!ok) return
    await run(async () => {
      await deleteSourceMap(dataset.id)
      notifySuccess("원천 매핑을 해제했습니다.")
    }, "원천 매핑을 해제하지 못했습니다.")
  }

  return (
    <DetailSection
      title="원천 매핑"
      icon={<Link2 />}
      description={
        <>
          <span className="font-medium">{dataset.catalog_name ?? "카탈로그"}</span> 에 직접 등록한 데이터셋입니다 — 메타데이터를 수집하지 않습니다.
          실제 테이블에 매핑하면 품질 진단 · 쿼리 리니지가 그 테이블을 이 데이터셋으로 보고, 수집은 그 테이블을 건너뜁니다.
        </>
      }
      actions={canManage ? (
        <>
          {map && (
            <>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => void recheck()}>
                <RefreshCw className="mr-1 h-3.5 w-3.5" /> 다시 확인
              </Button>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => void importSchema()}>
                <Download className="mr-1 h-3.5 w-3.5" /> 원천 스키마 가져오기
              </Button>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => void unmap()}>
                <Unlink className="mr-1 h-3.5 w-3.5" /> 해제
              </Button>
            </>
          )}
          <Button size="sm" disabled={busy} onClick={() => setDialog((d) => ({ open: true, session: d.session + 1 }))}>
            {map ? "매핑 변경" : "원천 매핑"}
          </Button>
        </>
      ) : undefined}
    >
      {map ? (
        <div className="grid gap-2 text-sm">
          <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1">
            <dt className="text-muted-foreground">데이터 소스</dt>
            <dd>{map.datasource_name} <span className="text-xs text-muted-foreground">{map.datasource_type}</span></dd>
            <dt className="text-muted-foreground">원천 경로</dt>
            <dd className="font-mono">{map.remote_path}</dd>
            <dt className="text-muted-foreground">원천 확인</dt>
            <dd className="flex flex-wrap items-center gap-2">
              <SourceMapBadge status={map.check_status ?? "UNCHECKED"} />
              {map.checked_at && <span className="text-xs text-muted-foreground">{new Date(map.checked_at).toLocaleString()}</span>}
              {map.check_detail?.message && <span className="text-xs text-muted-foreground">{map.check_detail.message}</span>}
            </dd>
            {map.created_by && (<>
              <dt className="text-muted-foreground">매핑한 사람</dt>
              <dd>{map.created_by}</dd>
            </>)}
          </dl>
          <SchemaDiffTable diffs={map.check_detail?.diffs ?? []} />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">매핑된 원천 테이블이 없습니다.</p>
      )}

      <SourceMapDialog
        key={dialog.session}
        open={dialog.open}
        onOpenChange={(open) => setDialog((d) => ({ ...d, open }))}
        dataset={{
          id: dataset.id,
          name: dataset.name,
          qualified_name: dataset.qualified_name,
          datasourceKey: dataset.datasource.datasource_id,
        }}
        current={map}
        onSaved={() => onChanged()}
      />
    </DetailSection>
  )
}
