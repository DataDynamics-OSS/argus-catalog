#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Argus Catalog 데이터셋 품질 배치 — PySpark 프로파일링 + 규칙 검증.

카탈로그 API 에서 데이터셋(URN)과 품질 규칙을 가져와, 원본 RDBMS 를 PySpark(JDBC)로
직접 읽어 프로파일링·검증을 수행하고, 결과를 다시 API 로 반입(import)한다.
CLI·흐름·평가 시맨틱은 python-quality.py(pandas)와 같다(공통 로직은 quality_common.py).

서버 내장 검증과의 차이:
  - 프로파일 "스냅숏"이 아닌 원본 전체 데이터로 평가하므로
    ACCEPTED_VALUES / REGEX / CUSTOM_SQL / CUSTOM_PYTHON 처럼 서버가 평가하지 않는 규칙도
    실제 데이터 기준으로 평가한다(서버 검증은 이 반입 결과를 이어받아 점수에 반영).
  - 대용량 테이블은 --partition-column 으로 JDBC 읽기를 나눠 Spark 클러스터에서 분산 처리한다.

실행 예:
    export ARGUS_PASSWORD='<ADMIN_PASSWORD>'               # 카탈로그 계정
    export ARGUS_SOURCE_DB_PASSWORD='<SOURCE_DB_PASSWORD>' # 원본 DB 계정 (카탈로그가 내려주지 않음)

    # 단일 데이터셋 — 공통 모듈(quality_common.py)을 --py-files 로 함께 배포
    spark-submit --jars mysql-connector-j-8.4.0.jar \\
        --py-files quality/quality_common.py quality/dataset-quality.py \\
        --urn sakila-mysql.sakila.rental.dataset \\
        --api-url http://localhost:4600 --username admin \\
        --partition-column rental_id --num-partitions 8

    # 데이터 소스 전체 — 등록된 모든 데이터셋을 한 번에 처리 (실패가 있으면 exit code 1)
    spark-submit --jars mysql-connector-j-8.4.0.jar \\
        --py-files quality/quality_common.py quality/dataset-quality.py \\
        --datasource-id sakila-mysql \\
        --api-url http://localhost:4600 --username admin --approx-distinct

    # 원본 접속 직접 지정(카탈로그 연결 설정 미사용) + 반입 없이 결과만 확인
    spark-submit --jars mysql-connector-j-8.4.0.jar \\
        --py-files quality/quality_common.py quality/dataset-quality.py \\
        --urn sakila-mysql.sakila.rental.dataset \\
        --api-url http://localhost:4600 --username admin \\
        --jdbc-url 'jdbc:mysql://127.0.0.1:3306/sakila?sslMode=REQUIRED' --jdbc-user sakila \\
        --jdbc-password-file ~/.argus/sakila.pw --dry-run

부가 효과:
  - 프로파일에서 얻은 전체 행 수로 카탈로그의 데이터셋 row_count 도 갱신한다.

원본 DB 접속 정보:
  - 기본: 카탈로그의 데이터 소스 연결 설정(GET /catalog/datasources/{id}/configuration,
    기능 권한 datasources.connection 필요 — 미설정이면 admin 전용)에서 host/port/database/username/SSL 을
    읽어 JDBC URL 을 조립한다. 카탈로그는 비밀번호를 내려주지 않으므로(마스킹) 비밀번호는
    --jdbc-password-file / 환경변수 ARGUS_SOURCE_DB_PASSWORD / --jdbc-password 로 전달한다
    (우선순위: 명령행 > 파일 > 환경변수). --jdbc-user 는 설정의 계정을 덮어쓴다.
  - 카탈로그 설정을 쓰지 않으려면 --jdbc-url / --jdbc-user (+ 비밀번호)로 직접 지정한다.
    이때 SSL 등 접속 옵션은 URL 에 직접 넣는다(pandas 버전의 --db-ssl 에 해당하는 인자는 없음).

