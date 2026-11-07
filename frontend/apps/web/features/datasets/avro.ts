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

export function fieldTypeToAvro(fieldType: string): unknown {
  // ``DECIMAL(12,2)``, ``varchar(100)``, ``int unsigned`` 처럼 길이·정밀도·부호 수식어가 붙은 타입도
  // 기본 타입으로 판정한다 (이전엔 정확히 일치하지 않으면 모두 string). 외부 Avro API 와 같은 규칙.
  const raw = fieldType.trim()
  const t = raw.split("(")[0]!.trim().toUpperCase().replace(/\s+UNSIGNED$/, "")
  const params = /\(([^)]*)\)/.exec(raw)?.[1]
  switch (t) {
    case "BOOLEAN":
    case "BOOL":
      return "boolean"
    case "TINYINT":
    case "SMALLINT":
    case "INT":
    case "INT8":
    case "INT16":
    case "INT32":
    case "INTEGER":
    case "MEDIUMINT":
    case "SERIAL":
      return "int"
    case "BIGINT":
    case "INT64":
    case "BIGSERIAL":
    case "LARGEINT":
      return "long"
    case "FLOAT":
    case "FLOAT32":
    case "REAL":
      return "float"
    case "DOUBLE":
    case "DOUBLE PRECISION":
    case "FLOAT64":
      return "double"
    case "DECIMAL":
    case "NUMERIC":
    case "NUMBER":
    case "MONEY":
    case "DECIMAL128": {
      // 정밀도·소수 자릿수가 있으면 그대로 (DECIMAL(12,2) → precision 12, scale 2)
      const [p, sc] = (params ?? "").split(",").map((x) => Number.parseInt(x.trim(), 10))
      if (p && Number.isFinite(p)) {
        return { type: "bytes", logicalType: "decimal", precision: p, scale: Number.isFinite(sc) ? sc : 0 }
      }
      return { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }
    }
    case "DATE":
      return { type: "int", logicalType: "date" }
    case "TIME":
      return { type: "long", logicalType: "time-millis" }
    case "TIMESTAMP":
    case "TIMESTAMPTZ":
    case "TIMESTAMP_NTZ":
    case "TIMESTAMP_LTZ":
    case "TIMESTAMP_TZ":
    case "DATETIME":
    case "UNIXTIME_MICROS":
      return { type: "long", logicalType: "timestamp-millis" }
    case "BINARY":
    case "BYTEA":
    case "VARBINARY":
    case "BYTES":
    case "BINDATA":
    case "BLOB":
      return "bytes"
    case "UUID":
      return { type: "string", logicalType: "uuid" }
    case "JSON":
    case "JSONB":
    case "VARIANT":
    case "SUPER":
      return "string"
    case "ARRAY":
      return { type: "array", items: "string" }
    case "MAP":
      return { type: "map", values: "string" }
    case "STRUCT":
    case "ROW":
    case "OBJECT":
      return { type: "record", name: "nested", fields: [] }
    default:
      return "string"
  }
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
    const avroType = fieldTypeToAvro(f.field_type)
    const fieldDef: Record<string, unknown> = {
      name: f.field_path.replace(/[^a-zA-Z0-9_]/g, "_"),
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
    name: datasetName.replace(/[^a-zA-Z0-9_]/g, "_"),
    namespace,
    doc: descriptionToAvroDoc(description) || `Avro schema for ${datasetName}`,
    fields: avroFields,
  }

  return JSON.stringify(schema, null, 2)
}
