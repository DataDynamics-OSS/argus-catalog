// 적재 코드 탭(PySpark · Kestra · Airflow)이 함께 쓰는 값 — 원본 이름 · JDBC · 방언별 SQL 조각 · 데이터레이크 경로 · 타입.
//
// 코드는 화면에서 카탈로그 스키마만 보고 만든다. 원본에 접속해 보지 않으므로, 사람이 확인해야 하는 값(증분 기준 컬럼 · 접속 정보 ·
// Trino 카탈로그 이름 등)은 <PLACEHOLDER> 와 [필수 확인] 주석으로 남긴다.
import type { DatasetDetail } from "@/features/datasets/data/schema"

type Field = DatasetDetail["schema_fields"][number]

// ---------------------------------------------------------------------------
// 원본 유형
// ---------------------------------------------------------------------------

/** 원본 유형을 방언으로 묶는다 — 같은 방언은 식별자 인용 · 날짜 상수 · JDBC 드라이버가 같다. */
export type SourceDialect = "mysql" | "postgresql" | "oracle" | "mssql" | "other"

export function sourceDialect(type: string): SourceDialect {
  switch (type) {
    case "mysql": case "mariadb": case "starrocks": return "mysql"
    case "postgresql": case "greenplum": return "postgresql"
    case "oracle": case "tibero": return "oracle"
    case "mssql": case "sqlserver": return "mssql"
    default: return "other"
  }
}

export type JdbcInfo = { url: string; driver: string; jar: string }

export function jdbcInfo(type: string): JdbcInfo {
  switch (type) {
    case "mysql": case "mariadb": case "starrocks":
      return { url: "jdbc:mysql://<HOST>:<PORT>/<DATABASE>", driver: "com.mysql.cj.jdbc.Driver", jar: "mysql-connector-j-8.4.0.jar" }
    case "postgresql": case "greenplum":
      return { url: "jdbc:postgresql://<HOST>:<PORT>/<DATABASE>", driver: "org.postgresql.Driver", jar: "postgresql-42.7.4.jar" }
    case "oracle":
      return { url: "jdbc:oracle:thin:@//<HOST>:<PORT>/<SERVICE_NAME>", driver: "oracle.jdbc.OracleDriver", jar: "ojdbc11.jar" }
    case "tibero":
      return { url: "jdbc:tibero:thin:@<HOST>:<PORT>:<DB_NAME>", driver: "com.tmax.tibero.jdbc.TbDriver", jar: "tibero7-jdbc.jar" }
    case "mssql": case "sqlserver":
      return {
        url: "jdbc:sqlserver://<HOST>:<PORT>;databaseName=<DATABASE>;encrypt=false",
        driver: "com.microsoft.sqlserver.jdbc.SQLServerDriver",
        jar: "mssql-jdbc-12.8.1.jre11.jar",
      }
    default:
      return { url: `jdbc:${type}://<HOST>:<PORT>/<DATABASE>`, driver: "<DRIVER_CLASS>", jar: "<JDBC_DRIVER>.jar" }
  }
}

/** Airflow 커넥션 ID — 원본 유형의 provider 기본 이름. */
export function airflowConnId(type: string): string {
  switch (sourceDialect(type)) {
    case "mysql": return "mysql_default"
    case "postgresql": return "postgres_default"
    case "mssql": return "mssql_default"
    case "oracle": return type === "oracle" ? "oracle_default" : `${type}_default`
    default: return `${type}_default`
  }
}

/** Kestra JDBC 플러그인 이름(`io.kestra.plugin.jdbc.<이름>.Query`). 플러그인이 없는 유형은 null. */
export function kestraJdbcPlugin(type: string): string | null {
  switch (type) {
    case "mysql": case "mariadb": case "starrocks": return "mysql"
    case "postgresql": case "greenplum": return "postgresql"
    case "oracle": return "oracle"
    case "mssql": case "sqlserver": return "sqlserver"
    default: return null
  }
}

