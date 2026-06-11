"use client"

/**
 * PySpark 이관 코드 탭 — RDBMS → S3 데이터레이크 적재 스크립트 자동 생성.
 *
 * - JDBC 표준 방식으로 원본 RDBMS(mysql/oracle/mssql/postgresql/greenplum 등)를 읽는다.
 * - 일단위 증분 적재가 기본: 증분 기준 컬럼으로 반개구간([당일 00:00, 익일 00:00)) 필터.
 * - 증분 기준 컬럼은 테이블마다 달라 사람의 확인이 필요하므로, 스키마에서 후보를
 *   자동 추출해 주석으로 안내하고 가장 유력한 컬럼을 기본값으로 채운다.
 * - 전체 적재(Full Load) 변형도 함께 제공한다 (증분 컬럼이 없는 테이블용).
 */

import { useMemo } from "react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import type { DatasetDetail } from "@/features/datasets/data/schema"
import { CodeViewer } from "@/components/code-viewer"

// ---------------------------------------------------------------------------
// JDBC helpers — 표준 JDBC URL/드라이버 (RDBMS 별)
// ---------------------------------------------------------------------------

function getJdbc(datasourceType: string): { url: string; driver: string; jar: string } {
  switch (datasourceType) {
    case "mysql":
    case "mariadb":
    case "starrocks":
      return {
        url: "jdbc:mysql://<HOST>:<PORT>/<DATABASE>",
        driver: "com.mysql.cj.jdbc.Driver",
        jar: "mysql-connector-j-8.4.0.jar",
      }
    case "postgresql":
    case "greenplum":
      return {
        url: "jdbc:postgresql://<HOST>:<PORT>/<DATABASE>",
        driver: "org.postgresql.Driver",
        jar: "postgresql-42.7.4.jar",
      }
    case "oracle":
      return {
        url: "jdbc:oracle:thin:@//<HOST>:<PORT>/<SERVICE_NAME>",
        driver: "oracle.jdbc.OracleDriver",
        jar: "ojdbc11.jar",
      }
    case "mssql":
    case "sqlserver":
      return {
        url: "jdbc:sqlserver://<HOST>:<PORT>;databaseName=<DATABASE>;encrypt=false",
        driver: "com.microsoft.sqlserver.jdbc.SQLServerDriver",
        jar: "mssql-jdbc-12.8.1.jre11.jar",
      }
    default:
      return {
        url: `jdbc:${datasourceType}://<HOST>:<PORT>/<DATABASE>`,
        driver: "<DRIVER_CLASS>",
        jar: "<JDBC_DRIVER>.jar",
      }
  }
}

// ---------------------------------------------------------------------------
// 증분 기준 컬럼 추정 — 스키마에서 시간성 컬럼 후보를 뽑는다.
// 우선순위: 잘 알려진 수정시각 이름 > 시간성 이름 패턴 > DATE 타입 컬럼.
// ---------------------------------------------------------------------------

const KNOWN_UPDATE_COLUMNS = ["last_update", "updated_at", "update_dt", "modified_at", "last_modified"]
const TIME_NAME_PATTERN = /(_date|_dt|_at|_time|_timestamp)$/i

type CandidateColumn = { name: string; nativeType: string; description: string }

function findIncrementalCandidates(dataset: DatasetDetail): CandidateColumn[] {
  const fields = dataset.schema_fields
  const seen = new Set<string>()
  const out: CandidateColumn[] = []
  const push = (f: { field_path: string; native_type?: string | null; field_type: string; description?: string | null }) => {
    if (seen.has(f.field_path)) return
    seen.add(f.field_path)
    out.push({
      name: f.field_path,
      nativeType: f.native_type || f.field_type,
      description: f.description || "",
    })
  }
  // 1) 잘 알려진 수정시각 컬럼
  for (const known of KNOWN_UPDATE_COLUMNS) {
    const f = fields.find((x) => x.field_path.toLowerCase() === known)
    if (f) push(f)
  }
  // 2) 시간성 이름 패턴 (생성/발생 시각 등)
  for (const f of fields) {
    if (TIME_NAME_PATTERN.test(f.field_path)) push(f)
  }
  // 3) DATE 타입으로 선언된 나머지 컬럼
  for (const f of fields) {
    if (f.field_type === "DATE") push(f)
  }
  return out
}