흐름:
    로그인 → 대상 데이터셋 조회 → JDBC 접속 결정(데이터 소스당 1회) → 데이터셋별
    [활성 규칙 조회 → JDBC 로 원본 읽기(파티션 분할 선택) → 프로파일 → 규칙 평가
    → 프로파일·row_count·결과 반입] → 요약 출력
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from pyspark import StorageLevel
from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

# 공통 모듈(quality_common.py) — 로컬 실행은 스크립트 위치에서, 클러스터는 --py-files 로 배포
sys.path.insert(0, str(Path(__file__).resolve().parent))
import quality_common as qc  # noqa: E402

# ---------------------------------------------------------------------------
# JDBC 접속 — 카탈로그 연결 설정 또는 CLI 인자에서 조립
# ---------------------------------------------------------------------------

JDBC_TEMPLATES = {
    # MySQL 프로토콜 계열 — StarRocks 는 MySQL 와이어 프로토콜(9030) 호환
    "mysql": ("jdbc:mysql://{host}:{port}/{database}", "com.mysql.cj.jdbc.Driver"),
    "mariadb": ("jdbc:mysql://{host}:{port}/{database}", "com.mysql.cj.jdbc.Driver"),
    "starrocks": ("jdbc:mysql://{host}:{port}/{database}", "com.mysql.cj.jdbc.Driver"),
    # PostgreSQL 프로토콜 계열
    "postgresql": ("jdbc:postgresql://{host}:{port}/{database}", "org.postgresql.Driver"),
    "greenplum": ("jdbc:postgresql://{host}:{port}/{database}", "org.postgresql.Driver"),
    "redshift": ("jdbc:redshift://{host}:{port}/{database}", "com.amazon.redshift.jdbc42.Driver"),
    "oracle": ("jdbc:oracle:thin:@//{host}:{port}/{database}", "oracle.jdbc.OracleDriver"),
    "tibero": ("jdbc:tibero:thin:@{host}:{port}:{database}", "com.tmax.tibero.jdbc.TbDriver"),
    "mssql": ("jdbc:sqlserver://{host}:{port};databaseName={database}",
              "com.microsoft.sqlserver.jdbc.SQLServerDriver"),
    "sqlserver": ("jdbc:sqlserver://{host}:{port};databaseName={database}",
                  "com.microsoft.sqlserver.jdbc.SQLServerDriver"),
    # SQL 엔진 — database 자리에 카탈로그/DB 명
    "trino": ("jdbc:trino://{host}:{port}/{database}", "io.trino.jdbc.TrinoDriver"),
    "hive": ("jdbc:hive2://{host}:{port}/{database}", "org.apache.hive.jdbc.HiveDriver"),
    "impala": ("jdbc:impala://{host}:{port}/{database}", "com.cloudera.impala.jdbc.Driver"),
    # host 자리에 Snowflake account 식별자(예: xy12345.ap-northeast-2) — 항상 TLS
    "snowflake": ("jdbc:snowflake://{host}.snowflakecomputing.com/?db={database}",
                  "net.snowflake.client.jdbc.SnowflakeDriver"),
}