// ---------------------------------------------------------------------------
// 식별자 · 날짜 상수
// ---------------------------------------------------------------------------

const SIMPLE_IDENT = /^[A-Za-z_][A-Za-z0-9_]*$/

/** 원본 SQL 의 식별자 — 영문 · 숫자 · 밑줄만이면 그대로(원본의 대소문자 규칙을 따른다), 아니면 방언에 맞게 인용. */
export function sourceIdent(type: string, name: string): string {
  if (SIMPLE_IDENT.test(name)) return name
  switch (sourceDialect(type)) {
    case "mysql": return `\`${name.replace(/`/g, "``")}\``
    case "mssql": return `[${name.replace(/]/g, "]]")}]`
    default: return `"${name.replace(/"/g, '""')}"`
  }
}

/** Trino 식별자 — 늘 큰따옴표로 인용한다(예약어 · 특수 문자 대비). */
export function trinoIdent(name: string): string {
  return `"${name.replace(/"/g, '""')}"`
}

/** Hive 식별자 — 늘 백틱으로 인용한다. */
export function hiveIdent(name: string): string {
  return `\`${name.replace(/`/g, "``")}\``
}

/**
 * 원본 SQL 에서 일자 `dateExpr`(템플릿 식, 예: `{target_date}` · `{{ ds }}`)의 0시를 뜻하는 상수.
 * MySQL 계열은 문자열을 시각으로 바꿔 비교하고, SQL Server 는 언어 설정과 무관한 ISO 8601(`T`) 문자열,
 * 나머지(PostgreSQL · Oracle 등)는 ANSI `TIMESTAMP '…'` — Oracle 은 문자열을 NLS 날짜 형식으로 읽어 실패할 수 있다.
 */
export function sourceDayStart(type: string, dateExpr: string): string {
  switch (sourceDialect(type)) {
    case "mysql": return `'${dateExpr} 00:00:00'`
    case "mssql": return `'${dateExpr}T00:00:00'`
    default: return `TIMESTAMP '${dateExpr} 00:00:00'`
  }
}

// ---------------------------------------------------------------------------
// 데이터셋 이름 · 경로
// ---------------------------------------------------------------------------

export type TransferTarget = {
  /** 이름의 앞부분(데이터베이스 · 스키마 — SQL Server 는 `db.schema`) */
  namespace: string[]
  table: string
  /** 원본 SQL 의 테이블 이름(인용 포함) */
  sourceTable: string
  type: string
  datasourceId: string
  /** 데이터레이크 원천 영역 경로(버킷 뒤) — raw/{데이터 소스}/{이름 앞부분…}/{테이블} */
  s3Prefix: string
  /** 일자 파티션 컬럼 — 원본에 dt 컬럼이 있으면 load_dt */
  partition: string
  /** 코드 · 시크릿 · 작업 ID 에 쓰는 이름(영문 · 숫자 · 밑줄) */
  slug: string
  secretPrefix: string
}

function slugify(text: string): string {
  return text.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toLowerCase() || "x"
}

export function transferTarget(dataset: DatasetDetail): TransferTarget {
  const parts = dataset.name.split(".").filter(Boolean)
  const table = parts[parts.length - 1] ?? dataset.name
  const namespace = parts.slice(0, -1)
  const type = dataset.datasource.type
  const datasourceId = dataset.datasource.datasource_id
  const sourceTable = [...namespace, table].map((p) => sourceIdent(type, p)).join(".")
  const pathParts = [datasourceId, ...namespace, table].map((p) => p.replace(/[^A-Za-z0-9._-]+/g, "_"))
  const names = new Set(dataset.schema_fields.map((f) => f.field_path.toLowerCase()))
  return {
    namespace,
    table,
    sourceTable,
    type,
    datasourceId,
    s3Prefix: `raw/${pathParts.join("/")}`,
    partition: names.has("dt") ? "load_dt" : "dt",
    slug: slugify([...namespace, table].join("_")),
    secretPrefix: slugify(datasourceId).toUpperCase(),
  }
}

