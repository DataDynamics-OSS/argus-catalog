"use client"

import { Tip } from "@/components/tip"
import { useCallback, useEffect, useMemo, useState } from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { ArrowLeft, FlaskConical, Loader2, RefreshCw } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { StatusBadge } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { DETAIL_TAB_LABELS, DetailHeader } from "@/components/model-hub/detail-layout"
import { LINE_TABS_LIST, LINE_TABS_TRIGGER } from "@/components/detail-section"
import { useAuth } from "@/features/auth"
import { usePermissions } from "@/features/permissions/use-permissions"
import { fetchOciLinkedToMlflow, type LinkedOciModel } from "@/features/oci-hub/api"

import {
  fetchModelDetail, fetchModelLineage, fetchModelMetrics, fetchModelVersions,
  type ModelDetail, type ModelLineage, type ModelVersionItem, type VersionMetrics,
} from "../api"
import { ActivityTab } from "./detail/activity"
import { LineageTab } from "./detail/lineage"
import { OverviewTab } from "./detail/overview"
import { PerformanceTab } from "./detail/performance"
import { frameworkLabel, StageBadge } from "./detail/shared"
import { UsageTab } from "./detail/usage"
import { VersionsTab } from "./detail/versions"
import { errorMessage } from "@/lib/notify"
import { AgentUsagePanel } from "@/features/ai-agents/components/ai-agents-skills-data"

type ModelsDetailProps = {
  modelName: string
  onBack: () => void
}

const TABS = ["overview", "versions", "performance", "lineage", "usage", "activity"] as const
type Tab = (typeof TABS)[number]
// 예전 탭 주소(?tab=card 등)로 들어와도 같은 내용이 열리게 한다.
const LEGACY_TABS: Record<string, Tab> = {
  card: "overview", metrics: "performance", download: "activity", comments: "activity",
}

