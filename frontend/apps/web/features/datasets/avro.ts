// 데이터셋 Avro 스키마 자동 생성 (상세 화면 "Avro" 탭) 과 URN path 추출.
// 외부 Avro API(backend/app/external/service.py `_build_avro_schema`)와 같은 결과가 되도록 맞춘다.

import { markdownToPlain } from "@/features/search/excerpt"

import type { SchemaField } from "./data/schema"

/**
 * URN 의 가운데 path 부분만 추출한다.
 * URN 은 ``{datasource_id}.{path}.dataset`` 형태(구 URN 은 ``{datasource_id}.{path}.{ENV}.dataset``)이며
 * path 의 segment 수는 데이터 소스별로 다르다 (PG: db.schema.table, MySQL/Hive: db.table, Iceberg: 가변).
 * 끝의 ``dataset``, (있으면) 환경 토큰, 맨 앞 datasource_id 를 떼어내고 나머지를 ``.`` 로 합친다.
 * 형식이 맞지 않으면 원문 URN 을 반환.
 */
export function pathFromUrn(urn: string | null | undefined): string {
  if (!urn) return ""
  const parts = urn.split(".")
  if (parts.length < 3 || parts[parts.length - 1] !== "dataset") return urn
  let body = parts.slice(1, -1)
  // 구 URN 의 환경 suffix (서버 get_dataset_by_urn 의 구 URN 재매칭과 같은 집합)
  if (body.length > 1 && ["PROD", "DEV", "STAGING"].includes(body[body.length - 1]!)) body = body.slice(0, -1)
  return body.join(".")
}

const AVRO_TIMESTAMP = { type: "long", logicalType: "timestamp-millis" }
// Avro 명세상 time-millis 는 int 다(long 은 time-micros). ``time(6)`` 처럼 정밀도가 붙어도 time-millis 로 통일한다 (DEF-190)
const AVRO_TIME = { type: "int", logicalType: "time-millis" }
const DEFAULT_DECIMAL = { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }

// 카탈로그 공통 타입 → Avro 타입 (backend/app/external/service.py ``AVRO_TYPE_MAP`` 과 같은 표).
// ``DATE`` 는 수집이 date · datetime · timestamp · time · year 를 모두 넣는 공통 타입이라, 네이티브 타입이 없으면
// 시각까지 담을 수 있는 timestamp-millis 로 둔다(날짜 전용 컬럼은 네이티브 ``date`` 로 구분).
const CATALOG_AVRO_TYPES: Record<string, unknown> = {
  STRING: "string",
  NUMBER: "long",
  DATE: AVRO_TIMESTAMP,
  BOOLEAN: "boolean",
  BYTES: "bytes",
  MAP: { type: "map", values: "string" },
  ARRAY: { type: "array", items: "string" },
  ENUM: "string",
  // 타입 선택지에만 있는 이름 — 네이티브 타입으로는 원천마다 뜻이 달라 네이티브 표에 두지 않는다
  // (``int8``: PostgreSQL 8바이트 · Kudu 8비트 → 넓은 쪽, ``long``: Oracle 은 문자열).
  INT8: "long",
  LONG: "long",
}

// 네이티브 타입 → Avro 타입 (backend/app/external/service.py ``NATIVE_AVRO_OVERRIDES`` 와 같은 표).
// 키는 ``nativeBase`` 가 정리한 이름(소문자, 괄호 · 부호 수식어 제외).
const NATIVE_AVRO_TYPES: Record<string, unknown> = {
  int: "int",
  integer: "int",
  smallint: "int",
  tinyint: "int",
  mediumint: "int",
  int16: "int",
  int32: "int",
  byte: "int",
  short: "int",
  serial: "int",
  year: "int",
  bigint: "long",
  int64: "long",
  bigserial: "long",
  float: "float",
  real: "float",
  float4: "float",
  float32: "float",
  half_float: "float",
  double: "double",
  "double precision": "double",
  float8: "double",
  float64: "double",
  decimal: DEFAULT_DECIMAL,
  numeric: DEFAULT_DECIMAL,
  // 통화 — 자릿수가 원천마다 달라 기본 decimal 로. long 이면 소수부가 빠진다(DEF-194)
  money: DEFAULT_DECIMAL,
  smallmoney: DEFAULT_DECIMAL,
  number: DEFAULT_DECIMAL,
  date: { type: "int", logicalType: "date" },
  time: AVRO_TIME,
  timetz: AVRO_TIME,
  "time with time zone": AVRO_TIME,
  "time without time zone": AVRO_TIME,
  timestamp: AVRO_TIMESTAMP,
  timestamptz: AVRO_TIMESTAMP,
  "timestamp with time zone": AVRO_TIMESTAMP,
  "timestamp without time zone": AVRO_TIMESTAMP,
  "timestamp with local time zone": AVRO_TIMESTAMP,
  timestamp_ntz: AVRO_TIMESTAMP,
  timestamp_ltz: AVRO_TIMESTAMP,
  timestamp_tz: AVRO_TIMESTAMP,
  datetime: AVRO_TIMESTAMP,
  datetime2: AVRO_TIMESTAMP,
  smalldatetime: AVRO_TIMESTAMP,
  datetimeoffset: AVRO_TIMESTAMP,
  unixtime_micros: AVRO_TIMESTAMP,
  interval: "string",
  boolean: "boolean",
  bool: "boolean",
  bytea: "bytes",
  blob: "bytes",
  binary: "bytes",
  varbinary: "bytes",
  bindata: "bytes",
  uuid: { type: "string", logicalType: "uuid" },
}