function getParts(dataset: DatasetDetail) {
  const parts = dataset.name.split(".")
  const dbName = parts.length > 1 ? parts[0] : "<database>"
  const tblName = parts.length > 1 ? parts[1] : parts[0]
  const datasourceType = dataset.datasource.type
  const datasourceId = dataset.datasource.datasource_id
  const jdbc = getJdbc(datasourceType)
  // 데이터레이크 표준 경로: s3a://<버킷>/raw/{데이터소스}/{DB}/{테이블}/dt={적재일}
  const s3Base = `s3a://<DATALAKE_BUCKET>/raw/${datasourceId}/${dbName}/${tblName}`
  const columns = dataset.schema_fields.map((f) => f.field_path)
  const pk = dataset.schema_fields.find((f) => f.is_primary_key === "true")
  const candidates = findIncrementalCandidates(dataset)
  const secretPrefix = datasourceId.toUpperCase().replace(/-/g, "_")
  return { dbName, tblName, datasourceType, datasourceId, jdbc, s3Base, columns, pk, candidates, secretPrefix }
}

/** 증분 후보 컬럼 안내 주석 블록 — 사람이 반드시 확인해야 하는 지점. */
function candidateCommentBlock(candidates: CandidateColumn[], chosen: string | null): string {
  const lines: string[] = []
  lines.push("# ─────────────────────────────────────────────────────────────────────────")
  lines.push("# [필수 확인] 일단위 증분 필터 기준 컬럼")
  lines.push("#")
  lines.push("# 증분 추출 기준은 테이블마다 다르므로 데이터 담당자가 반드시 확인해야 한다.")
  lines.push("# 카탈로그 스키마에서 자동 추출한 후보 컬럼:")
  if (candidates.length === 0) {
    lines.push("#   (시간성 컬럼을 찾지 못함 — 전체 적재 탭의 코드를 사용하거나,")
    lines.push("#    원본 테이블에 수정시각 컬럼 추가를 검토하세요)")
  } else {
    for (const c of candidates.slice(0, 5)) {
      const mark = c.name === chosen ? "  ← 현재 선택" : ""
      const desc = c.description ? ` — ${c.description}` : ""
      lines.push(`#   * ${c.name} (${c.nativeType})${desc}${mark}`)
    }
  }
  lines.push("#")
  lines.push("# 주의:")
  lines.push("#   - 수정(UPDATE)이 발생하는 테이블은 '생성 시각'이 아닌 '수정 시각' 컬럼을 쓴다.")
  lines.push("#   - DELETE 는 증분으로 잡히지 않는다 — CDC 또는 주기적 전체 적재 병행을 검토.")
  lines.push("#   - 원본 컬럼에 인덱스가 있는지 확인 (없으면 일 배치가 풀스캔이 된다).")
  lines.push("# ─────────────────────────────────────────────────────────────────────────")
  return lines.join("\n")
}

// ---------------------------------------------------------------------------
// 코드 생성 — 일단위 증분 적재
// ---------------------------------------------------------------------------

