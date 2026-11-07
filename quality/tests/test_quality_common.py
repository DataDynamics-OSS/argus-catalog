# SPDX-License-Identifier: Apache-2.0
"""quality_common 단위 테스트 — 표준 라이브러리 + pytest 만 사용 (네트워크·DB 없음).

실행: cd quality && python -m pytest -q -p no:cacheprovider tests
"""

from __future__ import annotations

import argparse
import io
import json
import socket
import sys
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import quality_common as qc  # noqa: E402

# ---------------------------------------------------------------------------
# validate_custom_sql
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM t",
    "  select 1;  ",
    "WITH x AS (SELECT 1 AS a) SELECT count(*) FROM x",
    "-- 위반 건수\nSELECT count(*) FROM t /* 고아 */ WHERE a IS NULL",
    "SELECT count(*) FROM t WHERE updated_at > now()",  # update 부분 문자열은 단어 경계로 허용
])
def test_validate_custom_sql_allowed(sql):
    assert qc.validate_custom_sql(sql) is None


@pytest.mark.parametrize("sql, reason", [
    ("", "비어"),
    ("-- 주석만\n/* 블록 */", "비어"),
    ("SELECT 1; SELECT 2", "다중 문장"),
    ("SELECT 1; DROP TABLE t", "다중 문장"),
    ("DELETE FROM t", "SELECT/WITH"),
    ("/* SELECT */ UPDATE t SET a=1", "SELECT/WITH"),
    ("WITH d AS (DELETE FROM t RETURNING *) SELECT count(*) FROM d", "delete"),
    ("SELECT * FROM t; -- ok\n", None),
])
def test_validate_custom_sql_forbidden(sql, reason):
    got = qc.validate_custom_sql(sql)
    if reason is None:
        assert got is None
    else:
        assert got is not None and reason.lower() in got.lower()


# ---------------------------------------------------------------------------
# 식별자 인용
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ds_type, name, expected", [
    ("mysql", "sakila.actor", "`sakila`.`actor`"),
    ("mariadb", "db.we`ird", "`db`.`we``ird`"),
    ("starrocks", "db.t", "`db`.`t`"),
    ("hive", "db.t", "`db`.`t`"),
    ("impala", "db.t", "`db`.`t`"),
    ("postgresql", "public.order", '"public"."order"'),
    ("greenplum", 'public.a"b', '"public"."a""b"'),
    ("redshift", "s.t", '"s"."t"'),
    ("oracle", "HR.Employees", '"HR"."Employees"'),
    ("tibero", "S.T", '"S"."T"'),
    ("trino", "hive.db.t", '"hive"."db"."t"'),
    ("snowflake", "PUBLIC.T", '"PUBLIC"."T"'),
    ("mssql", "dbo.my]table", "[dbo].[my]]table]"),
    ("sqlserver", "dbo.t", "[dbo].[t]"),
    ("unknown", "a.b", '"a"."b"'),
])
def test_quote_table(ds_type, name, expected):
    assert qc.quote_table(name, ds_type) == expected


def test_quote_ident_injection_is_escaped():
    assert qc.quote_ident("x`; DROP TABLE t; --", "mysql") == "`x``; DROP TABLE t; --`"
    assert qc.quote_ident('x"; DROP', "postgresql") == '"x""; DROP"'


# ---------------------------------------------------------------------------
# 임계값 / 정확한 건수 판정
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    (None, 100.0), ("", 100.0), (0, 0.0), (0.0, 0.0), ("0", 0.0), (95, 95.0), ("99.5", 99.5),
])
def test_rule_threshold(raw, expected):
    assert qc.rule_threshold({"threshold": raw}) == expected


def test_rule_threshold_missing_key():
    assert qc.rule_threshold({}) == 100.0


def test_not_null_threshold_zero_passes_all_null():
    passed, actual, _ = qc.decide_not_null(null_count=10, row_count=10, threshold=0.0)
    assert passed and actual == "0.0%"


def test_not_null_exact_count_99_9995_percent_fails():
    # 200,000 행 중 1행 NULL → 99.9995% — 반올림하면 100% 지만 임계값 100 은 실패해야 한다
    passed, actual, detail = qc.decide_not_null(null_count=1, row_count=200_000, threshold=100.0)
    assert passed is False
    assert actual != "100.0%"  # 표시가 판정과 어긋나지 않음
    assert "199,999/200,000" in detail


