"use client"

import { Fragment, useCallback, useEffect, useRef, useState } from "react"
import {
  Activity,
  CheckCircle2,
  Clock,
  DownloadCloud,
  Info,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  XCircle,
} from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { Textarea } from "@workspace/ui/components/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@workspace/ui/components/tooltip"
import { ConfirmDialog } from "@/components/confirm-dialog"

import {
  checkHealth,
  createInstance,
  deleteInstance,
  harvestInstance,
  instanceCapabilities,
  listInstances,
  listSyncRuns,
  probeCapabilities,
  updateInstance,
  type CapabilitiesResponse,
  type FederatedInstance,
  type FederationMode,
  type FederationSyncRun,
  type InstanceHealth,
} from "./api"
import { CapabilityChecklist } from "./capability-checklist"
import { HarvestProgress, runSummary, type HarvestProg } from "./harvest-progress"
import { SyncRunsDialog } from "./sync-runs-dialog"
import { errorMessage } from "@/lib/notify"

const MODES: FederationMode[] = ["HYBRID", "HARVEST", "LIVE"]

/**
 * 모드별 설명. ``brief`` 는 셀렉트 항목의 한 줄 보조설명, ``detail`` 은 선택 시
 * 폼 하단에 노출하는 helper 문구다. ``recommended`` 는 권장 배지 표시용.
 */
const MODE_INFO: Record<
  FederationMode,
  { brief: string; detail: string; recommended?: boolean }
> = {
  HYBRID: {
    brief: "가져온 데이터 검색 + 실시간 드릴다운",
    detail: "가져온 데이터로 검색하고, 상세는 원본 인스턴스에서 실시간 조회합니다. 권장 구성입니다.",
    recommended: true,
  },
  HARVEST: {
    brief: "주기적 미러·재임베딩",
    detail:
      "peer 메타데이터를 주기적으로 가져와 로컬에 미러하고 허브 모델로 재임베딩합니다. 빠르고 시맨틱이 일관되며 에어갭에 적합 — peer 가 꺼져 있어도 검색됩니다.",
  },
  LIVE: {
    brief: "검색 시 실시간 fan-out",
    detail:
      "검색 시점에 각 peer 로 실시간 요청(scatter-gather)합니다. 항상 최신이고 로컬 복제가 없지만, peer 가 꺼져 있으면 그 결과는 빠집니다.",
  },
}

type FormState = {
  instance_key: string
  name: string
  base_url: string
  auth_token: string
  // 수정 시: 저장된 토큰 보유 여부 / 토큰 제거 요청
  has_token: boolean
  clear_token: boolean
  mode: FederationMode
  sync_interval_sec: number
  status: "ACTIVE" | "PAUSED"
  description: string
}

const EMPTY_FORM: FormState = {
  instance_key: "",
  name: "",
  base_url: "",
  auth_token: "",
  has_token: false,
  clear_token: false,
  mode: "HYBRID",
  sync_interval_sec: 900,
  status: "ACTIVE",
  description: "",
}

/**
 * 상태점검 실패 사유를 사람이 읽는 한 줄로. 백엔드가 준 error 가 있으면 그대로,
 * 없으면(=200 이 아닌 응답) HTTP 상태코드를, 둘 다 없으면 "응답 없음".
 */
function healthReason(h: InstanceHealth): string {
  if (h.error) return h.error
  if (h.status_code != null) return `HTTP ${h.status_code}`
  return "응답 없음"
}