function generateIncrementalScript(dataset: DatasetDetail): string {
  const { dbName, tblName, datasourceId, jdbc, s3Base, columns, pk, candidates, secretPrefix } = getParts(dataset)
  const chosen = candidates[0]?.name ?? "<INCREMENTAL_COLUMN>"
  const columnList = columns.map((c) => `    ${c}`).join(",\n")

  return `#!/usr/bin/env python3
"""${dbName}.${tblName} → S3 데이터레이크 일단위 증분 적재.

원본:   ${dataset.datasource.name} (${dataset.datasource.type}) — JDBC 표준 접속
대상:   ${s3Base}/dt=<적재일>/  (Parquet, Snappy)
주기:   일 1회 (전일 데이터 적재). 같은 날짜 재실행 시 해당 파티션만 덮어써 멱등.

실행 예:
    spark-submit --jars ${jdbc.jar} ingest_${tblName}.py                  # 전일분
    spark-submit --jars ${jdbc.jar} ingest_${tblName}.py --target-date 2026-06-01

Generated by Argus Catalog.
"""

import argparse
import os
from datetime import date, datetime, timedelta

from pyspark.sql import SparkSession
from pyspark.sql.functions import lit

# ── 적재 대상 ────────────────────────────────────────────────────────────────
SOURCE_TABLE = "${dbName}.${tblName}"
S3_BASE = "${s3Base}"

JDBC_URL = "${jdbc.url}"
JDBC_DRIVER = "${jdbc.driver}"
# 자격 증명은 코드에 넣지 말고 환경변수/시크릿 매니저로 주입한다.
JDBC_USER = os.environ["${secretPrefix}_USERNAME"]
JDBC_PASSWORD = os.environ["${secretPrefix}_PASSWORD"]

${candidateCommentBlock(candidates, chosen)}
INCREMENTAL_COLUMN = "${chosen}"


def build_spark() -> SparkSession:
    spark = (
        SparkSession.builder
        .appName(f"ingest-${datasourceId}-${tblName}")
        # S3A 접속 설정 — 환경에 맞게 선택:
        #  - AWS EMR/EKS(IRSA): 별도 설정 불필요 (IAM Role 사용 권장)
        #  - 사내 MinIO/Ceph 등 S3 호환 스토리지:
        # .config("spark.hadoop.fs.s3a.endpoint", "http://<S3_ENDPOINT>:9000")
        # .config("spark.hadoop.fs.s3a.access.key", os.environ["S3_ACCESS_KEY"])
        # .config("spark.hadoop.fs.s3a.secret.key", os.environ["S3_SECRET_KEY"])
        # .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .getOrCreate()
    )
    return spark


def main() -> None:
    parser = argparse.ArgumentParser(description="${dbName}.${tblName} 일단위 증분 적재")
    parser.add_argument(
        "--target-date",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="적재 대상 일자 (YYYY-MM-DD, 기본: 어제)",
    )
    args = parser.parse_args()
    target_date = args.target_date
    next_date = (datetime.strptime(target_date, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()

    spark = build_spark()

    # 일단위 필터 — DATE(컬럼) = '...' 같은 함수 비교 대신 반개구간 비교를 쓴다.
    # (컬럼을 함수로 감싸면 원본 DB 인덱스를 타지 못한다)
    pushdown_query = f"""(
        SELECT
${columnList}
        FROM ${dbName}.${tblName}
        WHERE {INCREMENTAL_COLUMN} >= '{target_date} 00:00:00'
          AND {INCREMENTAL_COLUMN} <  '{next_date} 00:00:00'
    ) AS src"""

    reader = (
        spark.read.format("jdbc")
        .option("url", JDBC_URL)
        .option("driver", JDBC_DRIVER)
        .option("dbtable", pushdown_query)
        .option("user", JDBC_USER)
        .option("password", JDBC_PASSWORD)
        .option("fetchsize", "10000")
    )
${pk && (pk.field_type === "NUMBER")
    ? `    # 대용량 테이블 병렬 읽기 — PK(${pk.field_path}) 범위를 나눠 동시 추출.
    # lowerBound/upperBound 는 사전 조회(min/max)로 채우거나 운영값으로 고정한다.
    # reader = (
    #     reader.option("partitionColumn", "${pk.field_path}")
    #     .option("numPartitions", "8")
    #     .option("lowerBound", "1")
    #     .option("upperBound", "<MAX_${pk.field_path.toUpperCase()}>")
    # )`
    : `    # 이 테이블은 숫자형 PK 가 없어 단일 커넥션으로 읽는다.
    # 대용량이면 숫자형 컬럼을 partitionColumn 으로 지정해 병렬화를 검토한다.`}

    df = reader.load()
    row_count = df.count()
    print(f"[ingest] {SOURCE_TABLE} {target_date}: {row_count:,} rows")

    # dt 파티션 컬럼을 붙여 일자별 디렉터리로 적재.
    # mode=overwrite + 단일 파티션 경로 직접 지정 → 같은 날짜 재실행에 멱등.
    target_path = f"{S3_BASE}/dt={target_date}"
    (
        df.withColumn("dt", lit(target_date))
        .repartition(1)  # 일 배치 데이터가 크면 제거하거나 적정 파티션 수로 조정
        .write.mode("overwrite")
        .parquet(target_path)
    )
    print(f"[ingest] written → {target_path}")

    spark.stop()


if __name__ == "__main__":
    main()
`
}

// ---------------------------------------------------------------------------
// 코드 생성 — 전체 적재 (증분 컬럼이 없거나 초기 1회 적재용)
// ---------------------------------------------------------------------------

function generateFullLoadScript(dataset: DatasetDetail): string {
  const { dbName, tblName, datasourceId, jdbc, s3Base, columns, pk, secretPrefix } = getParts(dataset)
  const columnList = columns.map((c) => `    ${c}`).join(",\n")

  return `#!/usr/bin/env python3
