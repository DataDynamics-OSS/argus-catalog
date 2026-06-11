#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Argus Catalog 데이터셋 품질 배치 — pandas 프로파일링 + 규칙 검증 (소규모 데이터용).

dataset-quality.py(PySpark)와 동일한 흐름이지만 Spark 클러스터 없이
pandas 만으로 동작한다. 테이블이 메모리에 올라가는 크기(수백만 행 이하)면
이 스크립트로 충분하며, 그 이상은 PySpark 버전을 사용한다.

카탈로그 API 에서 데이터셋과 품질 규칙을 가져와, 원본 RDBMS 를 직접 읽어
프로파일링·검증을 수행하고, 결과를 다시 API 로 반입(import)한다.
프로파일에서 얻은 전체 행 수로 카탈로그의 데이터셋 row_count 도 갱신한다.

실행 예:
    # 단일 데이터셋
    python quality/python-quality.py \\
        --urn sakila-mysql.sakila.actor.dataset \\
        --api-url http://localhost:4600 \\
        --username admin --password '<ADMIN_PASSWORD>'

    # 데이터 소스 전체 — 등록된 모든 데이터셋을 한 번에 처리
    python quality/python-quality.py \\
        --datasource-id sakila-mysql \\
        --api-url http://localhost:4600 \\
        --username admin --password '<ADMIN_PASSWORD>'

의존성:
    pip install pandas
    # + 원본 DB 드라이버 (타입별):
    #   mysql/mariadb: pip install pymysql
    #   postgresql/greenplum: pip install psycopg2-binary
    #   mssql: pip install pymssql
    #   oracle: pip install oracledb

원본 DB 접속 정보:
  - 기본: 카탈로그의 데이터 소스 연결 설정에서 host/port/database/계정을 읽는다.
  - 카탈로그에 연결 설정이 없으면 --db-host/--db-port/--db-user/--db-password
    인자로 직접 지정한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pandas as pd

# ---------------------------------------------------------------------------
# 카탈로그 API 클라이언트 (표준 라이브러리만 사용)
# ---------------------------------------------------------------------------


