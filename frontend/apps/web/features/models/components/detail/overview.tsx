"use client"

import { useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { FileText, Loader2, Pencil } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Textarea } from "@workspace/ui/components/textarea"
import { CodeSnippet } from "@/components/model-hub/usage-steps"
import { EmptyState, FactList, SideCard } from "@/components/model-hub/detail-layout"
import { formatBytes, formatDate, formatDateTime, timeAgo } from "@/components/model-hub/format"
import { type LinkedOciModel, OCI_STATUS_LABELS } from "@/features/oci-hub/api"

import {
  fetchModelCard, saveModelCard,
  type ModelCard, type ModelDetail, type ModelLineage, type ModelVersionItem, type VersionMetrics,
} from "../../api"
import {
  frameworkLabel, MetricChip, metricsWithDelta, primaryVersion, StageBadge, VersionStatusBadge,
} from "./shared"
import { ModelFilesLink } from "./model-files"

// 모델 카드 본문(문서) 항목 — 프레임워크·라이선스·연락처는 사이드바 정보로 보여 준다.
const DOC_FIELDS: { key: keyof ModelCard; label: string; placeholder: string; rows: number }[] = [
  { key: "purpose", label: "용도", placeholder: "이 모델이 어떤 목적으로 쓰이나요?", rows: 3 },
  { key: "performance", label: "성능", placeholder: "AUC, F1, accuracy, latency 등", rows: 2 },
  { key: "limitations", label: "제약사항", placeholder: "알려진 한계, 편향, 엣지 케이스 등", rows: 3 },
  { key: "training_data", label: "학습 데이터", placeholder: "데이터셋, 기간, 행 수 등", rows: 2 },
]
const META_FIELDS: { key: keyof ModelCard; label: string; placeholder: string }[] = [
  { key: "framework", label: "프레임워크", placeholder: "예: sklearn 1.5.2 / Python 3.11" },
  { key: "license", label: "라이선스", placeholder: "Apache 2.0, Internal Use Only 등" },
  { key: "contact", label: "연락처", placeholder: "ml-team@company.com" },
]

