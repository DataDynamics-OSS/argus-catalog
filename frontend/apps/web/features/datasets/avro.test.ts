import { describe, expect, it } from "vitest"

import { avroName, avroNamespace, descriptionToAvroDoc, fieldTypeToAvro, generateAvroSchema, pathFromUrn } from "./avro"
import type { SchemaField } from "./data/schema"

describe("pathFromUrn", () => {
  it("현재 URN({ds}.{path}.dataset)에서 path 를 뽑는다 (DS-T15 — 이전엔 마지막 테이블 토큰이 잘림)", () => {
    expect(pathFromUrn("sakila-mysql.sakila.film.dataset")).toBe("sakila.film")
    expect(pathFromUrn("postgresql-1a.it_ds.orders.dataset")).toBe("it_ds.orders")
    expect(pathFromUrn("pg-1.db.schema.table.dataset")).toBe("db.schema.table")
  })

  it("구 URN 의 환경 suffix 는 뗀다", () => {
    expect(pathFromUrn("sakila-mysql.sakila.film.DEV.dataset")).toBe("sakila.film")
  })

  it("형식이 다르면 원문, 빈 값은 빈 문자열", () => {
    expect(pathFromUrn("no.such")).toBe("no.such")
    expect(pathFromUrn(null)).toBe("")
  })
})

describe("fieldTypeToAvro", () => {
  it("정밀도·길이·unsigned 가 붙은 타입도 기본 타입으로 판정", () => {
    expect(fieldTypeToAvro("DECIMAL(12,2)")).toEqual({ type: "bytes", logicalType: "decimal", precision: 12, scale: 2 })
    expect(fieldTypeToAvro("varchar(100)")).toBe("string")
    expect(fieldTypeToAvro("SMALLINT UNSIGNED")).toBe("int")
    expect(fieldTypeToAvro("integer")).toBe("int")
    expect(fieldTypeToAvro("NUMERIC")).toEqual({ type: "bytes", logicalType: "decimal", precision: 38, scale: 10 })
  })

  // 외부 Avro API(backend/tests/test_datasets_it03.py test_external_avro_types_without_native_type)와 같은 입력·기대값
  it("네이티브 타입이 없으면 카탈로그 타입으로 (외부 API 와 같은 결과)", () => {
    expect(fieldTypeToAvro("INTEGER", null)).toBe("int")
    expect(fieldTypeToAvro("DECIMAL(12,2)", "")).toEqual({ type: "bytes", logicalType: "decimal", precision: 12, scale: 2 })
    expect(fieldTypeToAvro("varchar", null)).toBe("string")
    expect(fieldTypeToAvro("NUMBER", null)).toBe("long")
    expect(fieldTypeToAvro("STRING", null)).toBe("string")
    expect(fieldTypeToAvro("DATE")).toEqual({ type: "long", logicalType: "timestamp-millis" })
    expect(fieldTypeToAvro("date")).toEqual({ type: "long", logicalType: "timestamp-millis" })
    expect(fieldTypeToAvro("BOOLEAN")).toBe("boolean")
    expect(fieldTypeToAvro("BYTES")).toBe("bytes")
    expect(fieldTypeToAvro("ENUM")).toBe("string")
    expect(fieldTypeToAvro("MAP")).toEqual({ type: "map", values: "string" })
    expect(fieldTypeToAvro("ARRAY")).toEqual({ type: "array", items: "string" })
  })

  it("네이티브 타입이 있으면 우선 (DEF-188 — 이전엔 NUMBER 가 decimal(38,10), DATE 가 date)", () => {
    expect(fieldTypeToAvro("NUMBER", "bigint unsigned")).toBe("long")
    expect(fieldTypeToAvro("NUMBER", "SMALLINT UNSIGNED")).toBe("int")
    expect(fieldTypeToAvro("NUMBER", "tinyint(1)")).toBe("int")
    expect(fieldTypeToAvro("NUMBER", "DECIMAL(4,2)")).toEqual({ type: "bytes", logicalType: "decimal", precision: 4, scale: 2 })
    expect(fieldTypeToAvro("NUMBER", "numeric(10)")).toEqual({ type: "bytes", logicalType: "decimal", precision: 10, scale: 0 })
    expect(fieldTypeToAvro("NUMBER", "decimal")).toEqual({ type: "bytes", logicalType: "decimal", precision: 38, scale: 10 })
    expect(fieldTypeToAvro("NUMBER", "double precision")).toBe("double")
    expect(fieldTypeToAvro("DATE", "TIMESTAMP")).toEqual({ type: "long", logicalType: "timestamp-millis" })
    expect(fieldTypeToAvro("DATE", "date")).toEqual({ type: "int", logicalType: "date" })
    expect(fieldTypeToAvro("DATE", "time")).toEqual({ type: "int", logicalType: "time-millis" })
    expect(fieldTypeToAvro("STRING", "uuid")).toEqual({ type: "string", logicalType: "uuid" })
    expect(fieldTypeToAvro("BYTES", "bytea")).toBe("bytes")
  })

  it("표에 없는 네이티브 타입은 카탈로그 타입으로 넘어간다", () => {
    expect(fieldTypeToAvro("STRING", "varchar(255)")).toBe("string")
    expect(fieldTypeToAvro("NUMBER", "geometry")).toBe("long")
    expect(fieldTypeToAvro("DATE", "struct<a:decimal(10,2)>")).toEqual({ type: "long", logicalType: "timestamp-millis" })
    expect(fieldTypeToAvro("jsonb", "jsonb")).toBe("string")
    expect(fieldTypeToAvro("constructor", "toString")).toBe("string")
  })
})

