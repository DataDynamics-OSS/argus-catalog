"use client"

import { Fragment } from "react"

import { Badge } from "@workspace/ui/components/badge"
import { Card, CardContent } from "@workspace/ui/components/card"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@workspace/ui/components/tooltip"

import { type DatasetDetail } from "../data/schema"

const FREQ_LABEL: Record<string, string> = {
  REALTIME: "실시간", HOURLY: "매시간", DAILY: "매일", WEEKLY: "매주", MONTHLY: "매월", MANUAL: "수동",
}
const MODE_LABEL: Record<string, string> = {
  BATCH: "배치", STREAMING: "스트리밍", CDC: "변경 데이터 캡처(CDC)", MANUAL: "수동",
}
const CATEGORY_LABEL: Record<string, string> = {
  STRUCTURED: "정형", SEMI_STRUCTURED: "반정형", UNSTRUCTURED: "비정형",
}
const STATUS_LABEL: Record<string, string> = {
  active: "활성", inactive: "비활성", deprecated: "사용 중단", removed: "삭제됨",
}
// 코드 → 한글 라벨 (맵에 없으면 코드 그대로 = 고유명사 유지, 예: SNAPPY/GZIP/CSV)
const UPDATE_LABEL: Record<string, string> = { FULL: "전체", INCREMENTAL: "증분", APPEND: "추가", UPSERT: "병합" }
const CERT_LABEL: Record<string, string> = { CERTIFIED: "인증됨", IN_REVIEW: "검토 중", DEPRECATED: "사용 중단", NONE: "없음" }
const TIER_LABEL: Record<string, string> = { GOLD: "골드", SILVER: "실버", BRONZE: "브론즈" }
const QUALITY_LABEL: Record<string, string> = { GOOD: "양호", WARN: "주의", BAD: "불량", UNKNOWN: "미확인" }
const COMPRESSION_LABEL: Record<string, string> = { NONE: "없음" } // 알고리즘명(GZIP/SNAPPY/ZSTD…)은 고유명사라 유지
const SENSITIVITY_LABEL: Record<string, string> = { PUBLIC: "공개", INTERNAL: "대내한정", CONFIDENTIAL: "대외비", RESTRICTED: "기밀" }
// 코드 → 라벨(없으면 코드 유지), 빈 값은 "—"
const lbl = (map: Record<string, string>, v?: string | null) => (v ? (map[v] ?? v) : "—")
// 데이터 소스 유형 표시 라벨 (소문자 type → 표시명)
const DS_TYPE_LABEL: Record<string, string> = {
  mysql: "MySQL", mariadb: "MariaDB", postgresql: "PostgreSQL", postgres: "PostgreSQL",
  oracle: "Oracle", mssql: "SQL Server", sqlserver: "SQL Server", trino: "Trino", presto: "Presto",
  hive: "Hive", impala: "Impala", bigquery: "BigQuery", snowflake: "Snowflake", redshift: "Redshift",
  mongodb: "MongoDB", kafka: "Kafka", clickhouse: "ClickHouse", databricks: "Databricks",
}
const dsTypeLabel = (t?: string | null) => (t ? (DS_TYPE_LABEL[t.toLowerCase()] ?? t) : "—")

const WEEKDAY_LABEL: Record<string, string> = {
  MON: "월요일", TUE: "화요일", WED: "수요일", THU: "목요일", FRI: "금요일", SAT: "토요일", SUN: "일요일",
}

// 수집 주기 + 시각/요일/일자 → 사람이 읽는 문자열. 예: "매일 06:00", "매주 월요일 09:00".
// cron(고급)이 있으면 함께 표기.
function formatIngestion(d: DatasetDetail): string {
  const f = d.ingestion_frequency
  const cron = d.ingestion_cron?.trim()
  const t = d.ingestion_time ?? ""
  const day = d.ingestion_day ?? ""
  const tz = d.ingestion_timezone && d.ingestion_timezone !== "Asia/Seoul" ? ` (${d.ingestion_timezone})` : ""
  let base = "—"
  switch (f) {
    case "REALTIME": base = "실시간"; break
    case "MANUAL": base = "수동"; break
    case "HOURLY": base = (t ? `매시간 ${t}분` : "매시간") + tz; break
    case "DAILY": base = (t ? `매일 ${t}` : "매일") + tz; break
    case "WEEKLY": base = (`매주 ${day ? (WEEKDAY_LABEL[day] ?? day) + " " : ""}${t}`.trim() || "매주") + tz; break
    case "MONTHLY": base = (`매월 ${day ? day + "일 " : ""}${t}`.trim() || "매월") + tz; break
    default: base = f ? (FREQ_LABEL[f] ?? f) : "—"
  }
  if (cron) return base !== "—" ? `${base} · cron: ${cron}` : `cron: ${cron}`
  return base
}

