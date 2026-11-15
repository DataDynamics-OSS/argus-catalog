import { describe, expect, it } from "vitest"

import type { DatasetDetail } from "@/features/datasets/data/schema"
import {
  generateBasicDag,
  generateHiveDag,
  generateIncrementalDag,
  generateSparkDag,
  generateTrinoDag,
} from "@/features/datasets/components/airflow-dag-tab"
import {
  generateBasicYaml,
  generateHiveYaml,
  generateShellYaml,
  generateSparkYaml,
  generateTrinoYaml,
} from "@/features/datasets/components/kestra-flow-tab"
import { generateFullLoadScript, generateIncrementalScript } from "@/features/datasets/components/pyspark-tab"
import {
  hiveSql,
  hiveType,
  kestraJdbcPlugin,
  sourceDayStart,
  sourceIdent,
  transferTarget,
  trinoSql,
} from "@/features/datasets/transfer-code"

type F = { name: string; type: string; native: string; pk?: boolean }

function dataset(type: string, name: string, fields: F[], datasourceId = `${type}-1a2b`): DatasetDetail {
  return {
    name,
    datasource: { type, datasource_id: datasourceId, name: "원본" },
    schema_fields: fields.map((f) => ({
      field_path: f.name,
      field_type: f.type,
      native_type: f.native,
      nullable: "true",
      is_primary_key: f.pk ? "true" : "false",
      description: null,
    })),
  } as unknown as DatasetDetail
}

const FILM = dataset("mysql", "sakila.film", [
  { name: "film_id", type: "NUMBER", native: "smallint unsigned", pk: true },
  { name: "title", type: "STRING", native: "varchar(128)" },
  { name: "rental_rate", type: "NUMBER", native: "decimal(4,2)" },
  { name: "is_new", type: "NUMBER", native: "tinyint(1)" },
  { name: "release_year", type: "DATE", native: "year" },
  { name: "last_update", type: "DATE", native: "timestamp" },
])

const ORDERS_MSSQL = dataset("mssql", "sales.dbo.Order Lines", [
  { name: "id", type: "NUMBER", native: "bigint", pk: true },
  { name: "dt", type: "STRING", native: "nvarchar(8)" },
  { name: "modified_at", type: "DATE", native: "datetime2" },
])

const EMP_ORACLE = dataset("oracle", "HR.EMPLOYEES", [
  { name: "EMPLOYEE_ID", type: "NUMBER", native: "NUMBER(6)", pk: true },
  { name: "SALARY", type: "NUMBER", native: "NUMBER(8,2)" },
  { name: "HIRE_DATE", type: "DATE", native: "DATE" },
])

const ALL = [FILM, ORDERS_MSSQL, EMP_ORACLE]
const GENERATORS = [
  generateBasicDag, generateSparkDag, generateIncrementalDag, generateHiveDag, generateTrinoDag,
  generateBasicYaml, generateSparkYaml, generateShellYaml, generateHiveYaml, generateTrinoYaml,
  generateIncrementalScript, generateFullLoadScript,
]

describe("원본 이름 · 방언", () => {
  it("이름의 마지막이 테이블, 앞부분이 DB · 스키마 (SQL Server 는 세 토막)", () => {
    const t = transferTarget(ORDERS_MSSQL)
    expect(t.namespace).toEqual(["sales", "dbo"])
    expect(t.table).toBe("Order Lines")
    expect(t.sourceTable).toBe("sales.dbo.[Order Lines]")
    expect(t.s3Prefix).toBe("raw/mssql-1a2b/sales/dbo/Order_Lines")
    expect(t.partition).toBe("load_dt") // 원본에 dt 컬럼이 있으면 파티션 이름을 바꾼다
    expect(transferTarget(FILM).partition).toBe("dt")
  })

  it("식별자는 필요할 때만 방언에 맞게 인용", () => {
    expect(sourceIdent("mysql", "title")).toBe("title")
    expect(sourceIdent("mysql", "order date")).toBe("`order date`")
    expect(sourceIdent("postgresql", "Order")).toBe("Order")
    expect(sourceIdent("oracle", "a-b")).toBe('"a-b"')
  })

  it("날짜 상수 — Oracle 은 문자열 비교가 NLS 에 매이므로 TIMESTAMP, SQL Server 는 ISO 8601", () => {
    expect(sourceDayStart("oracle", "{d}")).toBe("TIMESTAMP '{d} 00:00:00'")
    expect(sourceDayStart("mssql", "{d}")).toBe("'{d}T00:00:00'")
    expect(sourceDayStart("mariadb", "{d}")).toBe("'{d} 00:00:00'")
  })

  it("Kestra JDBC 플러그인 이름 — MariaDB 는 mysql, SQL Server 는 sqlserver, 없는 유형은 null", () => {
    expect(kestraJdbcPlugin("mariadb")).toBe("mysql")
    expect(kestraJdbcPlugin("mssql")).toBe("sqlserver")
    expect(kestraJdbcPlugin("tibero")).toBeNull()
  })
})