def build_jdbc_url(ds_type: str, p: dict) -> str:
    """접속 파라미터 → JDBC URL. SSL 은 카탈로그 설정(ssl_enabled/use_ssl)을 따른다.

    ssl=True 면 암호화를 강제하되 서버 인증서는 검증하지 않는다(서버 connection_ssl 과 같은 수준).
    ssl=None(설정 없음)/False 면 드라이버 기본값 — 단 MSSQL 은 종전대로 encrypt=false.
    """
    template, _ = JDBC_TEMPLATES.get(ds_type, (f"jdbc:{ds_type}://{{host}}:{{port}}/{{database}}", ""))
    ssl_on = p.get("ssl")
    if ds_type == "oracle" and ssl_on:
        template = "jdbc:oracle:thin:@tcps://{host}:{port}/{database}"
    url = template.format(host=p["host"], port=p["port"], database=p["database"])

    if ds_type in ("mssql", "sqlserver"):
        # 드라이버 10.x+ 는 encrypt=true 가 기본이라 종전 동작(평문)을 유지하려면 명시가 필요
        return url + (";encrypt=true;trustServerCertificate=true" if ssl_on else ";encrypt=false")
    if not ssl_on:
        return url
    if ds_type in ("mysql", "mariadb", "starrocks"):
        return url + "?sslMode=REQUIRED"
    if ds_type in ("postgresql", "greenplum"):
        return url + "?sslmode=require"
    if ds_type == "redshift":
        return url + "?ssl=true"
    if ds_type == "trino":
        return url + "?SSL=true"
    if ds_type == "hive":
        return url + ";ssl=true"
    if ds_type == "impala":
        return url + ";SSL=1"
    return url  # oracle(tcps 처리 완료) / snowflake(항상 TLS) / tibero(미지원)


def resolve_jdbc(
    args: argparse.Namespace, api: qc.CatalogApi,
    ds_type: str, datasource_id: str, default_database: str,
) -> dict:
    """JDBC 공통 옵션(url/driver/user/password) 결정. CLI 인자 > 카탈로그 연결 설정.

    - --jdbc-url: 카탈로그 설정을 조회하지 않는다(일반 계정용). --jdbc-user·비밀번호 필수.
    - 그 밖: 카탈로그 연결 설정(datasources.connection 권한)으로 URL 을 조립하고(build_jdbc_url),
      비밀번호는 배치 인자/환경변수에서 받는다. --jdbc-user 는 설정의 계정을 덮어쓴다.
    - 드라이버 클래스는 --jdbc-driver > 타입별 기본값.
    """
    _, driver = JDBC_TEMPLATES.get(ds_type, ("", ""))
    driver = args.jdbc_driver or driver
    if not driver:
        raise SystemExit(f"'{ds_type}' 의 JDBC 드라이버 클래스를 정하지 못했습니다 — --jdbc-driver 로 지정하세요.")
    password = qc.source_password(args, "jdbc")

    if args.jdbc_url:
        if not args.jdbc_user:
            raise SystemExit("--jdbc-url 사용 시 --jdbc-user 도 필요합니다.")
        if password is None:
            raise SystemExit(f"원본 DB 비밀번호를 확인하지 못했습니다 — {qc.source_password_hint('jdbc')}.")
        return {"url": args.jdbc_url, "driver": driver, "user": args.jdbc_user, "password": password}

    cfg, secret_fields = qc.fetch_source_config(api, datasource_id, "jdbc")
    p = qc.source_from_config(cfg, secret_fields, ds_type, default_database, password, "jdbc")
    return {"url": build_jdbc_url(ds_type, p), "driver": driver,
            "user": args.jdbc_user or p["user"], "password": p["password"]}


def jdbc_reader(spark: SparkSession, jdbc: dict, fetchsize: int):
    return (
        spark.read.format("jdbc")
        .option("url", jdbc["url"])
        .option("driver", jdbc["driver"])
        .option("user", jdbc["user"])
        .option("password", jdbc["password"])
        .option("fetchsize", str(fetchsize))
    )


# ---------------------------------------------------------------------------
# 원본 읽기 — 파티션 컬럼이 있으면 min/max 로 범위를 나눠 병렬 JDBC 읽기
# ---------------------------------------------------------------------------