// 각 Key(라벨)에 대한 설명 — 메타데이터 탭의 "설명" 열 내용과 동일하게 유지.
const KEY_DESC: Record<string, string> = {
  "분류 체계": "이 데이터셋이 매핑된 분류 체계 경로(분류체계 > 상위 > … > 분류)",
  "데이터셋명": "데이터셋의 논리명(사람이 읽기 위한 이름)",
  "요약설명": "데이터셋 한 줄 요약",
  "URN": "데이터셋 고유 식별자(Uniform Resource Name)",
  "용어집": "연결된 비즈니스 용어",
  "태그": "분류·검색용 태그",
  "데이터 소스 유형": "데이터 소스 종류(MySQL, PostgreSQL 등)",
  "데이터 소스명": "데이터 소스 이름",
  "소유자": "데이터 소유자",
  "상태": "데이터셋 상태 — 활성/비활성/사용 중단/삭제됨",
  "환경": "배포 환경 — PROD/STAGING/DEV",
  "수집 주기": "데이터를 적재·갱신하는 주기 (실시간/매시간/매일/매주/매월/수동)",
  "수집 방식": "배치: 정해진 주기에 모아 일괄 적재 · 스트리밍: 이벤트 발생 즉시 연속 적재 · CDC: 원천 변경분(insert/update/delete)만 감지해 반영 · 수동: 사람이 직접 적재",
  "갱신 유형": "적재 모드 — FULL: 매번 전체 재적재(기존 대체) · INCREMENTAL(증분): 마지막 이후 신규+변경분 반영(추가·수정 모두) · APPEND(추가): 기존은 그대로 두고 신규 행만 덧붙임(수정 없음) · UPSERT(병합): 키 기준 있으면 수정, 없으면 삽입",
  "최신성 SLA": "데이터가 최신이어야 하는 기준 시점",
  "보존 기한": "데이터 보관 기간(일). 비우면 영구 보관",
  "삭제 주기": "보존 기한이 지난 데이터를 삭제하는 주기(일). 비우면 자동 삭제 없음",
  "데이터 유형": "정형: 행·열 구조(RDB/테이블) · 반정형: 구조 일부 포함(JSON/XML/CSV 등) · 비정형: 구조 없음(이미지/동영상/문서 등)",
  "데이터 형식": "저장 형식 — CSV/TSV/JSON/XML/Parquet/ORC/Avro/이미지/동영상/오디오/문서/바이너리",
  "압축": "압축 방식 — GZIP/SNAPPY/ZSTD/BZIP2/LZ4 또는 없음",
  "인코딩": "문자 인코딩 (예: UTF-8, EUC-KR)",
  "행 수": "데이터셋의 레코드(행) 수",
  "크기": "데이터셋 전체 크기",
  "파일 수": "데이터셋을 구성하는 파일/객체 수",
  "민감도 등급": "공개(PUBLIC): 외부 공개 가능, 제약 없음 · 대내한정(INTERNAL): 사내 구성원만 열람 · 대외비(CONFIDENTIAL): 인가된 일부만 열람, 외부 유출 금지 · 기밀(RESTRICTED): 최고 민감도, 통제된 소수만 접근",
  "개인정보(PII)": "개인정보 포함 여부",
  "개인정보 항목": "포함된 개인정보 항목 (쉼표로 구분)",
  "규제 태그": "적용 규제 — 개인정보보호법/GDPR/PCI 등 (쉼표로 구분)",
  "등급(Tier)": "데이터 성숙도·신뢰 등급 — Gold: 검증·정제 완료된 공식 데이터(전사 신뢰·활용) · Silver: 일부 정제되어 활용 가능한 데이터 · Bronze: 원천/미정제(raw) 데이터",
  "인증 상태": "CERTIFIED(인증됨): 검증을 거친 신뢰 가능한 공식 데이터 · IN_REVIEW(검토 중): 인증 검토 진행 중 · DEPRECATED(사용 중단): 더 이상 권장되지 않음(폐기·대체 예정) · NONE(없음): 인증 절차 미진행(기본)",
  "품질 점수": "품질 모듈에서 자동 연동되는 품질 점수(0~100)",
  "품질 상태": "품질 상태 — GOOD/WARN/BAD/UNKNOWN",
  "등록일": "데이터셋 등록 일시",
  "수정일": "데이터셋 마지막 수정 일시",
}