"""${dbName}.${tblName} → S3 데이터레이크 전체 적재 (스냅숏).

용도:
  - 초기 1회 백필(이후 일단위 증분으로 전환)
  - 수정시각 컬럼이 없어 증분 추출이 불가능한 테이블의 일단위 스냅숏

대상: ${s3Base}/snapshot_dt=<적재일>/  (Parquet, Snappy)

실행 예:
    spark-submit --jars ${jdbc.jar} full_load_${tblName}.py

Generated by Argus Catalog.
"""

import argparse
import os
from datetime import date

from pyspark.sql import SparkSession

SOURCE_TABLE = "${dbName}.${tblName}"
S3_BASE = "${s3Base}"

JDBC_URL = "${jdbc.url}"
JDBC_DRIVER = "${jdbc.driver}"
JDBC_USER = os.environ["${secretPrefix}_USERNAME"]
JDBC_PASSWORD = os.environ["${secretPrefix}_PASSWORD"]


def main() -> None:
    parser = argparse.ArgumentParser(description="${dbName}.${tblName} 전체 적재")
    parser.add_argument("--snapshot-date", default=date.today().isoformat(),
                        help="스냅숏 일자 (YYYY-MM-DD, 기본: 오늘)")
    args = parser.parse_args()
    snapshot_date = args.snapshot_date

    spark = (
        SparkSession.builder
        .appName(f"full-load-${datasourceId}-${tblName}")
        # S3A 설정은 증분 적재 스크립트의 build_spark() 주석 참고
        .getOrCreate()
    )

    query = """(
        SELECT
${columnList}
        FROM ${dbName}.${tblName}
    ) AS src"""

    reader = (
        spark.read.format("jdbc")
        .option("url", JDBC_URL)
        .option("driver", JDBC_DRIVER)
        .option("dbtable", query)
        .option("user", JDBC_USER)
        .option("password", JDBC_PASSWORD)
        .option("fetchsize", "10000")
    )
${pk && (pk.field_type === "NUMBER")
    ? `    # 전체 적재는 데이터량이 크므로 PK(${pk.field_path}) 기준 병렬 읽기를 권장.
    # reader = (
    #     reader.option("partitionColumn", "${pk.field_path}")
    #     .option("numPartitions", "16")
    #     .option("lowerBound", "1")
    #     .option("upperBound", "<MAX_${pk.field_path.toUpperCase()}>")
    # )`
    : `    # 숫자형 PK 가 없어 단일 커넥션으로 읽는다. 대용량이면 병렬화 컬럼을 검토.`}

    df = reader.load()
    print(f"[full-load] {SOURCE_TABLE}: {df.count():,} rows")

    target_path = f"{S3_BASE}/snapshot_dt={snapshot_date}"
    df.write.mode("overwrite").parquet(target_path)
    print(f"[full-load] written → {target_path}")

    spark.stop()


if __name__ == "__main__":
    main()
`
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

function PythonViewer({ code }: { code: string }) {
  const lineCount = useMemo(() => code.split("\n").length, [code])
  return (
    <CodeViewer
      code={code}
      language="python"
      height={Math.min(lineCount * 20 + 20, 640)}
      copyLabel="PySpark 코드"
    />
  )
}

type PySparkTabProps = {
  dataset: DatasetDetail
}

export function PySparkTab({ dataset }: PySparkTabProps) {
  const incremental = useMemo(() => generateIncrementalScript(dataset), [dataset])
  const fullLoad = useMemo(() => generateFullLoadScript(dataset), [dataset])

  return (
    <Card>
      <CardHeader className="py-3">
        <CardTitle className="text-base">PySpark 이관 코드</CardTitle>
        <CardDescription className="text-xs mt-1">
          {dataset.datasource.type.toUpperCase()} → S3 데이터레이크 적재 스크립트.
          JDBC 표준 접속이며, 증분 기준 컬럼은 코드 주석의 안내에 따라 담당자 확인이 필요합니다.
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        <Tabs defaultValue="incremental" className="w-full">
          <div className="border-t px-4 pt-2">
            <TabsList>
              <TabsTrigger value="incremental">일단위 증분</TabsTrigger>
              <TabsTrigger value="full">전체 적재</TabsTrigger>
            </TabsList>
          </div>
          <TabsContent value="incremental" className="mt-0">
            <PythonViewer code={incremental} />
          </TabsContent>
          <TabsContent value="full" className="mt-0">
            <PythonViewer code={fullLoad} />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  )
}