/** 원본 SELECT 의 컬럼 목록(원본 방언으로 인용). */
export function sourceColumns(dataset: DatasetDetail): string[] {
  return dataset.schema_fields.map((f) => sourceIdent(dataset.datasource.type, f.field_path))
}

// ---------------------------------------------------------------------------
// 증분 기준 컬럼
// ---------------------------------------------------------------------------

// 잘 알려진 수정시각 컬럼 → 시간성 이름 패턴 → 날짜 · 시각 타입 순으로 증분 기준 후보를 찾는다.
const KNOWN_UPDATE_COLUMNS = ["last_update", "updated_at", "update_dt", "modified_at", "last_modified"]
const TIME_NAME_PATTERN = /(_date|_dt|_at|_time|_timestamp)$/i

export type CandidateColumn = { name: string; nativeType: string; description: string }

export function incrementalCandidates(dataset: DatasetDetail): CandidateColumn[] {
  const fields = dataset.schema_fields
  const seen = new Set<string>()
  const out: CandidateColumn[] = []
  const push = (f: Field) => {
    if (seen.has(f.field_path)) return
    seen.add(f.field_path)
    out.push({ name: f.field_path, nativeType: f.native_type || f.field_type, description: f.description || "" })
  }
  for (const known of KNOWN_UPDATE_COLUMNS) {
    const f = fields.find((x) => x.field_path.toLowerCase() === known)
    if (f) push(f)
  }
  for (const f of fields) if (TIME_NAME_PATTERN.test(f.field_path)) push(f)
  for (const f of fields) if (f.field_type === "DATE") push(f)
  return out
}

export function incrementalColumn(dataset: DatasetDetail): string {
  return incrementalCandidates(dataset)[0]?.name ?? "<INCREMENTAL_COLUMN>"
}

/** 숫자형 PK — JDBC 병렬 읽기(partitionColumn) 후보. */
export function numericPrimaryKey(dataset: DatasetDetail): Field | undefined {
  return dataset.schema_fields.find((f) => f.is_primary_key === "true" && f.field_type === "NUMBER")
}

// ---------------------------------------------------------------------------
// Hive 타입 — PySpark(JDBC)가 쓴 Parquet 의 타입을 원본 타입으로 추정
// ---------------------------------------------------------------------------

/**
 * 원본 컬럼 → Hive 타입. Hive 외부 테이블은 Parquet 파일의 타입과 맞아야 읽히므로, Spark JDBC 가 원본 타입을 읽는 규칙을 따른다
 * (MySQL `tinyint(1)` → BOOLEAN, `YEAR` → DATE, 부호 없는 INT → BIGINT, Oracle `DATE` → TIMESTAMP, `TIME` → TIMESTAMP 등).
 * 추정이므로 생성 코드에 확인 주석을 단다.
 */