// key 라벨 — 설명이 있으면 점선 밑줄 + hover 툴팁.
function KeyLabel({ label }: { label: string }) {
  const desc = KEY_DESC[label]
  if (!desc) return <>{label}</>
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-help underline decoration-dotted decoration-muted-foreground/50 underline-offset-2">{label}</span>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-sm whitespace-pre-wrap">{desc}</TooltipContent>
    </Tooltip>
  )
}

function fmtDate(v?: unknown): string {
  if (!v) return "—"
  const d = v instanceof Date ? v : new Date(v as string)
  if (Number.isNaN(d.getTime())) return "—"
  const p = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function fmtBytes(v?: number | null): string {
  if (v == null) return "—"
  if (v < 1024) return `${v} B`
  const units = ["KB", "MB", "GB", "TB", "PB"]
  let n = v / 1024
  let i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1 }
  return `${n.toFixed(1)} ${units[i]}`
}

const dash = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v))

// 콤마 구분 값을 배지로 표시하는 항목 (개요 탭, 읽기 전용)
const TAG_FIELDS = new Set(["개인정보 항목", "규제 태그"])

// 소유자 값 셀 — 이름 목록을 보여주고, hover 시 소유자별 소속/소속부서/Username/Email 툴팁 표시.
// 툴팁 스타일은 KeyLabel 과 동일(점선 밑줄 + side="right").
function OwnerValue({
  owners,
  ownerInfo,
}: {
  owners: { id?: number; owner_name: string }[]
  ownerInfo?: Record<string, { email?: string; organization?: string; department?: string }>
}) {
  if (owners.length === 0) return <span className="block truncate font-medium">—</span>
  const names = owners.map((o) => o.owner_name).join(", ")
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="block cursor-help truncate font-medium underline decoration-dotted decoration-muted-foreground/50 underline-offset-2">
          {names}
        </span>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-sm whitespace-pre-wrap">
        <div className="space-y-2">
          {owners.map((o, i) => {
            const username = o.owner_name.match(/\(([^()]+)\)\s*$/)?.[1]?.trim() ?? ""
            const info = (username && ownerInfo?.[username]) || {}
            const displayName = o.owner_name.replace(/\s*\([^()]+\)\s*$/, "").trim() || o.owner_name
            return (
              <div key={o.id ?? i} className="space-y-0.5">
                <div className="font-medium">{displayName}</div>
                <div>소속: {info.organization || "—"}</div>
                <div>소속부서: {info.department || "—"}</div>
                <div>Username: {username || "—"}</div>
                <div>Email: {info.email || "—"}</div>
              </div>
            )
          })}
        </div>
      </TooltipContent>
    </Tooltip>
  )
}