/** 모델 카드 — 읽기 화면이 기본이고, 권한이 있으면 "편집" 으로 폼을 연다. */
function ModelCardSection({
  modelName, card, canEdit, detected, onSaved,
}: {
  modelName: string
  card: ModelCard | null
  canEdit: boolean
  detected: string | null
  onSaved: (c: ModelCard) => void
}) {
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<Partial<ModelCard>>({})
  const [saving, setSaving] = useState(false)

  function startEdit() {
    setForm({ ...card, framework: card?.framework || detected })
    setEditing(true)
  }

  async function save() {
    setSaving(true)
    try {
      await saveModelCard(modelName, form)
      onSaved({ ...(card ?? ({} as ModelCard)), ...form } as ModelCard)
      toast.success("모델 카드를 저장했습니다.")
      setEditing(false)
    } catch (e) {
      notifyError(e, "모델 카드를 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div className="space-y-4 rounded-lg border p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">모델 카드 편집</h2>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setEditing(false)} disabled={saving}>취소</Button>
            <Button size="sm" onClick={save} disabled={saving}>
              {saving && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}저장
            </Button>
          </div>
        </div>
        {DOC_FIELDS.map((f) => (
          <div key={f.key} className="space-y-1.5">
            <Label htmlFor={`card-${f.key}`}>{f.label}</Label>
            <Textarea
              id={`card-${f.key}`} rows={f.rows} placeholder={f.placeholder} disabled={saving}
              value={(form[f.key] as string | null) ?? ""}
              onChange={(e) => setForm((p) => ({ ...p, [f.key]: e.target.value || null }))}
            />
          </div>
        ))}
        <div className="grid gap-4 sm:grid-cols-3">
          {META_FIELDS.map((f) => (
            <div key={f.key} className="space-y-1.5">
              <Label htmlFor={`card-${f.key}`}>{f.label}</Label>
              <Input
                id={`card-${f.key}`} placeholder={f.placeholder} disabled={saving}
                value={(form[f.key] as string | null) ?? ""}
                onChange={(e) => setForm((p) => ({ ...p, [f.key]: e.target.value || null }))}
              />
            </div>
          ))}
        </div>
      </div>
    )
  }

  const filled = DOC_FIELDS.filter((f) => card?.[f.key])
  if (filled.length === 0) {
    return (
      <EmptyState
        icon={FileText}
        text={<>모델 카드가 비어 있습니다.<br />용도·성능·제약사항·학습 데이터를 적어 두면 다른 팀이 이 모델을 판단하기 쉽습니다.</>}
        action={canEdit && <Button size="sm" variant="outline" onClick={startEdit}><Pencil className="mr-1.5 h-3.5 w-3.5" />모델 카드 작성</Button>}
      />
    )
  }
  return (
    <article className="space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">모델 카드</h2>
        <div className="flex items-center gap-3">
          {card?.updated_at && <span className="text-xs text-muted-foreground">{timeAgo(card.updated_at)} 수정</span>}
          {canEdit && <Button size="sm" variant="outline" onClick={startEdit}><Pencil className="mr-1.5 h-3.5 w-3.5" />편집</Button>}
        </div>
      </div>
      {DOC_FIELDS.map((f) => (
        <section key={f.key}>
          <h3 className="mb-1 text-sm font-semibold">{f.label}</h3>
          {card?.[f.key]
            ? <p className="whitespace-pre-wrap text-sm leading-relaxed">{card[f.key] as string}</p>
            : <p className="text-sm italic text-muted-foreground/70">작성되지 않음</p>}
        </section>
      ))}
    </article>
  )
}

function Dependencies({ requirements, conda }: { requirements: string | null; conda: string | null }) {
  const [open, setOpen] = useState(false)
  if (!requirements && !conda) return null
  const count = requirements ? requirements.split("\n").filter((l) => l.trim() && !l.startsWith("#")).length : 0
  return (
    <div className="rounded-lg border">
      <button
        type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex w-full items-center justify-between px-4 py-2.5 text-sm font-medium"
      >
        <span>의존성 {count > 0 && <span className="ml-1 text-xs font-normal text-muted-foreground">패키지 {count}개</span>}</span>
        <span className="text-xs text-muted-foreground">{open ? "접기" : "펼치기"}</span>
      </button>
      {open && (
        <div className="space-y-3 border-t p-4">
          {requirements && (
            <div>
              <p className="mb-1 text-xs text-muted-foreground">requirements.txt</p>
              <CodeSnippet code={requirements.trim()} language="text" />
            </div>
          )}
          {conda && (
            <div>
              <p className="mb-1 text-xs text-muted-foreground">conda.yaml</p>
              <CodeSnippet code={conda.trim()} language="yaml" />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** MLflow 모델 개요 — 왼쪽은 모델 카드(문서), 오른쪽은 대표 버전·정보·연결 요약. */
export function OverviewTab({
  detail, versions, metrics, lineage, linked, canEdit, onGoTab,
}: {
  detail: ModelDetail
  versions: ModelVersionItem[]
  metrics: VersionMetrics[]
  lineage: ModelLineage[]
  linked: LinkedOciModel[] | null
  canEdit: boolean
  onGoTab: (tab: string) => void
}) {
  const [card, setCard] = useState<ModelCard | null>(null)
  const [cardLoading, setCardLoading] = useState(true)

  useEffect(() => {
    setCardLoading(true)
    fetchModelCard(detail.name)
      .then(setCard)
      .catch(() => setCard(null))
      .finally(() => setCardLoading(false))
  }, [detail.name])

  const c = detail.catalog
  const fw = frameworkLabel(c)
  const detected = useMemo(() => (fw ? (c?.python_version ? `${fw} / Python ${c.python_version}` : fw) : null), [fw, c?.python_version])
  const primary = primaryVersion(versions)
  const primaryMetrics = primary ? metricsWithDelta(metrics, primary.version).slice(0, 4) : []
  const datasets = lineage.slice(0, 3)
  const missingFiles = versions.filter((v) => v.files_present === false).length
  // 모든 버전의 파일이 없으면 파일 브라우저 링크는 빈 폴더만 열게 되므로 숨긴다.
  const hasFiles = !!detail.files_path && (versions.length === 0 || missingFiles < versions.length)

  return (
    <>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-4">
          {cardLoading
            ? <div className="flex justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
            : <ModelCardSection modelName={detail.name} card={card} canEdit={canEdit} detected={detected} onSaved={setCard} />}
          <Dependencies requirements={c?.requirements ?? null} conda={c?.conda ?? null} />
        </div>

        <aside className="space-y-4">
          <SideCard
            title={primary?.stage === "PRODUCTION" ? "운영 버전" : "최신 버전"}
            action={versions.length > 0 && (
              <Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={() => onGoTab("versions")}>모든 버전</Button>
            )}
          >
            {primary ? (
              <div className="space-y-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-lg font-semibold">v{primary.version}</span>
                  <StageBadge stage={primary.stage} />
                  {primary.status !== "READY" && <VersionStatusBadge status={primary.status} />}
                </div>
                {primary.description && <p className="text-muted-foreground line-clamp-2">{primary.description}</p>}
                <p className="text-xs text-muted-foreground">
                  {formatBytes(primary.artifact_size)} · {formatDate(primary.finished_at ?? primary.created_at)} 등록
                </p>
                {primaryMetrics.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {primaryMetrics.map((m) => <MetricChip key={m.key} name={m.key} value={m.value} delta={m.delta} improved={m.improved} />)}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">등록된 버전이 없습니다.</p>
            )}
          </SideCard>

          <SideCard title="모델 정보">
            <FactList items={[
              ["소유자", detail.owner],
              ["등록자", detail.created_by && detail.created_by !== detail.owner ? detail.created_by : null],
              ["프레임워크", fw],
              ["Python", c?.python_version],
              ["MLflow", c?.mlflow_version],
              ["라이선스", card?.license],
              ["연락처", card?.contact],
              ["다운로드", `${detail.download_count.toLocaleString()}회`],
              ["등록", <span key="c" title={formatDateTime(detail.created_at)}>{formatDate(detail.created_at)}</span>],
              ["수정", <span key="u" title={formatDateTime(detail.updated_at)}>{timeAgo(detail.updated_at)}</span>],
            ]} />
            {hasFiles && detail.files_path && (
              <ModelFilesLink path={detail.files_path} title={detail.name} variant="button" />
            )}
            {missingFiles > 0 && (
              <p className="mt-2 text-xs text-amber-700">버전 {missingFiles}개의 파일이 저장 위치에 없습니다.</p>
            )}
          </SideCard>

          <SideCard title="배포 아티팩트 (OCI)">
            {linked === null ? (
              <p className="text-sm text-muted-foreground">불러오는 중…</p>
            ) : linked.length === 0 ? (
              <p className="text-sm text-muted-foreground">이 모델에서 변환·게시한 OCI 모델이 없습니다.</p>
            ) : (
              <ul className="space-y-2">
                {linked.map((o) => (
                  <li key={o.name} className="text-sm">
                    <Link href={`/dashboard/oci-hub/${encodeURIComponent(o.name)}`} className="font-medium text-primary hover:underline">
                      {o.display_name || o.name}
                    </Link>
                    <p className="text-xs text-muted-foreground">
                      {[o.framework, o.version_count > 0 ? `v${o.version_count}` : null, OCI_STATUS_LABELS[o.status] ?? o.status].filter(Boolean).join(" · ")}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </SideCard>

          <SideCard
            title="학습·평가 데이터"
            action={<Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={() => onGoTab("lineage")}>리니지</Button>}
          >
            {datasets.length === 0 ? (
              <p className="text-sm text-muted-foreground">연결된 데이터셋이 없습니다.</p>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {datasets.map((l) => (
                  <li key={l.id} className="truncate">
                    <Link href={`/dashboard/datasets/${l.dataset_id}`} className="text-primary hover:underline">
                      {l.dataset_name || `데이터셋 ${l.dataset_id}`}
                    </Link>
                  </li>
                ))}
                {lineage.length > 3 && <li className="text-xs text-muted-foreground">외 {lineage.length - 3}개</li>}
              </ul>
            )}
          </SideCard>
        </aside>
      </div>
    </>
  )
}