def partition_bounds(spark: SparkSession, jdbc: dict, ds_type: str, table_ref: str,
                     column: str) -> tuple[str, str] | None:
    """파티션 컬럼의 (min, max). 컬럼이 없거나 값이 없으면 None (단일 파티션으로 폴백)."""
    qcol = qc.quote_ident(column, ds_type)
    try:
        row = (
            jdbc_reader(spark, jdbc, 1000)
            .option("query", f"SELECT MIN({qcol}) AS lo, MAX({qcol}) AS hi FROM {table_ref}")
            .load()
            .collect()
        )
    except Exception as e:  # noqa: BLE001 — 컬럼 부재 등: 경고 후 단일 파티션
        print(f"[quality]   경고: 파티션 컬럼 '{column}' 범위를 조회하지 못했습니다 — 단일 파티션으로 읽습니다 "
              f"({qc.short_error(e)})", file=sys.stderr)
        return None
    if not row or row[0][0] is None or row[0][1] is None:
        return None
    return str(row[0][0]), str(row[0][1])


def read_table(spark: SparkSession, jdbc: dict, ds_type: str, table: str,
               args: argparse.Namespace) -> DataFrame:
    """원본 테이블 읽기 — --partition-column 과 --num-partitions > 1 이면 min/max 범위로 병렬 분할.

    분할 컬럼이 없거나(범위 조회 실패) 분할할 수 없는 타입이면 경고 후 단일 파티션으로 읽는다.
    """
    table_ref = qc.quote_table(table, ds_type)
    reader = jdbc_reader(spark, jdbc, args.fetchsize).option("dbtable", table_ref)
    if args.partition_column and args.num_partitions > 1:
        bounds = partition_bounds(spark, jdbc, ds_type, table_ref, args.partition_column)
        if bounds:
            lo, hi = bounds
            print(f"[quality]   partitioned read: {args.partition_column} "
                  f"[{lo} ~ {hi}] × {args.num_partitions}")
            # Spark 가 partitionColumn 을 방언에 맞게 인용한다 — 원래 이름 그대로 전달
            try:
                return (jdbc_reader(spark, jdbc, args.fetchsize).option("dbtable", table_ref)
                        .option("partitionColumn", args.partition_column)
                        .option("lowerBound", lo).option("upperBound", hi)
                        .option("numPartitions", str(args.num_partitions))
                        .load())
            except Exception as e:  # noqa: BLE001 — 분할 불가 타입(문자열 등): 단일 파티션 폴백
                print(f"[quality]   경고: '{args.partition_column}' 로 분할하지 못했습니다 — 단일 파티션으로 읽습니다 "
                      f"({qc.short_error(e)})", file=sys.stderr)
    return reader.load()


# ---------------------------------------------------------------------------
# 프로파일 계산 — 서버 ColumnProfile 스키마와 동일한 형태로 산출
# ---------------------------------------------------------------------------

NUMERIC_TYPES = (T.ByteType, T.ShortType, T.IntegerType, T.LongType,
                 T.FloatType, T.DoubleType, T.DecimalType)


def scol(name: str) -> Column:
    """컬럼 이름 그대로 참조 — 점(.)·공백·백틱이 든 이름도 안전하게 백틱 인용."""
    return F.col("`" + name.replace("`", "``") + "`")