def test_not_null_exact_boundary():
    assert qc.decide_not_null(1, 1000, 99.9)[0] is True     # 99.9% == 99.9
    assert qc.decide_not_null(2, 1000, 99.9)[0] is False    # 99.8%
    assert qc.decide_not_null(0, 1000, 100.0) == (True, "100.0%", "비-NULL 100.0% (1,000/1,000행, 임계값 100.0%)")


def test_unique_exact_count_99_9995_percent_fails():
    passed, actual, _ = qc.decide_unique(unique_count=199_999, row_count=200_000, threshold=100.0)
    assert passed is False and actual != "100.0%"
    assert qc.decide_unique(200_000, 200_000, 100.0)[0] is True


def test_unique_threshold_zero():
    assert qc.decide_unique(1, 1000, 0.0)[0] is True


def test_empty_table_matches_server_semantics():
    # 서버: row_count 0 → null_percent 0 → 비-NULL 100% (통과), unique_percent 0 (임계값 0 일 때만 통과)
    assert qc.decide_not_null(0, 0, 100.0)[0] is True
    assert qc.decide_unique(0, 0, 100.0)[0] is False
    assert qc.decide_unique(0, 0, 0.0)[0] is True


def test_percent_display_rounding():
    assert qc.percent(1, 3) == 33.33
    assert qc.percent(5, 0) == 0.0


def test_min_max_and_row_count():
    assert qc.decide_min_max("MIN_VALUE", "5", "1")[0] is True
    assert qc.decide_min_max("MIN_VALUE", "0", "1")[0] is False
    assert qc.decide_min_max("MAX_VALUE", "10", "9")[0] is False
    assert qc.decide_min_max("MAX_VALUE", None, "9")[0] is False
    assert qc.decide_min_max("MAX_VALUE", "abc", "9")[:2] == (False, "abc")
    assert qc.decide_row_count(5, "5")[0] is True
    assert qc.decide_row_count(4, "5")[0] is False
    assert qc.decide_row_count(4, "x")[0] is False


def test_parse_violation_count():
    assert qc.parse_violation_count(0)[0] is True
    assert qc.parse_violation_count("3")[:2] == (False, "위반 3행")
    assert qc.parse_violation_count("abc")[0] is False


# ---------------------------------------------------------------------------
# FRESHNESS
# ---------------------------------------------------------------------------

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def test_parse_timestamp_aware_and_naive():
    aware = qc.parse_timestamp("2026-11-07T10:00:00+00:00")
    assert aware == datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
    assert qc.parse_timestamp("2026-11-07T19:00:00+09:00") == aware
    # naive → 원천 데이터 시간대(기본 Asia/Seoul, 호스트 시간대와 무관)
    kst_10 = datetime(2026, 11, 7, 1, 0, tzinfo=timezone.utc)
    assert qc.parse_timestamp("2026-11-07 10:00:00") == kst_10
    assert qc.parse_timestamp(datetime(2026, 11, 7, 10, 0)) == kst_10
    assert qc.parse_timestamp(date(2026, 11, 7)) == datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)
    # 원천 시간대 지정 — aware 값은 지정과 무관하게 자기 시간대 유지
    assert qc.parse_timestamp("2026-11-07 10:00:00", timezone.utc) == datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
    assert qc.parse_timestamp("2026-11-07T19:00:00+09:00", timezone.utc) == aware


def test_parse_timestamp_ignores_host_timezone(monkeypatch):
    """호스트 TZ 를 바꿔도 naive 값 해석은 원천 시간대(기본 Asia/Seoul) 그대로 (QB-T12 회귀)."""
    import time
    if not hasattr(time, "tzset"):
        pytest.skip("tzset 미지원 플랫폼")
    expected = datetime(2006, 2, 22, 19, 12, 8, tzinfo=timezone.utc)  # 2006-02-23 04:12:08 KST
    try:
        for host_tz in ("UTC", "America/New_York", "Asia/Seoul"):
            monkeypatch.setenv("TZ", host_tz)
            time.tzset()
            assert qc.parse_timestamp("2006-02-23 04:12:08") == expected
    finally:
        monkeypatch.undo()
        time.tzset()


def test_resolve_timezone():
    assert qc.resolve_timezone("UTC") is timezone.utc
    assert qc.resolve_timezone("+09:00").utcoffset(None) == timedelta(hours=9)
    assert qc.resolve_timezone("-0530").utcoffset(None) == -timedelta(hours=5, minutes=30)
    assert qc.resolve_timezone("Asia/Seoul").utcoffset(datetime(2026, 1, 1)) == timedelta(hours=9)
    for bad in ("", "  ", "Mars/Base", "+25:00", "../etc/passwd"):
        with pytest.raises(ValueError):
            qc.resolve_timezone(bad)


