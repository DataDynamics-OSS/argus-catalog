"use client"

import { Tip } from "@/components/tip"
import { useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { X } from "lucide-react"

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
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { Switch } from "@workspace/ui/components/switch"
import { Textarea } from "@workspace/ui/components/textarea"
import { fetchDataset, fetchDatasets, fetchDatasources } from "@/features/datasets/api"
import {
  assignQualityDefinition,
  fetchQualityDatasetRules,
  fetchQualityDefinitions,
  fetchQualityEngineStatus,
  saveQualityJob,
  unassignQualityDefinition,
  type QualityDatasetRule,
  type QualityDefinition,
  type QualityEngineStatus,
  type QualityJob,
  type QualityJobWrite,
  type QualityScheduleType,
} from "@/features/quality/api"
import { criteriaText } from "@/features/quality/definitions"
import { engineLabel, scheduleProblem, WEEKDAYS } from "@/features/quality/format"
import {
  definitionsOf,
  mappedDefinitionIds,
  mappingChanges,
  mappingFromRules,
  mappingSize,
  toggleMapping,
  type ColumnMapping,
} from "@/features/quality/job-mapping"
import { qualityDefinitionsHref } from "@/features/quality/links"
import { usePermissions } from "@/features/permissions/use-permissions"
import { errorMessage, toast, notifyError } from "@/lib/notify"
import { SearchInput } from "@/components/search-input"

type Props = {
  open: boolean
  /** 고칠 작업 — null 이면 새로 등록 */
  job: QualityJob | null
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}

const SCHEDULE_TYPES: { value: QualityScheduleType; label: string }[] = [
  { value: "DAILY", label: "매일" },
  { value: "WEEKLY", label: "매주" },
  { value: "MONTHLY", label: "매월" },
  { value: "HOURLY", label: "N시간마다" },
  { value: "NONE", label: "수동 실행만" },
]

/** 데이터 소스 선택 상자의 "가리지 않음" */
const ANY_SOURCE = "__any__"
/** 목록에 한 번에 보여 줄 데이터셋 수(서버의 페이지 크기 상한) */
const PICK_LIMIT = 100

type DatasetHit = { id: number; name: string; datasource_name: string }
type Column = { name: string; type: string }
/** 고른 데이터셋의 컬럼 · 지금 할당된 규칙 · 정밀 진단 경로(Trino 또는 원천 직접) */
type Loaded = { columns: Column[]; rules: QualityDatasetRule[]; engine: QualityEngineStatus | null }

/** 예전 방식(카탈로그·스키마, 데이터셋 여러 개)으로 등록한 작업이면 true — 데이터셋 하나를 다시 골라야 한다. */
function isLegacyTarget(job: QualityJob | null): boolean {
  return job != null && !(job.targets.length === 1 && job.targets[0]!.type === "dataset")
}

/**
 * 품질 진단 작업 등록·수정 — 데이터셋 하나를 골라, 그 컬럼마다 품질 지표를 매핑하고, 주기를 정한다.
 * 매핑은 그 데이터셋의 지표 할당과 같은 것이라, 저장하면 더한 매핑은 할당되고 뺀 매핑은 할당이 해제된다.
 */
export function QualityJobDialog({ open, job, onOpenChange, onSaved }: Props) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [dataset, setDataset] = useState<{ id: number; label: string } | null>(null)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [mapping, setMapping] = useState<ColumnMapping>(new Map())
  const [definitions, setDefinitions] = useState<QualityDefinition[] | null>(null)
  // 주기가 있는 작업은 주기 설정 권한(quality.schedule)이 있어야 저장된다(서버도 403) — 없으면 '수동 실행만'
  const { canUseFeature } = usePermissions()
  const canSchedule = canUseFeature("quality.schedule")
  const [scheduleType, setScheduleType] = useState<QualityScheduleType>("DAILY")
  const [time, setTime] = useState("02:00")
  const [weekday, setWeekday] = useState("0")
  const [monthDay, setMonthDay] = useState("1")
  const [intervalHours, setIntervalHours] = useState("6")
  const [enabled, setEnabled] = useState(true)
  const [includeProfile, setIncludeProfile] = useState(false)
  const [saving, setSaving] = useState(false)
  // 조회 실패는 "없음" 과 구분한다
  const [loadError, setLoadError] = useState<string | null>(null)
  const [definitionsError, setDefinitionsError] = useState<string | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  // 데이터셋 고르기 — 데이터 소스를 골라 목록에서 고르거나, 이름으로 찾는다(둘을 함께 써도 된다)
  // key 는 데이터셋 목록의 조건이 받는 데이터 소스 식별자(datasource_id 문자열 — 숫자 id 가 아니다)
  const [sources, setSources] = useState<{ key: string; name: string }[]>([])
  const [sourceId, setSourceId] = useState(ANY_SOURCE)
  const [search, setSearch] = useState("")
  const [hits, setHits] = useState<DatasetHit[]>([])
  const [hitsTotal, setHitsTotal] = useState(0)
  const [searching, setSearching] = useState(false)

  // 열 때마다 작업 값으로 채운다
  useEffect(() => {
    if (!open) return
    const target = job && !isLegacyTarget(job) ? job.targets[0]! : null
    setName(job?.name ?? "")
    setDescription(job?.description ?? "")
    setDataset(target?.dataset_id != null ? { id: target.dataset_id, label: target.label ?? `#${target.dataset_id}` } : null)
    setScheduleType(job?.schedule_type ?? "DAILY")
    setTime(job?.schedule_time ?? "02:00")
    setWeekday(String(job?.schedule_type === "WEEKLY" ? job.schedule_day ?? 0 : 0))
    setMonthDay(String(job?.schedule_type === "MONTHLY" ? job.schedule_day ?? 1 : 1))
    setIntervalHours(String(job?.schedule_interval_hours ?? 6))
    setEnabled(job?.enabled ?? true)
    setIncludeProfile(job?.include_profile ?? false)
    setSearch("")
    setSourceId(ANY_SOURCE)
    setHits([])
    setHitsTotal(0)
    fetchDatasources()
      .then((rows) => setSources(rows.map((r) => ({ key: r.datasource_id, name: r.name }))))
      .catch(() => setSources([]))
    setDefinitions(null)
    setDefinitionsError(null)
    fetchQualityDefinitions().then(setDefinitions).catch((e) => setDefinitionsError(errorMessage(e) ?? String(e)))
  }, [open, job, reload])

  // 주기 설정 권한이 없으면 새 작업은 '수동 실행만' 으로 시작한다(권한 정보가 늦게 와도 맞춘다)
  useEffect(() => {
    if (open && !job && !canSchedule) setScheduleType("NONE")
  }, [open, job, canSchedule])

  // 데이터 소스를 골랐으면 그 데이터셋 목록을, 아니면 이름을 2글자 이상 적었을 때 찾은 것을 보여 준다(입력이 멈춘 뒤)
  const picking = open && dataset == null
  useEffect(() => {
    const term = search.trim()
    if (!picking || (sourceId === ANY_SOURCE && term.length < 2)) {
      setHits([])
      setHitsTotal(0)
      setSearching(false)
      return
    }
    let stale = false
    setSearching(true)
    setSearchError(null)
    const t = setTimeout(() => {
      fetchDatasets({
        search: term || undefined, datasource: sourceId === ANY_SOURCE ? undefined : sourceId, pageSize: PICK_LIMIT,
      }).then((page) => {
        if (stale) return
        setHits(page.items.map((d) => ({ id: d.id, name: d.name, datasource_name: d.datasource_name })))
        setHitsTotal(page.total)
      }).catch((e) => {
        if (stale) return
        setHits([])
        setHitsTotal(0)
        setSearchError(errorMessage(e) ?? String(e))
      }).finally(() => !stale && setSearching(false))
    }, 300)
    return () => {
      stale = true
      clearTimeout(t)
    }
  }, [picking, sourceId, search])

  // 데이터셋을 고르면 그 컬럼과 지금 할당된 지표를 읽어 매핑의 처음 상태로 삼는다
  const datasetId = dataset?.id ?? null
  useEffect(() => {
    setLoaded(null)
    setLoadError(null)
    setMapping(new Map())
    if (!open || datasetId == null) return
    let stale = false
    Promise.all([
      fetchDataset(datasetId),
      fetchQualityDatasetRules(datasetId),
      fetchQualityEngineStatus(datasetId).catch(() => null),
    ]).then(([detail, rules, engine]) => {
      if (stale) return
      const columns = [...(detail.schema_fields ?? [])]
        .sort((a, b) => (a.ordinal ?? 0) - (b.ordinal ?? 0))
        .map((f) => ({ name: f.field_path, type: f.native_type || f.field_type || "" }))
      setLoaded({ columns, rules, engine })
      setMapping(mappingFromRules(rules))
    }).catch((e) => {
      if (stale) return
      // 읽지 못한 것을 "컬럼 없음" 으로 두지 않는다 — 빈 매핑으로 저장하면 할당이 모두 해제된다
      setLoadError(errorMessage(e) ?? String(e))
    })
    return () => {
      stale = true
    }
  }, [open, datasetId, reload])

  const byId = useMemo(() => new Map((definitions ?? []).map((d) => [d.id, d])), [definitions])
  // 지표 선택 상자 — 영역 · 분류로 묶는다
  const groups = useMemo(() => {
    const out: { name: string; items: QualityDefinition[] }[] = []
    for (const d of definitions ?? []) {
      const label = `${d.area_name} · ${d.category_name}`
      const group = out.find((g) => g.name === label)
      if (group) group.items.push(d)
      else out.push({ name: label, items: [d] })
    }
    return out
  }, [definitions])

  const columnNames = useMemo(() => (loaded?.columns ?? []).map((c) => c.name), [loaded])
  const changes = useMemo(
    () => mappingChanges(loaded?.rules ?? [], mapping, columnNames), [loaded, mapping, columnNames])
  const mapped = mappingSize(mapping)
  const direct = (loaded?.rules ?? []).filter((r) => r.definition_id == null).length
  const timed = scheduleType === "DAILY" || scheduleType === "WEEKLY" || scheduleType === "MONTHLY"
  // 주기 입력은 저장 전에 검사한다 — 서버가 거절하기 전에 매핑(할당·해제)이 먼저 반영되지 않게
  const scheduleError = scheduleProblem({ type: scheduleType, time, monthDay, intervalHours })
  const scheduleDenied = !canSchedule && scheduleType !== "NONE"
  const canSave = name.trim().length > 0 && dataset != null && loaded != null && mapped > 0 && !scheduleError
    && !scheduleDenied && !saving

  const save = async () => {
    if (!dataset || !loaded || scheduleError) return
    setSaving(true)
    try {
      // 매핑을 먼저 맞춘다 — 더한 것은 할당, 뺀 것은 할당 해제
      for (const a of changes.assign) {
        await assignQualityDefinition(a.definitionId, a.columns.map((c) => ({ dataset_id: dataset.id, column_name: c })))
      }
      for (const u of changes.unassign) await unassignQualityDefinition(u.definitionId, u.ruleIds)
      const payload: QualityJobWrite = {
        name: name.trim(),
        description: description.trim() || null,
        targets: [{ type: "dataset", dataset_id: dataset.id }],
        indicators: [],
        definition_ids: mappedDefinitionIds(mapping),
        schedule_type: scheduleType,
        schedule_time: timed ? time : null,
        schedule_day: scheduleType === "WEEKLY" ? Number(weekday) : scheduleType === "MONTHLY" ? Number(monthDay) : null,
        schedule_interval_hours: scheduleType === "HOURLY" ? Number(intervalHours) : null,
        timezone: job?.timezone ?? "Asia/Seoul",
        enabled,
        include_profile: includeProfile,
      }
      await saveQualityJob(job?.id ?? null, payload)
      toast.success(job ? "품질 진단 작업을 수정했습니다." : "품질 진단 작업을 등록했습니다.")
      onSaved()
      onOpenChange(false)
    } catch (e) {
      notifyError(e, "품질 진단 작업을 저장하지 못했습니다.")
      // 매핑 일부가 이미 반영됐을 수 있다 — 지금 상태를 다시 읽는다
      fetchQualityDatasetRules(dataset.id)
        .then((rules) => setLoaded((prev) => (prev ? { ...prev, rules } : prev)))
        .catch(() => undefined)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{job ? "품질 진단 작업 수정" : "품질 진단 작업 등록"}</DialogTitle>
          <DialogDescription>
            데이터셋 하나를 골라 컬럼마다 품질 지표를 매핑하면, 주기마다 그 컬럼들을 정밀 진단합니다(Trino 연결이 있으면 Trino, 없으면 원천 직접).
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 text-sm">
          <div className="grid gap-1.5">
            <Label htmlFor="job-name">이름</Label>
            <Input id="job-name" value={name} maxLength={200} onChange={(e) => setName(e.target.value)}
              placeholder="예: 주소 테이블 야간 품질 진단" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="job-desc">설명</Label>
            <Textarea id="job-desc" value={description} rows={2} maxLength={2000}
              onChange={(e) => setDescription(e.target.value)} />
          </div>

          <div className="grid gap-2">
            <Label>진단 대상 데이터셋</Label>
            {isLegacyTarget(job) && dataset == null && (
              <p className="rounded border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs">
                이 작업은 예전 방식(카탈로그 · 스키마 또는 데이터셋 여러 개)으로 등록됐습니다 —{" "}
                {job!.targets.map((t) => t.label ?? "").filter(Boolean).join(", ")}. 지금은 작업 하나가 데이터셋 하나를
                진단합니다. 데이터셋을 하나 골라 저장하면 이 작업이 그 데이터셋의 작업으로 바뀝니다.
              </p>
            )}
            {dataset ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="flex items-center gap-1 rounded border bg-muted/40 px-2 py-1">
                  <span className="font-medium">{dataset.label}</span>
                  <Tip label="다른 데이터셋 고르기"><button type="button" aria-label="다른 데이터셋 고르기"
                    className="text-muted-foreground hover:text-foreground" onClick={() => setDataset(null)}>
                    <X className="size-3" />
                  </button></Tip>
                </span>
                {loaded?.engine?.engine_table && (
                  <span className="font-mono text-xs">{engineLabel(loaded.engine.engine)}: {loaded.engine.engine_table}</span>
                )}
              </div>
            ) : (
              <div className="grid gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Select value={sourceId} onValueChange={setSourceId}>
                    <SelectTrigger className="h-8 w-56" aria-label="데이터 소스"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={ANY_SOURCE}>모든 데이터 소스</SelectItem>
                      {sources.map((ds) => <SelectItem key={ds.key} value={ds.key}>{ds.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                  <SearchInput size="sm" className="min-w-48 flex-1" value={search} onValueChange={setSearch}
                    placeholder={sourceId === ANY_SOURCE ? "데이터셋 이름 검색(2글자 이상)…" : "이 데이터 소스에서 이름 검색…"} />
                </div>
                {sourceId === ANY_SOURCE && search.trim().length < 2 ? (
                  <p className="text-xs">
                    데이터 소스를 고르면 그 데이터셋 목록이 나옵니다. 이름을 2글자 이상 적어 바로 찾을 수도 있습니다.
                  </p>
                ) : searchError ? (
                  <p className="text-xs text-destructive">데이터셋을 찾지 못했습니다: {searchError}</p>
                ) : hits.length === 0 ? (
                  <p className="text-xs text-muted-foreground">{searching ? "찾는 중…" : "조건에 맞는 데이터셋이 없습니다."}</p>
                ) : (
                  <>
                    <ul className="max-h-48 overflow-auto rounded border">
                      {hits.map((d) => (
                        <li key={d.id} className="border-b last:border-b-0">
                          <button type="button"
                            className="flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left hover:bg-muted"
                            onClick={() => setDataset({ id: d.id, label: d.name })}>
                            <span className="break-all">{d.name}</span>
                            <span className="shrink-0 text-xs">{d.datasource_name}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                    {hitsTotal > hits.length && (
                      <p className="text-xs">
                        {hitsTotal.toLocaleString()}개 가운데 앞의 {hits.length}개만 보입니다 — 이름을 적어 좁히세요.
                      </p>
                    )}
                  </>
                )}
                <p className="text-xs">
                  작업 하나가 데이터셋 하나를 진단합니다. 여러 테이블을 진단하려면 테이블마다 작업을 만드세요.
                </p>
              </div>
            )}
            {loaded?.engine && !loaded.engine.available && (
              <p className="rounded border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                이 데이터셋은 지금 정밀 진단할 수 없습니다 — {loaded.engine.reason ?? "Trino 연결이나 원천 접속 설정을 확인하세요."}
              </p>
            )}
          </div>

          {dataset && (
            <div className="grid gap-2">
              <Label>컬럼별 품질 지표 ({mapped})</Label>
              {loadError || definitionsError ? (
                <p className="flex flex-wrap items-center gap-2 text-sm text-destructive">
                  {loadError ? "데이터셋의 컬럼과 할당을" : "품질 지표를"} 불러오지 못했습니다: {loadError ?? definitionsError}
                  <Button variant="outline" size="sm" onClick={() => setReload((n) => n + 1)}>다시 시도</Button>
                </p>
              ) : loaded == null || definitions == null ? (
                <div className="h-24 animate-pulse rounded border bg-muted/40" />
              ) : loaded.columns.length === 0 ? (
                <p className="text-sm text-muted-foreground">이 데이터셋에 컬럼 정보가 없습니다. 메타데이터 수집을 먼저 실행하세요.</p>
              ) : definitions.length === 0 ? (
                <p className="text-sm">
                  등록한 품질 지표가 없습니다.{" "}
                  <Link href={qualityDefinitionsHref()} className="underline" onClick={() => onOpenChange(false)}>품질 지표 탭</Link>
                  에서 지표를 먼저 만드세요.
                </p>
              ) : (
                <div className="max-h-80 overflow-y-auto rounded border">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 z-10 bg-muted text-left text-muted-foreground">
                      <tr>
                        <th className="w-48 px-3 py-2 font-medium">컬럼</th>
                        <th className="w-36 px-3 py-2 font-medium">타입</th>
                        <th className="px-3 py-2 font-medium">품질 지표</th>
                      </tr>
                    </thead>
                    <tbody>
                      {loaded.columns.map((c) => {
                        const chosen = definitionsOf(mapping, c.name)
                        return (
                          <tr key={c.name} className="border-t align-top">
                            <td className="break-all px-3 py-2 font-mono">{c.name}</td>
                            <td className="break-all px-3 py-2 font-mono">{c.type}</td>
                            <td className="px-3 py-2">
                              <div className="flex flex-wrap items-center gap-1.5">
                                {chosen.map((id) => {
                                  const d = byId.get(id)
                                  return (
                                    <span key={id} className="flex items-center gap-1 rounded border bg-muted/40 px-1.5 py-0.5"
                                      title={d ? criteriaText(d) : undefined}>
                                      {d?.name ?? `삭제된 지표 #${id}`}
                                      <Tip label={`${c.name} 에서 ${d?.name ?? id} 빼기`}><button type="button" aria-label={`${c.name} 에서 ${d?.name ?? id} 빼기`}
                                        className="text-muted-foreground hover:text-foreground"
                                        onClick={() => setMapping((m) => toggleMapping(m, c.name, id, false))}>
                                        <X className="size-3" />
                                      </button></Tip>
                                    </span>
                                  )
                                })}
                                {/* 고르면 바로 더해지고 선택 상자는 다시 비워진다 */}
                                <Select value="" onValueChange={(v) => setMapping((m) => toggleMapping(m, c.name, Number(v), true))}>
                                  <SelectTrigger className="h-8 w-44 text-sm" aria-label={`${c.name} 에 품질 지표 더하기`}>
                                    <SelectValue placeholder={chosen.length ? "지표 더하기" : "선택 안 함"} />
                                  </SelectTrigger>
                                  <SelectContent>
                                    {groups.map((g) => (
                                      <SelectGroup key={g.name}>
                                        <SelectLabel>{g.name}</SelectLabel>
                                        {g.items.map((d) => (
                                          <SelectItem key={d.id} value={String(d.id)} disabled={chosen.includes(d.id)}
                                            title={criteriaText(d)}>
                                            {d.name}
                                          </SelectItem>
                                        ))}
                                      </SelectGroup>
                                    ))}
                                  </SelectContent>
                                </Select>
                              </div>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="text-sm">
                매핑은 이 데이터셋의 지표 할당과 같습니다 — 품질 지표 탭이나 데이터셋 품질 탭에서 할당한 것이 여기에 보이고,
                여기서 바꾸면 그쪽에도 반영됩니다.
                {mapped === 0 && loaded != null && " 컬럼에 품질 지표를 하나 이상 매핑해야 저장할 수 있습니다."}
                {changes.added > 0 && ` 저장하면 ${changes.added}개를 새로 할당합니다.`}
                {changes.removed > 0 && (
                  <span className="text-destructive">
                    {" "}뺀 {changes.removed}개는 저장할 때 할당이 해제되고, 그 규칙의 지난 진단 결과도 함께 지워집니다.
                  </span>
                )}
                {direct > 0 && ` 지표 없이 직접 만든 규칙 ${direct}개는 이 작업이 진단하지 않습니다(데이터셋 품질 탭의 정밀 진단으로 진단).`}
              </p>
            </div>
          )}

          <div className="grid gap-2">
            <Label>주기</Label>
            <div className="flex flex-wrap items-center gap-2">
              <Select value={scheduleType} onValueChange={(v) => setScheduleType(v as QualityScheduleType)}>
                <SelectTrigger className="h-8 w-32"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {SCHEDULE_TYPES.filter((s) => canSchedule || s.value === "NONE" || s.value === scheduleType)
                    .map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
                </SelectContent>
              </Select>
              {scheduleType === "WEEKLY" && (
                <Select value={weekday} onValueChange={setWeekday}>
                  <SelectTrigger className="h-8 w-24"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {WEEKDAYS.map((d, i) => <SelectItem key={d} value={String(i)}>{d}요일</SelectItem>)}
                  </SelectContent>
                </Select>
              )}
              {scheduleType === "MONTHLY" && (
                <span className="flex items-center gap-1 text-xs">
                  <Input className="h-8 w-16" type="number" min={1} max={31} value={monthDay}
                    onChange={(e) => setMonthDay(e.target.value)} aria-label="날짜" /> 일
                </span>
              )}
              {timed && (
                <Input className="h-8 w-28" type="time" value={time} onChange={(e) => setTime(e.target.value)}
                  aria-label="실행 시각" />
              )}
              {scheduleType === "HOURLY" && (
                <span className="flex items-center gap-1 text-xs">
                  <Input className="h-8 w-16" type="number" min={1} max={168} value={intervalHours}
                    onChange={(e) => setIntervalHours(e.target.value)} aria-label="간격(시간)" /> 시간마다
                </span>
              )}
              <span className="ml-auto flex items-center gap-2 text-xs">
                <Switch id="job-enabled" checked={enabled} onCheckedChange={setEnabled} />
                <label htmlFor="job-enabled">주기 실행 사용</label>
              </span>
            </div>
            {scheduleError && <p className="text-xs text-destructive" role="alert">{scheduleError}</p>}
            {scheduleDenied && (
              <p className="text-xs text-destructive" role="alert">
                주기 설정 권한이 없어 주기가 있는 작업은 저장할 수 없습니다. &quot;수동 실행만&quot; 으로 바꿔 저장하세요.
              </p>
            )}
            <p className="text-xs">
              {scheduleType === "NONE"
                ? "\"지금 실행\" 으로만 돌립니다."
                : `시각은 ${job?.timezone ?? "Asia/Seoul"} 기준입니다. 이전 실행이 끝나지 않았으면 그 차례는 건너뜁니다.`}
              {scheduleType === "MONTHLY" && " 그 달에 없는 날짜는 말일에 실행합니다."}
            </p>
            <div className="flex items-center gap-2 text-xs">
              <Switch id="job-include-profile" checked={includeProfile} onCheckedChange={setIncludeProfile} />
              <label htmlFor="job-include-profile">진단과 함께 프로파일도 수집</label>
            </div>
            <p className="text-xs">
              회차마다 데이터셋의 컬럼 통계(행 수 · NULL · 고유값 · 최솟값 · 최댓값)를 새로 뜹니다. 규칙이 없는 데이터셋도
              프로파일은 수집하며, 프로파일 실패는 회차를 &quot;일부 실패&quot; 로 표시합니다.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>닫기</Button>
          <Button onClick={() => void save()} disabled={!canSave}>{saving ? "저장 중…" : "저장"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