/** MLflow 등록 모델 상세 — `/dashboard/models/{name}?tab=...`. */
export function ModelsDetail({ modelName, onBack }: ModelsDetailProps) {
  const { user } = useAuth()
  const { canUseFeature } = usePermissions()
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const rawTab = params.get("tab") ?? ""
  const tabParam = (LEGACY_TABS[rawTab] ?? rawTab) as Tab
  const tab: Tab = TABS.includes(tabParam) ? tabParam : "overview"
  const setTab = (t: string) => router.replace(t === "overview" ? pathname : `${pathname}?tab=${t}`, { scroll: false })

  const [detail, setDetail] = useState<ModelDetail | null>(null)
  const [versions, setVersions] = useState<ModelVersionItem[]>([])
  const [metrics, setMetrics] = useState<VersionMetrics[]>([])
  const [lineage, setLineage] = useState<ModelLineage[]>([])
  const [linked, setLinked] = useState<LinkedOciModel[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const reload = useCallback(() => setReloadKey((k) => k + 1), [])

  useEffect(() => {
    let cancelled = false
    if (reloadKey === 0) setLoading(true)
    setError(null)
    fetchModelDetail(modelName)
      .then((d) => { if (!cancelled) setDetail(d) })
      .catch((e: unknown) => { if (!cancelled) setError(errorMessage(e) ?? "모델을 불러오지 못했습니다.") })
      .finally(() => { if (!cancelled) setLoading(false) })
    // 부가 정보는 실패해도 화면을 막지 않는다 (탭에서 빈 상태로 보인다).
    fetchModelVersions(modelName, 1, 100).then((r) => !cancelled && setVersions(r.items)).catch(() => {})
    fetchModelMetrics(modelName).then((m) => !cancelled && setMetrics(m)).catch(() => {})
    fetchModelLineage(modelName).then((l) => !cancelled && setLineage(l)).catch(() => {})
    fetchOciLinkedToMlflow(modelName).then((o) => !cancelled && setLinked(o)).catch(() => !cancelled && setLinked([]))
    return () => { cancelled = true }
  }, [modelName, reloadKey])

  const reloadVersions = useCallback(
    () => fetchModelVersions(modelName, 1, 100).then((r) => setVersions(r.items)).catch(() => {}),
    [modelName],
  )
  const reloadLineage = useCallback(
    () => { fetchModelLineage(modelName).then(setLineage).catch(() => {}) },
    [modelName],
  )

  // 쓰기 권한 — 관리자, 모델 등록자, 모델 소유자 (서버 가드와 같은 기준) + 역할의 기능 권한 models.edit(TODO-122)
  const isOwner = !!user && !!detail && (user.is_admin || [detail.owner, detail.created_by].includes(user.username))
  const canEdit = isOwner && canUseFeature("models.edit")

  // 헤더 요약 — 운영·스테이징 버전 (스테이지는 버전 단위라 모델에는 여러 개일 수 있다)
  const stageSummary = useMemo(() => {
    const desc = [...versions].sort((a, b) => b.version - a.version)
    return {
      prod: desc.find((v) => v.stage === "PRODUCTION") ?? null,
      staging: desc.find((v) => v.stage === "STAGING") ?? null,
    }
  }, [versions])

  if (loading) {
    return <div className="flex items-center justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
  }

  const backButton = (
    <Button variant="ghost" size="sm" className="h-7 -ml-2 self-start px-2 text-muted-foreground hover:text-foreground" onClick={onBack}>
      <ArrowLeft className="mr-1 h-4 w-4" />목록으로
    </Button>
  )

  if (error || !detail) {
    const notFound = !!error && /404|찾을 수 없/.test(error)
    return (
      <div className="space-y-4">
        {backButton}
        <div className="space-y-3 py-16 text-center">
          <p className="text-base font-medium">{notFound ? `모델 '${modelName}' 을(를) 찾을 수 없습니다.` : "모델을 불러오지 못했습니다."}</p>
          {error && !notFound && <p className="text-sm text-destructive">{error}</p>}
          {!notFound && <Button variant="outline" size="sm" onClick={reload}>다시 시도</Button>}
        </div>
      </div>
    )
  }

  const fw = frameworkLabel(detail.catalog)

  return (
    <div className="flex flex-col gap-4">
      {backButton}

      <DetailHeader
        icon={<FlaskConical className="h-5 w-5 text-primary" />}
        title={detail.name}
        badges={
          <>
            {stageSummary.prod
              ? <StageBadge stage="PRODUCTION" version={stageSummary.prod.version} />
              : versions.length > 0 && <StatusBadge tone="muted">운영 버전 없음</StatusBadge>}
            {stageSummary.staging && <StageBadge stage="STAGING" version={stageSummary.staging.version} />}
            {fw && <Badge variant="outline" className="font-normal">{fw}</Badge>}
          </>
        }
        description={detail.description}
        actions={
          <Tip label="새로고침"><Button variant="ghost" size="icon-sm" onClick={reload} aria-label="새로고침">
            <RefreshCw className="h-3.5 w-3.5" />
          </Button></Tip>
        }
      />

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList variant="line" className={LINE_TABS_LIST}>
          <TabsTrigger value="overview" className={LINE_TABS_TRIGGER}>{DETAIL_TAB_LABELS.overview}</TabsTrigger>
          <TabsTrigger value="versions" className={LINE_TABS_TRIGGER}>
            {DETAIL_TAB_LABELS.versions} ({versions.length})
          </TabsTrigger>
          <TabsTrigger value="performance" className={LINE_TABS_TRIGGER}>{DETAIL_TAB_LABELS.performance}</TabsTrigger>
          <TabsTrigger value="lineage" className={LINE_TABS_TRIGGER}>
            {DETAIL_TAB_LABELS.lineage} ({lineage.length + (linked?.length ?? 0)})
          </TabsTrigger>
          <TabsTrigger value="usage" className={LINE_TABS_TRIGGER}>{DETAIL_TAB_LABELS.usage}</TabsTrigger>
          <TabsTrigger value="activity" className={LINE_TABS_TRIGGER}>{DETAIL_TAB_LABELS.activity}</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4">
          <OverviewTab
            detail={detail} versions={versions} metrics={metrics} lineage={lineage} linked={linked}
            canEdit={canEdit} onGoTab={setTab}
          />
        </TabsContent>
        <TabsContent value="versions" className="mt-4">
          <VersionsTab modelName={detail.name} versions={versions} metrics={metrics} canEdit={canEdit} onChanged={reloadVersions} />
        </TabsContent>
        <TabsContent value="performance" className="mt-4">
          <PerformanceTab metrics={metrics} versions={versions} />
        </TabsContent>
        <TabsContent value="lineage" className="mt-4">
          <LineageTab
            modelName={detail.name} lineage={lineage} linked={linked ?? []}
            versions={versions.map((v) => v.version).sort((a, b) => b - a)}
            canEdit={canEdit} onChanged={reloadLineage}
          />
          {/* 이 모델을 기반 모델·리니지로 쓰는 AI 에이전트 (TODO-019) */}
          <div className="mt-8">
            <AgentUsagePanel target={{ model_id: detail.id }} />
          </div>
        </TabsContent>
        <TabsContent value="usage" className="mt-4">
          <UsageTab detail={detail} versions={versions} />
        </TabsContent>
        <TabsContent value="activity" className="mt-4">
          <ActivityTab modelName={detail.name} canEdit={isOwner} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
