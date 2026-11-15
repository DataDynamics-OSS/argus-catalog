"use client"

import { useEffect, useState } from "react"
import { Loader2, Save, Wand2 } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
import { Input } from "@workspace/ui/components/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { Switch } from "@workspace/ui/components/switch"
import { Textarea } from "@workspace/ui/components/textarea"

import { fetchAiReadyDefaults, fetchAiReadySuggestions, updateDataset, type AiReadyDefaults } from "../api"
import { DatasetVersionHistoryButton } from "./dataset-version-history"
import { type DatasetDetail } from "../data/schema"
import { TagInput } from "@/components/tag-input"
import { joinCsv, splitCsv } from "@/lib/text-list"

const NONE = "__none__" // Select 에서 "미지정" 표현 (빈 문자열 SelectItem 불가)

type Opt = { value: string; label: string; desc?: string }

// 값(value)은 enum 코드로 저장하고 화면에는 한글 라벨을 표시.
const FREQ: Opt[] = [
  { value: "REALTIME", label: "실시간" },
  { value: "HOURLY", label: "매시간" },
  { value: "DAILY", label: "매일" },
  { value: "WEEKLY", label: "매주" },
  { value: "MONTHLY", label: "매월" },
  { value: "MANUAL", label: "수동" },
]
const MODE: Opt[] = [
  { value: "BATCH", label: "배치", desc: "정해진 주기에 데이터를 모아 일괄 적재" },
  { value: "STREAMING", label: "스트리밍", desc: "이벤트 발생 즉시 연속적으로 적재" },
  { value: "CDC", label: "변경 데이터 캡처(CDC)", desc: "원천의 변경분(insert/update/delete)만 감지해 반영" },
  { value: "MANUAL", label: "수동", desc: "사람이 직접 업로드하거나 적재를 실행" },
]
const UPDATE = ["FULL", "INCREMENTAL", "APPEND", "UPSERT"]
const WEEKDAYS: Opt[] = [
  { value: "MON", label: "월요일" }, { value: "TUE", label: "화요일" }, { value: "WED", label: "수요일" },
  { value: "THU", label: "목요일" }, { value: "FRI", label: "금요일" }, { value: "SAT", label: "토요일" },
  { value: "SUN", label: "일요일" },
]
// 24시 기준 시/분 (오전·오후 없음)
const HOURS = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0"))
const MINUTES = Array.from({ length: 60 }, (_, i) => String(i).padStart(2, "0"))
const DATA_CATEGORY: Opt[] = [
  { value: "STRUCTURED", label: "정형" },
  { value: "SEMI_STRUCTURED", label: "반정형" },
  { value: "UNSTRUCTURED", label: "비정형" },
]
const FORMAT = ["CSV", "TSV", "JSON", "XML", "PARQUET", "ORC", "AVRO", "IMAGE", "VIDEO", "AUDIO", "DOCUMENT", "BINARY", "OTHER"]
const COMPRESSION = ["NONE", "GZIP", "SNAPPY", "ZSTD", "BZIP2", "LZ4"]
// 문자 인코딩 목록 (한국어/유니코드/일본어/중국어/서구권). 코드는 고유명사라 그대로 표시.
const ENCODINGS = [
  "UTF-8", "UTF-16", "UTF-16LE", "UTF-16BE", "UTF-32",
  "EUC-KR", "MS949", "CP949", "ISO-2022-KR",
  "EUC-JP", "Shift_JIS", "ISO-2022-JP",
  "GB2312", "GBK", "GB18030", "Big5",
  "ASCII", "ISO-8859-1", "ISO-8859-15", "Windows-1252",
  "KOI8-R", "TIS-620", "Windows-1251",
]
const SENSITIVITY = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
const TIER = ["GOLD", "SILVER", "BRONZE"]
const CERTIFICATION = ["CERTIFIED", "IN_REVIEW", "DEPRECATED", "NONE"]
const QUALITY = ["GOOD", "WARN", "BAD", "UNKNOWN"]
// 포함 여부(미지정 / 포함 / 없음) — null 은 "확인하지 않음"이라 Switch 대신 선택으로 받는다.
const PRESENCE: Opt[] = [
  { value: "true", label: "포함" },
  { value: "false", label: "없음" },
]
// 서버 응답이 오기 전·실패 시 쓰는 선택지 (GET /catalog/ai-ready/defaults 와 같은 값)
const FALLBACK_LICENSES = ["KOGL-1", "KOGL-2", "KOGL-3", "KOGL-4", "CC-BY-4.0", "CC0-1.0", "CUSTOM"].map((c) => ({ code: c, label: c }))
const FALLBACK_USES = [
  { code: "VIEW", label: "열람" }, { code: "ANALYSIS", label: "분석" },
  { code: "COMBINE", label: "결합" }, { code: "AI_TRAINING", label: "AI 학습" },
]