// 외부 Avro API(backend/tests/test_datasets_it03.py AVRO_TYPE_CASES)와 같은 입력·기대값 — 두 구현이 같은 타입을 내야 한다.
// [카탈로그 타입, 네이티브 타입, Avro 타입]
const AVRO_TYPE_CASES: [string, string | null, unknown][] = [
  // time 은 int + time-millis (Avro 명세 — long 은 time-micros)
  ["DATE", "time", { type: "int", logicalType: "time-millis" }],
  ["DATE", "time(6)", { type: "int", logicalType: "time-millis" }],
  ["TIME", "time(3) with time zone", { type: "int", logicalType: "time-millis" }],
  ["DATE", "time without time zone", { type: "int", logicalType: "time-millis" }],
  ["DATE", "timetz", { type: "int", logicalType: "time-millis" }],
  ["TIME", null, { type: "int", logicalType: "time-millis" }],
  // 괄호 뒤 수식어(unsigned · zerofill · signed)를 걷어 내고 정밀도 · 스케일 유지
  ["NUMBER", "decimal(10,2) unsigned", { type: "bytes", logicalType: "decimal", precision: 10, scale: 2 }],
  ["NUMBER", "DECIMAL(10,2) UNSIGNED ZEROFILL", { type: "bytes", logicalType: "decimal", precision: 10, scale: 2 }],
  ["NUMBER", "int unsigned zerofill", "int"],
  ["NUMBER", "int(11) unsigned", "int"],
  ["NUMBER", "int(10) unsigned zerofill", "int"],
  ["NUMBER", "bigint(20) unsigned", "long"],
  ["NUMBER", "double(10,2) unsigned", "double"],
  ["DECIMAL", "NUMBER(10,2)", { type: "bytes", logicalType: "decimal", precision: 10, scale: 2 }],
  ["DECIMAL", "NUMBER(10)", { type: "bytes", logicalType: "decimal", precision: 10, scale: 0 }],
  ["DECIMAL", "NUMBER", { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }],
  ["DECIMAL", "decimal(10, 2)", { type: "bytes", logicalType: "decimal", precision: 10, scale: 2 }],
  // 비정상 decimal 인자는 기본값 (38,10)
  ["NUMBER", "decimal(5,2,1)", { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }],
  ["NUMBER", "decimal(a,b)", { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }],
  ["NUMBER", "decimal()", { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }],
  ["NUMBER", "decimal(0,0)", { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }],
  ["NUMBER", "decimal(5,9)", { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }],
  ["NUMBER", "decimal(10,-2)", { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }],
  // 날짜 · 시각 · 연도 계열 네이티브 타입
  ["DATE", "datetime", { type: "long", logicalType: "timestamp-millis" }],
  ["DATE", "datetime(6)", { type: "long", logicalType: "timestamp-millis" }],
  ["DATE", "timestamp(6)", { type: "long", logicalType: "timestamp-millis" }],
  ["DATE", "timestamptz", { type: "long", logicalType: "timestamp-millis" }],
  ["DATE", "timestamp with time zone", { type: "long", logicalType: "timestamp-millis" }],
  ["DATE", "timestamp without time zone", { type: "long", logicalType: "timestamp-millis" }],
  ["TIMESTAMP", "timestamp(6) with time zone", { type: "long", logicalType: "timestamp-millis" }],
  ["TIMESTAMP", "TIMESTAMP(6) WITH LOCAL TIME ZONE", { type: "long", logicalType: "timestamp-millis" }],
  ["TIMESTAMP", "DATETIME2", { type: "long", logicalType: "timestamp-millis" }],
  ["TIMESTAMP", "SMALLDATETIME", { type: "long", logicalType: "timestamp-millis" }],
  ["TIMESTAMP", "DATETIMEOFFSET", { type: "long", logicalType: "timestamp-millis" }],
  ["DATE", "year", "int"],
  ["DATE", "YEAR", "int"],
  ["DATE", "year(4)", "int"],
  ["DATE", "interval", "string"],
  ["DATE", "date", { type: "int", logicalType: "date" }],
  ["DATE", "DATE", { type: "int", logicalType: "date" }],
  // PostgreSQL udt · Kudu 타입명
  ["NUMBER", "int8", "long"],
  ["NUMBER", "float4", "float"],
  ["NUMBER", "float8", "double"],
  ["SMALLINT", "int16", "int"],
  ["INT", "int32", "int"],
  ["BIGINT", "int64", "long"],
  ["TINYINT", "int8", "int"],
  ["TIMESTAMP", "unixtime_micros", { type: "long", logicalType: "timestamp-millis" }],
  // 카탈로그 타입 DATE 는 날짜 · 시각 공통 타입 — 네이티브가 없으면 timestamp-millis 그대로
  ["DATE", null, { type: "long", logicalType: "timestamp-millis" }],
  ["date", "", { type: "long", logicalType: "timestamp-millis" }],
  // 타입 선택지의 별칭 (이전엔 string)
  ["DATETIME", null, { type: "long", logicalType: "timestamp-millis" }],
  ["TIMESTAMPTZ", null, { type: "long", logicalType: "timestamp-millis" }],
  ["timestamptz", null, { type: "long", logicalType: "timestamp-millis" }],
  ["TIMESTAMP_NTZ", null, { type: "long", logicalType: "timestamp-millis" }],
  ["TIMESTAMP_LTZ", null, { type: "long", logicalType: "timestamp-millis" }],
  ["TIMESTAMP_TZ", null, { type: "long", logicalType: "timestamp-millis" }],
  ["UNIXTIME_MICROS", null, { type: "long", logicalType: "timestamp-millis" }],
  ["YEAR", null, "int"],
  ["INT8", null, "long"],
  ["INT16", null, "int"],
  ["INT32", null, "int"],
  ["INT64", null, "long"],
  ["Int32", null, "int"],
  ["Int64", null, "long"],
  ["byte", null, "int"],
  ["short", null, "int"],
  ["long", null, "long"],
  ["LONG", null, "long"],
  ["serial", null, "int"],
  ["bigserial", null, "long"],
  ["FLOAT32", null, "float"],
  ["FLOAT64", null, "double"],
  ["half_float", null, "float"],
  ["BinData", null, "bytes"],
  // 바꾸지 않은 것
  ["NUMBER", "bigint unsigned", "long"],
  ["NUMBER", "int4", "long"],
  ["NUMBER", "int unsigned", "int"],
  ["MAP", "json", { type: "map", values: "string" }],
  ["MAP", "jsonb", { type: "map", values: "string" }],
  ["STRING", "text", "string"],
  ["TEXT", "LONG", "string"],
  ["ARRAY<DECIMAL>", "numeric(10,2)[]", "string"],
  ["money", null, { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }],
  // DEF-193 — 원천마다 뜻이 다른 네이티브 이름은 수집이 넣은 카탈로그 타입을 따른다
  ["TIMESTAMP", "DATE", { type: "long", logicalType: "timestamp-millis" }],
  ["TIMESTAMP", "date", { type: "long", logicalType: "timestamp-millis" }],
  ["DOUBLE", "FLOAT", "double"],
  ["DOUBLE", "float(53)", "double"],
  ["BINARY", "TIMESTAMP", "bytes"],
  // DEF-193 — 그 밖의 조합은 그대로 네이티브 타입 우선
  ["DATE", "Date", { type: "int", logicalType: "date" }],
  ["STRING", "date", { type: "int", logicalType: "date" }],
  ["DATETIME", "date", { type: "int", logicalType: "date" }],
  ["NUMBER", "float", "float"],
  ["FLOAT", "float", "float"],
  ["FLOAT", "FLOAT(7,2)", "float"],
  ["DOUBLE", "real", "float"],
  ["DOUBLE", "double", "double"],
  ["DOUBLE", "BINARY_DOUBLE", "double"],
  ["DATE", "timestamp", { type: "long", logicalType: "timestamp-millis" }],
  ["TIMESTAMP", "timestamp", { type: "long", logicalType: "timestamp-millis" }],
  ["BYTES", "timestamp", { type: "long", logicalType: "timestamp-millis" }],
  ["BINARY", "datetime", { type: "long", logicalType: "timestamp-millis" }],
  ["BINARY", "ROWVERSION", "bytes"],
  ["BINARY", "VARBINARY(MAX)", "bytes"],
  ["NUMBER", "money", { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }],
  ["DECIMAL", "SMALLMONEY", { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }],
  ["DECIMAL", "money", { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }],
  ["DECIMAL", "SMALLMONEY", { type: "bytes", logicalType: "decimal", precision: 38, scale: 10 }],
]

