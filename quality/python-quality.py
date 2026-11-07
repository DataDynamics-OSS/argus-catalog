#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Argus Catalog 데이터셋 품질 배치 — pandas 프로파일링 + 규칙 검증 (소규모 데이터용).

dataset-quality.py(PySpark)와 동일한 흐름이지만 Spark 클러스터 없이
pandas 만으로 동작한다. 테이블이 메모리에 올라가는 크기(수백만 행 이하)면
이 스크립트로 충분하며, 그 이상은 PySpark 버전을 사용한다.

카탈로그 API 에서 데이터셋과 품질 규칙을 가져와, 원본 RDBMS 를 직접 읽어
프로파일링·검증을 수행하고, 결과를 다시 API 로 반입(import)한다.
프로파일에서 얻은 전체 행 수로 카탈로그의 데이터셋 row_count 도 갱신한다.
ACCEPTED_VALUES / REGEX / CUSTOM_SQL / CUSTOM_PYTHON 처럼 서버가 평가하지 않는 규칙도
원본 전체 데이터로 평가한다(서버 검증은 이 반입 결과를 이어받아 점수에 반영).

실행 예:
    # 비밀번호는 환경변수/파일로 전달 (명령행 인자는 프로세스 목록에 노출됨)
    export ARGUS_PASSWORD='<ADMIN_PASSWORD>'               # 카탈로그 계정
    export ARGUS_SOURCE_DB_PASSWORD='<SOURCE_DB_PASSWORD>' # 원본 DB 계정 (카탈로그가 내려주지 않음)

    # 단일 데이터셋
    python quality/python-quality.py \\
        --urn sakila-mysql.sakila.actor.dataset \\
        --api-url http://localhost:4600 --username admin

    # 데이터 소스 전체 — 등록된 모든 데이터셋을 한 번에 처리 (실패가 있으면 exit code 1)
    python quality/python-quality.py \\
        --datasource-id sakila-mysql \\
        --api-url http://localhost:4600 --username admin --api-timeout 120

    # 비밀번호 파일 (첫 줄, 끝 개행 무시)
    python quality/python-quality.py --datasource-id sakila-mysql \\
        --api-url http://localhost:4600 --username admin \\
        --password-file ~/.argus/admin.pw --db-password-file ~/.argus/sakila.pw

    # 일반 계정 — 연결 설정 조회(403)·반입 권한이 없으므로 원본 접속을 직접 지정하고 --dry-run
    ARGUS_PASSWORD='<USER_PASSWORD>' python quality/python-quality.py \\
        --urn sakila-mysql.sakila.rental.dataset \\
        --api-url http://localhost:4600 --username <USER> \\
        --db-host 127.0.0.1 --db-port 3306 --db-user sakila --db-ssl --dry-run

의존성:
    pip install -r quality/requirements.txt   (pandas + 타입별 DB 드라이버, 지연 임포트)

원본 DB 접속 정보:
  - 기본: 카탈로그의 데이터 소스 연결 설정에서 host/port/database/계정/SSL 을 읽는다
    (기능 권한 datasources.connection 필요 — 미설정이면 admin 전용, 없으면 403 안내 후 종료).
    카탈로그는 비밀번호를 내려주지 않으므로(마스킹) 비밀번호는 --db-password-file /
    환경변수 ARGUS_SOURCE_DB_PASSWORD / --db-password 로 전달한다(우선순위: 명령행 > 파일 > 환경변수).
    --db-port/--db-name/--db-user/--db-ssl 로 설정 값 일부를 덮어쓸 수 있다.
  - --db-host 를 주면 카탈로그 설정을 전혀 쓰지 않는다 — --db-user 와 비밀번호가 필요하고,
    포트는 타입별 표준, DB 명은 데이터셋 이름의 첫 부분이 기본값, SSL 은 --db-ssl 로만 켠다.

흐름:
    로그인 → 대상 데이터셋 조회 → 원본 접속 결정(데이터 소스당 1회) → 데이터셋별
    [활성 규칙 조회 → SELECT * 로 원본 읽기 → 프로파일 → 규칙 평가 → 프로파일·row_count·결과 반입]
    → 요약 출력