describe("Hive", () => {
  it("Spark JDBC 가 쓰는 타입으로 추정", () => {
    const [id, title, rate, isNew, year, updated] = FILM.schema_fields
    expect(hiveType(id!, "mysql")).toBe("INT")
    expect(hiveType(title!, "mysql")).toBe("STRING")
    expect(hiveType(rate!, "mysql")).toBe("DECIMAL(4,2)")
    expect(hiveType(isNew!, "mysql")).toBe("BOOLEAN")
    expect(hiveType(year!, "mysql")).toBe("DATE")
    expect(hiveType(updated!, "mysql")).toBe("TIMESTAMP")
    expect(hiveType(EMP_ORACLE.schema_fields[0]!, "oracle")).toBe("DECIMAL(6,0)")
    expect(hiveType(EMP_ORACLE.schema_fields[2]!, "oracle")).toBe("TIMESTAMP") // Oracle DATE 는 시각까지
  })

  it("외부 테이블은 파티션 컬럼을 본문에 두지 않고 PARTITIONED BY 로", () => {
    const sql = hiveSql(FILM, "{{ ds }}")
    expect(sql.createDatabase).toBe("CREATE DATABASE IF NOT EXISTS `raw_mysql_1a2b`")
    expect(sql.create).toContain("PARTITIONED BY (`dt` STRING)")
    expect(sql.create).toContain("LOCATION 's3a://<DATALAKE_BUCKET>/raw/mysql-1a2b/sakila/film'")
    expect(sql.create).not.toMatch(/`dt` (?!STRING)/)
    expect(sql.addPartition).toContain("ADD IF NOT EXISTS PARTITION (`dt` = '{{ ds }}')")
    expect(sql.addPartition).toContain("/raw/mysql-1a2b/sakila/film/dt={{ ds }}'")
  })
})

describe("Trino", () => {
  it("원본 타입 그대로 CTAS WITH NO DATA, 파티션 컬럼은 마지막, 재실행은 파티션을 지우고 다시", () => {
    const sql = trinoSql(FILM, "{{ ds }}")
    expect(sql.create).toContain("partitioned_by = ARRAY['dt']")
    expect(sql.create).toMatch(/"last_update",\n {2}CAST\(NULL AS varchar\) AS "dt"\nFROM <SOURCE_CATALOG>\."sakila"\."film"\nWITH NO DATA$/)
    expect(sql.create).not.toContain("external_location")
    expect(sql.deletePartition).toBe(`DELETE FROM <LAKE_CATALOG>."raw_mysql_1a2b"."film" WHERE "dt" = '{{ ds }}'`)
    expect(sql.insert).toContain(`WHERE "last_update" >= TIMESTAMP '{{ ds }} 00:00:00'`)
    expect(sql.insert).toContain(`+ INTERVAL '1' DAY`)
  })

  it("SQL Server 원본은 스키마만(Trino 카탈로그 하나가 DB 하나)", () => {
    expect(trinoSql(ORDERS_MSSQL, "x").insert).toContain(`FROM <SOURCE_CATALOG>."dbo"."Order Lines"`)
  })
})

describe("생성 코드", () => {
  it("Airflow · Kestra 에 Hive 와 Trino 가 따로 있다", () => {
    expect(generateHiveDag(FILM)).toContain("hiveserver2_default")
    expect(generateHiveDag(FILM)).not.toContain("trino")
    expect(generateTrinoDag(FILM)).toContain("trino_default")
    expect(generateTrinoDag(FILM)).not.toContain("hiveserver2")
    expect(generateHiveYaml(FILM)).toContain("beeline")
    expect(generateTrinoYaml(FILM)).toContain("io.kestra.plugin.jdbc.trino.Query")
  })

  it("Kestra 시크릿은 Pebble 표현식, MariaDB 는 mysql 플러그인", () => {
    const yaml = generateBasicYaml(dataset("mariadb", "shop.orders", FILM.schema_fields.map((f) => ({
      name: f.field_path, type: f.field_type, native: f.native_type ?? "",
    }))))
    expect(yaml).toContain("{{ secret('MARIADB_1A2B_USERNAME') }}")
    expect(yaml).not.toContain("${secret")
    expect(yaml).toContain("io.kestra.plugin.jdbc.mysql.Query")
    expect(yaml).toContain("io.kestra.plugin.serdes.csv.IonToCsv")
  })

  it("PySpark — 부분 쿼리 별칭에 AS 없음(Oracle), 파티션 값은 경로에만, 원본을 두 번 읽지 않음", () => {
    const code = generateIncrementalScript(EMP_ORACLE)
    expect(code).toContain(") src\"\"\"")
    expect(code).not.toMatch(/\) AS src/)
    expect(code).toContain("TIMESTAMP '{target_date} 00:00:00'")
    expect(code).not.toContain("withColumn(")
    expect(code).not.toMatch(/= df\.count\(\)|{df\.count\(\)/)
    expect(generateFullLoadScript(EMP_ORACLE)).not.toMatch(/{df\.count\(\)/)
    expect(code).toContain('SOURCE_TABLE = "HR.EMPLOYEES"')
  })

  it("Airflow PySpark DAG 은 PySpark 탭 스크립트를 제출한다(스크립트를 복사하지 않음)", () => {
    const dag = generateSparkDag(FILM)
    expect(dag).toContain('application_args=["--target-date", "{{ ds }}"]')
    expect(dag).not.toContain("PYSPARK_SCRIPT")
  })

  it("모든 생성기가 세 원본 유형에서 만들어지고 자리 표시 외 undefined 가 없다", () => {
    for (const ds of ALL) {
      for (const make of GENERATORS) {
        const code = make(ds)
        expect(code).not.toContain("undefined")
        expect(code).not.toContain("[object Object]")
      }
    }
  })
})
