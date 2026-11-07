import { describe, expect, it } from "vitest"

import { descriptionToAvroDoc, fieldTypeToAvro, generateAvroSchema, pathFromUrn } from "./avro"
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
})

describe("generateAvroSchema", () => {
  it("record 이름은 path 의 . 을 _ 로", () => {
    const fields = [{ field_path: "order_id", field_type: "INTEGER", nullable: "false" }] as unknown as SchemaField[]
    const avro = JSON.parse(generateAvroSchema(pathFromUrn("pg-1.it_ds.orders.dataset"), "pg-1", fields))
    expect(avro.name).toBe("it_ds_orders")
    expect(avro.fields[0]).toEqual({ name: "order_id", type: "int" })
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