def compute_profile(df: DataFrame, row_count: int, approx_distinct: bool = False) -> list[dict]:
    """컬럼별 NULL/고유값/최소·최대/평균 통계를 한 번의 집계로 계산.

    approx_distinct=True 면 고유값 수를 approx_count_distinct(HyperLogLog++)로 근사한다.
    """
    aggs = []
    fields = df.schema.fields
    # 별칭은 위치 기반(c0__nulls …) — 컬럼 이름의 특수문자와 무관하게 안전
    for i, f in enumerate(fields):
        c = scol(f.name)
        aggs.append(F.count(F.when(c.isNull(), 1)).alias(f"c{i}__nulls"))
        if approx_distinct:
            aggs.append(F.approx_count_distinct(c).alias(f"c{i}__uniq"))
        else:
            aggs.append(F.countDistinct(c).alias(f"c{i}__uniq"))
        # 바이너리 컬럼(BLOB 등) — min/max 가 무의미하고 거대하므로 생략
        if not isinstance(f.dataType, T.BinaryType):
            aggs.append(F.min(c).cast("string").alias(f"c{i}__min"))
            aggs.append(F.max(c).cast("string").alias(f"c{i}__max"))
        if isinstance(f.dataType, NUMERIC_TYPES):
            aggs.append(F.avg(c).alias(f"c{i}__mean"))

    row = df.agg(*aggs).collect()[0].asDict() if aggs else {}

    profiles = []
    for i, f in enumerate(fields):
        nulls = int(row.get(f"c{i}__nulls") or 0)
        uniq = int(row.get(f"c{i}__uniq") or 0)
        mean = row.get(f"c{i}__mean")

        # 최빈값 top 5 — 저카디널리티(코드성) 컬럼만 추가 집계
        top_values = None
        if 0 < uniq <= 20 and not isinstance(f.dataType, T.BinaryType):
            tv = (df.filter(scol(f.name).isNotNull()).groupBy(scol(f.name)).count()
                  .orderBy(F.desc("count")).limit(5).collect())
            top_values = [{"value": str(r2[0]), "count": int(r2[1])} for r2 in tv]

        profiles.append({
            "column_name": f.name,
            "column_type": f.dataType.simpleString().upper(),
            "total_count": row_count,
            "null_count": nulls,
            "null_percent": qc.percent(nulls, row_count),
            "unique_count": uniq,
            "unique_percent": qc.percent(uniq, row_count),
            "min_value": row.get(f"c{i}__min"),
            "max_value": row.get(f"c{i}__max"),
            "mean_value": float(mean) if mean is not None else None,
            "top_values": top_values,
        })
    return profiles


# ---------------------------------------------------------------------------
# 규칙 평가 — 서버 _evaluate_rule 과 동일한 시맨틱.
# 단, ACCEPTED_VALUES / REGEX 는 원본 전체 데이터로 실제 평가한다.
# ---------------------------------------------------------------------------


def _spark_samples(sub: DataFrame, limit: int = 5) -> list[dict] | None:
    """위반 행을 카탈로그 반입용 샘플(dict 목록)로 변환 — 최대 limit 행, 값은 문자열화."""
    rows = sub.limit(limit).collect()
    if not rows:
        return None
    return [
        {k: (None if v is None else str(v)) for k, v in row.asDict().items()}
        for row in rows
    ]


def collect_failed_samples(rule: dict, df: DataFrame) -> list[dict] | None:
    """실패한 규칙의 위반 행 샘플(최대 5행)을 추출한다.

    행 단위 위반을 특정할 수 있는 유형만 지원 — NOT_NULL/UNIQUE/
    ACCEPTED_VALUES/REGEX/MIN_VALUE/MAX_VALUE. 집계형(ROW_COUNT/FRESHNESS)과
    커스텀(CUSTOM_SQL/CUSTOM_PYTHON — 위반 건수만 반환하는 규약)은 None.
    """
    check = rule["check_type"]
    column = rule.get("column_name")
    expected = rule.get("expected_value")
    if not column or column not in df.columns:
        return None
    c = scol(column)
    try:
        if check == "NOT_NULL":
            return _spark_samples(df.filter(c.isNull()))
        if check == "UNIQUE":
            dup_vals = [r[0] for r in (
                df.groupBy(c).count().filter(F.col("count") > 1).limit(5).collect()
            )]
            if not dup_vals:
                return None
            return _spark_samples(df.filter(c.isin(dup_vals)))
        if check == "ACCEPTED_VALUES" and expected:
            allowed = qc.accepted_values(expected)
            return _spark_samples(df.filter(c.isNotNull() & ~c.cast("string").isin(allowed)))
        if check == "REGEX" and expected:
            return _spark_samples(df.filter(c.isNotNull() & ~c.cast("string").rlike(expected)))
        if check == "MIN_VALUE" and expected is not None:
            return _spark_samples(df.filter(c.cast("double") < float(expected)))
        if check == "MAX_VALUE" and expected is not None:
            return _spark_samples(df.filter(c.cast("double") > float(expected)))
    except Exception:  # noqa: BLE001 — 샘플 추출 실패는 결과에 영향 주지 않음
        return None
    return None