export function DatasetMetadataSummary({
  dataset: d,
  categoryPaths,
  ownerInfo,
  children,
}: {
  dataset: DatasetDetail
  /** 매핑된 분류 체계 경로 (A > B > C). 각 항목 1행. */
  categoryPaths?: string[]
  /** username → 소속/부서/이메일 (소유자 tooltip 용) */
  ownerInfo?: Record<string, { email?: string; organization?: string; department?: string }>
  children?: React.ReactNode
}) {
  // 라벨 / 값 쌍 — 첫 행은 데이터 소스 정보, 이후 메타데이터 탭과 동일한 항목.
  const items: { label: string; value: string }[] = [
    { label: "데이터 소스 유형", value: dsTypeLabel(d.datasource?.type) },
    { label: "데이터 소스명", value: dash(d.datasource?.name) },
    { label: "소유자", value: dash((d.owners ?? []).map((o) => o.owner_name).join(", ")) },
    { label: "상태", value: d.status ? (STATUS_LABEL[d.status] ?? d.status) : "—" },
    { label: "환경", value: dash(d.origin) },
    { label: "수집 주기", value: formatIngestion(d) },
    { label: "수집 방식", value: d.ingestion_mode ? (MODE_LABEL[d.ingestion_mode] ?? d.ingestion_mode) : "—" },
    { label: "갱신 유형", value: lbl(UPDATE_LABEL, d.update_type) },
    { label: "최신성 SLA", value: dash(d.freshness_sla) },
    { label: "보존 기한", value: d.retention_days == null ? "영구" : `${d.retention_days}일` },
    { label: "삭제 주기", value: d.purge_days == null ? "—" : `${d.purge_days}일` },
    { label: "데이터 유형", value: d.data_category ? (CATEGORY_LABEL[d.data_category] ?? d.data_category) : "—" },
    { label: "데이터 형식", value: dash(d.data_format) },
    { label: "압축", value: lbl(COMPRESSION_LABEL, d.compression) },
    { label: "인코딩", value: dash(d.encoding) },
    { label: "행 수", value: d.row_count == null ? "—" : d.row_count.toLocaleString() },
    { label: "크기", value: fmtBytes(d.byte_size) },
    { label: "파일 수", value: d.file_count == null ? "—" : d.file_count.toLocaleString() },
    { label: "민감도 등급", value: lbl(SENSITIVITY_LABEL, d.sensitivity) },
    { label: "개인정보(PII)", value: d.contains_pii == null ? "—" : (d.contains_pii ? "예" : "아니오") },
    { label: "개인정보 항목", value: dash(d.pii_fields) },
    { label: "규제 태그", value: dash(d.compliance_tags) },
    { label: "등급(Tier)", value: lbl(TIER_LABEL, d.tier) },
    { label: "인증 상태", value: lbl(CERT_LABEL, d.certification) },
    { label: "품질 점수", value: d.quality_score != null ? `${d.quality_score} / 100` : "—" },
    { label: "품질 상태", value: lbl(QUALITY_LABEL, d.quality_status) },
    { label: "등록일", value: fmtDate(d.created_at) },
    { label: "수정일", value: fmtDate(d.updated_at) },
  ]

  // 상단 전체 폭 단일 행(각 1행): 논리명 → 요약 → URN (항상 표시)
  const topRows: { label: string; value: string }[] = [
    { label: "데이터셋명", value: dash(d.display_name) },
    { label: "요약설명", value: dash(d.summary) },
    { label: "URN", value: dash(d.urn) },
  ]

  // 전체 폭 단일 행(노드): 용어집/태그 모두 배지. 선택된 것이 없으면 행 자체를 숨김.
  const glossary = d.glossary_terms ?? []
  const tags = d.tags ?? []
  const nodeRows: { label: string; node: React.ReactNode }[] = []
  if (glossary.length > 0) {
    nodeRows.push({
      label: "용어집",
      node: (
        <div className="flex flex-wrap gap-1">
          {glossary.map((t) =>
            t.description ? (
              <Tooltip key={t.id}>
                <TooltipTrigger asChild>
                  <span className="cursor-help">
                    <Badge variant="secondary" className="text-xs">{t.name}</Badge>
                  </span>
                </TooltipTrigger>
                <TooltipContent side="top" className="max-w-xs whitespace-pre-wrap">{t.description}</TooltipContent>
              </Tooltip>
            ) : (
              <Badge key={t.id} variant="secondary" className="text-xs">{t.name}</Badge>
            ),
          )}
        </div>
      ),
    })
  }
  if (tags.length > 0) {
    nodeRows.push({
      label: "태그",
      node: (
        <div className="flex flex-wrap gap-1">
          {tags.map((t) => (
            <Badge key={t.id} className="text-xs" style={{ backgroundColor: t.color, color: "white" }}>
              {t.name}
            </Badge>
          ))}
        </div>
      ),
    })
  }

  // 빈 값("—") 항목은 숨김 (상단 단일 행 제외)
  const rows = items.filter((it) => it.value !== "—")

  // 1행에 3쌍(key/value) — 3개씩 묶기
  const groups: { label: string; value: string }[][] = []
  for (let i = 0; i < rows.length; i += 3) groups.push(rows.slice(i, i + 3))

  return (
    <TooltipProvider delayDuration={150}>
    <Card>
      <CardContent className="space-y-4 pt-6">
        <div className="space-y-2">
          <h3 className="text-base font-semibold">메타데이터</h3>
          {/* key/value 분리 셀 — key 셀 배경색. 상단은 전체 폭 단일 행, 이후 1행 3쌍.
              colgroup 으로 6컬럼(key 120px 고정 / value 균등) 정의 → 혼합 colSpan 정렬. */}
          <div className="overflow-hidden rounded-md border">
            <table className="w-full table-fixed border-collapse text-sm">
              <colgroup>
                <col className="w-[120px]" /><col />
                <col className="w-[120px]" /><col />
                <col className="w-[120px]" /><col />
              </colgroup>
              <tbody>
                {(categoryPaths ?? []).map((p, i) => (
                  <tr key={`cat-${i}`}>
                    <th className="border bg-muted/60 px-3 py-2 text-left align-middle font-medium text-black"><KeyLabel label="분류 체계" /></th>
                    <td colSpan={5} className="border px-3 py-2 align-top">
                      <span className="block truncate font-medium" title={p}>{p}</span>
                    </td>
                  </tr>
                ))}
                {topRows.map((r) => (
                  <tr key={r.label}>
                    <th className="border bg-muted/60 px-3 py-2 text-left align-middle font-medium text-black"><KeyLabel label={r.label} /></th>
                    <td colSpan={5} className="border px-3 py-2 align-top">
                      <span className="block truncate font-medium" title={r.value}>{r.value}</span>
                    </td>
                  </tr>
                ))}
                {nodeRows.map((r) => (
                  <tr key={r.label}>
                    <th className="border bg-muted/60 px-3 py-2 text-left align-middle font-medium text-black"><KeyLabel label={r.label} /></th>
                    <td colSpan={5} className="border px-3 py-2 align-top">{r.node}</td>
                  </tr>
                ))}
                {groups.map((group, ri) => (
                  <tr key={ri}>
                    {group.map((it) => (
                      <Fragment key={it.label}>
                        <th className="border bg-muted/60 px-3 py-2 text-left align-middle font-medium text-black">
                          <KeyLabel label={it.label} />
                        </th>
                        <td className="border px-3 py-2 align-top">
                          {it.label === "소유자" ? (
                            <OwnerValue owners={d.owners ?? []} ownerInfo={ownerInfo} />
                          ) : TAG_FIELDS.has(it.label) ? (
                            <div className="flex flex-wrap gap-1">
                              {it.value.split(",").map((t) => t.trim()).filter(Boolean).map((t) => (
                                <Badge key={t} variant="secondary" className="text-xs">{t}</Badge>
                              ))}
                            </div>
                          ) : (
                            <span className="block truncate font-medium" title={it.value}>{it.value}</span>
                          )}
                        </td>
                      </Fragment>
                    ))}
                    {/* 마지막 행 빈 칸 채우기(3쌍 정렬) */}
                    {Array.from({ length: 3 - group.length }).map((_, k) => (
                      <Fragment key={`pad-${k}`}>
                        <th className="border bg-muted/60" />
                        <td className="border" />
                      </Fragment>
                    ))}
                  </tr>
                ))}
                {/* 비고 — 표 최하단, 값이 있을 때만 표시 */}
                {d.note && d.note.trim() !== "" && (
                  <tr>
                    <th className="border bg-muted/60 px-3 py-2 text-left align-middle font-medium text-black">비고</th>
                    <td colSpan={5} className="border px-3 py-2 align-top">
                      <span className="block whitespace-pre-wrap">{d.note}</span>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* 상세 설명 — "메타데이터" 타이틀과 동일 형식의 제목 + 본문 */}
        {children !== undefined && (
          <div className="space-y-2">
            <h3 className="text-base font-semibold">상세 설명</h3>
            <div>{children}</div>
          </div>
        )}
      </CardContent>
    </Card>
    </TooltipProvider>
  )
}
