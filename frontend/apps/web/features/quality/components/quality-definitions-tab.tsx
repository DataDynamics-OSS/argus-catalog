"use client"

import { Tip } from "@/components/tip"
import { useCallback, useEffect, useState } from "react"
import { Code2, Columns3, Copy, Pencil, Plus, RefreshCw, Sparkles, Trash2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { useConfirm } from "@/components/confirm-provider"
import { SeverityBadge } from "@/components/severity-badge"
import { usePermissions } from "@/features/permissions/use-permissions"
import {
  addDefaultQualityDefinitions,
  deleteQualityDefinition,
  fetchQualityDefinitions,
  loadQualityIndicators,
  type QualityDefinition,
  type QualityIndicator,
} from "@/features/quality/api"
import { QualityAssignDialog } from "@/features/quality/components/quality-assign-dialog"
import { EmptyRow, StaleNote } from "@/features/quality/components/quality-list-state"
import { QualityDefinitionDialog } from "@/features/quality/components/quality-definition-dialog"
import { QualitySqlPreviewDialog } from "@/features/quality/components/quality-sql-preview"
import { copyName, criteriaText } from "@/features/quality/definitions"
import { errorMessage, toast, notifyError } from "@/lib/notify"

/**
 * 데이터 품질 > 품질 지표 탭 — 진단 기준을 미리 정해 두고 컬럼에 할당한다.
 * 지표의 기준을 바꾸면 할당된 모든 컬럼에 바로 반영된다.
 */
export function QualityDefinitionsTab() {
  const { canUseFeature } = usePermissions()
  // 지표(정의)는 모든 데이터셋이 함께 쓰는 기준 — 추가 · 수정 · 삭제는 서버와 같은 quality.manage(DEF-135).
  // 할당 · 해제는 규칙 관리 권한(quality.rules-manage) + 서버의 데이터셋 소유자 검사.
  const canManage = canUseFeature("quality.manage")
  const canAssign = canUseFeature("quality.rules-manage")
  const askConfirm = useConfirm()
  const [definitions, setDefinitions] = useState<QualityDefinition[]>([])
  const [indicators, setIndicators] = useState<QualityIndicator[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<QualityDefinition | null>(null)
  const [copying, setCopying] = useState<{ source: QualityDefinition; name: string } | null>(null)
  const [assigning, setAssigning] = useState<QualityDefinition | null>(null)
  const [sqlOf, setSqlOf] = useState<QualityDefinition | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setDefinitions(await fetchQualityDefinitions())
      setError(null)
    } catch (e) {
      const message = errorMessage(e) ?? String(e)
      setError(message)
      notifyError(e, "품질 지표 목록을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    loadQualityIndicators().then(setIndicators).catch(() => setIndicators([]))
  }, [load])

  const addDefaults = async () => {
    try {
      const { added } = await addDefaultQualityDefinitions()
      toast.success(added > 0 ? `기본 지표 ${added}개를 추가했습니다.` : "기본 지표가 이미 모두 있습니다.")
      await load()
    } catch (e) {
      notifyError(e, "기본 지표를 추가하지 못했습니다.")
    }
  }

  const remove = async (d: QualityDefinition) => {
    const kept = d.rules > 0
      ? ` 할당된 컬럼 ${d.rules}개의 규칙은 지워지지 않고, 각 데이터셋에서 직접 정한 규칙으로 남습니다.`
      : ""
    // 이 지표를 진단 범위로 고른 작업 — 지우면 그 작업은 이 지표의 규칙을 진단하지 않는다(DEF-148)
    const jobs = d.jobs ?? []
    const shown = jobs.slice(0, 5).map((j) => `'${j.name}'${j.only ? "(남는 지표 없음)" : ""}`).join(", ")
    const affected = jobs.length > 0
      ? ` 이 지표를 진단 범위로 고른 품질 진단 작업 ${jobs.length}개(${shown}${jobs.length > 5 ? ` 외 ${jobs.length - 5}개` : ""})는`
        + " 삭제 뒤 이 지표의 규칙을 진단하지 않습니다(남은 규칙은 '활성 규칙 전부' 범위의 작업만 진단)."
        + (jobs.some((j) => j.only) ? " '남는 지표 없음' 작업은 진단할 규칙이 없어지므로 작업의 진단 범위를 다시 정하세요." : "")
      : ""
    if (!(await askConfirm({
      title: "품질 지표 삭제", description: `'${d.name}' 지표를 삭제할까요?${kept}${affected}`, confirmText: "삭제",
      destructive: true,
    }))) return
    try {
      await deleteQualityDefinition(d.id)
      toast.success(`품질 지표 '${d.name}' 을(를) 삭제했습니다.`)
    } catch (e) {
      notifyError(e, "품질 지표를 삭제하지 못했습니다.")
    }
    await load()
  }

  const openDialog = (d: QualityDefinition | null) => {
    setEditing(d)
    setCopying(null)
    setDialogOpen(true)
  }

  // 복사 — 원본의 분류·기준을 채운 "추가" 대화상자를 연다. 저장해야 지표가 생긴다
  const openCopy = (d: QualityDefinition) => {
    setEditing(null)
    setCopying({ source: d, name: copyName(d.name, definitions.map((x) => x.name)) })
    setDialogOpen(true)
  }

  return (
    <div className="flex flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs">
          자주 쓰는 품질 기준을 지표로 한 번 정해 두고 여러 컬럼에 할당합니다. 지표의 기준을 바꾸면 할당된 모든 컬럼에 바로 반영됩니다.
          테이블마다 내용이 다른 기준(컬럼 간 비교 · 논리관계 · 계산식 · 참조관계)은 데이터셋의 품질 탭에서 직접 만듭니다.
        </span>
        <span className="ml-auto flex items-center gap-2">
          <Tip label="새로고침"><Button variant="outline" size="sm" onClick={() => void load()} disabled={loading} aria-label="새로고침">
            <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
          </Button></Tip>
          {canManage && (
            <>
              <Button variant="outline" size="sm" onClick={() => void addDefaults()}
                title="필수값 · 날짜 YYYYMMDD · 여부 Y/N 같은 흔한 지표 가운데 아직 없는 것을 넣습니다">
                <Sparkles className="mr-1 size-4" /> 기본 지표 추가
              </Button>
              <Button size="sm" onClick={() => openDialog(null)}>
                <Plus className="mr-1 size-4" /> 지표 추가
              </Button>
            </>
          )}
        </span>
      </div>

      <StaleNote error={error} show={definitions.length > 0} />
      <div className="overflow-auto rounded border">
        <table className="w-full text-sm">
          <thead className="bg-muted text-left text-xs">
            <tr>
              <th className="px-3 py-2">진단 기준</th>
              <th className="px-3 py-2">품질 지표</th>
              {/* 지표의 기준 방식 · 기대값 · 임계값을 한 줄로(criteriaText) — 첫 열 "진단 기준"(분류)과 겹치지 않게(DEF-157) */}
              <th className="px-3 py-2">통과 조건</th>
              <th className="px-3 py-2">심각도</th>
              <th className="px-3 py-2">할당</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {definitions.length === 0 ? (
              <EmptyRow colSpan={6} loading={loading} error={error} onRetry={() => void load()}>
                정의한 품질 지표가 없습니다. &quot;기본 지표 추가&quot; 로 흔한 지표를 넣거나 &quot;지표 추가&quot; 로 직접 만드세요.
              </EmptyRow>
            ) : definitions.map((d) => (
              <tr key={d.id} className="border-t align-top">
                <td className="whitespace-nowrap px-3 py-2 text-xs">
                  <span >{d.area_name} · </span>{d.category_name}
                </td>
                <td className="px-3 py-2">
                  <div className="font-medium">{d.name}</div>
                  {d.description && <div className="max-w-96 text-xs">{d.description}</div>}
                </td>
                <td className="max-w-72 px-3 py-2 text-xs">
                  <span className="break-all font-mono">{criteriaText(d)}</span>
                </td>
                <td className="px-3 py-2"><SeverityBadge severity={d.severity} /></td>
                <td className="whitespace-nowrap px-3 py-2 text-xs">
                  {d.rules > 0
                    ? <button type="button" className="hover:underline" onClick={() => setAssigning(d)}>
                        컬럼 {d.rules}개 · 데이터셋 {d.datasets}개
                      </button>
                    : <span className="text-muted-foreground">할당 없음</span>}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right">
                  <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => setAssigning(d)}>
                    <Columns3 className="mr-1 size-3" /> 할당
                  </Button>
                  <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => setSqlOf(d)}
                    title="이 지표가 실행하는 진단 쿼리(Trino 경로 예시 — 데이터셋의 컬럼을 고르면 그 데이터셋의 경로로 보여 줍니다)">
                    <Code2 className="mr-1 size-3" /> 쿼리
                  </Button>
                  {canManage && (
                    <>
                      <Tip label="복사"><Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => openCopy(d)}
                        aria-label="복사">
                        <Copy className="size-3" />
                      </Button></Tip>
                      <Tip label="수정"><Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => openDialog(d)} aria-label="수정">
                        <Pencil className="size-3" />
                      </Button></Tip>
                      <Tip label="삭제"><Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => void remove(d)} aria-label="삭제">
                        <Trash2 className="size-3" />
                      </Button></Tip>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <QualityDefinitionDialog open={dialogOpen} definition={editing} template={copying} indicators={indicators}
        onOpenChange={setDialogOpen} onSaved={() => void load()} />
      <QualityAssignDialog definition={assigning} canManage={canAssign}
        onClose={() => setAssigning(null)} onChanged={() => void load()} />
      <QualitySqlPreviewDialog
        title={`${sqlOf?.name ?? ""} — 진단 쿼리`}
        description={sqlOf ? `${sqlOf.category_name} · ${criteriaText(sqlOf)}` : undefined}
        request={sqlOf ? { check_type: sqlOf.check_type, expected_value: sqlOf.expected_value, threshold: sqlOf.threshold } : null}
        onClose={() => setSqlOf(null)}
      />
    </div>
  )
}