// 같은 네이티브 이름이 원천마다 다른 뜻이라, 수집이 넣은 카탈로그 타입 쪽을 따르는 조합 (DEF-193,
// backend/app/external/service.py ``CATALOG_OVER_NATIVE`` 와 같은 표). 키는 ``nativeBase`` 이름, 값은 그 이름과
// 어긋나는 카탈로그 타입. 카탈로그 ``DATE`` 는 날짜 · 일시 · 시각을 모두 넣는 공통 타입이라 넣지 않는다.
const CATALOG_OVER_NATIVE: Record<string, string[]> = {
  date: ["TIMESTAMP"], // Oracle DATE — 시각 포함
  float: ["DOUBLE"], // SQL Server FLOAT(기본 8바이트) · Oracle FLOAT
  timestamp: ["BINARY"], // SQL Server TIMESTAMP(rowversion) — 일시가 아닌 8바이트 값
}

// 타입 이름 뒤에 붙는 부호 · 채움 수식어(MySQL ``int(10) unsigned zerofill``) — Avro 타입 판정에서는 뺀다
const NATIVE_MODIFIERS = /\b(?:unsigned|signed|zerofill)\b/g
const NATIVE_SPACES = /[ \t\n\r\f\v]+/
const DECIMAL_ARG = /^[0-9]{1,9}$/
const DECIMAL_BASES = ["decimal", "numeric", "number"]

const squeeze = (text: string) => text.split(NATIVE_SPACES).filter(Boolean).join(" ")

/**
 * 네이티브 타입 문자열을 기본 타입명과 괄호 안 인자(없으면 null)로 나눈다. 외부 Avro API(``_native_base``)와 같은 규칙 —
 * 첫 ``(`` 부터 마지막 ``)`` 까지를 인자로 떼고 앞뒤를 이어 붙여, 괄호 뒤에 붙은 말도 타입명에 남긴다
 * (``timestamp(6) with time zone`` → ``timestamp with time zone``). 부호 · 채움 수식어는 빼고 공백은 하나로 줄인다
 * (``DECIMAL(10,2) UNSIGNED`` → ``decimal`` + ``10,2``, ``int(10) unsigned zerofill`` → ``int``).
 */
function nativeBase(typeText: string): { base: string; args: string | null } {
  let text = typeText.toLowerCase()
  let args: string | null = null
  const start = text.indexOf("(")
  if (start >= 0) {
    let end = text.lastIndexOf(")")
    if (end < start) end = text.length
    args = text.slice(start + 1, end)
    text = `${text.slice(0, start)} ${text.slice(end + 1)}`
  }
  return { base: squeeze(text.replace(NATIVE_MODIFIERS, " ")), args }
}

/**
 * DB 네이티브 타입 문자열(``DECIMAL(12,2)``, ``int unsigned`` 등)을 Avro 타입으로. 모르면 undefined.
 * 외부 Avro API(``_native_to_avro``)와 같은 규칙 — decimal/numeric/number 는 괄호 안 ``(precision, scale)`` 을 읽고,
 * 인자가 숫자가 아니거나 셋 이상이거나 Avro 명세(precision ≥ 1, 0 ≤ scale ≤ precision)에 맞지 않으면 기본값(38, 10).
 */
function nativeToAvro(typeText: string | null | undefined): unknown {
  if (!typeText) return undefined
  const { base, args } = nativeBase(typeText)
  if (!Object.hasOwn(NATIVE_AVRO_TYPES, base)) return undefined
  if (DECIMAL_BASES.includes(base) && args !== null) {
    const parts = args.split(",").map(squeeze)
    if (parts.length <= 2 && parts.every((x) => DECIMAL_ARG.test(x))) {
      const precision = Number.parseInt(parts[0]!, 10)
      const scale = parts.length > 1 ? Number.parseInt(parts[1]!, 10) : 0
      if (precision >= 1 && scale <= precision) {
        return { type: "bytes", logicalType: "decimal", precision, scale }
      }
    }
  }
  return NATIVE_AVRO_TYPES[base]
}