def test_source_timezone_cli_env_default():
    p = argparse.ArgumentParser()
    qc.add_common_args(p)
    base = ["--urn", "x", "--username", "admin"]
    off = lambda tz: tz.utcoffset(datetime(2026, 1, 1))  # noqa: E731
    # 기본: Asia/Seoul (호스트 TZ·환경변수 없음)
    assert off(qc.source_timezone(p.parse_args(base), env={})) == timedelta(hours=9)
    # 환경변수
    assert qc.source_timezone(p.parse_args(base), env={"ARGUS_SOURCE_TIMEZONE": "UTC"}) is timezone.utc
    # 명령행 > 환경변수
    args = p.parse_args([*base, "--source-timezone", "+01:00"])
    assert off(qc.source_timezone(args, env={"ARGUS_SOURCE_TIMEZONE": "UTC"})) == timedelta(hours=1)
    with pytest.raises(SystemExit, match="source-timezone"):
        qc.source_timezone(p.parse_args([*base, "--source-timezone", "Nowhere/City"]), env={})


def test_freshness_naive_uses_source_timezone():
    """같은 naive 최대값 — 원천 시간대에 따라 경과 시간이 9시간 달라진다."""
    latest = "2026-11-07 15:00:00"  # KST 15:00 = UTC 06:00 → NOW(UTC 12:00) 기준 6h
    assert qc.decide_freshness("24", "c", latest, now=NOW)[1] == "6.0h"
    assert qc.decide_freshness("24", "c", latest, now=NOW, source_tz=timezone.utc)[1] == "0.0h"  # 미래 → 0
    assert qc.decide_freshness("24", "c", "2026-11-07 03:00:00", now=NOW, source_tz=timezone.utc)[1] == "9.0h"
    assert qc.decide_freshness("24", "c", "2026-11-07 03:00:00", now=NOW)[1] == "18.0h"


def test_parse_timestamp_unparsable():
    for v in (None, "", "not-a-date", "2026-13-45"):
        assert qc.parse_timestamp(v) is None


def test_parse_timestamp_pandas_like_object():
    class FakeTs:
        def to_pydatetime(self):
            return datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)

    assert qc.parse_timestamp(FakeTs()) == datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)


def test_freshness_with_column():
    latest = NOW - timedelta(hours=5)
    passed, actual, detail = qc.decide_freshness("24", "updated_at", latest, now=NOW)
    assert passed and actual == "5.0h" and "updated_at" in detail
    passed, actual, _ = qc.decide_freshness("4", "updated_at", latest.isoformat(), now=NOW)
    assert not passed and actual == "5.0h"
    # 기본 24시간
    assert qc.decide_freshness(None, "c", NOW - timedelta(hours=30), now=NOW)[0] is False
    # 미래 시각 → 0h
    assert qc.decide_freshness("1", "c", NOW + timedelta(hours=3), now=NOW)[:2] == (True, "0.0h")


def test_freshness_unparsable_and_missing():
    passed, actual, detail = qc.decide_freshness("24", "c", "abc", now=NOW)
    assert (passed, actual) == (False, "N/A") and "해석할 수 없습니다" in detail
    assert qc.decide_freshness("24", "c", None, now=NOW)[0] is False
    assert qc.decide_freshness("24", "c", None, column_present=False)[2].startswith("데이터에 'c'")
    assert qc.decide_freshness("x", "c", NOW)[:2] == (False, "N/A")


def test_freshness_without_column():
    passed, actual, detail = qc.decide_freshness("12", None, None)
    assert passed and actual == "0.0h" and "시각 컬럼" in detail


# ---------------------------------------------------------------------------
# 비밀번호 / 접속 설정
# ---------------------------------------------------------------------------