// 바이트 크기 단위 (저장은 항상 bytes). 사용자 요청 단위 목록.
const BYTE_UNITS: { value: string; label: string; factor: number }[] = [
  { value: "B", label: "Bytes", factor: 1 },
  { value: "MiB", label: "MiB", factor: 1024 ** 2 },
  { value: "GiB", label: "GiB", factor: 1024 ** 3 },
  { value: "TiB", label: "TiB", factor: 1024 ** 4 },
  { value: "PiB", label: "PiB", factor: 1024 ** 5 },
]

// 저장된 bytes 를 표시용 (값, 단위) 로 분해 — 정확히 나누어떨어지는 가장 큰 단위 선택, 없으면 Bytes.
function splitBytes(bytes?: number | null): { value: string; unit: string } {
  if (bytes == null) return { value: "", unit: "B" }
  for (let i = BYTE_UNITS.length - 1; i >= 1; i--) {
    const f = BYTE_UNITS[i]!.factor
    if (bytes % f === 0 && bytes / f >= 1) return { value: String(bytes / f), unit: BYTE_UNITS[i]!.value }
  }
  return { value: String(bytes), unit: "B" }
}

type FormState = {
  ingestion_frequency: string
  ingestion_time: string
  ingestion_day: string
  ingestion_timezone: string
  ingestion_cron: string
  ingestion_mode: string
  update_type: string
  freshness_sla: string
  retention_days: string
  purge_days: string
  data_category: string
  data_format: string
  compression: string
  encoding: string
  row_count: string
  byte_size_value: string
  byte_size_unit: string
  file_count: string
  sensitivity: string
  contains_pii: boolean
  pii_fields: string[]
  compliance_tags: string[]
  tier: string
  certification: string
  quality_status: string
  note: string
  // AI-Ready
  legal_basis: string
  publisher: string
  landing_page: string
  contact_email: string
  data_language: string
  persistent_id: string
  temporal_start: string
  temporal_end: string
  spatial_coverage: string
  data_version: string
  version_notes: string
  license: string
  license_url: string
  rights: string
  allowed_uses: string[] | null // null = 정하지 않음
  contains_synthetic: string // "" | "true" | "false"
  contains_machine_annotation: string
  data_biases: string
  known_limitations: string
  missing_data_info: string
  missing_value_notation: string
  privacy_measures: string
}

const presence = (v: boolean | null | undefined) => (v == null ? "" : v ? "true" : "false")

function initForm(d: DatasetDetail): FormState {
  const s = (v: string | null | undefined) => v ?? ""
  const n = (v: number | null | undefined) => (v == null ? "" : String(v))
  return {
    ingestion_frequency: s(d.ingestion_frequency),
    ingestion_time: s(d.ingestion_time),
    ingestion_day: s(d.ingestion_day),
    ingestion_timezone: d.ingestion_timezone || "Asia/Seoul",
    ingestion_cron: s(d.ingestion_cron),
    ingestion_mode: s(d.ingestion_mode),
    update_type: s(d.update_type),
    freshness_sla: s(d.freshness_sla),
    retention_days: n(d.retention_days),
    purge_days: n(d.purge_days),
    data_category: d.data_category || "STRUCTURED",
    data_format: s(d.data_format),
    compression: d.compression || "NONE",
    encoding: s(d.encoding),
    row_count: n(d.row_count),
    byte_size_value: splitBytes(d.byte_size).value,
    byte_size_unit: splitBytes(d.byte_size).unit,
    file_count: n(d.file_count),
    sensitivity: d.sensitivity || "INTERNAL",
    contains_pii: d.contains_pii === true,
    pii_fields: d.pii_fields ?? [],
    compliance_tags: d.compliance_tags ?? [],
    tier: d.tier || "BRONZE",
    certification: d.certification || "NONE",
    quality_status: d.quality_status || "UNKNOWN",
    note: s(d.note),
    legal_basis: s(d.legal_basis),
    publisher: s(d.publisher),
    landing_page: s(d.landing_page),
    contact_email: s(d.contact_email),
    data_language: s(d.data_language),
    persistent_id: s(d.persistent_id),
    temporal_start: s(d.temporal_start),
    temporal_end: s(d.temporal_end),
    spatial_coverage: s(d.spatial_coverage),
    data_version: s(d.data_version),
    version_notes: s(d.version_notes),
    license: s(d.license),
    license_url: s(d.license_url),
    rights: s(d.rights),
    allowed_uses: d.allowed_uses ?? null,
    contains_synthetic: presence(d.contains_synthetic),
    contains_machine_annotation: presence(d.contains_machine_annotation),
    data_biases: s(d.data_biases),
    known_limitations: s(d.known_limitations),
    missing_data_info: s(d.missing_data_info),
    missing_value_notation: s(d.missing_value_notation),
    privacy_measures: s(d.privacy_measures),
  }
}