/**
 * 컬럼의 Avro 타입 (nullable union 은 호출 쪽에서 감싼다). 외부 Avro API(``_resolve_avro_type``)와 같은 순서:
 * 네이티브 타입 → 카탈로그 공통 타입(``NUMBER`` · ``DATE`` 등) → 카탈로그 타입을 네이티브 타입처럼 해석 → string.
 * 수집된 컬럼은 카탈로그 타입이 ``NUMBER`` 여도 네이티브 타입(``SMALLINT UNSIGNED``)으로 ``int`` 가 된다 (DEF-188 —
 * 이전엔 카탈로그 타입만 보고 별도 표로 판정해 외부 API 와 어긋났다).
 * 네이티브 이름이 ``CATALOG_OVER_NATIVE`` 의 카탈로그 타입과 짝이면 네이티브 대신 카탈로그 타입으로 해석한다
 * (Oracle ``DATE`` + ``TIMESTAMP`` → timestamp-millis).
 */
export function fieldTypeToAvro(fieldType: string, nativeType?: string | null): unknown {
  const native = nativeToAvro(nativeType)
  const catalog = (fieldType ?? "").toUpperCase()
  if (native !== undefined) {
    // 원천마다 뜻이 다른 이름이면 수집이 정한 카탈로그 타입으로 해석한다 (DEF-193)
    const base = nativeBase(nativeType!).base
    const follows = Object.hasOwn(CATALOG_OVER_NATIVE, base) && CATALOG_OVER_NATIVE[base]!.includes(catalog)
    if (!follows) return native
  }
  if (Object.hasOwn(CATALOG_AVRO_TYPES, catalog)) return CATALOG_AVRO_TYPES[catalog]
  return nativeToAvro(fieldType) ?? "string"
}

const HTML_ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " }

/**
 * 데이터셋 설명(마크다운, 예전 리치 텍스트는 HTML) → Avro ``doc`` 용 한 줄 평문.
 * HTML 태그·엔티티를 걷어낸 뒤 마크다운 기호를 뺀다(제목 줄 제외 — 검색 요약과 같은 규칙).
 * 외부 Avro API(backend/app/external/service.py ``_description_doc``)와 같은 결과여야 한다.
 */
export function descriptionToAvroDoc(description: string | null | undefined): string {
  if (!description) return ""
  const text = description
    .replace(/<(br|\/p|\/li|\/h[1-6]|\/div)\s*\/?>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, ent: string) => {
      if (ent[0] === "#") {
        const code = ent[1]?.toLowerCase() === "x" ? Number.parseInt(ent.slice(2), 16) : Number.parseInt(ent.slice(1), 10)
        return Number.isFinite(code) && code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : m
      }
      return HTML_ENTITIES[ent.toLowerCase()] ?? m
    })
  return markdownToPlain(text)
}

/** Avro 이름 규칙(``[A-Za-z_][A-Za-z0-9_]*``) — 그 밖의 문자는 ``_``, 숫자로 시작하면 앞에 ``_``.
 * 외부 Avro API(``backend/app/external/service.py`` ``avro_name``)와 같은 규칙. */
export function avroName(text: string): string {
  const name = (text ?? "").replace(/[^A-Za-z0-9_]/g, "_") || "_"
  return /^[0-9]/.test(name) ? `_${name}` : name
}

/** 점으로 나눈 각 부분을 ``avroName`` 으로 맞춘 네임스페이스 — 예: ``s3-1a2b`` → ``s3_1a2b`` (DEF-040). */
export function avroNamespace(text: string): string {
  return (text ?? "").split(".").filter(Boolean).map(avroName).join(".") || "default"
}

/**
 * 화면 Avro 탭의 자동 생성 스키마. ``doc`` 은 데이터셋 설명 평문, 비어 있으면 ``Avro schema for {name}``
 * (외부 Avro API 와 동일).
 */
export function generateAvroSchema(
  datasetName: string,
  namespace: string,
  fields: SchemaField[],
  description?: string | null,
): string {
  const avroFields = fields.map((f) => {
    const avroType = fieldTypeToAvro(f.field_type, f.native_type)
    const fieldDef: Record<string, unknown> = {
      name: avroName(f.field_path),
      type: f.nullable === "true" ? ["null", avroType] : avroType,
    }
    if (f.nullable === "true") {
      fieldDef.default = null
    }
    if (f.description) {
      fieldDef.doc = f.description
    }
    return fieldDef
  })

  const schema = {
    type: "record",
    name: avroName(datasetName),
    namespace: avroNamespace(namespace),
    doc: descriptionToAvroDoc(description) || `Avro schema for ${datasetName}`,
    fields: avroFields,
  }

  return JSON.stringify(schema, null, 2)
}
