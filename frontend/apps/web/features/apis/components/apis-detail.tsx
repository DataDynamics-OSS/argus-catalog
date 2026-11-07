"use client"

import { useCallback, useEffect, useState } from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { AlertTriangle, ArrowLeft, Check, Loader2, Search, Trash2 } from "lucide-react"
import { errorMessage, notifyError, toast } from "@/lib/notify"
import { useConfirm } from "@/components/confirm-provider"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Label } from "@workspace/ui/components/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { Textarea } from "@workspace/ui/components/textarea"
import { cn } from "@workspace/ui/lib/utils"

import {
  type ApiAlert, type ApiCredential, type ApiDetail, type ApiLineage, type ApiPermissions, type ApiStatusHistory,
  ApiRequestError, acknowledgeApiAlert, deleteApi, fetchApiAlerts, fetchApiCredentials, fetchApiDetail, fetchApiLineage, fetchApiStatusHistory, updateApi,
} from "../api"
import { useApis } from "./apis-provider"
import { API_STATUS_LABEL, API_STATUS_VARIANTS, SPEC_FORMAT_LABEL, STATUS_ORDER, protocolLabel } from "./apis-constants"
import { TabError, TabLoading, fmtDateTime } from "./apis-detail-shared"
import { AuthTab } from "./auth-tab"
import { EndpointsTab } from "./endpoints-tab"
import { type DiffRequest, HistoryTab } from "./history-tab"
import { LineageTab } from "./lineage-tab"
import { LintTab } from "./lint-tab"
import { OverviewTab } from "./overview-tab"
import { SpecTab } from "./spec-tab"
import { UsageTab } from "./usage-tab"

/** 자격증명 목록 조회 결과 — 권한 없음(403)·오류를 0건과 구분해 안내한다. */
export type CredentialsAccess = "ok" | "forbidden" | "error"

const NO_PERMISSIONS: ApiPermissions = { can_edit: false, can_admin: false }

// 부가 데이터 로더 — 초기 로드와 개별 새로고침이 같은 함수를 쓴다(실패 시 빈 목록).
const loadHistory = (name: string) => fetchApiStatusHistory(name).catch((): ApiStatusHistory[] => [])
const loadAlerts = (name: string) => fetchApiAlerts(name, "OPEN").catch((): ApiAlert[] => [])
const loadLineage = (name: string) => fetchApiLineage(name).catch((): ApiLineage[] => [])
async function loadCredentials(name: string): Promise<{ items: ApiCredential[]; access: CredentialsAccess }> {
  try {
    return { items: await fetchApiCredentials(name), access: "ok" }
  } catch (e) {
    return { items: [], access: e instanceof ApiRequestError && e.status === 403 ? "forbidden" : "error" }
  }
}

// 상세 탭 — 주소의 ?tab= 로 유지(새로고침·링크 공유 시 같은 탭). 기본(개요)은 주소에 남기지 않는다.
const DETAIL_TABS = ["overview", "endpoints", "auth", "lineage", "usage", "lint", "spec", "history"] as const
// 바꿀 때 사유를 받는 상태 — 사용자에게 영향이 큰 변경
const STATUS_NEEDS_NOTE = new Set(["deprecated", "retired"])

// 입력 폼이 있는 탭은 forceMount 로 마운트를 유지해 탭을 오가도 입력·편집 중 내용이 사라지지 않게 한다.
const KEEP_MOUNTED = "data-[state=inactive]:hidden"

