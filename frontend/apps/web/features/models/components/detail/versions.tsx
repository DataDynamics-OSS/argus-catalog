"use client"

import { useState } from "react"
import { AlertTriangle, ExternalLink, GitBranch } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { cn } from "@workspace/ui/lib/utils"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { EmptyState } from "@/components/model-hub/detail-layout"
import { formatBytes, formatDateTime, timeAgo } from "@/components/model-hub/format"

import { updateVersionStage, type ModelStage, type ModelVersionItem, type VersionMetrics } from "../../api"
import {
  MetricChip, metricsWithDelta, ReadOnlyNote, STAGE_LABEL, StageBadge, VersionStatusBadge,
} from "./shared"
import { ModelFilesLink } from "./model-files"
import { usePermissions } from "@/features/permissions/use-permissions"

/** 버전 탭 — 최신 버전부터 카드로 나열하고, 권한이 있으면 스테이지를 바꾼다. */
export function VersionsTab({
  modelName, versions, metrics, canEdit, onChanged,
}: {
  modelName: string
  versions: ModelVersionItem[]
  metrics: VersionMetrics[]
  canEdit: boolean
  onChanged: () => Promise<void> | void
}) {
  const [pending, setPending] = useState<{ v: ModelVersionItem; stage: ModelStage } | null>(null)
  // 운영(Production)으로 올리거나 내리는 변경은 모델 소유자여도 models.promote 권한이 있어야 한다(서버와 같은 판정)
  const canPromote = usePermissions().canUseFeature("models.promote")
  const [saving, setSaving] = useState(false)

  const sorted = [...versions].sort((a, b) => b.version - a.version)
  const missing = sorted.filter((v) => v.files_present === false)

  const applyStage = async (v: ModelVersionItem, stage: ModelStage) => {
    setSaving(true)
    try {
      await updateVersionStage(modelName, v.version, stage)
      toast.success(`v${v.version} 스테이지를 '${STAGE_LABEL[stage]}'(으)로 변경했습니다.`)
      await onChanged()
    } catch (e) {
      notifyError(e, "스테이지를 변경하지 못했습니다.")
    } finally {
      setSaving(false)
      setPending(null)
    }
  }

  // 운영으로 올리거나 운영에서 내릴 때는 확인을 받는다 — 서비스에 바로 영향을 주는 변경.
  const onStageChange = (v: ModelVersionItem, stage: ModelStage) => {
    if (stage === v.stage) return
    if (stage === "PRODUCTION" || v.stage === "PRODUCTION") setPending({ v, stage })
    else void applyStage(v, stage)
  }

  if (sorted.length === 0) {
    return <EmptyState icon={GitBranch} text="등록된 버전이 없습니다. 사용법 탭의 '새 버전 등록' 을 참고하세요." />
  }

  const otherProd = pending
    ? sorted.filter((x) => x.stage === "PRODUCTION" && x.version !== pending.v.version).map((x) => `v${x.version}`)
    : []

  return (
    <div className="space-y-3">
      {!canEdit && <ReadOnlyNote what="스테이지" />}
      {missing.length > 0 && (
        <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {missing.map((v) => `v${v.version}`).join(", ")} 의 파일이 저장 위치에 없습니다.
            메타데이터만 등록된 버전이라 내려받거나 로드할 수 없습니다.
          </span>
        </div>
      )}

      <ol className="space-y-2">
        {sorted.map((v) => {
          const chips = metricsWithDelta(metrics, v.version).slice(0, 5)
          return (
            <li
              key={v.id}
              className={cn(
                "rounded-lg border p-4",
                v.stage === "PRODUCTION" && "border-emerald-200 bg-emerald-50/30",
                v.stage === "ARCHIVED" && "opacity-70",
              )}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-base font-semibold">v{v.version}</span>
                    {v.stage !== "NONE" && <StageBadge stage={v.stage} />}
                    {v.status !== "READY" && <VersionStatusBadge status={v.status} />}
                    {v.files_present === false && (
                      <span className="inline-flex items-center gap-1 text-xs text-amber-700"><AlertTriangle className="h-3 w-3" />파일 없음</span>
                    )}
                  </div>
                  {v.description && <p className="text-sm text-muted-foreground">{v.description}</p>}
                  {v.status_message && v.status !== "READY" && <p className="text-xs text-destructive">{v.status_message}</p>}
                  <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                    <span title={formatDateTime(v.finished_at ?? v.created_at)}>{timeAgo(v.finished_at ?? v.created_at)} 등록</span>
                    <span>·</span>
                    <span>{formatBytes(v.artifact_size)} · 파일 {v.artifact_count}개</span>
                    {v.files_path && v.files_present !== false && (
                      <ModelFilesLink path={v.files_path} title={`${modelName} v${v.version}`} />
                    )}
                    {v.run_link ? (
                      <a href={v.run_link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 text-primary hover:underline">
                        실험 실행<ExternalLink className="h-3 w-3" />
                      </a>
                    ) : v.run_id ? (
                      <span className="font-mono" title="MLflow run ID">run {v.run_id.slice(0, 8)}</span>
                    ) : null}
                  </p>
                  {chips.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {chips.map((m) => <MetricChip key={m.key} name={m.key} value={m.value} delta={m.delta} improved={m.improved} />)}
                    </div>
                  )}
                </div>

                <div className="shrink-0 text-right">
                  {canEdit ? (
                    <Select
                      value={v.stage}
                      onValueChange={(val) => onStageChange(v, val as ModelStage)}
                      disabled={saving || (v.stage === "PRODUCTION" && !canPromote)}
                    >
                      <SelectTrigger size="sm" className="w-[120px]" aria-label={`v${v.version} 스테이지`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {(Object.keys(STAGE_LABEL) as ModelStage[]).map((st) => (
                          <SelectItem key={st} value={st} disabled={st === "PRODUCTION" && !canPromote}>
                            {st === "NONE" ? "스테이지 없음" : STAGE_LABEL[st]}
                            {st === "PRODUCTION" && !canPromote ? " (권한 필요)" : ""}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : null}
                  {v.stage_changed_at && (
                    <p className="mt-1 text-[11px] text-muted-foreground" title={formatDateTime(v.stage_changed_at)}>
                      {timeAgo(v.stage_changed_at)} 변경{v.stage_changed_by ? ` · ${v.stage_changed_by}` : ""}
                    </p>
                  )}
                </div>
              </div>
            </li>
          )
        })}
      </ol>

      <ConfirmDialog
        open={!!pending}
        onOpenChange={(o) => !o && !saving && setPending(null)}
        title={pending?.stage === "PRODUCTION" ? "운영 스테이지로 변경" : "운영 스테이지 해제"}
        desc={
          pending ? (
            <div className="space-y-1">
              <p>
                v{pending.v.version} 의 스테이지를 &apos;{STAGE_LABEL[pending.v.stage]}&apos; 에서{" "}
                &apos;{STAGE_LABEL[pending.stage]}&apos;(으)로 변경합니다.
              </p>
              {pending.stage === "PRODUCTION" && otherProd.length > 0 && (
                <p>현재 운영 중인 버전: {otherProd.join(", ")} (그대로 유지됩니다 — 필요하면 따로 보관으로 바꾸세요)</p>
              )}
              {pending.v.stage === "PRODUCTION" && <p>운영에서 이 버전을 사용 중인지 먼저 확인하세요.</p>}
            </div>
          ) : ""
        }
        confirmText="변경"
        isLoading={saving}
        handleConfirm={() => pending && applyStage(pending.v, pending.stage)}
      />
    </div>
  )
}