export function hiveType(field: Field, sourceType: string): string {
  const native = (field.native_type || field.field_type || "").trim().toLowerCase()
  const base = native.replace(/\(.*$/, "").replace(/\s+(unsigned|signed|zerofill).*$/, "").trim()
  const unsigned = /\bunsigned\b/.test(native)
  const precision = /\(\s*(\d+)\s*(?:,\s*(-?\d+)\s*)?\)/.exec(native)
  const dialect = sourceDialect(sourceType)

  if (native === "tinyint(1)" || ["bool", "boolean", "bit"].includes(base)) return "BOOLEAN"
  if (["tinyint", "smallint", "mediumint", "int2", "smallserial"].includes(base)) return "INT"
  if (["int", "integer", "int4", "serial"].includes(base)) return unsigned ? "BIGINT" : "INT"
  if (["bigint", "int8", "bigserial"].includes(base)) return unsigned ? "DECIMAL(20,0)" : "BIGINT"
  if (["decimal", "numeric", "number", "dec"].includes(base)) {
    if (precision) {
      const p = Math.min(Number(precision[1]), 38)
      const s = Math.max(0, Math.min(Number(precision[2] ?? 0), p))
      return `DECIMAL(${p},${s})`
    }
    return dialect === "oracle" ? "DECIMAL(38,10)" : "DECIMAL(38,18)"
  }
  if (["money", "smallmoney"].includes(base)) return "DECIMAL(19,4)"
  if (["real", "float4"].includes(base)) return "FLOAT"
  if (["float", "float8", "double", "double precision", "binary_double", "binary_float"].includes(base)) return "DOUBLE"
  if (base === "year") return "DATE"
  if (base === "date") return dialect === "oracle" ? "TIMESTAMP" : "DATE"
  if (/^(datetime|datetime2|smalldatetime|datetimeoffset|timestamp|timestamptz|time|timetz)/.test(base)) return "TIMESTAMP"
  if (/(blob|binary|bytea|varbinary|image|raw|geometry)/.test(base)) return "BINARY"
  return "STRING"
}

// ---------------------------------------------------------------------------
// 증분 컬럼 안내 주석 (# 주석 언어용)
// ---------------------------------------------------------------------------

export function candidateCommentBlock(candidates: CandidateColumn[], chosen: string | null, prefix = "#"): string {
  const p = prefix
  const lines: string[] = [
    `${p} ─────────────────────────────────────────────────────────────────────────`,
    `${p} [필수 확인] 일단위 증분 필터 기준 컬럼`,
    `${p}`,
    `${p} 증분 추출 기준은 테이블마다 다르므로 데이터 담당자가 반드시 확인해야 한다.`,
    `${p} 카탈로그 스키마에서 자동 추출한 후보 컬럼:`,
  ]
  if (candidates.length === 0) {
    lines.push(`${p}   (시간성 컬럼을 찾지 못함 — 전체 적재를 쓰거나, 원본 테이블에 수정시각 컬럼 추가를 검토하세요)`)
  } else {
    for (const c of candidates.slice(0, 5)) {
      const mark = c.name === chosen ? "  ← 현재 선택" : ""
      const desc = c.description ? ` — ${c.description}` : ""
      lines.push(`${p}   * ${c.name} (${c.nativeType})${desc}${mark}`)
    }
  }
  lines.push(
    `${p}`,
    `${p} 주의:`,
    `${p}   - 수정(UPDATE)이 발생하는 테이블은 '생성 시각'이 아닌 '수정 시각' 컬럼을 쓴다.`,
    `${p}   - DELETE 는 증분으로 잡히지 않는다 — CDC 또는 주기적 전체 적재 병행을 검토.`,
    `${p}   - 원본 컬럼에 인덱스가 있는지 확인 (없으면 일 배치가 풀스캔이 된다).`,
    `${p} ─────────────────────────────────────────────────────────────────────────`,
  )
  return lines.join("\n")
}

// ---------------------------------------------------------------------------
// Hive · Trino SQL — Airflow · Kestra 의 Hive / Trino 하위 탭이 함께 쓴다
// ---------------------------------------------------------------------------

export const DATALAKE_BUCKET = "<DATALAKE_BUCKET>"

/** 데이터레이크 쪽 Hive 데이터베이스(= Trino 스키마) 기본 이름 — raw_{데이터 소스}. */
export function lakeSchema(target: TransferTarget): string {
  return `raw_${slugify(target.datasourceId)}`
}

export type HiveSql = { createDatabase: string; create: string; addPartition: string; verify: string; table: string; location: string }

/**
 * Hive — PySpark 탭(또는 Airflow · Kestra 의 PySpark 하위 탭)이 `raw/…/{파티션}=YYYY-MM-DD/` 에 쓴 Parquet 을 외부 테이블로 등록한다.
 * Hive 는 원본 DB 를 읽지 않는다. `dateExpr` 는 일자 템플릿 식(`{{ ds }}` 등).
 */
export function hiveSql(dataset: DatasetDetail, dateExpr: string): HiveSql {
  const target = transferTarget(dataset)
  const table = `${hiveIdent(lakeSchema(target))}.${hiveIdent(target.table)}`
  const location = `s3a://${DATALAKE_BUCKET}/${target.s3Prefix}`
  const columns = dataset.schema_fields
    .map((f) => `${hiveIdent(f.field_path)} ${hiveType(f, target.type)}`)
    .join(",\n  ")
  const part = hiveIdent(target.partition)
  return {
    table,
    location,
    createDatabase: `CREATE DATABASE IF NOT EXISTS ${hiveIdent(lakeSchema(target))}`,
    create: `CREATE EXTERNAL TABLE IF NOT EXISTS ${table} (
  ${columns}
)
PARTITIONED BY (${part} STRING)
STORED AS PARQUET
LOCATION '${location}'`,
    addPartition: `ALTER TABLE ${table} ADD IF NOT EXISTS PARTITION (${part} = '${dateExpr}')
LOCATION '${location}/${target.partition}=${dateExpr}'`,
    verify: `SELECT count(*) AS row_count FROM ${table} WHERE ${part} = '${dateExpr}'`,
  }
}

export type TrinoSql = { createSchema: string; create: string; deletePartition: string; insert: string; verify: string }

/**
 * Trino — 원본을 Trino 커넥터 카탈로그(`<SOURCE_CATALOG>`)로 읽어 데이터레이크 Hive 커넥터 카탈로그(`<LAKE_CATALOG>`)의
 * 일자 파티션 테이블에 넣는다. 테이블은 원본 타입 그대로(CTAS … WITH NO DATA) 만들고, 같은 날짜를 다시 돌리면 그 파티션을 지우고
 * 다시 넣는다(멱등). `dateExpr` 는 일자 템플릿 식.
 */
export function trinoSql(dataset: DatasetDetail, dateExpr: string): TrinoSql {
  const target = transferTarget(dataset)
  const schema = `<LAKE_CATALOG>.${trinoIdent(lakeSchema(target))}`
  const table = `${schema}.${trinoIdent(target.table)}`
  // Trino 커넥터는 카탈로그 하나가 데이터베이스 하나 — 스키마는 이름 앞부분의 마지막(MySQL 은 DB, SQL Server 는 스키마)
  const sourceSchema = target.namespace[target.namespace.length - 1] ?? "<SOURCE_SCHEMA>"
  const source = `<SOURCE_CATALOG>.${trinoIdent(sourceSchema)}.${trinoIdent(target.table)}`
  const columns = dataset.schema_fields.map((f) => trinoIdent(f.field_path)).join(",\n  ")
  const part = trinoIdent(target.partition)
  const inc = trinoIdent(incrementalColumn(dataset))
  const start = `TIMESTAMP '${dateExpr} 00:00:00'`
  return {
    createSchema: `CREATE SCHEMA IF NOT EXISTS ${schema}
WITH (location = 's3a://${DATALAKE_BUCKET}/lake/${lakeSchema(target)}')`,
    create: `CREATE TABLE IF NOT EXISTS ${table}
WITH (format = 'PARQUET', partitioned_by = ARRAY['${target.partition}'])
AS SELECT
  ${columns},
  CAST(NULL AS varchar) AS ${part}
FROM ${source}
WITH NO DATA`,
    deletePartition: `DELETE FROM ${table} WHERE ${part} = '${dateExpr}'`,
    insert: `INSERT INTO ${table}
SELECT
  ${columns},
  '${dateExpr}' AS ${part}
FROM ${source}
WHERE ${inc} >= ${start}
  AND ${inc} <  ${start} + INTERVAL '1' DAY`,
    verify: `SELECT count(*) AS row_count FROM ${table} WHERE ${part} = '${dateExpr}'`,
  }
}

/** 여러 줄 SQL 을 들여쓴다(YAML 블록 · 파이썬 문자열 안에 넣을 때). */
export function indent(text: string, spaces: number): string {
  const pad = " ".repeat(spaces)
  return text.split("\n").map((line) => (line ? pad + line : line)).join("\n")
}