type MetaGroup = { name: string; rows: { label: string; control: React.ReactNode; desc: React.ReactNode }[] }

export function DatasetMetadataTab({
  dataset,
  onSaved,
  extraGroup,
  canEdit = true,
}: {
  dataset: DatasetDetail
  onSaved: (updated: DatasetDetail) => void
  /** 표 맨 앞에 추가로 렌더할 그룹(예: 분류·소유 — 태그/소유자/용어집 인라인 편집). 각 칸이 자기 권한을 따르며 canEdit 로 비활성화하지 않는다. */
  extraGroup?: MetaGroup
  /** 편집 가능 여부 — 데이터셋 소유자 또는 관리자(서버 PUT 과 같은 기준). false 면 읽기 전용(입력 비활성, 저장 버튼 없음). */
  canEdit?: boolean
}) {
  const [form, setForm] = useState<FormState>(() => initForm(dataset))
  const [saving, setSaving] = useState(false)
  const [aiDefaults, setAiDefaults] = useState<AiReadyDefaults | null>(null)
  const [suggesting, setSuggesting] = useState(false)

  // 기관 기본값·선택지 — 실패해도 입력은 가능(기본값 안내와 서버 라벨만 빠진다)
  useEffect(() => {
    let alive = true
    fetchAiReadyDefaults().then((d) => { if (alive) setAiDefaults(d) }).catch(() => {})
    return () => { alive = false }
  }, [])

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const numOrNull = (v: string) => (v.trim() === "" ? null : Number(v))
  const strOrNull = (v: string) => (v.trim() === "" ? null : v.trim())
  const bytesOrNull = (): number | null => {
    if (form.byte_size_value.trim() === "") return null
    const f = BYTE_UNITS.find((u) => u.value === form.byte_size_unit)?.factor ?? 1
    return Math.round(Number(form.byte_size_value) * f)
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const updated = await updateDataset(dataset.id, {
        ingestion_frequency: strOrNull(form.ingestion_frequency),
        ingestion_time: strOrNull(form.ingestion_time),
        ingestion_day: strOrNull(form.ingestion_day),
        ingestion_timezone: form.ingestion_timezone,
        ingestion_cron: strOrNull(form.ingestion_cron),
        ingestion_mode: strOrNull(form.ingestion_mode),
        update_type: strOrNull(form.update_type),
        freshness_sla: strOrNull(form.freshness_sla),
        retention_days: numOrNull(form.retention_days),
        purge_days: numOrNull(form.purge_days),
        data_category: strOrNull(form.data_category),
        data_format: strOrNull(form.data_format),
        compression: strOrNull(form.compression),
        encoding: strOrNull(form.encoding),
        row_count: numOrNull(form.row_count),
        byte_size: bytesOrNull(),
        file_count: numOrNull(form.file_count),
        sensitivity: strOrNull(form.sensitivity),
        contains_pii: form.contains_pii,
        pii_fields: form.pii_fields.length ? form.pii_fields : null,
        compliance_tags: form.compliance_tags.length ? form.compliance_tags : null,
        tier: strOrNull(form.tier),
        certification: strOrNull(form.certification),
        quality_status: strOrNull(form.quality_status),
        note: form.note.trim() === "" ? null : form.note,
        // AI-Ready — 빈 값은 null(지움). 기관 기본값은 데이터셋에 써 넣지 않는다.
        legal_basis: strOrNull(form.legal_basis),
        publisher: strOrNull(form.publisher),
        landing_page: strOrNull(form.landing_page),
        contact_email: strOrNull(form.contact_email),
        data_language: strOrNull(form.data_language),
        persistent_id: strOrNull(form.persistent_id),
        temporal_start: strOrNull(form.temporal_start),
        temporal_end: strOrNull(form.temporal_end),
        spatial_coverage: strOrNull(form.spatial_coverage),
        data_version: strOrNull(form.data_version),
        version_notes: strOrNull(form.version_notes),
        license: strOrNull(form.license),
        license_url: strOrNull(form.license_url),
        rights: strOrNull(form.rights),
        allowed_uses: form.allowed_uses,
        contains_synthetic: form.contains_synthetic === "" ? null : form.contains_synthetic === "true",
        contains_machine_annotation: form.contains_machine_annotation === "" ? null : form.contains_machine_annotation === "true",
        data_biases: strOrNull(form.data_biases),
        known_limitations: strOrNull(form.known_limitations),
        missing_data_info: strOrNull(form.missing_data_info),
        missing_value_notation: strOrNull(form.missing_value_notation),
        privacy_measures: strOrNull(form.privacy_measures),
      })
      setForm(initForm(updated)) // 서버가 다듬은 값(언어 소문자·이용 유형 순서 등)으로 맞춘다
      onSaved(updated)
      toast.success("메타데이터를 저장했습니다.")
    } catch (e) {
      notifyError(e, "메타데이터를 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  // 선택형 컨트롤 ("미지정" 옵션 포함). 문자열/Opt 혼용 가능.
  const sel = (key: keyof FormState, options: Array<string | Opt>, placeholder = "미지정") => {
    const opts: Opt[] = options.map((o) => (typeof o === "string" ? { value: o, label: o } : o))
    return (
      <Select
        value={(form[key] as string) || NONE}
        onValueChange={(v) => set(key, (v === NONE ? "" : v) as never)}
      >
        <SelectTrigger className="h-9 w-full text-sm"><SelectValue placeholder={placeholder} /></SelectTrigger>
        <SelectContent>
          <SelectItem value={NONE} className="text-sm text-muted-foreground">미지정</SelectItem>
          {opts.map((o) => (
            <SelectItem key={o.value} value={o.value} className="text-sm">{o.label}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }

  // 정수만 입력 (숫자 외 문자는 제거)
  const intInput = (key: keyof FormState, placeholder = "") => (
    <Input
      inputMode="numeric"
      className="h-9 w-full text-sm"
      value={form[key] as string}
      onChange={(e) => set(key, e.target.value.replace(/[^0-9]/g, "") as never)}
      placeholder={placeholder}
    />
  )
  const txtInput = (key: keyof FormState, placeholder = "") => (
    <Input className="h-9 w-full text-sm" value={form[key] as string} onChange={(e) => set(key, e.target.value as never)} placeholder={placeholder} />
  )
  // 바이트 크기: 숫자(소수 허용) + 단위 선택. 저장 시 bytes 로 환산.
  const byteSizeControl = (
    <div className="flex items-center gap-2">
      <Input
        inputMode="decimal"
        className="h-9 flex-1 text-sm"
        value={form.byte_size_value}
        onChange={(e) => set("byte_size_value", e.target.value.replace(/[^0-9.]/g, ""))}
        placeholder="숫자"
      />
      <Select value={form.byte_size_unit} onValueChange={(v) => set("byte_size_unit", v)}>
        <SelectTrigger className="h-9 w-24 shrink-0 text-sm"><SelectValue /></SelectTrigger>
        <SelectContent>
          {BYTE_UNITS.map((u) => <SelectItem key={u.value} value={u.value} className="text-sm">{u.label}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  )

  // 수집 주기 + 주기별 상세(시각/요일/일자) 조건부 입력. 시각은 24시 시/분 Select(HH:mm), 시간별은 분.
  const [curHH = "", curMM = ""] = (form.ingestion_time || "").split(":")
  const timeInput = (
    <div className="flex items-center gap-1">
      <Select value={curHH} onValueChange={(h) => set("ingestion_time", `${h}:${curMM || "00"}`)}>
        <SelectTrigger className="h-9 w-[68px] text-sm"><SelectValue placeholder="시" /></SelectTrigger>
        <SelectContent className="max-h-60">
          {HOURS.map((h) => <SelectItem key={h} value={h} className="text-sm">{h}</SelectItem>)}
        </SelectContent>
      </Select>
      <span className="text-sm text-muted-foreground">:</span>
      <Select value={curMM} onValueChange={(m) => set("ingestion_time", `${curHH || "00"}:${m}`)}>
        <SelectTrigger className="h-9 w-[68px] text-sm"><SelectValue placeholder="분" /></SelectTrigger>
        <SelectContent className="max-h-60">
          {MINUTES.map((m) => <SelectItem key={m} value={m} className="text-sm">{m}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  )
  const ingestionControl = (
    <div className="flex flex-col gap-1.5">
      {sel("ingestion_frequency", FREQ)}
      {form.ingestion_frequency === "DAILY" && timeInput}
      {form.ingestion_frequency === "WEEKLY" && (
        <div className="flex gap-1.5">
          <Select value={form.ingestion_day || NONE} onValueChange={(v) => set("ingestion_day", v === NONE ? "" : v)}>
            <SelectTrigger className="h-9 w-24 text-sm"><SelectValue placeholder="요일" /></SelectTrigger>
            <SelectContent>
              {WEEKDAYS.map((w) => <SelectItem key={w.value} value={w.value} className="text-sm">{w.label}</SelectItem>)}
            </SelectContent>
          </Select>
          {timeInput}
        </div>
      )}
      {form.ingestion_frequency === "MONTHLY" && (
        <div className="flex items-center gap-1.5">
          <Input inputMode="numeric" className="h-9 w-24 text-sm" placeholder="일(1~31)"
            value={form.ingestion_day} onChange={(e) => set("ingestion_day", e.target.value.replace(/[^0-9]/g, ""))} />
          {timeInput}
        </div>
      )}
      {form.ingestion_frequency === "HOURLY" && (
        <div className="flex items-center gap-1.5">
          <Input inputMode="numeric" className="h-9 w-20 text-sm" placeholder="분(0~59)"
            value={form.ingestion_time} onChange={(e) => set("ingestion_time", e.target.value.replace(/[^0-9]/g, ""))} />
          <span className="text-xs">분</span>
        </div>
      )}
      {/* 고급: cron 표현식 (선택). 입력 시 표시에 함께 노출. */}
      <Input
        className="h-9 w-full font-mono text-sm"
        value={form.ingestion_cron}
        onChange={(e) => set("ingestion_cron", e.target.value)}
        placeholder="cron (고급, 선택) 예: 0 6 * * *"
      />
    </div>
  )

  // --- AI-Ready 컨트롤 ---
  const def = aiDefaults?.defaults
  const licenses = aiDefaults?.licenses ?? FALLBACK_LICENSES
  const useOptions = aiDefaults?.allowed_uses ?? FALLBACK_USES
  const licenseLabel = (code?: string | null) => licenses.find((l) => l.code === code)?.label ?? code ?? ""
  const defaultHint = (v?: string | null) => (v ? `비우면 기본값: ${v}` : "")
  const areaInput = (key: keyof FormState, placeholder = "") => (
    <Textarea className="min-h-[60px] w-full text-sm" rows={2} value={form[key] as string}
      onChange={(e) => set(key, e.target.value as never)} placeholder={placeholder} />
  )
  // 최신 프로파일에서 시간범위·결측 정보를 채운다(저장은 사용자가 '저장'을 눌러야)
  const fillFromProfile = async (what: "temporal" | "missing") => {
    setSuggesting(true)
    try {
      const sug = await fetchAiReadySuggestions(dataset.id)
      if (!sug.profile) {
        toast.info("프로파일이 없습니다. 품질 탭에서 프로파일을 먼저 수집하세요.")
      } else if (what === "temporal") {
        if (!sug.temporal) toast.info("프로파일에 날짜·시각 컬럼의 최소·최대값이 없습니다.")
        else {
          setForm((f) => ({ ...f, temporal_start: sug.temporal!.temporal_start, temporal_end: sug.temporal!.temporal_end }))
          toast.success(`'${sug.temporal.column}' 컬럼의 최소~최대값으로 채웠습니다. 확인 후 저장하세요.`)
        }
      } else if (sug.missing) {
        set("missing_data_info", sug.missing.missing_data_info)
        toast.success("프로파일의 결측률로 채웠습니다. 결측 사유·처리 방식을 덧붙여 저장하세요.")
      }
    } catch (e) {
      notifyError(e, "프로파일에서 제안을 만들지 못했습니다.")
    } finally {
      setSuggesting(false)
    }
  }
  const profileButton = (what: "temporal" | "missing") =>
    canEdit ? (
      <Button type="button" variant="outline" size="sm" className="h-7 w-fit text-xs" disabled={suggesting}
        onClick={() => fillFromProfile(what)}>
        {suggesting ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Wand2 className="mr-1 h-3 w-3" />}
        프로파일에서 채우기
      </Button>
    ) : null
  const temporalControl = (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1">
        <Input type="date" className="h-9 min-w-0 flex-1 text-sm" value={form.temporal_start}
          onChange={(e) => set("temporal_start", e.target.value)} aria-label="시간범위 시작" />
        <span className="text-sm">~</span>
        <Input type="date" className="h-9 min-w-0 flex-1 text-sm" value={form.temporal_end}
          onChange={(e) => set("temporal_end", e.target.value)} aria-label="시간범위 끝" />
      </div>
      {profileButton("temporal")}
    </div>
  )
  const licenseControl = (
    <div className="flex flex-col gap-1.5">
      <Select value={form.license || NONE} onValueChange={(v) => set("license", v === NONE ? "" : v)}>
        <SelectTrigger className="h-9 w-full text-sm">
          <SelectValue placeholder="미지정" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NONE} className="text-sm text-muted-foreground">
            {def?.license ? `기본값 사용 (${licenseLabel(def.license)})` : "미지정"}
          </SelectItem>
          {licenses.map((l) => <SelectItem key={l.code} value={l.code} className="text-sm">{l.label}</SelectItem>)}
        </SelectContent>
      </Select>
      <Input className="h-9 w-full text-sm" value={form.license_url} onChange={(e) => set("license_url", e.target.value)}
        placeholder={defaultHint(def?.license_url) || "라이선스·이용약관 URL (https://…)"} />
    </div>
  )
  const usesControl = (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {useOptions.map((u) => {
          const checked = form.allowed_uses?.includes(u.code) ?? false
          return (
            <label key={u.code} className="flex items-center gap-1.5 text-sm">
              <Checkbox checked={checked} onCheckedChange={(v) => {
                const cur = form.allowed_uses ?? []
                set("allowed_uses", v === true ? [...cur, u.code] : cur.filter((x) => x !== u.code))
              }} />
              {u.label}
            </label>
          )
        })}
      </div>
      {form.allowed_uses === null ? (
        <span className="text-xs">정하지 않음</span>
      ) : (
        <button type="button" className="w-fit text-xs text-muted-foreground underline-offset-2 hover:underline"
          onClick={() => set("allowed_uses", null)}>
          {form.allowed_uses.length === 0 ? "모두 허용하지 않음 · " : ""}정하지 않음으로 되돌리기
        </button>
      )}
    </div>
  )
  const missingControl = (
    <div className="flex flex-col gap-1.5">
      {areaInput("missing_data_info", "예: 센서 점검(2.1%), 통신 오류(1.1%) — 결측 행은 제외하지 않음")}
      {profileButton("missing")}
    </div>
  )

  // 영역별 행: { 항목, 설정값 컨트롤, 설명(기존 툴팁 내용 포함) }
  const fieldGroups: MetaGroup[] = [
    {
      name: "생명주기 · 운영",
      rows: [
        { label: "수집 주기", control: ingestionControl, desc: "데이터를 적재·갱신하는 주기와 시각. 매일=시각, 매주=요일+시각, 매월=일자+시각, 매시간=분 (기준 KST). 복잡한 일정은 cron(고급) 입력." },
        { label: "수집 방식", control: sel("ingestion_mode", MODE), desc: "배치: 정해진 주기에 모아 일괄 적재 · 스트리밍: 이벤트 발생 즉시 연속 적재 · CDC: 원천 변경분(insert/update/delete)만 감지해 반영 · 수동: 사람이 직접 적재" },
        { label: "갱신 유형", control: sel("update_type", UPDATE), desc: "적재 모드 — FULL: 매번 전체 재적재(기존 대체) · INCREMENTAL(증분): 마지막 이후 신규+변경분 반영(추가·수정 모두) · APPEND(추가): 기존은 그대로 두고 신규 행만 덧붙임(수정 없음) · UPSERT(병합): 키 기준 있으면 수정, 없으면 삽입" },
        { label: "최신성 SLA", control: txtInput("freshness_sla", "예: 매일 06:00까지"), desc: "데이터가 최신이어야 하는 기준 시점" },
        { label: "보존 기한(일)", control: intInput("retention_days", "비우면 영구"), desc: "데이터 보관 기간(일). 비우면 영구 보관" },
        { label: "삭제 주기(일)", control: intInput("purge_days", "비우면 없음"), desc: "보존 기한이 지난 데이터를 삭제하는 주기(일). 비우면 자동 삭제 없음" },
      ],
    },
    {
      name: "물리 · 형식",
      rows: [
        { label: "데이터 유형", control: sel("data_category", DATA_CATEGORY), desc: "정형: 행·열 구조(RDB/테이블) · 반정형: 구조 일부 포함(JSON/XML/CSV 등) · 비정형: 구조 없음(이미지/동영상/문서 등)" },
        { label: "데이터 형식", control: sel("data_format", FORMAT), desc: "저장 형식 — CSV/TSV/JSON/XML/Parquet/ORC/Avro/이미지/동영상/오디오/문서/바이너리" },
        { label: "압축", control: sel("compression", COMPRESSION), desc: "압축 방식 — GZIP/SNAPPY/ZSTD/BZIP2/LZ4 또는 없음" },
        { label: "인코딩", control: sel("encoding", ENCODINGS), desc: "문자 인코딩 (예: UTF-8, EUC-KR, MS949, CP949 등)" },
        { label: "행 수", control: intInput("row_count", "숫자"), desc: "데이터셋의 레코드(행) 수" },
        { label: "바이트 크기", control: byteSizeControl, desc: "데이터셋 전체 크기 (숫자 + 단위, 저장은 bytes 로 환산)" },
        { label: "파일 수", control: intInput("file_count", "숫자"), desc: "데이터셋을 구성하는 파일/객체 수" },
      ],
    },
    {
      name: "거버넌스 · 보안",
      rows: [
        { label: "민감도 등급", control: sel("sensitivity", SENSITIVITY), desc: "공개(PUBLIC): 외부 공개 가능, 제약 없음 · 대내한정(INTERNAL): 사내 구성원만 열람 · 대외비(CONFIDENTIAL): 인가된 일부만 열람, 외부 유출 금지 · 기밀(RESTRICTED): 최고 민감도, 통제된 소수만 접근" },
        { label: "개인정보(PII) 포함", control: <Switch checked={form.contains_pii} onCheckedChange={(v) => set("contains_pii", v)} />, desc: "개인정보 포함 여부" },
        { label: "개인정보 항목", control: <TagInput value={form.pii_fields} onChange={(v) => set("pii_fields", v as never)} placeholder="입력 후 Enter (예: 이름)" />, desc: "포함된 개인정보 항목. 입력 후 Enter 로 추가" },
        { label: "규제 태그", control: <TagInput value={form.compliance_tags} onChange={(v) => set("compliance_tags", v as never)} placeholder="입력 후 Enter (예: GDPR)" />, desc: "적용 규제(개인정보보호법/GDPR/PCI 등). 입력 후 Enter 로 추가" },
      ],
    },
    {
      name: "비즈니스",
      rows: [
        { label: "등급(Tier)", control: sel("tier", TIER), desc: "데이터 성숙도·신뢰 등급 — Gold: 검증·정제 완료된 공식 데이터(전사 신뢰·활용) · Silver: 일부 정제되어 활용 가능한 데이터 · Bronze: 원천/미정제(raw) 데이터" },
        { label: "인증 상태", control: sel("certification", CERTIFICATION), desc: "CERTIFIED(인증됨): 검증을 거친 신뢰 가능한 공식 데이터 · IN_REVIEW(검토 중): 인증 검토 진행 중 · DEPRECATED(사용 중단): 더 이상 권장되지 않음(폐기·대체 예정) · NONE(없음): 인증 절차 미진행(기본)" },
      ],
    },
    {
      name: "품질",
      rows: [
        { label: "품질 상태", control: sel("quality_status", QUALITY), desc: "품질 상태 — GOOD/WARN/BAD/UNKNOWN" },
      ],
    },
    {
      name: "AI 활용 (AI-Ready)",
      rows: [
        { label: "관련법령", control: areaInput("legal_basis", "예: 통계법 제3조"), desc: "데이터를 보유·제공하는 법적 근거 (dct:references). 가이드라인 필수 항목" },
        { label: "제공기관", control: txtInput("publisher", defaultHint(def?.publisher) || "예: 서울특별시"), desc: "데이터셋을 대외적으로 배포하는 기관 (dct:publisher). 비우면 설정 › AI-Ready 의 기관 기본값" },
        { label: "제공시스템 URL", control: txtInput("landing_page", "https://data.example.go.kr/dataset/…"), desc: "데이터를 내려받거나 자세히 볼 수 있는 페이지 (dcat:landingPage)" },
        { label: "문의 연락처", control: txtInput("contact_email", defaultHint(def?.contact_email) || "data@example.go.kr"), desc: "오류 신고·문의를 받는 역할 기준 공용 이메일 (dcat:contactPoint). 개인 메일보다 부서 공용 주소 권장" },
        { label: "언어", control: <TagInput commaSeparates mono value={splitCsv(form.data_language)} onChange={(v) => set("data_language", joinCsv(v) as never)} placeholder={defaultHint(def?.data_language) || "예: ko 입력 후 Enter"} />, desc: "데이터의 언어 — ISO 639-1 두 글자 코드를 하나씩 (예: ko · en)" },
        { label: "영구 식별자", control: txtInput("persistent_id", `비우면 URN: ${dataset.urn}`), desc: "이름·위치가 바뀌어도 변하지 않는 식별자 (dct:identifier). DOI 등이 있으면 입력" },
        { label: "시간범위", control: temporalControl, desc: "데이터가 다루는 기간 (dct:temporal). '프로파일에서 채우기'는 날짜·시각 컬럼 중 가장 넓은 최소~최대값을 넣습니다" },
        { label: "공간범위", control: txtInput("spatial_coverage", "예: 서울특별시 25개 자치구"), desc: "데이터가 다루는 지역 (dct:spatial)" },
        {
          label: "버전",
          control: txtInput("data_version", "예: 2025.01.v1"),
          // 이력 단추는 읽기 전용(fieldset disabled) 밖인 설명 칸에 — 누구나 이력을 볼 수 있다
          desc: (
            <div className="flex items-center justify-between gap-2">
              <span>현재 데이터 버전 (owl:versionInfo). 가이드라인 필수 항목. 버전·버전 노트를 저장할 때마다 이력에 남습니다</span>
              <DatasetVersionHistoryButton datasetId={dataset.id} canEdit={canEdit} />
            </div>
          ),
        },
        { label: "버전 노트", control: areaInput("version_notes", "이전 버전 대비 변경 사항"), desc: "이전 버전 대비 변경 사항 (adms:versionNotes). 가이드라인 필수 항목" },
        { label: "라이선스", control: licenseControl, desc: "이용허락 범위 (dct:license) — 공공누리 1~4유형, CC, 기관 이용약관(URL 지정). 비우면 기관 기본값" },
        { label: "저작권·이용 조건", control: areaInput("rights", "예: 상업적·비상업적 이용 모두 허용"), desc: "저작자 표시, 상업적 이용·변경 허용 여부 등 (dct:rights)" },
        { label: "허용 이용 유형", control: usesControl, desc: "이 데이터로 할 수 있는 일 — 열람·분석·결합·AI 학습. 가이드라인은 유형별 허용 여부를 메타데이터에 명시하도록 권고합니다" },
        { label: "합성 데이터", control: sel("contains_synthetic", PRESENCE), desc: "인공적으로 생성한 합성 데이터가 들어 있는지. 들어 있으면 반드시 표시 (체크리스트 필수)" },
        { label: "자동 주석", control: sel("contains_machine_annotation", PRESENCE), desc: "기계가 자동으로 단 주석·라벨이 들어 있는지 (체크리스트 필수)" },
        { label: "알려진 편향", control: areaInput("data_biases", "예: 도시 지역 측정소 편중으로 농촌 대표성 낮음"), desc: "대표성 한계·불균형 (rai:dataBiases). AI 모델의 편향을 줄이기 위해 권장" },
        { label: "한계·유의사항", control: areaInput("known_limitations", "예: 단기 체류 외국인은 포함하지 않음"), desc: "구조적·내용적 제한과 해석 시 주의할 점 (rai:knownLimitations)" },
        { label: "결측 정보", control: missingControl, desc: "결측 비율·사유·처리 방식 (rai:dataCollectionMissingData). '프로파일에서 채우기'는 최신 프로파일의 컬럼별 결측률을 넣습니다" },
        { label: "결측·특수값 표기", control: txtInput("missing_value_notation", "예: NULL, -9999=측정불가"), desc: "결측·특수값을 어떻게 적는지 — 표기 규칙 통일은 체크리스트 필수 항목" },
        { label: "개인정보 보호조치", control: areaInput("privacy_measures", "예: 주민번호 삭제, 연령 5세 범주화, 적정성 검토 완료(2025-03)"), desc: "개인정보가 있으면 비식별·가명처리 방식과 적정성 검토 여부 (체크리스트 필수)" },
      ],
    },
    {
      name: "비고",
      rows: [
        {
          label: "메모",
          control: (
            <Textarea
              className="min-h-[72px] w-full text-sm"
              rows={3}
              value={form.note}
              onChange={(e) => set("note", e.target.value)}
              placeholder="운영·검토 메모 등 자유롭게 입력"
            />
          ),
          desc: "자유 메모. 값이 있을 때만 개요에 표시됩니다.",
        },
      ],
    },
  ]

  // extraGroup(분류·소유)를 표 맨 앞에 배치.
  const groups: MetaGroup[] = extraGroup ? [extraGroup, ...fieldGroups] : fieldGroups

  return (
    <div className="space-y-4">
      {/* 읽기 전용이면 메타데이터 칸만 fieldset disabled 로 비활성화한다. extraGroup(분류·소유)은 칸마다 자기 권한으로
          단추를 보이거나 숨기므로 감싸지 않는다 — 분류는 taxonomies.map 으로 붙이고 뗀다(DEF-077, TODO-138) */}
      <div className="overflow-hidden rounded-md border">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-muted/40 text-left">
              <th className="w-36 border-b px-3 py-2 font-medium">영역</th>
              <th className="w-44 border-b px-3 py-2 font-medium">항목</th>
              <th className="w-64 border-b px-3 py-2 font-medium">설정값</th>
              <th className="border-b px-3 py-2 font-medium">설명</th>
            </tr>
          </thead>
          {/* 박스는 바깥 테두리 하나 — 칸 사이 세로선 없이 줄 사이 가로선만, 영역 칸만 오른쪽 선으로 묶음을 나눈다 */}
          <tbody className="[&>tr:last-child>td]:border-b-0">
            {groups.map((g) =>
              g.rows.map((r, ri) => (
                <tr key={`${g.name}-${r.label}`}>
                  {ri === 0 && (
                    <td rowSpan={g.rows.length}
                      className={`border-r bg-muted/30 px-3 py-2 align-top font-medium ${g === groups[groups.length - 1] ? "" : "border-b"}`}>
                      {g.name}
                    </td>
                  )}
                  <td className="border-b px-3 py-2 align-middle whitespace-nowrap">{r.label}</td>
                  <td className="border-b px-3 py-2 align-middle">
                    {g === extraGroup ? r.control : <fieldset disabled={!canEdit} className="contents">{r.control}</fieldset>}
                  </td>
                  <td className="border-b px-3 py-2 align-middle">{r.desc}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>

      {canEdit && (
        <Button onClick={handleSave} disabled={saving}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Save className="h-4 w-4 mr-1" />}
          저장
        </Button>
      )}
    </div>
  )
}