describe("fieldTypeToAvro — Avro 타입 표 (DEF-190 · DEF-193)", () => {
  it.each(AVRO_TYPE_CASES)("%s / %s", (fieldType, nativeType, expected) => {
    expect(fieldTypeToAvro(fieldType, nativeType)).toEqual(expected)
  })
})

describe("generateAvroSchema", () => {
  it("record 이름은 path 의 . 을 _ 로", () => {
    const fields = [{ field_path: "order_id", field_type: "INTEGER", nullable: "false" }] as unknown as SchemaField[]
    const avro = JSON.parse(generateAvroSchema(pathFromUrn("pg-1.it_ds.orders.dataset"), "pg-1", fields))
    expect(avro.name).toBe("it_ds_orders")
    expect(avro.namespace).toBe("pg_1")
    expect(avro.fields[0]).toEqual({ name: "order_id", type: "int" })
  })

  it("수집된 컬럼은 네이티브 타입으로, nullable 은 null union + 기본값 (DEF-188)", () => {
    const fields = [
      { field_path: "film_id", field_type: "NUMBER", native_type: "SMALLINT UNSIGNED", nullable: "false" },
      { field_path: "rental_rate", field_type: "NUMBER", native_type: "DECIMAL(4,2)", nullable: "false" },
      { field_path: "length", field_type: "NUMBER", native_type: "SMALLINT UNSIGNED", nullable: "true" },
      { field_path: "last_update", field_type: "DATE", native_type: "TIMESTAMP", nullable: "false" },
    ] as unknown as SchemaField[]
    const avro = JSON.parse(generateAvroSchema("sakila.film", "mysql-1", fields))
    expect(avro.fields).toEqual([
      { name: "film_id", type: "int" },
      { name: "rental_rate", type: { type: "bytes", logicalType: "decimal", precision: 4, scale: 2 } },
      { name: "length", type: ["null", "int"], default: null },
      { name: "last_update", type: { type: "long", logicalType: "timestamp-millis" } },
    ])
  })
})