def _args(**kw) -> argparse.Namespace:
    base = {"password": None, "password_file": None, "db_password": None, "db_password_file": None,
            "jdbc_password": None, "jdbc_password_file": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_read_secret_precedence(tmp_path):
    f = tmp_path / "pw"
    f.write_text("from-file\n")
    env = {"E": "from-env"}
    assert qc.read_secret("cli", str(f), "E", env) == "cli"
    assert qc.read_secret(None, str(f), "E", env) == "from-file"
    assert qc.read_secret(None, None, "E", env) == "from-env"
    assert qc.read_secret(None, None, "E", {}) is None
    assert qc.read_secret("", None, "E", env) == ""  # 빈 비밀번호도 명시값으로 존중


def test_read_secret_missing_file():
    with pytest.raises(SystemExit, match="비밀번호 파일을 읽지 못했습니다"):
        qc.read_secret(None, "/nonexistent/pw", "E", {})


def test_admin_password(tmp_path):
    assert qc.resolve_admin_password(_args(password="p"), env={}) == "p"
    assert qc.resolve_admin_password(_args(), env={qc.ADMIN_PASSWORD_ENV: "e"}) == "e"
    f = tmp_path / "a"
    f.write_text("f\r\n")
    assert qc.resolve_admin_password(_args(password_file=str(f)), env={qc.ADMIN_PASSWORD_ENV: "e"}) == "f"
    with pytest.raises(SystemExit, match="ARGUS_PASSWORD"):
        qc.resolve_admin_password(_args(), env={})


def test_source_password_flags():
    assert qc.source_password(_args(db_password="x"), "db", env={}) == "x"
    assert qc.source_password(_args(), "jdbc", env={qc.SOURCE_PASSWORD_ENV: "y"}) == "y"
    assert qc.source_password(_args(), "db", env={}) is None


CFG = {"host": "db.local", "port": 3306, "database": "sakila", "username": "ro",
       "password": "", "ssl_enabled": True}


def test_source_from_config_masked_secret_requires_password():
    with pytest.raises(SystemExit) as ei:
        qc.source_from_config(CFG, ["password"], "mysql", "sakila", None, "db")
    msg = str(ei.value)
    assert "비밀번호를 확인하지 못했습니다" in msg
    assert "--db-password-file" in msg and qc.SOURCE_PASSWORD_ENV in msg


def test_source_from_config_with_password():
    p = qc.source_from_config(CFG, ["password"], "mysql", "x", "secret", "jdbc")
    assert p["host"] == "db.local" and p["port"] == 3306 and p["database"] == "sakila"
    assert p["user"] == "ro" and p["password"] == "secret" and p["ssl"] is True


def test_source_from_config_no_stored_password_and_legacy_plaintext():
    cfg = dict(CFG, ssl_enabled=False)
    assert qc.source_from_config(cfg, [], "mysql", "x", None, "db")["password"] == ""
    legacy = dict(CFG, password="plain")
    assert qc.source_from_config(legacy, [], "mysql", "x", None, "db")["password"] == "plain"


def test_source_from_config_type_specific_keys():
    trino = {"host": "t", "port": 8443, "catalog": "hive", "schema": "s", "username": "u",
             "password": "", "use_ssl": True}
    p = qc.source_from_config(trino, [], "trino", "x", None, "db")
    assert p["database"] == "hive" and p["ssl"] is True and p["schema"] == "s"
    snow = {"account": "xy12345", "database": "D", "username": "u", "password": ""}
    p = qc.source_from_config(snow, ["password"], "snowflake", "x", "pw", "db")
    assert p["host"] == "xy12345" and p["port"] == 443 and p["ssl"] is None
    impala = {"host": "i", "auth_type": "LDAP", "ldap_username": "lu", "ldap_password": ""}
    with pytest.raises(SystemExit, match="ldap_password"):
        qc.source_from_config(impala, ["ldap_password"], "impala", "default", None, "db")


def test_source_from_config_no_host():
    with pytest.raises(SystemExit, match="host"):
        qc.source_from_config({"database": "d"}, [], "mysql", "d", "p", "db")


@pytest.mark.parametrize("cfg, expected", [
    ({}, None), ({"ssl_enabled": True}, True), ({"ssl_enabled": "false"}, False),
    ({"use_ssl": "1"}, True), ({"ssl_enabled": None}, None),
])
def test_ssl_setting(cfg, expected):
    assert qc.ssl_setting(cfg) is expected


# ---------------------------------------------------------------------------
# CatalogApi — urlopen 스텁 (네트워크 없음)
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, body):
        self._body = json.dumps(body).encode() if not isinstance(body, bytes) else body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(url, code, body="err"):
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(body.encode()))


