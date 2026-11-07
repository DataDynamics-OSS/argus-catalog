# SPDX-License-Identifier: Apache-2.0
"""Argus Catalog 품질 배치 공통 모듈 — python-quality.py(pandas) / dataset-quality.py(PySpark) 공용.

엔진과 무관한 부분만 모았다: 카탈로그 API 클라이언트, 비밀번호·접속 설정 결정,
식별자 인용, 규칙 판정(임계값·정확한 건수·FRESHNESS), CUSTOM_SQL 가드,
CUSTOM_PYTHON 로더, 공통 CLI 인자, 요약 출력.

표준 라이브러리만 사용한다(pandas/pyspark 임포트 금지) — 어디서든 단위 테스트할 수 있도록::

    cd quality && python -m pytest -q tests

spark-submit 으로 실행할 때는 이 파일을 함께 배포한다(클러스터 모드에서는 필수)::

    spark-submit --py-files quality/quality_common.py quality/dataset-quality.py ...

두 스크립트가 공유하는 동작:
  - 비밀번호: 카탈로그 계정은 ``--password-file`` / 환경변수 ``ARGUS_PASSWORD`` / ``--password``,
    원본 DB 는 ``--{db,jdbc}-password-file`` / 환경변수 ``ARGUS_SOURCE_DB_PASSWORD`` / ``--{db,jdbc}-password``.
    우선순위는 명령행 값 > 파일 > 환경변수. 카탈로그는 원본 비밀번호를 내려주지 않는다(마스킹).
  - 원본 접속: 기본은 카탈로그 연결 설정(``datasources.connection`` 권한 필요, 비밀번호 제외).
    권한이 없으면 403 안내와 함께 종료 — 직접 접속 인자(``--db-host``/``--jdbc-url``)로 지정한다.
  - 반입(프로파일·row_count·결과)은 관리자 전용 API 다. ``--dry-run`` 은 반입을 건너뛰므로
    조회 API 만 쓰며 일반 계정으로도 실행할 수 있다(원본 접속은 직접 지정).
  - 데이터셋 단위 실패(원본 읽기·API 오류)는 기록하고 계속하며, 실패가 있으면 exit code 1.
  - FRESHNESS 의 시간대 없는 시각 값(MySQL DATETIME 등)은 ``--source-timezone`` / 환경변수
    ``ARGUS_SOURCE_TIMEZONE`` (기본 Asia/Seoul)의 벽시계 시각으로 해석한다 — 배치 호스트 시간대와 무관.
    서버 설정 ``quality_source_timezone``(기본 Asia/Seoul)과 같게 두면 두 엔진 판정이 일치한다.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ---------------------------------------------------------------------------
# 카탈로그 API 클라이언트
# ---------------------------------------------------------------------------

DEFAULT_API_TIMEOUT = 60.0


class CatalogApiError(Exception):
    """카탈로그 API 호출 실패. status 가 None 이면 네트워크/타임아웃 오류."""

    def __init__(self, method: str, path: str, status: int | None, body: str) -> None:
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        where = f"{method} {path}"
        if status is None:
            super().__init__(f"API 호출에 실패했습니다 ({where}): {body}")
        else:
            super().__init__(f"API 오류 {status} {where}: {body}")


class CatalogApi:
    """Argus Catalog REST API 의 최소 클라이언트.

    - 모든 요청에 timeout 을 건다(기본 60초).
    - access token 이 만료되어 401 이 오면 refresh token 으로 1회 재발급
      (실패하면 저장된 자격증명으로 재로그인) 후 같은 요청을 1회 재시도한다.
    - 실패는 :class:`CatalogApiError` 로 올린다 — 호출부가 데이터셋 단위로 기록하고 계속한다.
    """

    def __init__(self, base_url: str, username: str, password: str,
                 timeout: float = DEFAULT_API_TIMEOUT,
                 urlopen: Callable[..., Any] | None = None) -> None:
        self.base = base_url.rstrip("/") + "/api/v1"
        self.timeout = timeout
        self._urlopen = urlopen or urllib.request.urlopen
        self._username = username
        self._password = password
        self.token: str | None = None
        self.refresh_token: str | None = None
        self._login()

    # -- 저수준 ---------------------------------------------------------------

    def _send(self, method: str, path: str, payload: dict | None, auth: bool) -> Any:
        headers = {"Content-Type": "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with self._urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode(errors="replace")
            except Exception:  # noqa: BLE001 — 본문 없는 오류 응답
                body = ""
            raise CatalogApiError(method, path, e.code, body) from e
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", None) or e
            raise CatalogApiError(method, path, None, str(reason)) from e
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError as e:
            raise CatalogApiError(method, path, None, f"JSON 이 아닌 응답: {raw[:200]!r}") from e

    def _store_tokens(self, data: Any) -> None:
        if not isinstance(data, dict) or not data.get("access_token"):
            raise CatalogApiError("POST", "/auth/login", None, "응답에 access_token 이 없습니다")
        self.token = data["access_token"]
        self.refresh_token = data.get("refresh_token") or self.refresh_token

    def _login(self) -> None:
        self.token = None
        data = self._send("POST", "/auth/login",
                          {"username": self._username, "password": self._password}, auth=False)
        self._store_tokens(data)

    def _reauthenticate(self) -> None:
        """access token 재발급 — refresh 엔드포인트 우선, 실패 시 재로그인."""
        if self.refresh_token:
            try:
                data = self._send("POST", "/auth/refresh",
                                  {"refresh_token": self.refresh_token}, auth=False)
                self._store_tokens(data)
                return
            except CatalogApiError:
                pass  # refresh 만료/미지원 — 재로그인으로 폴백
        self._login()

    def request(self, method: str, path: str, payload: dict | None = None) -> Any:
        """인증 요청 — 401 이면 토큰을 1회 재발급하고 같은 요청을 1회 재시도한다."""
        try:
            return self._send(method, path, payload, auth=True)
        except CatalogApiError as e:
            if e.status != 401:
                raise
        # 401 — 토큰 만료로 보고 1회 재인증 후 재시도
        self._reauthenticate()
        return self._send(method, path, payload, auth=True)

    # -- 엔드포인트 -----------------------------------------------------------

    def get_dataset_by_urn(self, urn: str) -> dict:
        return self.request("GET", f"/catalog/datasets/urn/{urllib.parse.quote(urn, safe='')}")

    def get_rules(self, dataset_id: int) -> list[dict]:
        return self.request("GET", f"/quality/rules?dataset_id={dataset_id}")

    def datasource_pk(self, datasource_id: str) -> int | None:
        """문자열 datasource_id → 숫자 PK (configuration 엔드포인트는 PK 를 받는다).

        ``GET /catalog/datasources`` 는 로그인 사용자면 조회할 수 있다.
        """
        for ds in self.request("GET", "/catalog/datasources"):
            if ds.get("datasource_id") == datasource_id:
                return ds["id"]
        return None

    def get_datasource_configuration(self, datasource_id: str) -> tuple[dict, list[str]] | None:
        """(config, secret_fields). 데이터 소스/설정이 없으면 None.

        config 의 비밀값(password 등)은 빈 문자열로 마스킹되어 오고, secret_fields 가
        "저장돼 있음"만 알린다. 403(datasources.connection 권한 없음 — 미설정이면 admin 전용) 등
        그 밖의 오류는 CatalogApiError 로 올린다.
        """
        pk = self.datasource_pk(datasource_id)
        if pk is None:
            return None
        try:
            data = self.request("GET", f"/catalog/datasources/{pk}/configuration")
        except CatalogApiError as e:
            if e.status == 404:
                return None
            raise
        # 응답은 config(객체) — 구버전 호환으로 config_json(문자열)도 지원
        if isinstance(data.get("config"), dict):
            cfg = data["config"]
        else:
            cfg = json.loads(data.get("config_json") or "{}")
        return cfg, list(data.get("secret_fields") or [])

    def import_profile(self, dataset_id: int, payload: dict) -> dict:
        return self.request("POST", f"/quality/datasets/{dataset_id}/profile/import", payload)

    def import_results(self, dataset_id: int, payload: dict) -> dict:
        return self.request("POST", f"/quality/datasets/{dataset_id}/results/import", payload)

    def list_datasets_by_datasource(self, datasource_id: str) -> list[dict]:
        """데이터 소스에 등록된 모든 데이터셋 (페이지네이션 순회)."""
        items: list[dict] = []
        page = 1
        while True:
            data = self.request(
                "GET",
                f"/catalog/datasets?datasource={urllib.parse.quote(datasource_id)}"
                f"&page={page}&page_size=100",
            )
            items.extend(data.get("items", []))
            if page * 100 >= int(data.get("total", 0)):
                return items
            page += 1

    def update_row_count(self, dataset_id: int, row_count: int) -> None:
        """카탈로그 데이터셋의 row_count 갱신 (부분 업데이트 — 데이터셋 소유자 또는 관리자)."""
        self.request("PUT", f"/catalog/datasets/{dataset_id}", {"row_count": row_count})


# ---------------------------------------------------------------------------
# 비밀번호 결정 — CLI 값 > 파일 > 환경변수
# ---------------------------------------------------------------------------

ADMIN_PASSWORD_ENV = "ARGUS_PASSWORD"
SOURCE_PASSWORD_ENV = "ARGUS_SOURCE_DB_PASSWORD"


def read_secret(value: str | None, file_path: str | None, env_name: str,
                env: dict | None = None) -> str | None:
    """비밀값 결정. 우선순위: CLI 값 > 파일(끝의 개행 제거) > 환경변수. 없으면 None."""
    if value is not None:
        return value
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as e:
            raise SystemExit(f"비밀번호 파일을 읽지 못했습니다: {file_path} ({e})") from e
    environ = os.environ if env is None else env
    if env_name in environ:
        return environ[env_name]
    return None


def resolve_admin_password(args: argparse.Namespace, env: dict | None = None) -> str:
    """카탈로그 계정 비밀번호 — --password > --password-file > 환경변수 ARGUS_PASSWORD. 없으면 종료."""
    password = read_secret(args.password, args.password_file, ADMIN_PASSWORD_ENV, env)
    if password is None:
        raise SystemExit(
            "카탈로그 계정 비밀번호를 확인하지 못했습니다. --password-file <파일> 또는 "
            f"환경변수 {ADMIN_PASSWORD_ENV} 로 전달하세요 (--password 도 가능하나 프로세스 목록에 노출됩니다)."
        )
    return password


def source_password_hint(flag: str) -> str:
    """원본 비밀번호 전달 방법 안내 문구 (flag: 'db' 또는 'jdbc')."""
    return (f"--{flag}-password-file <파일> 또는 환경변수 {SOURCE_PASSWORD_ENV} 로 전달하세요 "
            f"(--{flag}-password 도 가능하나 프로세스 목록에 노출됩니다)")


# ---------------------------------------------------------------------------
# 원본 접속 설정 — 카탈로그 설정(비밀값 제외) + 배치 쪽 비밀번호
# ---------------------------------------------------------------------------

DEFAULT_PORTS = {
    "mysql": 3306, "mariadb": 3306, "starrocks": 9030,
    "postgresql": 5432, "greenplum": 5432,
    "mssql": 1433, "sqlserver": 1433, "oracle": 1521, "tibero": 8629,
    "trino": 8080, "hive": 10000, "impala": 21050,
    "redshift": 5439, "snowflake": 443,
}

_TRUE = ("true", "1", "yes", "on")


def ssl_setting(cfg: dict) -> bool | None:
    """카탈로그 설정의 SSL 토글 → True/False, 설정 항목이 없으면 None.

    키: RDBMS 계열 ``ssl_enabled``, Trino/Impala ``use_ssl`` (프론트 datasource-configs.ts).
    서버 ``connection_ssl`` 과 같이 켜져 있으면 암호화만 강제(서버 인증서 미검증)한다.
    """
    for key in ("ssl_enabled", "use_ssl"):
        if key in cfg and cfg[key] not in (None, ""):
            v = cfg[key]
            return v is True or str(v).strip().lower() in _TRUE
    return None


def _password_key(cfg: dict, secret_fields: list[str]) -> str:
    """원본 접속 비밀번호 키 — Impala LDAP 은 ldap_password, 그 외 password."""
    if str(cfg.get("auth_type") or "").upper() == "LDAP":
        return "ldap_password"
    if "password" not in cfg and "ldap_password" in secret_fields:
        return "ldap_password"
    return "password"


def source_from_config(cfg: dict, secret_fields: list[str], ds_type: str,
                       default_database: str, password: str | None, flag: str) -> dict:
    """카탈로그 연결 설정 + 배치 비밀번호 → 접속 파라미터.

    카탈로그는 비밀번호를 마스킹("")하고 ``secret_fields`` 로 저장 여부만 알린다 —
    저장된 비밀번호가 있는데 배치에 비밀번호가 주어지지 않았으면(password=None) 종료한다.
    저장된 비밀번호가 없으면 설정 값(마스킹 이전 서버의 평문 응답) 또는 빈 비밀번호로 접속한다.
    host 가 없으면(예: Hive) 직접 지정 안내와 함께 종료한다.
    """
    host = cfg.get("host") or cfg.get("account")  # Snowflake 는 account 식별자
    if not host:
        direct = "--db-host/--db-user" if flag == "db" else "--jdbc-url/--jdbc-user"
        raise SystemExit(
            "카탈로그 연결 설정에 host 가 없어 원본 접속 정보를 정하지 못했습니다. "
            f"{direct} 인자로 직접 지정하세요 — 비밀번호는 {source_password_hint(flag)}."
        )
    pw_key = _password_key(cfg, secret_fields)
    if password is None:
        if pw_key in secret_fields:
            raise SystemExit(
                "원본 DB 비밀번호를 확인하지 못했습니다. 카탈로그는 저장된 비밀번호를 내려주지 않으므로"
                f"(secret_fields: {pw_key}) 배치에 직접 전달해야 합니다 — {source_password_hint(flag)}."
            )
        # 마스킹 이전 서버(평문 응답) 호환, 또는 비밀번호 없는 계정("")
        password = str(cfg.get(pw_key) or "")
    try:
        port = int(cfg.get("port") or DEFAULT_PORTS.get(ds_type, 0))
    except (TypeError, ValueError):
        raise SystemExit(f"카탈로그 연결 설정의 포트가 올바르지 않습니다: {cfg.get('port')!r}") from None
    user = cfg.get("username") or cfg.get("ldap_username") or ""
    return {
        "host": str(host),
        "port": port,
        "database": cfg.get("database") or cfg.get("catalog") or default_database,
        "user": str(user),
        "password": password,
        "ssl": ssl_setting(cfg),
        # 타입별 부가 항목 (있을 때만 사용)
        "schema": cfg.get("schema"),
        "warehouse": cfg.get("warehouse"),
        "role": cfg.get("role"),
        "auth_type": cfg.get("auth_type"),
    }


def fetch_source_config(api: CatalogApi, datasource_id: str, flag: str) -> tuple[dict, list[str]]:
    """카탈로그에서 원본 연결 설정을 가져온다. 권한·미등록은 안내 메시지와 함께 종료."""
    direct = ("--db-host/--db-user 와 --db-password-file(또는 환경변수 "
              f"{SOURCE_PASSWORD_ENV})" if flag == "db" else
              f"--jdbc-url/--jdbc-user 와 --jdbc-password-file(또는 환경변수 {SOURCE_PASSWORD_ENV})")
    try:
        got = api.get_datasource_configuration(datasource_id)
    except CatalogApiError as e:
        if e.status == 403:
            raise SystemExit(
                "카탈로그 연결 설정을 조회하지 못했습니다(403). 배치 계정에 datasources.connection "
                f"권한이 필요하거나, {direct} 로 원본 접속을 직접 지정하세요."
            ) from e
        raise SystemExit(f"카탈로그 연결 설정을 조회하지 못했습니다: {e}") from e
    if got is None:
        raise SystemExit(
            f"카탈로그에 데이터 소스 '{datasource_id}' 의 연결 설정이 없습니다. {direct} 로 직접 지정하세요."
        )
    return got


# ---------------------------------------------------------------------------
# 식별자 인용 — 방언별
# ---------------------------------------------------------------------------

_BACKTICK = ("`", "`")
_DOUBLE = ('"', '"')
_BRACKET = ("[", "]")

QUOTE_STYLES = {
    "mysql": _BACKTICK, "mariadb": _BACKTICK, "starrocks": _BACKTICK,
    "hive": _BACKTICK, "impala": _BACKTICK,
    "postgresql": _DOUBLE, "greenplum": _DOUBLE, "redshift": _DOUBLE,
    "oracle": _DOUBLE, "tibero": _DOUBLE, "trino": _DOUBLE, "snowflake": _DOUBLE,
    "mssql": _BRACKET, "sqlserver": _BRACKET,
}


def quote_ident(name: str, ds_type: str) -> str:
    """식별자 1개 인용 — 닫는 인용 문자는 두 번 써서 이스케이프. 미지원 타입은 ANSI 큰따옴표."""
    open_q, close_q = QUOTE_STYLES.get(ds_type, _DOUBLE)
    return open_q + str(name).replace(close_q, close_q * 2) + close_q


def quote_table(name: str, ds_type: str) -> str:
    """점 구분 데이터셋 이름("db.table" / "schema.table") → 부분별 인용한 테이블 참조.

    원본 카탈로그에 저장된 대소문자 그대로 인용하므로 Oracle/Snowflake 의 대소문자도 보존된다.
    """
    return ".".join(quote_ident(part, ds_type) for part in str(name).split("."))


# ---------------------------------------------------------------------------
# CUSTOM_SQL 가드
# ---------------------------------------------------------------------------

# CUSTOM_SQL 에서 허용하지 않는 키워드 — SELECT 전용 read-only 검증의 2차 방어선.
# (1차 방어선은 원본 접속을 read-only 계정으로 구성하는 것)
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|call|exec|execute)\b",
    re.IGNORECASE,
)


def strip_sql(sql: str) -> str:
    """주석 제거 + 끝의 ; 제거."""
    stripped = re.sub(r"--[^\n]*", "", sql)            # 라인 주석 제거
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)  # 블록 주석 제거
    return stripped.strip().rstrip(";").strip()


def validate_custom_sql(sql: str) -> str | None:
    """커스텀 SQL 의 안전성 검사. 문제 없으면 None, 있으면 사유 문자열."""
    stripped = strip_sql(sql or "")
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


def parse_violation_count(value: Any) -> tuple[bool, str, str]:
    """CUSTOM_SQL 결과 첫 행 첫 컬럼(위반 건수) → 판정."""
    try:
        bad = int(value)
    except (TypeError, ValueError):
        return False, str(value), "커스텀 SQL 의 첫 컬럼이 숫자(위반 건수)가 아닙니다"
    return bad == 0, f"위반 {bad}행", f"커스텀 SQL 기준 위반 {bad}행 (전체 데이터 검사)"


# ---------------------------------------------------------------------------
# 규칙 판정 헬퍼 — 서버 app/quality/service.py::_evaluate_rule 과 같은 시맨틱
# ---------------------------------------------------------------------------


def rule_threshold(rule: dict) -> float:
    """임계값(%) — 미지정이면 100. 0 은 0 으로 존중한다."""
    t = rule.get("threshold")
    if t is None or (isinstance(t, str) and not t.strip()):
        return 100.0
    return float(t)


def percent(count: int, total: int) -> float:
    """표시용 백분율(소수 2자리) — 서버 프로파일과 같은 반올림."""
    return round(count / total * 100, 2) if total else 0.0


def _fmt_pct(pct: float, threshold: float, passed: bool) -> str:
    """표시값이 반올림 때문에 판정과 어긋나 보이지 않도록 자릿수를 조정한다."""
    text = f"{pct:.1f}"
    if (float(text) >= threshold) == passed:
        return text
    # 예: 99.9995% → "100.0" 이지만 임계값 100 미달 — 실패면 내림, 통과면 올림으로 표시
    scale = 10 ** 4
    v = math.floor(pct * scale) / scale if not passed else math.ceil(pct * scale) / scale
    return f"{v:.4f}"


def decide_not_null(null_count: int, row_count: int, threshold: float) -> tuple[bool, str, str]:
    """비-NULL 비율 판정 — 정확한 건수로 비교(non_null*100 >= threshold*rows).

    빈 테이블은 서버와 같이 비-NULL 100% 로 본다.
    """
    if row_count <= 0:
        pct, passed = 100.0, 100.0 >= threshold
        counts = "0행"
    else:
        non_null = row_count - null_count
        passed = non_null * 100 >= threshold * row_count
        pct = non_null / row_count * 100
        counts = f"{non_null:,}/{row_count:,}행"
    shown = _fmt_pct(pct, threshold, passed)
    return passed, f"{shown}%", f"비-NULL {shown}% ({counts}, 임계값 {threshold}%)"


def decide_unique(unique_count: int, row_count: int, threshold: float) -> tuple[bool, str, str]:
    """고유값 비율 판정 — 정확한 건수로 비교(unique*100 >= threshold*rows).

    빈 테이블은 서버와 같이 고유 0% 로 본다(임계값 0 일 때만 통과).
    """
    if row_count <= 0:
        pct, passed = 0.0, 0.0 >= threshold
        counts = "0행"
    else:
        passed = unique_count * 100 >= threshold * row_count
        pct = unique_count / row_count * 100
        counts = f"{unique_count:,}/{row_count:,}행"
    shown = _fmt_pct(pct, threshold, passed)
    return passed, f"{shown}%", f"고유값 {shown}% ({counts}, 임계값 {threshold}%)"


def decide_min_max(check: str, value: Any, expected: Any) -> tuple[bool, str, str]:
    """MIN_VALUE(min ≥ 기대) / MAX_VALUE(max ≤ 기대)."""
    label = "최소값" if check == "MIN_VALUE" else "최대값"
    if value is None:
        return False, "N/A", f"{label}을 구할 수 없습니다"
    try:
        actual = float(value)
        exp = float(expected) if expected not in (None, "") else 0.0
    except (ValueError, TypeError):
        return False, str(value), f"숫자 비교 불가: {value}"
    if check == "MIN_VALUE":
        return actual >= exp, str(actual), f"최소값 {actual} (기대 ≥ {exp})"
    return actual <= exp, str(actual), f"최대값 {actual} (기대 ≤ {exp})"


def decide_row_count(row_count: int, expected: Any) -> tuple[bool, str, str]:
    """ROW_COUNT — 전체 행 수 ≥ 기대값(비어 있으면 0)."""
    try:
        exp = int(expected) if expected not in (None, "") else 0
    except (ValueError, TypeError):
        return False, str(row_count), f"잘못된 기대값: {expected}"
    return row_count >= exp, str(row_count), f"행 수 {row_count} (기대 ≥ {exp})"


DEFAULT_SOURCE_TIMEZONE = "Asia/Seoul"
SOURCE_TIMEZONE_ENV = "ARGUS_SOURCE_TIMEZONE"
_OFFSET_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


def resolve_timezone(name: str | None) -> tzinfo:
    """시간대 이름 → tzinfo. IANA 이름(``Asia/Seoul``·``UTC``) 또는 고정 오프셋(``+09:00``).

    서버 ``app.quality.service.resolve_timezone`` 과 같은 규칙. 빈 값·알 수 없는 이름은 ``ValueError``.
    """
    text = (name or "").strip()
    if not text:
        raise ValueError("시간대가 비어 있습니다")
    m = _OFFSET_RE.match(text)
    if m:
        sign, hh, mm = m.groups()
        if int(hh) > 23 or int(mm) > 59:
            raise ValueError(f"잘못된 시간대 오프셋: {text}")
        delta = timedelta(hours=int(hh), minutes=int(mm))
        return timezone(-delta if sign == "-" else delta)
    if text.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"알 수 없는 시간대: {text} (예: Asia/Seoul, UTC, +09:00)") from e


def _default_tzinfo() -> tzinfo:
    try:
        return resolve_timezone(DEFAULT_SOURCE_TIMEZONE)
    except ValueError:  # tzdata 없는 환경 — 한국 표준시는 일광 절약 시간이 없어 고정 오프셋과 같다
        return timezone(timedelta(hours=9))


DEFAULT_SOURCE_TZINFO = _default_tzinfo()


def source_timezone(args: argparse.Namespace, env: dict | None = None) -> tzinfo:
    """원천 데이터 시간대 — ``--source-timezone`` > 환경변수 ``ARGUS_SOURCE_TIMEZONE`` > 기본 Asia/Seoul.

    해석할 수 없는 값이면 배치 시작 전에 종료한다.
    """
    environ = os.environ if env is None else env
    name = getattr(args, "source_timezone", None) or environ.get(SOURCE_TIMEZONE_ENV) or DEFAULT_SOURCE_TIMEZONE
    try:
        return resolve_timezone(name)
    except ValueError as e:
        raise SystemExit(f"--source-timezone: {e}") from e


def parse_timestamp(value: Any, source_tz: tzinfo | None = None) -> datetime | None:
    """데이터 시각 값 → UTC aware datetime. 해석 불가면 None.

    서버 ``_parse_timestamp`` 와 같은 규칙 — 시간대 없는 값은 **원천 데이터 시간대**(``source_tz``,
    기본 Asia/Seoul)의 벽시계 시각으로 본다(배치 호스트의 로컬 시간대에 의존하지 않음).
    시간대가 있는 값은 그 시간대 그대로. datetime/date 객체(pandas Timestamp 포함)와 ISO 문자열을 받는다.
    """
    if value is None:
        return None
    if hasattr(value, "to_pydatetime"):  # pandas Timestamp
        try:
            value = value.to_pydatetime()
        except Exception:  # noqa: BLE001 — NaT 등
            return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=source_tz or DEFAULT_SOURCE_TZINFO)
        return dt.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def decide_freshness(expected: Any, column: str | None, latest: Any, *,
                     column_present: bool = True,
                     now: datetime | None = None,
                     source_tz: tzinfo | None = None) -> tuple[bool, str, str]:
    """FRESHNESS 판정.

    - column 지정: 실제 데이터의 그 컬럼 최대값(=최신 데이터 시각) 기준 경과 시간 ≤ 기대(시간, 기본 24).
      값이 모두 NULL 이거나 시각으로 해석할 수 없으면 실패. 미래 시각은 0h.
      시간대 없는 값은 ``source_tz``(원천 데이터 시간대, 기본 Asia/Seoul)로 해석한다.
    - column 미지정: 데이터 시각을 알 수 없으므로 배치가 방금 원본을 읽은 것을 기준으로 0h 로 통과한다
      (서버 검증은 같은 경우 마지막 프로파일 이후 경과 시간으로 판정).
    """
    try:
        max_hours = float(expected) if expected not in (None, "") else 24.0
    except (ValueError, TypeError):
        return False, "N/A", f"잘못된 기대값: {expected}"
    if not column:
        return True, "0.0h", (f"데이터 나이 0.0시간 — 원본 직접 조회 (최대 {max_hours}시간). "
                              "시각 컬럼을 지정하면 데이터의 최신 시각 기준으로 평가합니다")
    if not column_present:
        return False, "N/A", f"데이터에 '{column}' 컬럼이 없습니다"
    latest_dt = parse_timestamp(latest, source_tz)
    if latest_dt is None:
        return False, "N/A", f"'{column}' 의 최대값을 시각으로 해석할 수 없습니다: {latest}"
    now = now or datetime.now(timezone.utc)
    # 미래 시각(시간대 차이 등)은 0 으로 본다
    age_hours = max(0.0, (now - latest_dt).total_seconds() / 3600)
    passed = age_hours <= max_hours
    return passed, f"{age_hours:.1f}h", (
        f"최신 데이터({column} 최대값 {latest}) 이후 {age_hours:.1f}시간 (최대 {max_hours}시간)")


def short_error(e: BaseException, limit: int = 500) -> str:
    """예외 메시지 요약 — Py4J/JDBC 의 Java 스택트레이스(\tat ...)를 잘라 결과 detail 에 싣는다."""
    lines = []
    for line in str(e).splitlines():
        if line.lstrip().startswith("at ") or line.startswith("\t"):
            break
        if line.strip():
            lines.append(line.strip().lstrip(": ").strip())
    text = " ".join(lines) or type(e).__name__
    return text if len(text) <= limit else text[:limit] + "…"


def accepted_values(expected: Any) -> list[str]:
    """ACCEPTED_VALUES 기대값(쉼표 구분) → 허용 값 목록 (앞뒤 공백 제거, 빈 항목 제외)."""
    return [v.strip() for v in str(expected or "").split(",") if v.strip()]


# ---------------------------------------------------------------------------
# CUSTOM_PYTHON 로더
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def run_custom_python(expected: str | None, df: Any, checks_dir: str | None) -> tuple[bool, str, str]:
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
    if not expected:
        return False, "N/A", "expected_value 에 플러그인 설정(JSON)이 필요합니다"
    try:
        cfg = json.loads(expected)
    except json.JSONDecodeError as e:
        return False, "N/A", f"플러그인 설정 JSON 파싱 실패: {e}"
    if not isinstance(cfg, dict):
        return False, "N/A", "플러그인 설정은 JSON 객체여야 합니다"
    module = str(cfg.get("module") or "")
    fn_name = str(cfg.get("fn") or "")
    params = cfg.get("params") or {}
    if not _IDENT_RE.fullmatch(module):
        return False, "N/A", f"module 이름이 올바르지 않습니다: {module!r}"
    if not _IDENT_RE.fullmatch(fn_name):
        return False, "N/A", f"fn 이름이 올바르지 않습니다: {fn_name!r}"
    if not checks_dir:
        return False, "N/A", "체크 모듈 디렉터리(--checks-dir)가 지정되지 않았습니다"

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


# ---------------------------------------------------------------------------
# CLI 공통 인자 / 실행 골격
# ---------------------------------------------------------------------------


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """두 스크립트 공통 인자 — 대상(--urn/--datasource-id), API 접속·계정, --checks-dir, --dry-run."""
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--urn", help="단일 데이터셋 URN (예: sakila-mysql.sakila.actor.dataset)")
    target.add_argument("--datasource-id",
                        help="데이터 소스 ID — 등록된 모든 데이터셋을 일괄 처리 (예: sakila-mysql)")
    parser.add_argument("--api-url", default="http://localhost:4600",
                        help="카탈로그 API 서버 주소")
    parser.add_argument("--api-timeout", type=float, default=DEFAULT_API_TIMEOUT,
                        help="카탈로그 API 요청 타임아웃(초)")
    parser.add_argument("--username", required=True, help="카탈로그 계정 (반입은 관리자)")
    parser.add_argument("--password", default=None,
                        help=f"카탈로그 계정 비밀번호 — --password-file 또는 환경변수 {ADMIN_PASSWORD_ENV} 권장")
    parser.add_argument("--password-file", default=None,
                        help="카탈로그 계정 비밀번호 파일 (첫 줄 끝 개행 무시)")
    parser.add_argument("--checks-dir", default=None,
                        help="CUSTOM_PYTHON 체크 모듈 디렉터리 (기본: 스크립트 위치/custom_checks)")
    parser.add_argument("--dry-run", action="store_true",
                        help="API 반입 없이 프로파일/검증 결과만 출력")
    parser.add_argument("--source-timezone", default=None,
                        help=f"원천 데이터 시간대 — FRESHNESS 에서 시간대 없는 시각 값(DATETIME 등) 해석. "
                             f"IANA 이름 또는 +09:00 (기본: 환경변수 {SOURCE_TIMEZONE_ENV}, 없으면 "
                             f"{DEFAULT_SOURCE_TIMEZONE}). 서버 설정 quality_source_timezone 과 같게 둔다")


def add_source_password_args(parser: argparse.ArgumentParser, flag: str) -> None:
    """원본 DB 비밀번호 인자 — flag 는 'db'(pandas) 또는 'jdbc'(PySpark)."""
    parser.add_argument(f"--{flag}-password", default=None,
                        help=f"원본 DB 비밀번호 — --{flag}-password-file 또는 환경변수 "
                             f"{SOURCE_PASSWORD_ENV} 권장 (카탈로그는 비밀번호를 내려주지 않음)")
    parser.add_argument(f"--{flag}-password-file", default=None,
                        help="원본 DB 비밀번호 파일 (첫 줄 끝 개행 무시)")


def source_password(args: argparse.Namespace, flag: str, env: dict | None = None) -> str | None:
    """원본 DB 비밀번호 — --{flag}-password > --{flag}-password-file > ARGUS_SOURCE_DB_PASSWORD. 없으면 None."""
    attr = flag.replace("-", "_")
    return read_secret(getattr(args, f"{attr}_password"), getattr(args, f"{attr}_password_file"),
                       SOURCE_PASSWORD_ENV, env)


def connect_api(args: argparse.Namespace) -> CatalogApi:
    """로그인 — 실패는 배치 전체 실패(종료)."""
    password = resolve_admin_password(args)
    try:
        return CatalogApi(args.api_url, args.username, password, timeout=args.api_timeout)
    except CatalogApiError as e:
        raise SystemExit(f"카탈로그에 로그인하지 못했습니다: {e}") from e


def resolve_targets(api: CatalogApi, args: argparse.Namespace) -> tuple[list[tuple[int, str]], str, str]:
    """처리 대상 (id, name) 목록, 데이터 소스 타입, datasource_id — 실패 시 종료.

    --urn 은 그 데이터셋 1건, --datasource-id 는 그 데이터 소스에 등록된 데이터셋 전체(페이지 순회).
    """
    try:
        if args.urn:
            d = api.get_dataset_by_urn(args.urn)
            return [(d["id"], d["name"])], d["datasource"]["type"], d["datasource"]["datasource_id"]
        items = api.list_datasets_by_datasource(args.datasource_id)
    except CatalogApiError as e:
        raise SystemExit(f"처리 대상 데이터셋을 조회하지 못했습니다: {e}") from e
    if not items:
        raise SystemExit(f"데이터 소스 '{args.datasource_id}' 에 등록된 데이터셋이 없습니다.")
    return [(it["id"], it["name"]) for it in items], items[0]["datasource_type"], args.datasource_id


def default_checks_dir(script_file: str) -> str:
    """--checks-dir 기본값 — 스크립트와 같은 디렉터리의 custom_checks/."""
    return str(Path(script_file).resolve().parent / "custom_checks")


def rule_label(rule: dict) -> str:
    """로그용 규칙 표시 — "이름 (유형:컬럼)"."""
    col = rule.get("column_name")
    return f"{rule['rule_name']} ({rule['check_type']}{':' + col if col else ''})"


def import_results(api: CatalogApi, dataset_id: int, row_count: int,
                   columns: list[dict], results: list[dict], dry_run: bool) -> float | None:
    """프로파일 → row_count → 결과 순으로 반입. 반환: 점수(반입하지 않았으면 None).

    - dry_run 이면 아무것도 반입하지 않는다(로그인·조회·원본 읽기·평가는 그대로 수행).
    - 활성 규칙이 없으면 결과 반입을 건너뛴다(서버는 빈 결과 목록을 422 로 거절).
    - 반입 API 는 관리자 전용 — 실패(403 등)는 CatalogApiError 로 올라가 데이터셋 실패로 기록된다.
    """
    if dry_run:
        print("[quality]   --dry-run: API 반입 생략")
        return None
    api.import_profile(dataset_id, {"row_count": row_count, "columns": columns})
    # 프로파일에서 얻은 전체 행 수로 카탈로그 데이터셋의 row_count 도 갱신
    api.update_row_count(dataset_id, row_count)
    if not results:
        print("[quality]   활성 규칙이 없어 프로파일·row_count 만 반입했습니다")
        return None
    summary = api.import_results(dataset_id, {"results": results})
    print(f"[quality]   imported: score={summary['score']}%, "
          f"passed={summary['passed']}/{summary['total_rules']}, row_count updated")
    return summary["score"]


def print_summary(started: datetime, summaries: list[dict], failures: list[tuple[str, str]],
                  out=None) -> int:
    """요약 출력. 반환: exit code (실패가 있으면 1)."""
    out = out or sys.stdout
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\n[quality] ==== 요약 ({elapsed:.1f}s) ====", file=out)
    for sm in summaries:
        score = f"{sm['score']}%" if sm["score"] is not None else "-"
        print(f"  {sm['table']}: rows={sm['rows']:,}, rules={sm['passed']}/{sm['rules']}, score={score}",
              file=out)
    if failures:
        print(f"  실패 {len(failures)}건:", file=out)
        for table, err in failures:
            print(f"    {table}: {err}", file=out)
    return 1 if failures else 0
