"use client"

import { useState } from "react"
import { Loader2, Save, X } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
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

import { updateDataset } from "../api"
import { type DatasetDetail } from "../data/schema"

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
  pii_fields: string
  compliance_tags: string
  tier: string
  certification: string
  quality_status: string
  note: string
}

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
    pii_fields: s(d.pii_fields),
    compliance_tags: s(d.compliance_tags),
    tier: d.tier || "BRONZE",
    certification: d.certification || "NONE",
    quality_status: d.quality_status || "UNKNOWN",
    note: s(d.note),
  }
}

type MetaGroup = { name: string; rows: { label: string; control: React.ReactNode; desc: string }[] }

// 콤마 구분 문자열을 배지로 편집하는 입력. 입력 후 Enter → 배지 추가, X → 삭제.
// 저장 포맷은 기존과 동일한 콤마 구분 문자열을 유지한다.
function TagsInput({
  value,
  onChange,
  placeholder,
}: {
  value: string
  onChange: (next: string) => void
  placeholder?: string
}) {
  const [text, setText] = useState("")
  const tags = value.split(",").map((t) => t.trim()).filter(Boolean)

  // 콤마/Enter 로 한 번에 여러 개 입력 허용. 중복은 무시.
  const commit = (raw: string) => {
    const incoming = raw.split(",").map((t) => t.trim()).filter(Boolean)
    if (incoming.length === 0) return
    const next = [...tags]
    for (const t of incoming) if (!next.includes(t)) next.push(t)
    onChange(next.join(", "))
    setText("")
  }
  const remove = (t: string) => onChange(tags.filter((x) => x !== t).join(", "))

  return (
    <div className="flex w-full flex-wrap items-center gap-1">
      {tags.map((t) => (
        <Badge key={t} variant="secondary" className="gap-1 text-xs">
          {t}
          <button type="button" className="hover:opacity-70" onClick={() => remove(t)} aria-label={`${t} 제거`}>
            <X className="h-3 w-3" />
          </button>
        </Badge>
      ))}
      <Input
        className="h-9 min-w-[120px] flex-1 text-sm"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault()
            commit(text)
          } else if (e.key === "Backspace" && text === "" && tags.length > 0) {
            remove(tags[tags.length - 1]!)
          }
        }}
        onBlur={() => { if (text.trim()) commit(text) }}
        placeholder={placeholder}
      />
    </div>
  )
}

export function DatasetMetadataTab({
  dataset,
  onSaved,
  extraGroup,
}: {
  dataset: DatasetDetail
  onSaved: (updated: DatasetDetail) => void
  /** 표 맨 앞에 추가로 렌더할 그룹(예: 분류·소유 — 태그/소유자/용어집 인라인 편집). */
  extraGroup?: MetaGroup
}) {
  const [form, setForm] = useState<FormState>(() => initForm(dataset))
  const [saving, setSaving] = useState(false)

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
        pii_fields: strOrNull(form.pii_fields),
        compliance_tags: strOrNull(form.compliance_tags),
        tier: strOrNull(form.tier),
        certification: strOrNull(form.certification),
        quality_status: strOrNull(form.quality_status),
        note: form.note.trim() === "" ? null : form.note,
      })
      onSaved(updated)
      toast.success("메타데이터를 저장했습니다.")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "저장에 실패했습니다.")
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
          <span className="text-xs text-muted-foreground">분</span>
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
        { label: "개인정보 항목", control: <TagsInput value={form.pii_fields} onChange={(v) => set("pii_fields", v as never)} placeholder="입력 후 Enter (예: 이름)" />, desc: "포함된 개인정보 항목. 입력 후 Enter 로 추가" },
        { label: "규제 태그", control: <TagsInput value={form.compliance_tags} onChange={(v) => set("compliance_tags", v as never)} placeholder="입력 후 Enter (예: GDPR)" />, desc: "적용 규제(개인정보보호법/GDPR/PCI 등). 입력 후 Enter 로 추가" },
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
      <div className="overflow-hidden rounded-md border">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-muted/60 text-left">
              <th className="w-36 border px-3 py-2 font-medium">영역</th>
              <th className="w-44 border px-3 py-2 font-medium">항목</th>
              <th className="w-64 border px-3 py-2 font-medium">설정값</th>
              <th className="border px-3 py-2 font-medium">설명</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) =>
              g.rows.map((r, ri) => (
                <tr key={`${g.name}-${r.label}`}>
                  {ri === 0 && (
                    <td rowSpan={g.rows.length} className="border bg-muted/30 px-3 py-2 align-top font-medium">
                      {g.name}
                    </td>
                  )}
                  <td className="border px-3 py-2 align-middle whitespace-nowrap">{r.label}</td>
                  <td className="border px-3 py-2 align-middle">{r.control}</td>
                  <td className="border px-3 py-2 align-middle">{r.desc}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>

      <Button onClick={handleSave} disabled={saving}>
        {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Save className="h-4 w-4 mr-1" />}
        저장
      </Button>
    </div>
  )
}