def latest_value(df: DataFrame, column: str):
    """FRESHNESS — 컬럼의 최대값(최신 데이터 시각).

    타임스탬프 컬럼은 collect() 하면 PySpark 가 **파이썬 프로세스(배치 호스트) 로컬 시간대**의 naive datetime
    으로 바꾸므로 쓰지 않고, Spark 세션 시간대(기본 = JVM 시간대 — JDBC 가 DATETIME 을 읽을 때 쓴 시간대)의
    벽시계 문자열로 받는다 → 원천의 시각 값 그대로가 되어 ``--source-timezone`` 으로 해석된다.
    """
    c = scol(column)
    dtype = df.schema[column].dataType.typeName()
    if dtype in ("timestamp", "timestamp_ntz"):
        text = df.agg(F.date_format(F.max(c), "yyyy-MM-dd HH:mm:ss.SSSSSS")).collect()[0][0]
        return text[:-7] if text and text.endswith(".000000") else text
    return df.agg(F.max(c)).collect()[0][0]


def evaluate_rule(rule: dict, df: DataFrame, row_count: int,
                  col_map: dict[str, dict],
                  spark: SparkSession | None = None,
                  jdbc: dict | None = None,
                  checks_dir: str | None = None,
                  fetchsize: int = 10000,
                  source_tz=None) -> tuple[bool, str, str]:
    """규칙 1건 평가 → (passed, actual_value, detail).

    ``source_tz`` — FRESHNESS 에서 시간대 없는 시각 값을 해석할 원천 데이터 시간대(기본 Asia/Seoul).
    """
    check = rule["check_type"]
    column = rule.get("column_name") or ""
    try:
        threshold = qc.rule_threshold(rule)
    except (TypeError, ValueError):
        return False, "N/A", f"잘못된 임계값: {rule.get('threshold')}"
    expected = rule.get("expected_value")
    cp = col_map.get(column)
    missing = (False, "N/A", f"프로파일에 '{column}' 컬럼이 없습니다")

    if check == "NOT_NULL":
        return qc.decide_not_null(cp["null_count"], row_count, threshold) if cp else missing

    if check == "UNIQUE":
        return qc.decide_unique(cp["unique_count"], row_count, threshold) if cp else missing

    if check in ("MIN_VALUE", "MAX_VALUE"):
        if not cp:
            return missing
        return qc.decide_min_max(check, cp["min_value" if check == "MIN_VALUE" else "max_value"], expected)

    if check == "ROW_COUNT":
        return qc.decide_row_count(row_count, expected)

    if check == "ACCEPTED_VALUES":
        # 서버는 profile-only 로 평가를 건너뛰지만 여기서는 실제 데이터로 검사한다.
        if not cp:
            return missing
        accepted = qc.accepted_values(expected)
        if not accepted:
            return False, "N/A", "expected_value 에 허용 값 목록(쉼표 구분)이 필요합니다"
        c = scol(column)
        bad = df.filter(c.isNotNull() & ~c.cast("string").isin(accepted)).count()
        return bad == 0, f"위반 {bad}행", \
            f"허용 값 {accepted} 기준 위반 {bad}행 (전체 데이터 검사)"

    if check == "REGEX":
        # 서버는 profile-only 로 평가를 건너뛰지만 여기서는 실제 데이터로 검사한다.
        if not cp:
            return missing
        if not expected:
            return False, "N/A", "expected_value 에 정규식이 필요합니다"
        try:
            re.compile(expected)
        except re.error as e:
            return False, "N/A", f"잘못된 정규식: {e}"
        c = scol(column)
        bad = df.filter(c.isNotNull() & ~c.cast("string").rlike(expected)).count()
        return bad == 0, f"위반 {bad}행", \
            f"정규식 '{expected}' 기준 위반 {bad}행 (전체 데이터 검사)"

    if check == "CUSTOM_SQL":
        # 기대값의 SELECT 쿼리를 JDBC query 옵션으로 원본에서 실행.
        # 규약: 첫 행 첫 컬럼 = 위반 건수 (0 = 통과). read-only 계정 사용 전제.
        if not expected:
            return False, "N/A", "expected_value 에 검증 SQL 이 필요합니다"
        reason = qc.validate_custom_sql(expected)
        if reason:
            return False, "N/A", f"SQL 검증 실패: {reason}"
        if spark is None or jdbc is None:
            return False, "N/A", "원본 JDBC 컨텍스트가 없어 커스텀 SQL 을 실행하지 못했습니다"
        try:
            # dbtable "(sql) AS alias" 래핑은 Oracle/Tibero(AS 별칭 불가)에서 깨지므로 query 옵션 사용
            result = (
                jdbc_reader(spark, jdbc, fetchsize)
                .option("query", expected.strip().rstrip(";").strip())
                .load()
                .limit(1)
                .collect()
            )
        except Exception as e:  # noqa: BLE001 — 쿼리 오류를 결과로 보고
            return False, "N/A", f"커스텀 SQL 을 실행하지 못했습니다: {qc.short_error(e)}"
        if not result or len(result[0]) == 0:
            return False, "N/A", "커스텀 SQL 이 결과를 반환하지 않았습니다 (첫 행 첫 컬럼 = 위반 건수 규약)"
        return qc.parse_violation_count(result[0][0])

    if check == "CUSTOM_PYTHON":
        return qc.run_custom_python(expected, df, checks_dir)

    if check == "FRESHNESS":
        if not column:
            return qc.decide_freshness(expected, None, None)
        if column not in df.columns:
            return qc.decide_freshness(expected, column, None, column_present=False)
        return qc.decide_freshness(expected, column, latest_value(df, column), source_tz=source_tz)

    return False, "N/A", f"알 수 없는 검증 유형: {check}"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def process_dataset(
    spark: SparkSession, api: qc.CatalogApi, dataset_id: int, table: str, ds_type: str,
    jdbc: dict, args: argparse.Namespace, checks_dir: str,
) -> dict:
    """데이터셋 1건: 활성 규칙 조회 → 원본 읽기 → 프로파일 → 규칙 평가 → 반입(프로파일·row_count·결과).

    예외(원본 읽기 실패·API 오류)는 호출부(main)가 데이터셋 실패로 기록하고 다음 데이터셋으로 넘어간다.
    """
    rules = [r for r in api.get_rules(dataset_id) if r.get("is_active") == "true"]
    print(f"[quality] ─ {table} (id={dataset_id}), active rules: {len(rules)}")

    # 프로파일 + 규칙 평가에서 반복 사용 — 메모리에 다 안 들어가면 디스크로 넘긴다
    df = read_table(spark, jdbc, ds_type, table, args).persist(StorageLevel.MEMORY_AND_DISK)
    try:
        row_count = df.count()
        print(f"[quality]   source rows: {row_count:,}")

        columns = compute_profile(df, row_count, approx_distinct=args.approx_distinct)
        col_map = {c["column_name"]: c for c in columns}

        if args.approx_distinct:
            # UNIQUE 판정은 근사값으로 하지 않는다 — 규칙이 걸린 컬럼만 정확히 다시 센다
            for col in {r.get("column_name") for r in rules if r["check_type"] == "UNIQUE"}:
                if col in col_map:
                    exact = df.select(scol(col)).where(scol(col).isNotNull()).distinct().count()
                    col_map[col]["unique_count"] = exact
                    col_map[col]["unique_percent"] = qc.percent(exact, row_count)

        results = []
        passed_count = 0
        for rule in rules:
            passed, actual, detail = evaluate_rule(rule, df, row_count, col_map, spark=spark, jdbc=jdbc,
                                                   checks_dir=checks_dir, fetchsize=args.fetchsize,
                                                   source_tz=args.resolved_source_tz)
            samples = None if passed else collect_failed_samples(rule, df)
            passed_count += 1 if passed else 0
            results.append({
                "rule_id": rule["id"],
                "passed": passed,
                "actual_value": actual,
                "detail": detail,
                "failed_samples": samples,
            })
            mark = "PASS" if passed else "FAIL"
            sample_note = f" [샘플 {len(samples)}행]" if samples else ""
            print(f"  [{mark}] {qc.rule_label(rule)} — {detail}{sample_note}")
    finally:
        df.unpersist()

    score = qc.import_results(api, dataset_id, row_count, columns, results, args.dry_run)
    return {"table": table, "rows": row_count, "rules": len(rules),
            "passed": passed_count, "score": score}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="카탈로그 데이터셋 품질 배치 (PySpark 프로파일링 + 규칙 검증)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    qc.add_common_args(parser)
    # 원본 JDBC — 생략 시 카탈로그의 데이터 소스 연결 설정 사용 (비밀번호 제외)
    parser.add_argument("--jdbc-url", default=None, help="원본 JDBC URL (카탈로그 설정 대신 직접 지정)")
    parser.add_argument("--jdbc-user", default=None, help="원본 DB 계정")
    parser.add_argument("--jdbc-driver", default=None, help="JDBC 드라이버 클래스 (기본: 타입별 표준)")
    qc.add_source_password_args(parser, "jdbc")
    # 분산 읽기 / 대용량 옵션
    parser.add_argument("--partition-column", default=None,
                        help="병렬 JDBC 읽기 분할 컬럼(숫자/날짜/타임스탬프). 테이블에 없으면 단일 파티션")
    parser.add_argument("--num-partitions", type=int, default=8,
                        help="--partition-column 사용 시 파티션(동시 JDBC 커넥션) 수")
    parser.add_argument("--fetchsize", type=int, default=10000, help="JDBC fetchsize")
    parser.add_argument("--approx-distinct", action="store_true",
                        help="프로파일 고유값 수를 approx_count_distinct 로 근사 (UNIQUE 규칙 컬럼은 정확히 계산)")
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    checks_dir = args.checks_dir or qc.default_checks_dir(__file__)
    args.resolved_source_tz = qc.source_timezone(args)  # 잘못된 값이면 로그인 전에 종료
    api = qc.connect_api(args)

    # 1) 대상 데이터셋 결정 — (id, table 이름) 목록과 데이터 소스 정보
    targets, ds_type, datasource_id = qc.resolve_targets(api, args)
    print(f"[quality] targets: {len(targets)} dataset(s) from {datasource_id} ({ds_type})")

    # 2) JDBC 접속 결정 (같은 데이터 소스이므로 1회)
    jdbc = resolve_jdbc(args, api, ds_type, datasource_id, targets[0][1].split(".")[0])

    spark = (
        SparkSession.builder
        .appName(f"dataset-quality-{datasource_id}")
        .getOrCreate()
    )

    # 3) 데이터셋별 처리 — 한 건 실패(API 오류 포함)해도 나머지는 계속
    summaries = []
    failures = []
    try:
        for dataset_id, table in targets:
            try:
                summaries.append(process_dataset(spark, api, dataset_id, table, ds_type, jdbc,
                                                 args, checks_dir))
            except Exception as e:  # noqa: BLE001 — 배치 연속성 우선 (CatalogApiError 포함)
                failures.append((table, qc.short_error(e)))
                print(f"[quality]   ERROR {table}: {e}", file=sys.stderr)
    finally:
        # 4) 요약
        code = qc.print_summary(started, summaries, failures)
        spark.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