class StubServer:
    """경로별 응답 큐. 항목이 Exception 이면 raise, 아니면 JSON 응답."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []

    def __call__(self, req, timeout=None):
        path = req.full_url.split("/api/v1", 1)[1]
        self.calls.append((req.get_method(), path, req.headers.get("Authorization"), timeout,
                           json.loads(req.data) if req.data else None))
        key = path.split("?", 1)[0]
        queue = self.routes[key]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, int):
            raise _http_error(req.full_url, item)
        if isinstance(item, BaseException):
            raise item
        return _Resp(item)


LOGIN = {"access_token": "a1", "refresh_token": "r1"}


def test_api_login_and_timeout_passed():
    srv = StubServer({"/auth/login": [LOGIN], "/quality/rules": [[{"id": 1}]]})
    api = qc.CatalogApi("http://h:1/", "admin", "pw", timeout=7, urlopen=srv)
    assert api.get_rules(3) == [{"id": 1}]
    assert srv.calls[0][4] == {"username": "admin", "password": "pw"}
    assert srv.calls[1][2] == "Bearer a1"
    assert all(c[3] == 7 for c in srv.calls)


def test_api_error_raises_catalog_api_error_not_systemexit():
    srv = StubServer({"/auth/login": [LOGIN], "/quality/rules": [500]})
    api = qc.CatalogApi("http://h", "u", "p", urlopen=srv)
    with pytest.raises(qc.CatalogApiError) as ei:
        api.get_rules(1)
    assert ei.value.status == 500 and "API 오류 500" in str(ei.value)


def test_api_timeout_becomes_catalog_api_error():
    srv = StubServer({"/auth/login": [LOGIN],
                      "/quality/rules": [urllib.error.URLError(socket.timeout("timed out"))]})
    api = qc.CatalogApi("http://h", "u", "p", urlopen=srv)
    with pytest.raises(qc.CatalogApiError) as ei:
        api.get_rules(1)
    assert ei.value.status is None and "timed out" in str(ei.value)
    srv2 = StubServer({"/auth/login": [LOGIN], "/quality/rules": [TimeoutError("read timeout")]})
    with pytest.raises(qc.CatalogApiError):
        qc.CatalogApi("http://h", "u", "p", urlopen=srv2).get_rules(1)


def test_api_refresh_on_401_then_retry():
    srv = StubServer({
        "/auth/login": [LOGIN],
        "/auth/refresh": [{"access_token": "a2", "refresh_token": "r2"}],
        "/quality/rules": [401, [{"id": 9}]],
    })
    api = qc.CatalogApi("http://h", "u", "p", urlopen=srv)
    assert api.get_rules(1) == [{"id": 9}]
    paths = [c[1].split("?")[0] for c in srv.calls]
    assert paths == ["/auth/login", "/quality/rules", "/auth/refresh", "/quality/rules"]
    assert srv.calls[2][4] == {"refresh_token": "r1"}
    assert srv.calls[3][2] == "Bearer a2"
    assert api.refresh_token == "r2"


def test_api_relogin_when_refresh_fails():
    srv = StubServer({
        "/auth/login": [LOGIN, {"access_token": "a3", "refresh_token": "r3"}],
        "/auth/refresh": [401],
        "/quality/rules": [401, [{"id": 1}]],
    })
    api = qc.CatalogApi("http://h", "u", "p", urlopen=srv)
    assert api.get_rules(1) == [{"id": 1}]
    paths = [c[1].split("?")[0] for c in srv.calls]
    assert paths == ["/auth/login", "/quality/rules", "/auth/refresh", "/auth/login", "/quality/rules"]
    assert srv.calls[-1][2] == "Bearer a3"


def test_api_retries_only_once():
    srv = StubServer({"/auth/login": [LOGIN], "/auth/refresh": [LOGIN], "/quality/rules": [401]})
    api = qc.CatalogApi("http://h", "u", "p", urlopen=srv)
    with pytest.raises(qc.CatalogApiError) as ei:
        api.get_rules(1)
    assert ei.value.status == 401
    assert [c[1].split("?")[0] for c in srv.calls].count("/quality/rules") == 2


def test_api_configuration_masked_and_403():
    cfg_resp = {"config": {"host": "h", "password": ""}, "secret_fields": ["password"]}
    srv = StubServer({
        "/auth/login": [LOGIN],
        "/catalog/datasources": [[{"id": 5, "datasource_id": "ds"}]],
        "/catalog/datasources/5/configuration": [cfg_resp],
    })
    api = qc.CatalogApi("http://h", "u", "p", urlopen=srv)
    assert api.get_datasource_configuration("ds") == ({"host": "h", "password": ""}, ["password"])
    assert api.get_datasource_configuration("nope") is None

    srv403 = StubServer({
        "/auth/login": [LOGIN],
        "/catalog/datasources": [[{"id": 5, "datasource_id": "ds"}]],
        "/catalog/datasources/5/configuration": [403],
    })
    api = qc.CatalogApi("http://h", "u", "p", urlopen=srv403)
    with pytest.raises(SystemExit) as ei:
        qc.fetch_source_config(api, "ds", "db")
    assert "datasources.connection" in str(ei.value) and "--db-host" in str(ei.value)

    srv404 = StubServer({
        "/auth/login": [LOGIN],
        "/catalog/datasources": [[{"id": 5, "datasource_id": "ds"}]],
        "/catalog/datasources/5/configuration": [404],
    })
    api = qc.CatalogApi("http://h", "u", "p", urlopen=srv404)
    with pytest.raises(SystemExit, match="연결 설정이 없습니다"):
        qc.fetch_source_config(api, "ds", "jdbc")


def test_connect_api_login_failure_exits(monkeypatch):
    srv = StubServer({"/auth/login": [401]})
    args = argparse.Namespace(password="p", password_file=None, api_url="http://h", username="u",
                              api_timeout=5)
    monkeypatch.setattr(qc.urllib.request, "urlopen", srv)
    with pytest.raises(SystemExit, match="로그인하지 못했습니다"):
        qc.connect_api(args)


def test_print_summary_exit_code():
    out = io.StringIO()
    started = datetime.now(timezone.utc)
    sm = [{"table": "a.b", "rows": 1, "rules": 1, "passed": 1, "score": 100.0}]
    assert qc.print_summary(started, sm, [], out=out) == 0
    assert qc.print_summary(started, sm, [("a.c", "boom")], out=out) == 1
    assert "실패 1건" in out.getvalue()


# ---------------------------------------------------------------------------
# CUSTOM_PYTHON 로더
# ---------------------------------------------------------------------------


@pytest.fixture()
def checks_dir(tmp_path):
    (tmp_path / "good.py").write_text(
        "def ok(df, params):\n    return True, str(len(df)), f\"p={params.get('x')}\"\n"
        "def bad_shape(df, params):\n    return True\n"
        "def boom(df, params):\n    raise ValueError('x')\n"
        "NOT_CALLABLE = 1\n"
    )
    (tmp_path / "broken.py").write_text("raise RuntimeError('import fail')\n")
    return str(tmp_path)


def test_custom_python_ok(checks_dir):
    exp = json.dumps({"module": "good", "fn": "ok", "params": {"x": 3}})
    assert qc.run_custom_python(exp, [1, 2], checks_dir) == (True, "2", "p=3")


@pytest.mark.parametrize("cfg, msg", [
    ({"module": "../etc/passwd", "fn": "ok"}, "module 이름"),
    ({"module": "good.sub", "fn": "ok"}, "module 이름"),
    ({"module": "good", "fn": "ok; import os"}, "fn 이름"),
    ({"module": "", "fn": "ok"}, "module 이름"),
    ({"module": "missing", "fn": "ok"}, "체크 모듈이 없습니다"),
    ({"module": "good", "fn": "nope"}, "체크 함수가 없습니다"),
    ({"module": "good", "fn": "NOT_CALLABLE"}, "체크 함수가 없습니다"),
    ({"module": "good", "fn": "bad_shape"}, "3-튜플"),
    ({"module": "good", "fn": "boom"}, "실행 오류"),
    ({"module": "broken", "fn": "x"}, "로드 실패"),
])
def test_custom_python_errors(checks_dir, cfg, msg):
    passed, actual, detail = qc.run_custom_python(json.dumps(cfg), [], checks_dir)
    assert passed is False and actual == "N/A" and msg in detail


def test_custom_python_bad_json(checks_dir):
    assert "JSON" in qc.run_custom_python("{not json", [], checks_dir)[2]
    assert "JSON 객체" in qc.run_custom_python("[1]", [], checks_dir)[2]
    assert "필요" in qc.run_custom_python("", [], checks_dir)[2]


def test_short_error_strips_java_stacktrace():
    e = RuntimeError("An error occurred while calling o324.load.\n: org.x.SparkException: Table NOPE not found\n"
                     "\tat org.apache.spark.Foo(Foo.scala:1)\n\tat bar")
    assert qc.short_error(e) == "An error occurred while calling o324.load. org.x.SparkException: Table NOPE not found"
    assert qc.short_error(ValueError()) == "ValueError"
    assert qc.short_error(ValueError("x" * 600)).endswith("…")