class CatalogApi:
    """Argus Catalog REST API 의 최소 클라이언트."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base = base_url.rstrip("/") + "/api/v1"
        self.token = self._login(username, password)

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.token}"} if getattr(self, "token", None) else {}),
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise SystemExit(f"API 오류 {e.code} {method} {path}: {body}") from e

    def _login(self, username: str, password: str) -> str:
        self.token = None
        data = self._request("POST", "/auth/login", {"username": username, "password": password})
        return data["access_token"]

    def get_dataset_by_urn(self, urn: str) -> dict:
        return self._request("GET", f"/catalog/datasets/urn/{urllib.parse.quote(urn, safe='')}")

    def get_rules(self, dataset_id: int) -> list[dict]:
        return self._request("GET", f"/quality/rules?dataset_id={dataset_id}")

    def _datasource_pk(self, datasource_id: str) -> int | None:
        """문자열 datasource_id → 숫자 PK (configuration 엔드포인트는 PK 를 받는다)."""
        for ds in self._request("GET", "/catalog/datasources"):
            if ds.get("datasource_id") == datasource_id:
                return ds["id"]
        return None

    def get_datasource_configuration(self, datasource_id: str) -> dict | None:
        pk = self._datasource_pk(datasource_id)
        if pk is None:
            return None
        try:
            data = self._request("GET", f"/catalog/datasources/{pk}/configuration")
            # 응답은 config(객체) — 구버전 호환으로 config_json(문자열)도 지원
            if isinstance(data.get("config"), dict):
                return data["config"]
            return json.loads(data.get("config_json") or "{}")
        except SystemExit:
            return None  # 연결 설정 미등록 — CLI 인자로 폴백

    def import_profile(self, dataset_id: int, payload: dict) -> dict:
        return self._request("POST", f"/quality/datasets/{dataset_id}/profile/import", payload)

    def import_results(self, dataset_id: int, payload: dict) -> dict:
        return self._request("POST", f"/quality/datasets/{dataset_id}/results/import", payload)

    def list_datasets_by_datasource(self, datasource_id: str) -> list[dict]:
        """데이터 소스에 등록된 모든 데이터셋 (페이지네이션 순회)."""
        items: list[dict] = []
        page = 1
        while True:
            data = self._request(
                "GET",
                f"/catalog/datasets?datasource={urllib.parse.quote(datasource_id)}"
                f"&page={page}&page_size=100",
            )
            items.extend(data.get("items", []))
            if page * 100 >= int(data.get("total", 0)):
                return items
            page += 1

    def update_row_count(self, dataset_id: int, row_count: int) -> None:
        """카탈로그 데이터셋의 row_count 갱신 (부분 업데이트)."""
        self._request("PUT", f"/catalog/datasets/{dataset_id}", {"row_count": row_count})


# ---------------------------------------------------------------------------
# 원본 DB 접속 — DBAPI 커넥션 (타입별 드라이버, 지연 임포트)
# ---------------------------------------------------------------------------

DEFAULT_PORTS = {
    "mysql": 3306, "mariadb": 3306, "starrocks": 9030,
    "postgresql": 5432, "greenplum": 5432,
    "mssql": 1433, "sqlserver": 1433, "oracle": 1521, "tibero": 8629,
    "trino": 8080, "hive": 10000, "impala": 21050,
    "redshift": 5439, "snowflake": 443,
}

DRIVER_HINTS = {
    "mysql": "pymysql", "mariadb": "pymysql", "starrocks": "pymysql",
    "postgresql": "psycopg2-binary", "greenplum": "psycopg2-binary",
    "mssql": "pymssql", "sqlserver": "pymssql",
    "oracle": "oracledb", "tibero": "oracledb",
    "trino": "trino", "hive": "impyla", "impala": "impyla",
    "redshift": "redshift_connector", "snowflake": "snowflake-connector-python",
}


def connect_source(ds_type: str, host: str, port: int, database: str,
                   user: str, password: str):
    """타입별 DBAPI 커넥션 생성. 드라이버 미설치 시 설치 안내."""
    try:
        # MySQL 프로토콜 계열 — StarRocks 는 MySQL 와이어 프로토콜(9030) 호환
        if ds_type in ("mysql", "mariadb", "starrocks"):
            import pymysql
            return pymysql.connect(host=host, port=port, user=user,
                                   password=password, database=database)
        # PostgreSQL 프로토콜 계열
        if ds_type in ("postgresql", "greenplum"):
            import psycopg2
            return psycopg2.connect(host=host, port=port, user=user,
                                    password=password, dbname=database)
        if ds_type == "redshift":
            import redshift_connector
            return redshift_connector.connect(host=host, port=port, user=user,
                                              password=password, database=database)
        if ds_type in ("mssql", "sqlserver"):
            import pymssql
            return pymssql.connect(server=host, port=port, user=user,
                                   password=password, database=database)
        if ds_type in ("oracle", "tibero"):
            # Tibero 는 Oracle 호환 — oracledb thin 모드로 접속 가능한 환경 기준
            import oracledb
            return oracledb.connect(user=user, password=password,
                                    dsn=f"{host}:{port}/{database}")
        if ds_type == "trino":
            import trino
            return trino.dbapi.connect(host=host, port=port, user=user,
                                       catalog=database)
        # Hive/Impala — HiveServer2 프로토콜 (impyla)
        if ds_type in ("hive", "impala"):
            from impala.dbapi import connect as impala_connect
            return impala_connect(host=host, port=port, user=user,
                                  password=password or None, database=database)
        if ds_type == "snowflake":
            # host 자리에 Snowflake account 식별자(예: xy12345.ap-northeast-2)를 넣는다
            import snowflake.connector
            return snowflake.connector.connect(account=host, user=user,
                                               password=password, database=database)
    except ImportError as e:
        raise SystemExit(
            f"드라이버가 없습니다: pip install {DRIVER_HINTS.get(ds_type, ds_type)}"
        ) from e
    raise SystemExit(f"지원하지 않는 데이터 소스 타입: {ds_type}")


def resolve_connection(
    args: argparse.Namespace, api: CatalogApi,
    ds_type: str, datasource_id: str, default_database: str,
) -> dict:
    """접속 파라미터 결정. CLI 인자 > 카탈로그 연결 설정."""
    if args.db_host:
        if not (args.db_user and args.db_password is not None):
            raise SystemExit("--db-host 사용 시 --db-user / --db-password 도 필요합니다.")
        return {
            "host": args.db_host,
            "port": args.db_port or DEFAULT_PORTS.get(ds_type, 0),
            "database": args.db_name or default_database,
            "user": args.db_user,
            "password": args.db_password,
        }

    cfg = api.get_datasource_configuration(datasource_id) or {}
    if not cfg.get("host"):
        raise SystemExit(
            "카탈로그에 데이터 소스 연결 설정이 없습니다. "
            "--db-host/--db-user/--db-password 인자로 직접 지정하세요."
        )
    return {
        "host": cfg["host"],
        "port": int(cfg.get("port", DEFAULT_PORTS.get(ds_type, 0))),
        "database": cfg.get("database") or default_database,
        "user": cfg.get("username", ""),
        "password": cfg.get("password", ""),
    }


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
            if pd.api.types.is_numeric_dtype(series):
                mean_v = float(non_null.mean())

        profiles.append({
            "column_name": str(col),
            "column_type": "BINARY" if is_binary else str(series.dtype).upper(),
            "total_count": row_count,
            "null_count": nulls,
            "null_percent": round(nulls / row_count * 100, 2) if row_count else 0.0,
            "unique_count": uniq,
            "unique_percent": round(uniq / row_count * 100, 2) if row_count else 0.0,
            "min_value": min_v,
            "max_value": max_v,
            "mean_value": mean_v,
            "top_values": top_values,
        })
    return profiles




# CUSTOM_SQL 에서 허용하지 않는 키워드 — SELECT 전용 read-only 검증의 2차 방어선.
# (1차 방어선은 원본 접속을 read-only 계정으로 구성하는 것)
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|call|exec|execute)\b",
    re.IGNORECASE,
)


def validate_custom_sql(sql: str) -> str | None:
    """커스텀 SQL 의 안전성 검사. 문제 없으면 None, 있으면 사유 문자열."""
    stripped = re.sub(r"--[^\n]*", "", sql)            # 라인 주석 제거
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)  # 블록 주석 제거
    stripped = stripped.strip().rstrip(";").strip()
    if not stripped:
        return "SQL 이 비어 있습니다"
    if ";" in stripped:
        return "다중 문장(;)은 허용되지 않습니다"
    head = stripped.split(None, 1)[0].lower()
    if head not in ("select", "with"):
        return "SELECT/WITH 로 시작하는 조회 쿼리만 허용됩니다"
    m = _FORBIDDEN_SQL.search(stripped)
    if m:
        return f"허용되지 않는 키워드: {m.group(1)}"
    return None


# ---------------------------------------------------------------------------
# 규칙 평가 — 서버 _evaluate_rule / dataset-quality.py(PySpark)와 동일한 시맨틱.
# ACCEPTED_VALUES / REGEX 는 원본 전체 데이터로 실제 평가한다.
# ---------------------------------------------------------------------------





def _df_samples(sub: "pd.DataFrame", limit: int = 5) -> list[dict] | None:
    """위반 행을 카탈로그 반입용 샘플(dict 목록)로 변환 — 최대 limit 행, 값은 문자열화."""
    if sub is None or len(sub) == 0:
        return None
    rows = []
    for rec in sub.head(limit).to_dict("records"):
        rows.append({k: (None if pd.isna(v) else str(v)) for k, v in rec.items()})
    return rows or None


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
            allowed = [v.strip() for v in expected.split(",")]
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


def run_custom_python(expected: str, df, checks_dir) -> tuple[bool, str, str]:
    """CUSTOM_PYTHON 평가 — quality/custom_checks/ 의 플러그인 함수를 로드해 실행.

    expected_value(JSON) 규약:
        {"module": "rental_checks", "fn": "rental_duration_outlier", "params": {...}}
    함수 규약:
        fn(df, params) -> (passed: bool, actual: str, detail: str)
        df 는 실행 엔진의 DataFrame (pandas 또는 PySpark) — 양쪽을 지원하려면
        함수 안에서 hasattr(df, "toPandas") 로 분기한다.

    보안: 플러그인은 git 으로 리뷰되는 신뢰 코드라는 전제. 모듈/함수 이름은
    식별자만 허용해 경로 탈출을 차단하고, 로드는 checks_dir 내부로 한정한다.
    """
    import importlib.util
    from pathlib import Path

    if not expected:
        return False, "N/A", "expected_value 에 플러그인 설정(JSON)이 필요합니다"
    try:
        cfg = json.loads(expected)
    except json.JSONDecodeError as e:
        return False, "N/A", f"플러그인 설정 JSON 파싱 실패: {e}"
    module = str(cfg.get("module") or "")
    fn_name = str(cfg.get("fn") or "")
    params = cfg.get("params") or {}
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", module):
        return False, "N/A", f"module 이름이 올바르지 않습니다: {module!r}"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", fn_name):
        return False, "N/A", f"fn 이름이 올바르지 않습니다: {fn_name!r}"

    path = Path(checks_dir) / f"{module}.py"
    if not path.is_file():
        return False, "N/A", f"체크 모듈이 없습니다: {path}"
    try:
        spec = importlib.util.spec_from_file_location(f"argus_custom_checks.{module}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001 — 모듈 오류를 결과로 보고
        return False, "N/A", f"체크 모듈 로드 실패: {e}"
    fn = getattr(mod, fn_name, None)
    if not callable(fn):
        return False, "N/A", f"체크 함수가 없습니다: {module}.{fn_name}"

    try:
        result = fn(df, params)
    except Exception as e:  # noqa: BLE001 — 체크 오류를 결과로 보고
        return False, "N/A", f"커스텀 체크 실행 오류: {e}"
    if not (isinstance(result, tuple) and len(result) == 3):
        return False, "N/A", "체크 함수는 (passed, actual, detail) 3-튜플을 반환해야 합니다"
    passed, actual, detail = result
    return bool(passed), str(actual), str(detail)


def evaluate_rule(rule: dict, df: pd.DataFrame, row_count: int,
                  col_map: dict[str, dict], conn=None, checks_dir=None) -> tuple[bool, str, str]:
    """규칙 1건 평가 → (passed, actual_value, detail)."""
    check = rule["check_type"]
    column = rule.get("column_name") or ""
    threshold = float(rule.get("threshold") or 100.0)
    expected = rule.get("expected_value")
    cp = col_map.get(column)

    def need_column() -> tuple[bool, str, str] | None:
        if not cp:
            return False, "N/A", f"프로파일에 '{column}' 컬럼이 없습니다"
        return None

    if check == "NOT_NULL":
        if (err := need_column()):
            return err
        non_null_pct = 100.0 - cp["null_percent"]
        return non_null_pct >= threshold, f"{non_null_pct:.1f}%", \
            f"비-NULL {non_null_pct:.1f}% (임계값 {threshold}%)"

    if check == "UNIQUE":
        if (err := need_column()):
            return err
        return cp["unique_percent"] >= threshold, f"{cp['unique_percent']:.1f}%", \
            f"고유값 {cp['unique_percent']:.1f}% (임계값 {threshold}%)"

    if check == "MIN_VALUE":
        if (err := need_column()) or cp["min_value"] is None:
            return err or (False, "N/A", "최소값을 구할 수 없습니다")
        try:
            actual = float(cp["min_value"])
            exp = float(expected) if expected else 0
            return actual >= exp, str(actual), f"최소값 {actual} (기대 ≥ {exp})"
        except (ValueError, TypeError):
            return False, str(cp["min_value"]), f"숫자 비교 불가: {cp['min_value']}"

    if check == "MAX_VALUE":
        if (err := need_column()) or cp["max_value"] is None:
            return err or (False, "N/A", "최대값을 구할 수 없습니다")
        try:
            actual = float(cp["max_value"])
            exp = float(expected) if expected else 0
            return actual <= exp, str(actual), f"최대값 {actual} (기대 ≤ {exp})"
        except (ValueError, TypeError):
            return False, str(cp["max_value"]), f"숫자 비교 불가: {cp['max_value']}"

    if check == "ROW_COUNT":
        try:
            exp = int(expected) if expected else 0
            return row_count >= exp, str(row_count), f"행 수 {row_count} (기대 ≥ {exp})"
        except (ValueError, TypeError):
            return False, str(row_count), f"잘못된 기대값: {expected}"

    if check == "ACCEPTED_VALUES":
        # 서버는 profile-only 로 평가를 건너뛰지만 여기서는 실제 데이터로 검사한다.
        if (err := need_column()):
            return err
        accepted = [v.strip() for v in (expected or "").split(",") if v.strip()]
        if not accepted:
            return False, "N/A", "expected_value 에 허용 값 목록(쉼표 구분)이 필요합니다"
        series = df[column]
        bad = int((series.notna() & ~series.astype(str).isin(accepted)).sum())
        return bad == 0, f"위반 {bad}행", \
            f"허용 값 {accepted} 기준 위반 {bad}행 (전체 데이터 검사)"

    if check == "REGEX":
        # 서버는 profile-only 로 평가를 건너뛰지만 여기서는 실제 데이터로 검사한다.
        # PySpark rlike(부분 일치)와 동일하게 re.search 시맨틱(contains)을 사용한다.
        if (err := need_column()):
            return err
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
        reason = validate_custom_sql(expected)
        if reason:
            return False, "N/A", f"SQL 검증 실패: {reason}"
        if conn is None:
            return False, "N/A", "원본 DB 커넥션이 없어 커스텀 SQL 을 실행할 수 없습니다"
        try:
            result = pd.read_sql(expected, conn)
        except Exception as e:  # noqa: BLE001 — 쿼리 오류를 결과로 보고
            return False, "N/A", f"커스텀 SQL 실행 오류: {e}"
        if result.empty or result.shape[1] == 0:
            return False, "N/A", "커스텀 SQL 이 결과를 반환하지 않았습니다 (첫 행 첫 컬럼 = 위반 건수 규약)"
        try:
            bad = int(result.iat[0, 0])
        except (TypeError, ValueError):
            return False, str(result.iat[0, 0]), "커스텀 SQL 의 첫 컬럼이 숫자(위반 건수)가 아닙니다"
        return bad == 0, f"위반 {bad}행", f"커스텀 SQL 기준 위반 {bad}행 (전체 데이터 검사)"

    if check == "CUSTOM_PYTHON":
        return run_custom_python(expected, df, checks_dir)

    if check == "FRESHNESS":
        # 외부 배치는 지금 막 원본을 읽었으므로 데이터 나이 = 0h 로 평가한다.
        try:
            max_hours = float(expected) if expected else 24
            return True, "0.0h", f"데이터 나이 0.0시간 — 원본 직접 조회 (최대 {max_hours}시간)"
        except (ValueError, TypeError):
            return False, "N/A", f"잘못된 기대값: {expected}"

    return False, "N/A", f"알 수 없는 검증 유형: {check}"


# ---------------------------------------------------------------------------
# 데이터셋 1건 처리
# ---------------------------------------------------------------------------


def process_dataset(api: CatalogApi, dataset_id: int, table: str,
                    conn, dry_run: bool, checks_dir) -> dict:
    """데이터셋 1건: 원본 읽기 → 프로파일 → 규칙 평가 → 반입 → row_count 갱신."""
    rules = [r for r in api.get_rules(dataset_id) if r.get("is_active") == "true"]
    print(f"[quality] ─ {table} (id={dataset_id}), active rules: {len(rules)}")

    # 전체 행을 메모리에 적재 — 대용량 테이블은 dataset-quality.py(PySpark)를 사용할 것
    df = pd.read_sql(f"SELECT * FROM {table}", conn)
    row_count = len(df)
    print(f"[quality]   source rows: {row_count:,}")

    columns = compute_profile(df, row_count)
    col_map = {c["column_name"]: c for c in columns}

    results = []
    passed_count = 0
    for rule in rules:
        passed, actual, detail = evaluate_rule(rule, df, row_count, col_map, conn=conn, checks_dir=checks_dir)
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
        print(f"  [{mark}] {rule['rule_name']} ({rule['check_type']}"
              f"{':' + rule['column_name'] if rule.get('column_name') else ''}) — {detail}{sample_note}")

    score = None
    if dry_run:
        print("[quality]   --dry-run: API 반입 생략")
    else:
        api.import_profile(dataset_id, {"row_count": row_count, "columns": columns})
        # 프로파일에서 얻은 전체 행 수로 카탈로그 데이터셋의 row_count 도 갱신
        api.update_row_count(dataset_id, row_count)
        if results:
            summary = api.import_results(dataset_id, {"results": results})
            score = summary["score"]
            print(f"[quality]   imported: score={summary['score']}%, "
                  f"passed={summary['passed']}/{summary['total_rules']}, row_count updated")
        else:
            print("[quality]   활성 규칙이 없어 프로파일·row_count 만 반입했습니다")

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
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--urn", help="단일 데이터셋 URN (예: sakila-mysql.sakila.actor.dataset)")
    target.add_argument("--datasource-id",
                        help="데이터 소스 ID — 등록된 모든 데이터셋을 일괄 처리 (예: sakila-mysql)")
    parser.add_argument("--api-url", default="http://localhost:4600",
                        help="카탈로그 API 서버 주소")
    parser.add_argument("--username", required=True, help="관리자 계정")
    parser.add_argument("--password", required=True, help="관리자 비밀번호")
    # 원본 DB — 생략 시 카탈로그의 데이터 소스 연결 설정 사용
    parser.add_argument("--db-host", default=None, help="원본 DB 호스트 (카탈로그 설정 대신 직접 지정)")
    parser.add_argument("--db-port", type=int, default=None, help="원본 DB 포트 (기본: 타입별 표준)")
    parser.add_argument("--db-name", default=None, help="원본 데이터베이스명")
    parser.add_argument("--db-user", default=None, help="원본 DB 계정")
    parser.add_argument("--db-password", default=None, help="원본 DB 비밀번호")
    parser.add_argument("--checks-dir", default=None,
                        help="CUSTOM_PYTHON 체크 모듈 디렉터리 (기본: 스크립트 위치/custom_checks)")
    parser.add_argument("--dry-run", action="store_true",
                        help="API 반입 없이 프로파일/검증 결과만 출력")
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    from pathlib import Path as _Path
    checks_dir = args.checks_dir or str(_Path(__file__).resolve().parent / "custom_checks")
    api = CatalogApi(args.api_url, args.username, args.password)

    # 1) 대상 데이터셋 결정
    if args.urn:
        d = api.get_dataset_by_urn(args.urn)
        targets = [(d["id"], d["name"])]
        ds_type = d["datasource"]["type"]
        datasource_id = d["datasource"]["datasource_id"]
    else:
        items = api.list_datasets_by_datasource(args.datasource_id)
        if not items:
            raise SystemExit(f"데이터 소스 '{args.datasource_id}' 에 등록된 데이터셋이 없습니다.")
        targets = [(it["id"], it["name"]) for it in items]
        ds_type = items[0]["datasource_type"]
        datasource_id = args.datasource_id
    print(f"[quality] targets: {len(targets)} dataset(s) from {datasource_id} ({ds_type})")

    # 2) 원본 접속 (같은 데이터 소스이므로 커넥션 1개 재사용)
    conn_params = resolve_connection(args, api, ds_type, datasource_id, targets[0][1].split(".")[0])
    conn = connect_source(ds_type, **conn_params)

    # 3) 데이터셋별 처리 — 한 건 실패해도 나머지는 계속
    summaries = []
    failures = []
    try:
        for dataset_id, table in targets:
            try:
                summaries.append(process_dataset(api, dataset_id, table, conn, args.dry_run, checks_dir))
            except SystemExit:
                raise
            except Exception as e:  # noqa: BLE001 — 배치 연속성 우선
                failures.append((table, str(e)))
                print(f"[quality]   ERROR {table}: {e}", file=sys.stderr)
    finally:
        conn.close()

    # 4) 요약
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\n[quality] ==== 요약 ({elapsed:.1f}s) ====")
    for sm in summaries:
        score = f"{sm['score']}%" if sm["score"] is not None else "-"
        print(f"  {sm['table']}: rows={sm['rows']:,}, rules={sm['passed']}/{sm['rules']}, score={score}")
    if failures:
        print(f"  실패 {len(failures)}건:")
        for table, err in failures:
            print(f"    {table}: {err}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