"""

from __future__ import annotations

import argparse
import re
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# 공통 모듈(quality_common.py) — 스크립트 위치 기준으로 임포트
sys.path.insert(0, str(Path(__file__).resolve().parent))
import quality_common as qc  # noqa: E402

# pandas 는 SQLAlchemy 가 아닌 DBAPI 커넥션에 경고를 내지만 조회 전용 사용이라 무해하다
warnings.filterwarnings("ignore", message=".*pandas only supports SQLAlchemy.*")

# ---------------------------------------------------------------------------
# 원본 DB 접속 — DBAPI 커넥션 (타입별 드라이버, 지연 임포트)
# ---------------------------------------------------------------------------

DRIVER_HINTS = {
    "mysql": "pymysql", "mariadb": "pymysql", "starrocks": "pymysql",
    "postgresql": "psycopg2-binary", "greenplum": "psycopg2-binary",
    "mssql": "pymssql", "sqlserver": "pymssql",
    "oracle": "oracledb", "tibero": "oracledb",
    "trino": "trino", "hive": "impyla", "impala": "impyla",
    "redshift": "redshift_connector", "snowflake": "snowflake-connector-python",
}


def _no_verify_ssl_context():
    """서버 connection_ssl 과 같은 수준 — 암호화 강제, 서버 인증서 미검증."""
    import ssl as _ssl

    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    return ctx


def connect_source(ds_type: str, p: dict):
    """타입별 DBAPI 커넥션 생성. 드라이버 미설치 시 설치 안내.

    p: host/port/database/user/password/ssl(+schema/warehouse/role/auth_type).
    ssl 은 True(암호화 강제, 서버 인증서 미검증)/False/None(설정 없음 — 드라이버 기본값).
    Trino 는 비밀번호가 있으면 SSL 설정과 무관하게 https + BasicAuth 로 접속한다.
    조회 실패가 다음 조회를 막지 않도록 가능한 드라이버는 autocommit 으로 연다.
    """
    host, port, database = p["host"], p["port"], p["database"]
    user, password, ssl_on = p["user"], p["password"], p.get("ssl")
    try:
        # MySQL 프로토콜 계열 — StarRocks 는 MySQL 와이어 프로토콜(9030) 호환
        if ds_type in ("mysql", "mariadb", "starrocks"):
            import pymysql
            kw = {"ssl": _no_verify_ssl_context()} if ssl_on else {}
            # autocommit — 조회 실패가 이후 데이터셋 조회에 영향을 주지 않도록
            return pymysql.connect(host=host, port=port, user=user, password=password,
                                   database=database, charset="utf8mb4", autocommit=True, **kw)
        # PostgreSQL 프로토콜 계열
        if ds_type in ("postgresql", "greenplum"):
            import psycopg2
            kw = {"sslmode": "require"} if ssl_on else {}
            conn = psycopg2.connect(host=host, port=port, user=user,
                                    password=password, dbname=database, **kw)
            # autocommit — 실패한 쿼리가 트랜잭션을 aborted 상태로 남겨 이후 데이터셋을
            # 모두 실패시키는 것을 막는다(조회 전용이라 트랜잭션 불필요)
            conn.autocommit = True
            return conn
        if ds_type == "redshift":
            import redshift_connector
            kw = {"ssl": True, "sslmode": "require"} if ssl_on else {}
            conn = redshift_connector.connect(host=host, port=port, user=user,
                                              password=password, database=database, **kw)
            conn.autocommit = True
            return conn
        if ds_type in ("mssql", "sqlserver"):
            import pymssql
            kw = {"autocommit": True}
            if ssl_on is not None:
                kw["encryption"] = "require" if ssl_on else "off"
            try:
                return pymssql.connect(server=host, port=port, user=user,
                                       password=password, database=database, **kw)
            except TypeError:
                # encryption 인자를 모르는 구버전 pymssql — FreeTDS 설정(freetds.conf)을 따른다
                kw.pop("encryption", None)
                print("[quality]   경고: 설치된 pymssql 이 encryption 인자를 지원하지 않아 "
                      "SSL 설정을 적용하지 못했습니다 (freetds.conf 의 encryption 사용)", file=sys.stderr)
                return pymssql.connect(server=host, port=port, user=user,
                                       password=password, database=database, **kw)
        if ds_type in ("oracle", "tibero"):
            # Tibero 는 Oracle 호환 — oracledb thin 모드로 접속 가능한 환경 기준
            import oracledb
            if ssl_on:
                return oracledb.connect(user=user, password=password,
                                        dsn=f"tcps://{host}:{port}/{database}",
                                        ssl_server_dn_match=False)
            return oracledb.connect(user=user, password=password,
                                    dsn=f"{host}:{port}/{database}")
        if ds_type == "trino":
            import trino
            kw = {}
            if p.get("schema"):
                kw["schema"] = p["schema"]
            if ssl_on:
                kw["http_scheme"] = "https"
            if password:
                # 비밀번호 인증은 HTTPS 에서만 허용된다
                kw["http_scheme"] = "https"
                kw["auth"] = trino.auth.BasicAuthentication(user, password)
            return trino.dbapi.connect(host=host, port=port, user=user,
                                       catalog=database, **kw)
        # Hive/Impala — HiveServer2 프로토콜 (impyla)
        if ds_type in ("hive", "impala"):
            from impala.dbapi import connect as impala_connect
            kw = {}
            auth = str(p.get("auth_type") or "").upper()
            if auth == "LDAP":
                kw["auth_mechanism"] = "LDAP"
            elif auth == "KERBEROS":
                kw["auth_mechanism"] = "GSSAPI"
            if ssl_on:
                kw["use_ssl"] = True
            return impala_connect(host=host, port=port, user=user,
                                  password=password or None, database=database, **kw)
        if ds_type == "snowflake":
            # host 자리에 Snowflake account 식별자(예: xy12345.ap-northeast-2) — 항상 TLS
            import snowflake.connector
            kw = {k: p[k] for k in ("warehouse", "schema", "role") if p.get(k)}
            return snowflake.connector.connect(account=host, user=user,
                                               password=password, database=database, **kw)
    except ImportError as e:
        raise SystemExit(
            f"원본 DB 드라이버를 불러오지 못했습니다: pip install {DRIVER_HINTS.get(ds_type, ds_type)}"
        ) from e
    raise SystemExit(f"지원하지 않는 데이터 소스 타입이라 접속하지 못했습니다: {ds_type}")


def safe_rollback(conn) -> None:
    """실패한 쿼리 뒤 트랜잭션 정리 — autocommit 이 아닌 드라이버에서 다음 조회가 막히지 않도록."""
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 — 트랜잭션 미지원 드라이버(trino/impyla 등)
        pass


def resolve_connection(
    args: argparse.Namespace, api: qc.CatalogApi,
    ds_type: str, datasource_id: str, default_database: str,
) -> dict:
    """접속 파라미터 결정. CLI 인자(--db-host) > 카탈로그 연결 설정. 비밀번호는 항상 배치 쪽에서.

    - --db-host: 카탈로그 설정을 조회하지 않는다(일반 계정용). --db-user·비밀번호 필수,
      SSL 은 --db-ssl 일 때만 켠다.
    - 그 밖: 카탈로그 연결 설정(datasources.connection 권한) + 배치 비밀번호. --db-port/--db-name/
      --db-user 는 설정 값을 덮어쓰고, --db-ssl 은 SSL 을 켜기만 한다(설정의 SSL 을 끄지는 않음).
    """
    password = qc.source_password(args, "db")
    if args.db_host:
        if not args.db_user:
            raise SystemExit("--db-host 사용 시 --db-user 도 필요합니다.")
        if password is None:
            raise SystemExit(f"원본 DB 비밀번호를 확인하지 못했습니다 — {qc.source_password_hint('db')}.")
        return {
            "host": args.db_host,
            "port": args.db_port or qc.DEFAULT_PORTS.get(ds_type, 0),
            "database": args.db_name or default_database,
            "user": args.db_user,
            "password": password,
            "ssl": True if args.db_ssl else None,
        }

    cfg, secret_fields = qc.fetch_source_config(api, datasource_id, "db")
    params = qc.source_from_config(cfg, secret_fields, ds_type, default_database, password, "db")
    # 일부 항목만 CLI 로 덮어쓰기 허용 (포트·DB명·계정)
    if args.db_port:
        params["port"] = args.db_port
    if args.db_name:
        params["database"] = args.db_name
    if args.db_user:
        params["user"] = args.db_user
    if args.db_ssl:
        params["ssl"] = True
    return params


# ---------------------------------------------------------------------------
# 프로파일 계산 — 서버 ColumnProfile 스키마와 동일한 형태로 산출
# ---------------------------------------------------------------------------


def compute_profile(df: pd.DataFrame, row_count: int) -> list[dict]:
    """컬럼별 NULL/고유값/최소·최대/평균 통계 계산."""
    profiles = []
    for col in df.columns:
        series = df[col]
        nulls = int(series.isna().sum())
        uniq = int(series.nunique(dropna=True))
        non_null = series.dropna()

        # 바이너리 컬럼(BLOB 등) — min/max repr 이 거대하고 무의미하므로 통계 생략
        is_binary = len(non_null) > 0 and isinstance(
            non_null.iloc[0], (bytes, bytearray, memoryview))

        # 최빈값 top 5 — 저카디널리티(코드성) 컬럼만
        top_values = None
        if 0 < uniq <= 20 and not is_binary:
            vc = non_null.value_counts().head(5)
            top_values = [{"value": str(v), "count": int(c)} for v, c in vc.items()]

        min_v = max_v = None
        mean_v = None
        if len(non_null) > 0 and not is_binary:
            try:
                min_v, max_v = str(non_null.min()), str(non_null.max())
            except TypeError:
                pass  # 혼합 타입 등 비교 불가 컬럼
            if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
                mean_v = float(non_null.mean())

        profiles.append({
            "column_name": str(col),
            "column_type": "BINARY" if is_binary else str(series.dtype).upper(),
            "total_count": row_count,
            "null_count": nulls,
            "null_percent": qc.percent(nulls, row_count),
            "unique_count": uniq,
            "unique_percent": qc.percent(uniq, row_count),
            "min_value": min_v,
            "max_value": max_v,
            "mean_value": mean_v,
            "top_values": top_values,
        })
    return profiles


# ---------------------------------------------------------------------------
# 규칙 평가 — 서버 _evaluate_rule / dataset-quality.py(PySpark)와 동일한 시맨틱.
# ACCEPTED_VALUES / REGEX 는 원본 전체 데이터로 실제 평가한다.
# ---------------------------------------------------------------------------


def _df_samples(sub: pd.DataFrame, limit: int = 5) -> list[dict] | None:
    """위반 행을 카탈로그 반입용 샘플(dict 목록)로 변환 — 최대 limit 행, 값은 문자열화."""
    if sub is None or len(sub) == 0:
        return None
    rows = []
    for rec in sub.head(limit).to_dict("records"):
        rows.append({str(k): (None if _is_na(v) else str(v)) for k, v in rec.items()})
    return rows or None


def _is_na(v) -> bool:
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):  # 리스트 등 배열형 값
        return False


def collect_failed_samples(rule: dict, df: pd.DataFrame) -> list[dict] | None:
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
    col = df[column]
    try:
        if check == "NOT_NULL":
            return _df_samples(df[col.isna()])
        if check == "UNIQUE":
            return _df_samples(df[df.duplicated(subset=[column], keep=False)])
        if check == "ACCEPTED_VALUES" and expected:
            allowed = qc.accepted_values(expected)
            return _df_samples(df[col.notna() & ~col.astype(str).isin(allowed)])
        if check == "REGEX" and expected:
            matched = col.dropna().astype(str).str.contains(expected, regex=True, na=False)
            bad_idx = matched[~matched].index
            return _df_samples(df.loc[bad_idx])
        if check == "MIN_VALUE" and expected is not None:
            return _df_samples(df[pd.to_numeric(col, errors="coerce") < float(expected)])
        if check == "MAX_VALUE" and expected is not None:
            return _df_samples(df[pd.to_numeric(col, errors="coerce") > float(expected)])
    except Exception:  # noqa: BLE001 — 샘플 추출 실패는 결과에 영향 주지 않음
        return None
    return None


def latest_value(series: pd.Series):
    """FRESHNESS — 컬럼의 최대값(최신 데이터 시각). 비교 불가/값 없음이면 None."""
    non_null = series.dropna()
    if non_null.empty:
        return None
    try:
        return non_null.max()
    except TypeError:
        return None


def evaluate_rule(rule: dict, df: pd.DataFrame, row_count: int,
                  col_map: dict[str, dict], conn=None, checks_dir=None,
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
        series = df[column]
        bad = int((series.notna() & ~series.astype(str).isin(accepted)).sum())
        return bad == 0, f"위반 {bad}행", \
            f"허용 값 {accepted} 기준 위반 {bad}행 (전체 데이터 검사)"

    if check == "REGEX":
        # 서버는 profile-only 로 평가를 건너뛰지만 여기서는 실제 데이터로 검사한다.
        # PySpark rlike(부분 일치)와 동일하게 re.search 시맨틱(contains)을 사용한다.
        if not cp:
            return missing
        if not expected:
            return False, "N/A", "expected_value 에 정규식이 필요합니다"
        try:
            re.compile(expected)
        except re.error as e:
            return False, "N/A", f"잘못된 정규식: {e}"
        series = df[column]
        matched = series.astype(str).str.contains(expected, regex=True, na=False)
        bad = int((series.notna() & ~matched).sum())
        return bad == 0, f"위반 {bad}행", \
            f"정규식 '{expected}' 기준 위반 {bad}행 (전체 데이터 검사)"

    if check == "CUSTOM_SQL":
        # 기대값의 SELECT 쿼리를 원본 DB 에서 실행 — 규약: 첫 행 첫 컬럼 = 위반 건수 (0 = 통과).
        # SELECT 전용 가드 + read-only 계정 사용을 전제로 한다.
        if not expected:
            return False, "N/A", "expected_value 에 검증 SQL 이 필요합니다"
        reason = qc.validate_custom_sql(expected)
        if reason:
            return False, "N/A", f"SQL 검증 실패: {reason}"
        if conn is None:
            return False, "N/A", "원본 DB 커넥션이 없어 커스텀 SQL 을 실행하지 못했습니다"
        try:
            result = pd.read_sql(expected.strip().rstrip(";").strip(), conn)
        except Exception as e:  # noqa: BLE001 — 쿼리 오류를 결과로 보고
            safe_rollback(conn)  # 실패한 트랜잭션이 다음 규칙/데이터셋을 막지 않도록
            return False, "N/A", f"커스텀 SQL 을 실행하지 못했습니다: {qc.short_error(e)}"
        if result.empty or result.shape[1] == 0:
            return False, "N/A", "커스텀 SQL 이 결과를 반환하지 않았습니다 (첫 행 첫 컬럼 = 위반 건수 규약)"
        return qc.parse_violation_count(result.iat[0, 0])

    if check == "CUSTOM_PYTHON":
        return qc.run_custom_python(expected, df, checks_dir)

    if check == "FRESHNESS":
        if not column:
            return qc.decide_freshness(expected, None, None)
        present = column in df.columns
        # 드라이버가 돌려준 값 그대로 — DATETIME/timestamp 는 naive(원천 벽시계), timestamptz 는 aware
        return qc.decide_freshness(expected, column, latest_value(df[column]) if present else None,
                                   column_present=present, source_tz=source_tz)

    return False, "N/A", f"알 수 없는 검증 유형: {check}"


# ---------------------------------------------------------------------------
# 데이터셋 1건 처리
# ---------------------------------------------------------------------------


def process_dataset(api: qc.CatalogApi, dataset_id: int, table: str, ds_type: str,
                    conn, dry_run: bool, checks_dir, source_tz=None) -> dict:
    """데이터셋 1건: 활성 규칙 조회 → 원본 읽기 → 프로파일 → 규칙 평가 → 반입(프로파일·row_count·결과).

    예외(원본 읽기 실패·API 오류)는 호출부(main)가 데이터셋 실패로 기록하고 다음 데이터셋으로 넘어간다.
    규칙 평가 오류(잘못된 정규식·SQL 실행 오류·플러그인 오류 등)는 예외가 아니라 그 규칙의
    실패 결과(detail 에 사유)로 기록되고 다음 규칙으로 넘어간다.
    """
    rules = [r for r in api.get_rules(dataset_id) if r.get("is_active") == "true"]
    print(f"[quality] ─ {table} (id={dataset_id}), active rules: {len(rules)}")

    # 전체 행을 메모리에 적재 — 대용량 테이블은 dataset-quality.py(PySpark)를 사용할 것
    try:
        df = pd.read_sql(f"SELECT * FROM {qc.quote_table(table, ds_type)}", conn)
    except Exception:
        safe_rollback(conn)
        raise
    row_count = len(df)
    print(f"[quality]   source rows: {row_count:,}")

    columns = compute_profile(df, row_count)
    col_map = {c["column_name"]: c for c in columns}

    results = []
    passed_count = 0
    for rule in rules:
        passed, actual, detail = evaluate_rule(rule, df, row_count, col_map, conn=conn, checks_dir=checks_dir,
                                         source_tz=source_tz)
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

    score = qc.import_results(api, dataset_id, row_count, columns, results, dry_run)
    return {"table": table, "rows": row_count, "rules": len(rules),
            "passed": passed_count, "score": score}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="카탈로그 데이터셋 품질 배치 (pandas 프로파일링 + 규칙 검증, 소규모 데이터용)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    qc.add_common_args(parser)
    # 원본 DB — 생략 시 카탈로그의 데이터 소스 연결 설정 사용 (비밀번호 제외)
    parser.add_argument("--db-host", default=None, help="원본 DB 호스트 (카탈로그 설정 대신 직접 지정)")
    parser.add_argument("--db-port", type=int, default=None, help="원본 DB 포트 (기본: 타입별 표준)")
    parser.add_argument("--db-name", default=None, help="원본 데이터베이스명")
    parser.add_argument("--db-user", default=None, help="원본 DB 계정")
    parser.add_argument("--db-ssl", action="store_true",
                        help="원본 접속 SSL 강제 (카탈로그 설정의 SSL 토글 대신/추가)")
    qc.add_source_password_args(parser, "db")
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    checks_dir = args.checks_dir or qc.default_checks_dir(__file__)
    source_tz = qc.source_timezone(args)  # 잘못된 값이면 로그인 전에 종료
    api = qc.connect_api(args)

    # 1) 대상 데이터셋 결정
    targets, ds_type, datasource_id = qc.resolve_targets(api, args)
    print(f"[quality] targets: {len(targets)} dataset(s) from {datasource_id} ({ds_type})")

    # 2) 원본 접속 (같은 데이터 소스이므로 커넥션 1개 재사용 — autocommit + 실패 시 rollback 으로 격리)
    conn_params = resolve_connection(args, api, ds_type, datasource_id, targets[0][1].split(".")[0])
    try:
        conn = connect_source(ds_type, conn_params)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — 드라이버 예외 종류가 다양함
        raise SystemExit(f"원본 DB 에 접속하지 못했습니다 ({ds_type} {conn_params['host']}:"
                         f"{conn_params['port']}): {e}") from e

    # 3) 데이터셋별 처리 — 한 건 실패(API 오류 포함)해도 나머지는 계속
    summaries = []
    failures = []
    try:
        for dataset_id, table in targets:
            try:
                summaries.append(process_dataset(api, dataset_id, table, ds_type, conn,
                                                 args.dry_run, checks_dir, source_tz))
            except Exception as e:  # noqa: BLE001 — 배치 연속성 우선 (CatalogApiError 포함)
                safe_rollback(conn)
                failures.append((table, qc.short_error(e)))
                print(f"[quality]   ERROR {table}: {e}", file=sys.stderr)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    # 4) 요약
    return qc.print_summary(started, summaries, failures)


if __name__ == "__main__":
    sys.exit(main())