// ---------------------------------------------------------------------------
// 메인 상세 — 이름이 바뀌면 상위에서 key 로 새로 마운트(이전 API 의 상태가 남지 않음)
// ---------------------------------------------------------------------------
export function ApisDetail({ name }: { name: string }) {
  const { setSelectedApiName, refreshApis } = useApis()
  const [api, setApi] = useState<ApiDetail | null>(null)
  const [history, setHistory] = useState<ApiStatusHistory[]>([])
  const [credentials, setCredentials] = useState<ApiCredential[]>([])
  const [credAccess, setCredAccess] = useState<CredentialsAccess>("ok")
  const [alerts, setAlerts] = useState<ApiAlert[]>([])
  const [lineage, setLineage] = useState<ApiLineage[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loadSeq, setLoadSeq] = useState(0)
  const [changingStatus, setChangingStatus] = useState(false)
  // 사유를 받는 상태 변경(사용 중단·폐기) 확인 대화상자
  const [pendingStatus, setPendingStatus] = useState<string | null>(null)
  const [statusNote, setStatusNote] = useState("")
  // 알림 "자세히" → 이력 탭의 버전 비교 대상
  const [diffRequest, setDiffRequest] = useState<DiffRequest | null>(null)
  // 관측 탭의 인기 엔드포인트 → 엔드포인트 탭에서 그 행 펼치기
  const [endpointFocus, setEndpointFocus] = useState<{ method: string; path: string; seq: number } | null>(null)
  const router = useRouter()
  const askConfirm = useConfirm()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const tabParam = searchParams.get("tab")
  const tab = (DETAIL_TABS as readonly string[]).includes(tabParam ?? "") ? tabParam! : "overview"
  const changeTab = (next: string) => {
    const sp = new URLSearchParams(window.location.search)
    if (next === "overview") sp.delete("tab")
    else sp.set("tab", next)
    const qs = sp.toString()
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false })
    if (next !== "history") setDiffRequest(null)
  }
  // 개요 편집 중 저장하지 않은 변경 여부 — 목록으로 나갈 때 확인용
  const [overviewDirty, setOverviewDirty] = useState(false)

  const reload = useCallback(async () => {
    const [d, h] = await Promise.all([fetchApiDetail(name), loadHistory(name)])
    setApi(d)
    setHistory(h)
  }, [name])
  const reloadCredentials = useCallback(async () => {
    const c = await loadCredentials(name)
    setCredentials(c.items)
    setCredAccess(c.access)
  }, [name])
  const reloadAlerts = useCallback(async () => { setAlerts(await loadAlerts(name)) }, [name])
  const reloadLineage = useCallback(async () => { setLineage(await loadLineage(name)) }, [name])

  useEffect(() => {
    let active = true
    Promise.all([fetchApiDetail(name), loadHistory(name), loadCredentials(name), loadAlerts(name), loadLineage(name)])
      .then(([d, h, c, a, l]) => {
        if (!active) return
        setApi(d); setHistory(h); setCredentials(c.items); setCredAccess(c.access); setAlerts(a); setLineage(l)
      })
      .catch((e) => { if (active) setError(errorMessage(e) ?? "API 정보를 불러오지 못했습니다.") })
    return () => { active = false }
  }, [name, loadSeq])

  // 상세를 열면 맨 위부터 보이게(목록 스크롤 위치는 목록이 따로 기억해 복귀 시 복원)
  useEffect(() => { window.scrollTo(0, 0) }, [])

  const retry = () => { setError(null); setLoadSeq((n) => n + 1) }

  const backToList = async () => {
    if (overviewDirty && !(await askConfirm({
      title: "변경 내용 버리기",
      description: "저장하지 않은 개요 변경 사항이 있습니다. 목록으로 이동할까요?",
      confirmText: "버리기",
      destructive: true,
    }))) return
    setSelectedApiName(null)
  }

  const handleAckAlert = async (alertId: number) => {
    try {
      await acknowledgeApiAlert(name, alertId)
      await reloadAlerts()
      toast.success("알림을 확인 처리했습니다.")
    } catch (e) { notifyError(e, "알림을 확인 처리하지 못했습니다.") }
  }

  const applyStatus = async (next: string, note: string | null) => {
    if (!api || next === api.status || changingStatus) return false
    setChangingStatus(true)
    try {
      await updateApi(api.name, { status: next, ...(note ? { status_note: note } : {}) })
    } catch (e) {
      notifyError(e, "API 상태를 바꾸지 못했습니다.")
      setChangingStatus(false)
      return false
    }
    toast.success(`상태를 '${API_STATUS_LABEL[next] ?? next}'(으)로 변경했습니다.`)
    try { await reload(); await refreshApis() }
    catch { toast.warning("상태는 변경했지만 화면을 새로고침하지 못했습니다.") }
    finally { setChangingStatus(false) }
    return true
  }
  // 사용 중단·폐기는 확인과 사유 입력 후 변경(사유는 이력 탭에 표시)
  const changeStatus = (next: string) => {
    if (!api || next === api.status) return
    if (STATUS_NEEDS_NOTE.has(next)) { setStatusNote(""); setPendingStatus(next); return }
    void applyStatus(next, null)
  }
  const confirmPendingStatus = async () => {
    if (!pendingStatus) return
    if (await applyStatus(pendingStatus, statusNote.trim() || null)) setPendingStatus(null)
  }
  const openEndpoint = (method: string, path: string) => {
    setEndpointFocus((prev) => ({ method, path, seq: (prev?.seq ?? 0) + 1 }))
    changeTab("endpoints")
  }
  const showAlertDiff = (al: ApiAlert) => {
    setDiffRequest((prev) => ({ from: al.from_spec_id, to: al.to_spec_id, seq: (prev?.seq ?? 0) + 1 }))
    changeTab("history")
  }

  const handleDelete = async () => {
    if (!api) return
    if (!(await askConfirm({
      title: "API 삭제",
      description: `'${api.display_name || api.name}' API를 삭제하시겠습니까?`,
      confirmText: "삭제",
      destructive: true,
    }))) return
    try {
      await deleteApi(api.name)
    } catch (e) {
      notifyError(e, "API를 삭제하지 못했습니다.")
      return
    }
    toast.success("API를 삭제했습니다.")
    setSelectedApiName(null)
    await refreshApis().catch(() => {})
  }

  const backButton = (
    <Button variant="ghost" size="sm" className="w-fit" onClick={() => { void backToList() }}><ArrowLeft className="mr-1 h-4 w-4" /> API 목록으로</Button>
  )
  if (error) {
    return <div className="flex flex-col gap-4">{backButton}<TabError message={error} onRetry={retry} /></div>
  }
  if (!api) {
    return <div className="flex flex-col gap-4">{backButton}<TabLoading /></div>
  }

  const perm = api.permissions ?? NO_PERMISSIONS

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="mb-2 -ml-2">{backButton}</div>
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-semibold">{api.display_name || api.name}</h2>
            {perm.can_edit ? (
              <Select value={api.status} onValueChange={changeStatus} disabled={changingStatus}>
                <SelectTrigger className={cn("h-auto w-auto gap-1 rounded-md border-0 px-2 py-1 text-sm font-medium shadow-none focus:ring-0", API_STATUS_VARIANTS[api.status] ?? API_STATUS_VARIANTS.draft)} title="상태 변경">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>{STATUS_ORDER.map((s) => <SelectItem key={s} value={s}>{API_STATUS_LABEL[s]}</SelectItem>)}</SelectContent>
              </Select>
            ) : (
              <span className={cn("rounded-md px-2 py-1 text-sm font-medium", API_STATUS_VARIANTS[api.status] ?? API_STATUS_VARIANTS.draft)}>{API_STATUS_LABEL[api.status] ?? api.status}</span>
            )}
            <Badge variant="outline" className="text-sm">{api.version}</Badge>
            {api.protocol && <Badge variant="outline" className="text-sm">{protocolLabel(api.protocol)}</Badge>}
            {api.spec_format && <Badge variant="outline" className="text-sm">{SPEC_FORMAT_LABEL[api.spec_format] ?? api.spec_format}</Badge>}
          </div>
          <p className="font-mono text-xs text-muted-foreground">{api.urn}</p>
          {api.description && <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{api.description}</p>}
        </div>
        {perm.can_edit && <Button variant="destructive" size="sm" onClick={handleDelete}><Trash2 className="mr-1 h-4 w-4" /> 삭제</Button>}
      </div>

      {alerts.length > 0 && (
        <div className="flex flex-col gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3">
          {alerts.map((al) => (
            <div key={al.id} className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                <div className="text-sm">
                  <span className="font-medium text-destructive">Breaking 변경 감지</span>
                  <span className="ml-2 text-muted-foreground">{fmtDateTime(al.created_at)}</span>
                  <p className="mt-0.5 text-foreground">{al.summary}</p>
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                {al.from_spec_id != null && al.to_spec_id != null && (
                  <Button size="sm" variant="outline" onClick={() => showAlertDiff(al)}>
                    <Search className="mr-1 h-4 w-4" /> 자세히
                  </Button>
                )}
                {/* 알림 확인 처리는 소유자·관리자만(서버도 같은 기준) */}
                {perm.can_edit && (
                  <Button size="sm" variant="outline" onClick={() => handleAckAlert(al.id)}>
                    <Check className="mr-1 h-4 w-4" /> 확인
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <Tabs value={tab} onValueChange={changeTab}>
        <TabsList className="flex-wrap">
          <TabsTrigger value="overview">개요</TabsTrigger>
          <TabsTrigger value="endpoints">엔드포인트 ({api.endpoints.length})</TabsTrigger>
          <TabsTrigger value="auth">인증 ({api.security_schemes.length})</TabsTrigger>
          <TabsTrigger value="lineage">리니지 ({lineage.length})</TabsTrigger>
          <TabsTrigger value="usage">관측</TabsTrigger>
          <TabsTrigger value="lint">린팅</TabsTrigger>
          <TabsTrigger value="spec">원본</TabsTrigger>
          <TabsTrigger value="history">이력</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" forceMount className={cn("mt-4", KEEP_MOUNTED)}>
          <OverviewTab
            api={api}
            canEdit={perm.can_edit}
            onSaved={async () => { await reload(); await refreshApis() }}
            onDirtyChange={setOverviewDirty}
          />
        </TabsContent>

        <TabsContent value="endpoints" forceMount className={cn("mt-4", KEEP_MOUNTED)}>
          <EndpointsTab api={api} credentials={credentials} canEdit={api.source === "manual" && perm.can_edit} onEndpointsChanged={reload} focus={endpointFocus} />
        </TabsContent>

        <TabsContent value="auth" forceMount className={cn("mt-4", KEEP_MOUNTED)}>
          <AuthTab api={api} credentials={credentials} access={credAccess} canAdmin={perm.can_admin} onChanged={reloadCredentials} />
        </TabsContent>

        <TabsContent value="lineage" forceMount className={cn("mt-4", KEEP_MOUNTED)}>
          <LineageTab apiName={api.name} lineage={lineage} canAdmin={perm.can_admin} onChanged={reloadLineage} />
        </TabsContent>

        <TabsContent value="usage" className="mt-4">
          <UsageTab api={api} onOpenEndpoint={openEndpoint} />
        </TabsContent>

        <TabsContent value="lint" className="mt-4">
          <LintTab apiName={api.name} specs={api.specs} />
        </TabsContent>

        {/* 수동 API 는 계약 문서 편집 폼이 있어 마운트 유지(스펙 API 는 코드 뷰어만이라 필요할 때만) */}
        <TabsContent value="spec" forceMount={api.source === "manual" || undefined} className={cn("mt-4 space-y-4", KEEP_MOUNTED)}>
          <SpecTab
            api={api}
            canEdit={perm.can_edit}
            onChanged={async () => { await Promise.all([reload(), reloadAlerts()]); await refreshApis() }}
          />
        </TabsContent>

        <TabsContent value="history" className="mt-4 space-y-4">
          <HistoryTab apiName={api.name} history={history} specs={api.specs} diffRequest={diffRequest} />
        </TabsContent>
      </Tabs>

      <Dialog open={pendingStatus !== null} onOpenChange={(o) => { if (!o && !changingStatus) setPendingStatus(null) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>상태를 &apos;{API_STATUS_LABEL[pendingStatus ?? ""] ?? pendingStatus}&apos;(으)로 변경</DialogTitle>
            <DialogDescription>
              {pendingStatus === "retired"
                ? "폐기된 API 는 더 이상 사용하지 않는 것으로 표시됩니다. 이 API 를 쓰는 곳이 없는지 확인하세요."
                : "사용 중단은 대체 API 로 옮기도록 안내하는 상태입니다. 대체 API·종료 예정일 등을 사유에 남겨 두면 이용자에게 도움이 됩니다."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="api-status-note">사유 (선택, 이력 탭에 표시)</Label>
            <Textarea id="api-status-note" value={statusNote} onChange={(e) => setStatusNote(e.target.value)} maxLength={1000} className="min-h-20 text-sm" placeholder="예: v2(payments-v2)로 대체, 2026-12-31 종료 예정" />
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={changingStatus} onClick={() => setPendingStatus(null)}>취소</Button>
            <Button variant={pendingStatus === "retired" ? "destructive" : "default"} disabled={changingStatus} onClick={() => void confirmPendingStatus()}>
              {changingStatus ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}변경
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