export function FederationInstancesPanel() {
  const [instances, setInstances] = useState<FederatedInstance[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // 상태점검·가져오기 진행 중인 인스턴스 — 서로 독립(한쪽 종료가 다른 쪽 버튼을 풀지 않도록)
  const [checking, setChecking] = useState<Set<number>>(() => new Set())
  const [harvesting, setHarvesting] = useState<Set<number>>(() => new Set())
  const [health, setHealth] = useState<Record<number, InstanceHealth>>({})

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  // 식별 키 중복 등 키 입력란 단위 에러(상단 배너와 분리해 입력란 아래 표시)
  const [keyError, setKeyError] = useState<string | null>(null)
  // 다이얼로그 내부 에러(검증·저장 실패) — 패널 배너는 다이얼로그에 가려지므로 분리
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // 수정 시 원래 base_url — 변경되면 caps 를 저장된 URL 이 아닌 새 URL 로 probe
  const [origBaseUrl, setOrigBaseUrl] = useState("")
  // 삭제 확인 다이얼로그 대상(팝업 confirm 대체). null=닫힘.
  const [deleteTarget, setDeleteTarget] = useState<FederatedInstance | null>(null)
  const [deleting, setDeleting] = useState(false)
  // 전체 재동기화(full=true, prune 포함) 확인 다이얼로그 대상. null=닫힘.
  const [resyncTarget, setResyncTarget] = useState<FederatedInstance | null>(null)

  // 소비자 표시 선택 — peer 가 advertise 한 항목(caps) 중에서 고른다.
  const [caps, setCaps] = useState<CapabilitiesResponse | null>(null)
  // null = 선택 없음(제공 항목 전부 표시), [] = 명시적으로 아무것도 표시 안 함
  const [displayFields, setDisplayFields] = useState<string[] | null>(null)
  const [capsLoading, setCapsLoading] = useState(false)
  const [capsError, setCapsError] = useState<string | null>(null)

  const [runsOpen, setRunsOpen] = useState(false)
  const [runs, setRuns] = useState<FederationSyncRun[]>([])
  const [runsInstance, setRunsInstance] = useState<string>("")
  const [runsLoading, setRunsLoading] = useState(false)
  const [runsError, setRunsError] = useState<string | null>(null)
  // 이력 요청 세대 — 다른 인스턴스 이력을 연달아 열 때 늦게 온 응답이 덮어쓰지 않도록
  const runsReq = useRef(0)

  // 가져오기 진행률 — 인스턴스별 단계/진척. 존재하면 해당 행에 진행 바 표시.
  const [harvestProgress, setHarvestProgress] = useState<
    Record<number, HarvestProg>
  >({})
  // 인스턴스별 진행률 폴링 타이머 — 언마운트(탭 이동) 시 모두 정리한다.
  const pollTimers = useRef<Record<number, ReturnType<typeof setInterval>>>({})
  useEffect(() => {
    const timers = pollTimers.current
    return () => {
      Object.values(timers).forEach(clearInterval)
    }
  }, [])

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      setInstances(await listInstances())
      setError(null)
    } catch (e) {
      setError(errorMessage(e) ?? String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const resetCaps = () => {
    setCaps(null)
    setCapsError(null)
    setCapsLoading(false)
  }

  const openCreate = () => {
    setEditId(null)
    setForm(EMPTY_FORM)
    setKeyError(null)
    setFormError(null)
    setOrigBaseUrl("")
    setDisplayFields(null)
    resetCaps()
    setDialogOpen(true)
  }

  const openEdit = (inst: FederatedInstance) => {
    setEditId(inst.id)
    setKeyError(null)
    setFormError(null)
    setOrigBaseUrl(inst.base_url)
    setForm({
      instance_key: inst.instance_key,
      name: inst.name,
      base_url: inst.base_url,
      auth_token: "",
      has_token: inst.has_auth_token,
      clear_token: false,
      mode: inst.mode,
      sync_interval_sec: inst.sync_interval_sec,
      status: inst.status,
      description: inst.description ?? "",
    })
    setDisplayFields(inst.display_fields ?? null)
    resetCaps()
    setDialogOpen(true)
  }

  // peer 가 advertise 하는 노출 항목을 가져온다.
  // 등록 전·URL/토큰 변경 시 = 입력값으로 probe, 그 외 수정 = 저장된 설정으로 조회.
  const loadCaps = async () => {
    const url = form.base_url.trim()
    const urlChanged = editId !== null && url !== origBaseUrl
    const tokenChanged = !!form.auth_token || form.clear_token
    // URL 만 바뀌고 저장된 토큰을 유지하는 경우 — 저장 토큰은 프론트로 오지 않아
    // 새 URL 로 인증된 probe 를 할 수 없다.
    if (urlChanged && !tokenChanged && form.has_token) {
      setCapsError(
        "Base URL 을 바꾼 경우 토큰을 다시 입력하거나, 먼저 저장한 뒤 불러오세요.",
      )
      return
    }
    setCapsLoading(true)
    setCapsError(null)
    try {
      const c =
        editId === null || urlChanged || tokenChanged
          ? await probeCapabilities(url, form.auth_token)
          : await instanceCapabilities(editId)
      setCaps(c)
      // 기존 선택이 있으면 advertise 범위로 교집합, 없으면(null) 전체 노출을 기본 선택
      setDisplayFields((prev) =>
        (prev ?? c.exposed).filter((k) => c.exposed.includes(k)),
      )
    } catch (e) {
      setCapsError(errorMessage(e) ?? String(e))
    } finally {
      setCapsLoading(false)
    }
  }

  const submit = async () => {
    if (submitting) return
    setFormError(null)
    const name = form.name.trim()
    const baseUrl = form.base_url.trim().replace(/\/+$/, "")
    if (!name) {
      setFormError("이름을 입력하세요.")
      return
    }
    if (!/^https?:\/\/[^\s/]+/i.test(baseUrl)) {
      setFormError("Base URL 은 http:// 또는 https:// 로 시작해야 합니다.")
      return
    }
    const interval = form.sync_interval_sec
    if (!Number.isInteger(interval) || interval < 30) {
      setFormError("동기화 주기는 30초 이상의 정수여야 합니다.")
      return
    }
    // 등록(신규)일 때만 식별 키 중복을 사전 검사 — 이미 로드된 목록과 비교해
    // API 호출 전에 즉시 입력란 단위 에러로 막는다. 수정은 키를 바꾸지 않는다.
    const key = form.instance_key.trim()
    if (editId === null) {
      // 형식 검사: 영문 대소문자·숫자·_·- 만 허용
      if (!/^[A-Za-z0-9_-]+$/.test(key)) {
        setKeyError("식별 키는 영문 대소문자, 숫자, _, - 만 사용할 수 있습니다.")
        return
      }
      const dup = instances.some((i) => i.instance_key === key)
      if (dup) {
        setKeyError(`이미 사용 중인 식별 키입니다: ${key}`)
        return
      }
    }
    setKeyError(null)
    setSubmitting(true)
    try {
      if (editId === null) {
        await createInstance({
          instance_key: key,
          name,
          base_url: baseUrl,
          auth_token: form.auth_token || null,
          mode: form.mode,
          sync_interval_sec: form.sync_interval_sec,
          description: form.description || null,
          // caps 를 불러왔으면 선택값 저장, 아니면 null(전부 표시)
          display_fields: caps ? (displayFields ?? caps.exposed) : null,
        })
      } else {
        await updateInstance(editId, {
          name,
          base_url: baseUrl,
          mode: form.mode,
          sync_interval_sec: form.sync_interval_sec,
          status: form.status,
          description: form.description || null,
          // 새 토큰 입력 시 교체, '토큰 제거' 선택 시 null 로 삭제, 둘 다 아니면 유지
          ...(form.auth_token
            ? { auth_token: form.auth_token }
            : form.clear_token
              ? { auth_token: null }
              : {}),
          // caps 를 불러온 경우에만 표시 선택을 갱신(아니면 기존 유지)
          ...(caps ? { display_fields: displayFields ?? caps.exposed } : {}),
        })
      }
      setDialogOpen(false)
      await reload()
    } catch (e) {
      const msg = errorMessage(e) ?? String(e)
      // 동시 등록 경합 등으로 백엔드가 키 중복(409)을 돌려주면 입력란 단위로 표시
      if (editId === null && msg.includes("식별 키")) {
        setKeyError(msg)
      } else {
        setFormError(msg)
      }
    } finally {
      setSubmitting(false)
    }
  }

  // 삭제 요청 — 팝업 대신 확인 다이얼로그를 연다.
  const remove = (inst: FederatedInstance) => setDeleteTarget(inst)

  const confirmDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await deleteInstance(deleteTarget.id)
      setDeleteTarget(null)
      await reload()
    } catch (e) {
      setError(errorMessage(e) ?? String(e))
    } finally {
      setDeleting(false)
    }
  }

  const doHealth = async (inst: FederatedInstance) => {
    setChecking((s) => withId(s, inst.id))
    try {
      const h = await checkHealth(inst.id)
      setHealth((prev) => ({ ...prev, [inst.id]: h }))
    } catch (e) {
      setError(errorMessage(e) ?? String(e))
    } finally {
      setChecking((s) => withoutId(s, inst.id))
    }
  }

  // 가져오기 완료/실패를 진행 바 영역에 인라인 결과로 표시(팝업 대신). 잠시 뒤 자동 사라짐.
  const finishHarvest = (id: number, ok: boolean, text: string) => {
    setHarvestProgress((p) => {
      const cur = p[id] ?? { phase: "FINALIZE", done: 1, total: 1, seen: 0 }
      const total = cur.total || 1
      return {
        ...p,
        [id]: { ...cur, phase: "FINALIZE", done: total, total, result: { ok, text } },
      }
    })
    // 새 가져오기가 다시 시작되지 않았을 때만(=result 가 아직 남아있을 때) 자동 제거.
    setTimeout(() => {
      setHarvestProgress((p) => {
        if (!p[id]?.result) return p
        const next = { ...p }
        delete next[id]
        return next
      })
    }, 8000)
  }

  const stopPolling = (id: number) => {
    clearInterval(pollTimers.current[id])
    delete pollTimers.current[id]
  }

  // RUNNING 실행 이력을 폴링해 진행률을 갱신한다. ``finishOnDone`` 이면(이 화면이 시작하지 않은
  // 실행을 지켜보는 경우) RUNNING 이 끝났을 때 그 실행 결과로 완료 처리한다.
  const startPolling = (id: number, finishOnDone: boolean) => {
    if (pollTimers.current[id]) return
    pollTimers.current[id] = setInterval(async () => {
      try {
        const r = (await listSyncRuns(id, 1))[0]
        if (r && r.status === "RUNNING") {
          setHarvestProgress((p) => ({
            ...p,
            [id]: {
              phase: r.phase,
              done: r.phase_done,
              total: r.phase_total,
              seen: r.datasets_seen,
            },
          }))
        } else if (finishOnDone) {
          stopPolling(id)
          setHarvesting((s) => withoutId(s, id))
          if (r) {
            finishHarvest(
              id,
              r.status === "SUCCESS",
              r.status === "SUCCESS" ? runSummary(r) : (r.error ?? r.status)
            )
          }
        }
      } catch {
        /* 폴링 실패는 무시 */
      }
    }, 800)
  }

  // 화면 진입(탭 복귀 포함) 시 스케줄러·다른 관리자·이전 화면이 시작한 진행 중 가져오기를 복원한다.
  // 진행 바와 버튼 비활성이 사라져 같은 peer 를 중복 실행하는 것을 막는다.
  useEffect(() => {
    let cancelled = false
    for (const inst of instances) {
      if (inst.mode === "LIVE" || pollTimers.current[inst.id]) continue
      listSyncRuns(inst.id, 1)
        .then((runs) => {
          const r = runs[0]
          if (cancelled || !r || r.status !== "RUNNING") return
          setHarvesting((s) => withId(s, inst.id))
          setHarvestProgress((p) => ({
            ...p,
            [inst.id]: {
              phase: r.phase,
              done: r.phase_done,
              total: r.phase_total,
              seen: r.datasets_seen,
            },
          }))
          startPolling(inst.id, true)
        })
        .catch(() => {
          /* 복원 실패는 무시(수동 실행 시 서버가 409 로 막는다) */
        })
    }
    return () => {
      cancelled = true
    }
    // startPolling 은 ref·setState 만 쓰므로 instances 변경 시에만 재검사한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instances])

  const doHarvest = async (inst: FederatedInstance, full: boolean) => {
    if (harvesting.has(inst.id)) return
    setHarvesting((s) => withId(s, inst.id))
    setHarvestProgress((p) => ({
      ...p,
      [inst.id]: { phase: "FETCH", done: 0, total: 0, seen: 0 },
    }))
    // 가져오기 요청은 완료 시점에만 반환되므로, 진행 중 RUNNING 실행 이력을 폴링해 진행률 표시.
    startPolling(inst.id, false)
    try {
      const r = await harvestInstance(inst.id, full)
      finishHarvest(
        inst.id,
        r.status === "SUCCESS",
        r.status === "SUCCESS" ? runSummary(r) : (r.error ?? "알 수 없는 오류")
      )
    } catch (e) {
      // 409 — 스케줄러 등 다른 실행이 진행 중. 서버 메시지를 그대로 표시한다.
      finishHarvest(inst.id, false, errorMessage(e) ?? String(e))
    } finally {
      stopPolling(inst.id)
      setHarvesting((s) => withoutId(s, inst.id))
    }
  }

  const openRuns = async (inst: FederatedInstance) => {
    const req = ++runsReq.current
    setRunsInstance(inst.name)
    setRuns([])
    setRunsError(null)
    setRunsLoading(true)
    setRunsOpen(true)
    try {
      const data = await listSyncRuns(inst.id)
      if (req === runsReq.current) setRuns(data)
    } catch (e) {
      if (req === runsReq.current)
        setRunsError(errorMessage(e) ?? String(e))
    } finally {
      if (req === runsReq.current) setRunsLoading(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          연합할 다른 팀의 Argus Catalog 인스턴스를 peer 로 등록합니다.
        </p>
        <Button onClick={openCreate}>
          <Plus className="mr-1 h-4 w-4" /> 인스턴스 등록
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      <div className="overflow-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>인스턴스</TableHead>
              <TableHead>모드</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>주기</TableHead>
              <TableHead>상태점검</TableHead>
              <TableHead className="text-right">작업</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="py-8 text-center text-muted-foreground"
                >
                  <Loader2 className="inline h-4 w-4 animate-spin" /> 불러오는
                  중...
                </TableCell>
              </TableRow>
            ) : instances.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="py-8 text-center text-muted-foreground"
                >
                  등록된 인스턴스가 없습니다.
                </TableCell>
              </TableRow>
            ) : (
              instances.map((inst) => {
                const h = health[inst.id]
                const prog = harvestProgress[inst.id]
                return (
                  <Fragment key={inst.id}>
                  <TableRow>
                    <TableCell>
                      <div className="font-medium">{inst.name}</div>
                      <div className="font-mono text-xs text-muted-foreground">
                        {inst.instance_key} · {inst.base_url}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{inst.mode}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          inst.status === "ACTIVE" ? "default" : "secondary"
                        }
                      >
                        {inst.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {inst.sync_interval_sec}s
                    </TableCell>
                    <TableCell>
                      {checking.has(inst.id) ? (
                        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                      ) : h ? (
                        h.reachable ? (
                          <button
                            type="button"
                            onClick={() => doHealth(inst)}
                            title="다시 점검"
                            className="flex items-center gap-1 text-sm text-green-600 hover:underline"
                          >
                            <CheckCircle2 className="h-4 w-4 shrink-0" />
                            {h.latency_ms}ms {h.version && `· v${h.version}`}
                          </button>
                        ) : (
                          <button
                            type="button"
                            onClick={() => doHealth(inst)}
                            title={`${healthReason(h)} · 클릭하면 다시 점검`}
                            className="flex max-w-[240px] items-center gap-1 text-sm text-destructive hover:underline"
                          >
                            <XCircle className="h-4 w-4 shrink-0" />
                            <span className="shrink-0">불가</span>
                            <span className="truncate font-normal text-muted-foreground">
                              · {healthReason(h)}
                            </span>
                          </button>
                        )
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => doHealth(inst)}
                        >
                          <Activity className="mr-1 h-3.5 w-3.5" /> 점검
                        </Button>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        {inst.mode !== "LIVE" && inst.status === "ACTIVE" && (
                          <IconAction
                            label="가져오기 (증분)"
                            onClick={() => doHarvest(inst, false)}
                            disabled={harvesting.has(inst.id)}
                          >
                            <DownloadCloud className="h-3.5 w-3.5" />
                          </IconAction>
                        )}
                        {inst.mode !== "LIVE" && inst.status === "ACTIVE" && (
                          <IconAction
                            label="전체 재동기화"
                            onClick={() => setResyncTarget(inst)}
                            disabled={harvesting.has(inst.id)}
                          >
                            <RefreshCw className="h-3.5 w-3.5" />
                          </IconAction>
                        )}
                        <IconAction
                          label="동기화 이력"
                          onClick={() => openRuns(inst)}
                        >
                          <Clock className="h-3.5 w-3.5" />
                        </IconAction>
                        <IconAction label="수정" onClick={() => openEdit(inst)}>
                          <Pencil className="h-3.5 w-3.5" />
                        </IconAction>
                        <IconAction
                          label="삭제"
                          onClick={() => remove(inst)}
                          className="text-destructive"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </IconAction>
                      </div>
                    </TableCell>
                  </TableRow>
                  {prog && (
                    <TableRow>
                      <TableCell colSpan={6} className="py-2">
                        <HarvestProgress prog={prog} />
                      </TableCell>
                    </TableRow>
                  )}
                  </Fragment>
                )
              })
            )}
          </TableBody>
        </Table>
      </div>

      {/* 등록/수정 다이얼로그 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-auto">
          <DialogHeader>
            <DialogTitle>
              {editId === null ? "인스턴스 등록" : "인스턴스 수정"}
            </DialogTitle>
            <DialogDescription>
              peer Argus Catalog 의 접속 정보를 입력합니다.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 py-2">
            <div className="grid gap-1.5">
              <Label>식별 키 (instance_key)</Label>
              <Input
                value={form.instance_key}
                disabled={editId !== null}
                placeholder="team-payments"
                aria-invalid={keyError !== null}
                onChange={(e) => {
                  setKeyError(null)
                  setForm({ ...form, instance_key: e.target.value })
                }}
              />
              {keyError ? (
                <p className="flex items-center gap-1.5 text-xs text-destructive">
                  <XCircle className="h-3.5 w-3.5 shrink-0" />
                  {keyError}
                </p>
              ) : (
                editId === null && (
                  <p className="text-xs text-muted-foreground">
                    영문 대소문자, 숫자, _, - 만 사용할 수 있습니다.
                  </p>
                )
              )}
            </div>
            <div className="grid gap-1.5">
              <Label>이름</Label>
              <Input
                value={form.name}
                placeholder="Payments Catalog"
                onChange={(e) => {
                  setFormError(null)
                  setForm({ ...form, name: e.target.value })
                }}
              />
            </div>
            <div className="grid gap-1.5">
              <Label>Base URL</Label>
              <Input
                value={form.base_url}
                placeholder="https://catalog.payments.internal"
                onChange={(e) => {
                  setFormError(null)
                  setForm({ ...form, base_url: e.target.value })
                }}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label>모드</Label>
                <Select
                  value={form.mode}
                  onValueChange={(v) =>
                    setForm({ ...form, mode: v as FederationMode })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {MODES.map((m) => (
                      <SelectItem key={m} value={m} className="items-start">
                        <div className="flex flex-col gap-0.5">
                          <span className="flex items-center gap-1.5 font-medium">
                            {m}
                            {MODE_INFO[m].recommended && (
                              <Badge
                                variant="secondary"
                                className="px-1 py-0 text-[10px]"
                              >
                                권장
                              </Badge>
                            )}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {MODE_INFO[m].brief}
                          </span>
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
                  <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>{MODE_INFO[form.mode].detail}</span>
                </p>
              </div>
              <div className="grid gap-1.5">
                <Label>동기화 주기(초)</Label>
                <Input
                  type="number"
                  min={30}
                  step={1}
                  value={
                    Number.isNaN(form.sync_interval_sec)
                      ? ""
                      : form.sync_interval_sec
                  }
                  onChange={(e) => {
                    setFormError(null)
                    setForm({
                      ...form,
                      // 빈 입력은 0 이 아닌 NaN 으로 두어 저장 시 검증에 걸리게 한다
                      sync_interval_sec:
                        e.target.value === "" ? NaN : Number(e.target.value),
                    })
                  }}
                />
                <p className="text-xs text-muted-foreground">최소 30초</p>
              </div>
            </div>
            <div className="grid gap-1.5">
              <Label>
                서비스 토큰 {editId !== null && "(변경 시에만 입력)"}
              </Label>
              <Input
                type="password"
                value={form.auth_token}
                placeholder={
                  editId !== null && form.has_token && !form.clear_token
                    ? "저장된 토큰 유지 (암호화 보관)"
                    : "peer export 토큰 (선택)"
                }
                disabled={form.clear_token}
                onChange={(e) =>
                  setForm({ ...form, auth_token: e.target.value })
                }
              />
              {editId !== null && form.has_token && (
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Checkbox
                    checked={form.clear_token}
                    onCheckedChange={(v) =>
                      setForm({ ...form, clear_token: v === true, auth_token: "" })
                    }
                  />
                  저장된 토큰 제거
                </label>
              )}
            </div>
            {editId !== null && (
              <div className="grid gap-1.5">
                <Label>상태</Label>
                <Select
                  value={form.status}
                  onValueChange={(v) =>
                    setForm({ ...form, status: v as "ACTIVE" | "PAUSED" })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ACTIVE">ACTIVE</SelectItem>
                    <SelectItem value="PAUSED">PAUSED</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
            <div className="grid gap-1.5">
              <Label>설명</Label>
              <Textarea
                value={form.description}
                onChange={(e) =>
                  setForm({ ...form, description: e.target.value })
                }
              />
            </div>

            {/* 표시 정보 선택 — peer 가 노출하는 항목 중 화면에 표시할 것 */}
            <div className="grid gap-2 rounded-md border p-3">
              <div className="flex items-center justify-between">
                <Label>표시 정보 선택</Label>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={loadCaps}
                  disabled={capsLoading || !form.base_url}
                >
                  {capsLoading ? (
                    <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <DownloadCloud className="mr-1 h-3.5 w-3.5" />
                  )}
                  노출 항목 불러오기
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                peer 가 제공하는 항목을 불러와, 이 인스턴스의 상세 화면에 표시할
                항목만 선택합니다. 불러오지 않으면 제공 항목 전부 표시됩니다.
              </p>
              {capsError && (
                <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-xs text-destructive">
                  <XCircle className="h-3.5 w-3.5" /> {capsError}
                </div>
              )}
              {caps && (
                <>
                  <CapabilityChecklist
                    items={caps.items}
                    groups={caps.groups}
                    value={displayFields ?? caps.exposed}
                    onChange={setDisplayFields}
                    available={caps.exposed}
                  />
                  <span className="text-xs text-muted-foreground">
                    {(displayFields ?? caps.exposed).length}개 표시 /{" "}
                    {caps.exposed.length}개 제공
                  </span>
                </>
              )}
            </div>
          </div>
          {formError && (
            <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              <XCircle className="h-4 w-4 shrink-0" /> {formError}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              취소
            </Button>
            <Button
              onClick={submit}
              disabled={
                submitting ||
                !form.instance_key.trim() ||
                !form.name.trim() ||
                !form.base_url.trim()
              }
            >
              {submitting && (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              )}
              {editId === null ? "등록" : "저장"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 동기화 이력 */}
      <SyncRunsDialog
        open={runsOpen}
        onOpenChange={setRunsOpen}
        instanceName={runsInstance}
        runs={runs}
        loading={runsLoading}
        error={runsError}
      />

      {/* 인스턴스 삭제 확인 — 팝업 confirm 대체 */}
      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(o) => !o && !deleting && setDeleteTarget(null)}
        title="인스턴스 삭제"
        desc={
          <>
            <span className="font-medium">{deleteTarget?.name}</span> 인스턴스를
            삭제할까요? 이 인스턴스에서 가져온{" "}
            <span className="font-medium">
              미러 데이터셋 · 임베딩 · 리니지 · 가져오기 이력
            </span>
            이 모두 함께 삭제됩니다. 원본 peer 의 실제 데이터와 본 카탈로그의 로컬
            데이터셋은 영향을 받지 않습니다. 이 작업은 되돌릴 수 없습니다.
          </>
        }
        destructive
        confirmText="삭제"
        isLoading={deleting}
        handleConfirm={confirmDelete}
      />

      {/* 전체 재동기화 확인 — 증분과 달리 전체를 다시 받고 사라진 항목을 정리(prune)한다 */}
      <ConfirmDialog
        open={resyncTarget !== null}
        onOpenChange={(o) => !o && setResyncTarget(null)}
        title="전체 재동기화"
        desc={
          <>
            <span className="font-medium">{resyncTarget?.name}</span> 의 데이터셋
            전체를 다시 가져올까요? 증분 가져오기와 달리 peer 에서 삭제되었거나
            노출이 중단된 미러 데이터셋은 이 카탈로그에서도 정리됩니다. 데이터셋
            수에 따라 시간이 오래 걸릴 수 있습니다.
          </>
        }
        confirmText="전체 재동기화"
        handleConfirm={() => {
          const target = resyncTarget
          setResyncTarget(null)
          if (target) void doHarvest(target, true)
        }}
      />
    </div>
  )
}

/** 라벨 tooltip 이 달린 아이콘 액션 버튼. */
function IconAction({
  label,
  onClick,
  disabled,
  className,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  className?: string
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          onClick={onClick}
          disabled={disabled}
          className={className}
          aria-label={label}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

/** 가져오기 진행률 — 인스턴스별 단계/진척 스냅샷. */
function withId(s: Set<number>, id: number): Set<number> {
  const next = new Set(s)
  next.add(id)
  return next
}

function withoutId(s: Set<number>, id: number): Set<number> {
  const next = new Set(s)
  next.delete(id)
  return next
}