// 외부 Avro API(backend/tests/test_datasets_it03.py test_avro_names_follow_avro_spec)와 같은 입력·기대값
describe("avroName / avroNamespace (DEF-040)", () => {
  it("Avro 이름 규칙 [A-Za-z_][A-Za-z0-9_]*", () => {
    expect(avroName("commerce.order_events")).toBe("commerce_order_events")
    expect(avroName("order-id")).toBe("order_id")
    expect(avroName("2026_sales")).toBe("_2026_sales")
    expect(avroName("")).toBe("_")
  })

  it("네임스페이스는 점으로 나눈 부분마다", () => {
    expect(avroNamespace("s3-1a1c0a9f9a98360d4")).toBe("s3_1a1c0a9f9a98360d4")
    expect(avroNamespace("a-b.c d")).toBe("a_b.c_d")
    expect(avroNamespace("")).toBe("default")
  })
})

// 외부 Avro API(backend/tests/test_datasets_it03.py AVRO_DOC_CASES)와 같은 입력·기대값 — 두 구현의 doc 이 같아야 한다.
const AVRO_DOC_CASES: [string, string][] = [
  [
    "## 개요\nDVD 대여점이 보유한 **영화 상품 마스터**. `film_id` 기준.\n\n## 주요 컬럼\n- `title` — 제목",
    "DVD 대여점이 보유한 영화 상품 마스터. film_id 기준. title — 제목",
  ],
  [
    "<p>주문 <b>원장</b> &amp; 결제&nbsp;내역</p><p>보관 &lt;5년&gt; &#8211; 감사</p>",
    "주문 원장 & 결제 내역 보관 <5년> – 감사",
  ],
  ["[문서](http://x) 참고 *중요* rental_rate 와 _강조_", "문서 참고 중요 rental_rate 와 강조"],
  ["한*기울임*끝", "한기울임끝"],
  ["## 제목만", ""],
  ["", ""],
]

describe("descriptionToAvroDoc", () => {
  it.each(AVRO_DOC_CASES)("%s", (src, expected) => {
    expect(descriptionToAvroDoc(src)).toBe(expected)
  })
})

describe("generateAvroSchema doc (DS-T15 — 외부 API 와 통일)", () => {
  const fields = [{ field_path: "id", field_type: "INTEGER", nullable: "false" }] as unknown as SchemaField[]

  it("설명이 있으면 설명 평문", () => {
    const avro = JSON.parse(generateAvroSchema("shop.orders", "pg-1", fields, "## 개요\n**주문** 원장"))
    expect(avro.doc).toBe("주문 원장")
  })

  it("설명이 없거나 평문이 비면 Avro schema for {name}", () => {
    expect(JSON.parse(generateAvroSchema("shop.orders", "pg-1", fields)).doc).toBe("Avro schema for shop.orders")
    expect(JSON.parse(generateAvroSchema("shop.orders", "pg-1", fields, "  ")).doc).toBe("Avro schema for shop.orders")
    expect(JSON.parse(generateAvroSchema("shop.orders", "pg-1", fields, "## 제목만")).doc).toBe(
      "Avro schema for shop.orders",
    )
  })
})
