# SPDX-License-Identifier: Apache-2.0
"""원천 직접 진단(TODO-095 1단계) 테스트 — Trino 연결이 없는 데이터셋은 품질 워커가 원천 DB 를 직접 읽는다.

- A: 규칙 → 직접 진단 명세(rule_direct) — 조건·계산식 트리, 컬럼, 읽기 쿼리
- B: 워커의 계산(quality_worker.direct) — Trino 집계 식과 같은 값(NULL·CHAR·정수 나눗셈·날짜 형식·3값 논리)
- C: 경로 판정 — Trino 연결이 있으면 Trino, 없으면 원천 직접(PostgreSQL·MySQL·MariaDB)
- D: 끝에서 끝 — 실제 워커 코드가 가짜 원천을 읽어 결과를 제출하고 카탈로그가 판정·저장

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_direct.py
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.catalog.models as cm
from app.quality import rule_direct, rule_sql, runs
from app.quality.models import QualityRule
from app.quality.rule_sql import RuleSpec
from tests.test_quality_trino_runs import (  # noqa: F401 — _no_alerts 는 autouse fixture
    _app,
    _FakeTrino,
    _make_sessionmaker,
    _no_alerts,
    _results,
    _run,
    _seed,
    _start,
    _work,
)

# 품질 워커 확장의 소스 — 설치하지 않고 모노레포 경로에서 불러온다
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "argus-catalog-quality-worker" / "src"))
from quality_worker import direct  # noqa: E402

TYPES = {"use_yn": "char(1)", "reg_ymd": "varchar(8)", "bgn_ymd": "varchar(8)", "end_ymd": "varchar(8)",
         "cust_id": "integer", "amt": "numeric", "vat": "numeric", "total": "numeric", "paid_at": "timestamp"}
SOURCE = {"host": "pg.local", "port": 5432, "database": "sales", "username": "reader", "password": "s3cret"}


def _probe(check: str, column: str | None = None, expected: str | None = None) -> dict:
    return rule_direct.probe(RuleSpec(1, check, column, expected), TYPES)


def _values(spec: dict, rows: list[dict], char: tuple[str, ...] = ()) -> list:
    """명세 하나를 행들로 계산한 값(행 수 빼고)."""
    columns = list(rows[0]) if rows else []
    step = {"columns": columns, "char_columns": list(char), "probes": [{"key": "k", **spec}]}
    results, _ = direct.evaluate(step, [tuple(r.values()) for r in rows])
    (result,) = results
    assert "error" not in result, result
    return result["row"][1:]


# ---------------------------------------------------------------------------
# A — 명세
# ---------------------------------------------------------------------------

def test_path_support_table() -> None:
    # 원천 직접: Trino 함수·커스텀 SQL 은 못 함, 커스텀 Python 은 직접만의 것. 최신성은 두 경로 모두(지표 밖)
    assert set(rule_direct.DIRECT_CHECK_TYPES) == set(rule_sql.TRINO_CHECK_TYPES) - {"FUNCTION"} | {"CUSTOM_PYTHON", "FRESHNESS"}
    assert set(rule_sql.TRINO_PATH_TYPES) == set(rule_sql.TRINO_CHECK_TYPES) | {"CUSTOM_SQL", "FRESHNESS"}
    assert set(rule_direct.ENGINE_CHECK_TYPES) == set(rule_sql.TRINO_PATH_TYPES) | {"CUSTOM_PYTHON"}
    # 품질 탭이 만드는 모든 유형을 어느 한 경로는 평가한다 — 간이 진단 없이도 빠지는 유형이 없다(TODO-095)
    from app.quality.schemas import SUPPORTED_CHECK_TYPES
    assert set(SUPPORTED_CHECK_TYPES) == set(rule_direct.ENGINE_CHECK_TYPES)
    assert _probe("FUNCTION", "cust_id", "memory.default.f") is None
    assert _probe("CUSTOM_SQL", None, "SELECT 1") is None


def test_condition_tree_binds_and_before_or() -> None:
    tree = rule_direct.condition_tree("use_yn = 'Y' AND amt > 10 OR cust_id IN (1, 2) AND end_ymd IS NOT NULL", TYPES)
    assert tree == {"or": [
        {"and": [{"col": "use_yn", "op": "=", "right": {"str": "Y"}}, {"col": "amt", "op": ">", "right": {"num": "10"}}]},
        {"and": [{"col": "cust_id", "op": "IN", "values": [{"num": "1"}, {"num": "2"}]},
                 {"col": "end_ymd", "op": "IS NOT NULL"}]},
    ]}
    assert rule_direct.condition_tree("\"BGN_YMD\" != end_ymd AND reg_ymd NOT IN ('it''s')", TYPES) == {"or": [{"and": [
        {"col": "bgn_ymd", "op": "<>", "right": {"col": "end_ymd"}},
        {"col": "reg_ymd", "op": "NOT IN", "values": [{"str": "it's"}]}]}]}
    with pytest.raises(rule_sql.ExpressionError, match="컬럼이 없습니다"):
        rule_direct.condition_tree("nope = 1", TYPES)


def test_formula_tree_follows_precedence() -> None:
    tree, tolerance = rule_direct.formula_tree("amt + vat * 2 - (cust_id / -3) ~ 0.5", TYPES)
    assert tolerance == 0.5
    assert tree == {"op": "-",
                    "l": {"op": "+", "l": {"col": "amt"}, "r": {"op": "*", "l": {"col": "vat"}, "r": {"num": "2"}}},
                    "r": {"op": "/", "l": {"col": "cust_id"}, "r": {"neg": {"num": "3"}}}}


def test_probe_shapes_and_columns() -> None:
    probes = [
        _probe("NOT_NULL", "use_yn"), _probe("COLUMN_COMPARE", "bgn_ymd", "<= end_ymd"),
        _probe("FORMULA", "total", "amt + vat"), _probe("ROW_COUNT", None, "1"),
        _probe("LOGICAL", None, "IF use_yn = 'Y' THEN cust_id IS NOT NULL"),
    ]
    assert probes[0] == {"column": "use_yn", "kind": "nulls"}
    assert probes[1] == {"column": "bgn_ymd", "kind": "compare", "op": "<=", "other": "end_ymd"}
    assert probes[3] == {"kind": "rows"}
    assert rule_direct.columns_of(probes) == ["use_yn", "bgn_ymd", "end_ymd", "total", "amt", "vat", "cust_id"]
    date_probe = _probe("DATE_FORMAT", "reg_ymd", "YYYY-MM")
    assert date_probe["date"] == {"shape": r"^\d{4}\-\d{2}$", "format": "%Y-%m %d", "suffix": " 01"}
    assert _probe("DATE_FORMAT", "paid_at", "YYYYMMDD") == {"column": "paid_at", "kind": "text", "valid": True}
    assert _probe("WHITESPACE", "reg_ymd", "#$")["forbid"] == [rule_sql.WHITESPACE_PATTERN, "[#$]"]


def test_select_sql_quotes_per_dialect() -> None:
    assert rule_direct.select_sql("postgresql", "pub\"lic", "orders", ["a", 'b"c']) == \
        'SELECT "a", "b""c" FROM "pub""lic"."orders"'
    assert rule_direct.select_sql("mysql", "sakila", "film", ["ti`tle"]) == "SELECT `ti``tle` FROM `sakila`.`film`"
    assert rule_direct.select_sql("mariadb", "db", "t", []) == "SELECT 1 FROM `db`.`t`"


# ---------------------------------------------------------------------------
# B — 워커의 계산 (Trino 집계 식과 같은 값)
# ---------------------------------------------------------------------------

def test_counts_for_null_unique_and_bounds() -> None:
    rows = [{"amt": Decimal("10")}, {"amt": None}, {"amt": Decimal("10")}, {"amt": Decimal("-3.5")}]
    assert _values(_probe("NOT_NULL", "amt"), rows) == [1]
    assert _values(_probe("UNIQUE", "amt"), rows) == [2]
    # [최소, 비-NULL 수, 하한보다 작은 수]
    assert _values(_probe("MIN_VALUE", "amt", "0"), rows) == [-3.5, 3, 1]
    assert _values(_probe("MAX_VALUE", "amt", "5"), rows) == [10.0, 3, 2]
    # 숫자로 읽을 수 없는 문자열은 세되(비-NULL) 비교하지 않는다 — TRY_CAST
    text_rows = [{"amt": "7"}, {"amt": "x"}, {"amt": " 12 "}]
    assert _values(_probe("MAX_VALUE", "amt", "10"), text_rows) == [12.0, 3, 1]
    assert _values(_probe("MIN_VALUE", "amt", "0"), [{"amt": None}]) == [None, 0, 0]


def test_text_checks_trim_char_and_skip_null() -> None:
    rows = [{"use_yn": "Y"}, {"use_yn": "N "}, {"use_yn": "x"}, {"use_yn": None}]
    assert _values(_probe("ACCEPTED_VALUES", "use_yn", "Y, N"), rows, char=("use_yn",)) == [3, 1]
    # CHAR 가 아니면 뒤 공백도 값이다
    assert _values(_probe("ACCEPTED_VALUES", "use_yn", "Y,N"), rows) == [3, 2]
    assert _values(_probe("REGEX", "use_yn", "^[YN]"), rows) == [3, 1]
    broken = [{"reg_ymd": "정상"}, {"reg_ymd": "ㄱ나"}, {"reg_ymd": "a�"}]
    assert _values(_probe("BROKEN_TEXT", "reg_ymd"), broken) == [3, 2]
    spaced = [{"reg_ymd": " a"}, {"reg_ymd": "b　"}, {"reg_ymd": "c\td"}, {"reg_ymd": "e#"}, {"reg_ymd": "ok"}]
    assert _values(_probe("WHITESPACE", "reg_ymd"), spaced) == [5, 3]
    assert _values(_probe("WHITESPACE", "reg_ymd", "#"), spaced) == [5, 4]
    # 숫자·불리언은 Trino 의 CAST(… AS varchar) 처럼 문자열로
    assert _values(_probe("ACCEPTED_VALUES", "cust_id", "1,true"), [{"cust_id": 1}, {"cust_id": True},
                                                                    {"cust_id": 2}]) == [3, 1]


def test_date_format_checks_shape_and_calendar() -> None:
    rows = [{"reg_ymd": v} for v in ("20240229", "20230229", "2024011", "202401011", "20241301", None, "２０２４０１０１")]
    assert _values(_probe("DATE_FORMAT", "reg_ymd", "YYYYMMDD"), rows) == [6, 5]
    months = [{"reg_ymd": v} for v in ("2024-01", "2024-13", "2024-1")]
    assert _values(_probe("DATE_FORMAT", "reg_ymd", "YYYY-MM"), months) == [3, 2]
    stamps = [{"reg_ymd": v} for v in ("2024-01-05 13:30:00.123", "2024-01-05 25:00:00.000", "2024-01-05 13:30:00")]
    assert _values(_probe("DATE_FORMAT", "reg_ymd", "YYYY-MM-DD HH24:MI:SS.SSS"), stamps) == [3, 2]
    assert _values(_probe("DATE_FORMAT", "paid_at", "YYYYMMDD"), [{"paid_at": datetime(2024, 1, 1)}]) == [1, 0]


def test_column_compare_and_types() -> None:
    rows = [{"bgn_ymd": "20240101", "end_ymd": "20240201"}, {"bgn_ymd": "20240301", "end_ymd": "20240201"},
            {"bgn_ymd": None, "end_ymd": "1"}]
    assert _values(_probe("COLUMN_COMPARE", "bgn_ymd", "<= end_ymd"), rows) == [2, 1]
    mixed = [{"bgn_ymd": date(2024, 1, 2), "end_ymd": datetime(2024, 1, 1, 12)}]
    assert _values(_probe("COLUMN_COMPARE", "bgn_ymd", "< end_ymd"), mixed) == [1, 1]
    step = {"columns": ["bgn_ymd", "end_ymd"], "probes": [{"key": "k", **_probe("COLUMN_COMPARE", "bgn_ymd", "= end_ymd")}]}
    (result,), _ = direct.evaluate(step, [("a", 1)])
    assert result["error_type"] == "USER_ERROR" and "비교할 수 없는 타입" in result["error"]


def test_logical_uses_three_valued_logic() -> None:
    spec = _probe("LOGICAL", None, "IF use_yn = 'Y' AND amt >= 10 THEN end_ymd IS NOT NULL OR cust_id = 0")
    rows = [
        {"use_yn": "Y", "amt": Decimal("10"), "end_ymd": None, "cust_id": 1},     # 전제 참, 결론 거짓 → 위반
        {"use_yn": "Y", "amt": Decimal("12"), "end_ymd": "x", "cust_id": 1},      # 결론 참
        {"use_yn": "Y", "amt": Decimal("12"), "end_ymd": None, "cust_id": None},  # 결론 모름 → 위반 아님
        {"use_yn": "Y", "amt": None, "end_ymd": None, "cust_id": 1},              # 전제 모름 → 대상 아님
        {"use_yn": "N", "amt": Decimal("50"), "end_ymd": None, "cust_id": 1},     # 전제 거짓
    ]
    assert _values(spec, rows) == [3, 1]
    # 날짜·시각 컬럼과 문자열 상수의 비교는 Trino 처럼 규칙 오류 ("Cannot apply operator: timestamp(0) <= varchar")
    dated = _probe("LOGICAL", None, "IF paid_at >= '2024-01-01' THEN use_yn IN ('Y')")
    step = {"columns": ["paid_at", "use_yn"], "probes": [{"key": "k", **dated}]}
    (result,), _ = direct.evaluate(step, [(datetime(2024, 3, 1), "N")])
    assert result["error_type"] == "USER_ERROR" and "비교할 수 없습니다" in result["error"]
    numeric = _probe("LOGICAL", None, "IF use_yn = 1 THEN amt > 0")
    (result,), _ = direct.evaluate({"columns": ["use_yn", "amt"], "probes": [{"key": "k", **numeric}]}, [("Y", 1)])
    assert result["error_type"] == "USER_ERROR"


def test_formula_follows_trino_arithmetic() -> None:
    spec = _probe("FORMULA", "total", "amt + vat")
    rows = [{"total": Decimal("110"), "amt": Decimal("100"), "vat": Decimal("10")},
            {"total": Decimal("111"), "amt": Decimal("100"), "vat": Decimal("10")},
            {"total": None, "amt": Decimal("1"), "vat": Decimal("1")},
            {"total": Decimal("5"), "amt": None, "vat": Decimal("1")}]
    assert _values(spec, rows) == [2, 1]
    # 정수끼리 나누면 0 쪽으로 버린다, 0 으로 나누면 NULL(대상 아님)
    div = _probe("FORMULA", "total", "amt / vat")
    assert _values(div, [{"total": 3, "amt": 7, "vat": 2}, {"total": -3, "amt": -7, "vat": 2},
                         {"total": 1, "amt": 1, "vat": 0}]) == [2, 0]
    tolerant = _probe("FORMULA", "total", "amt * 1.1 ~ 0.01")
    assert _values(tolerant, [{"total": 110.0, "amt": 100}, {"total": 110.5, "amt": 100}]) == [2, 1]


def test_bad_regex_fails_only_that_rule() -> None:
    step = {"columns": ["use_yn"], "probes": [
        {"key": "bad", **_probe("REGEX", "use_yn", "([")}, {"key": "ok", **_probe("NOT_NULL", "use_yn")}]}
    results, rows = direct.evaluate(step, [("Y",), (None,)])
    by_key = {r["key"]: r for r in results}
    assert rows == 2 and by_key["ok"]["row"] == [2, 1]
    assert by_key["bad"]["error_type"] == "USER_ERROR" and "정규식" in by_key["bad"]["error"]


# ---------------------------------------------------------------------------
# C — 경로 판정
# ---------------------------------------------------------------------------

def test_path_is_trino_when_connected_otherwise_direct(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [], connect=False, source_config=SOURCE))
    status = _run(sm, lambda s: runs.engine_status(s, ds_id))
    assert status.available and status.engine == "DIRECT" and status.engine_datasource_name == "sales-pg"
    assert status.engine_table == "sales-pg.public.orders"

    sm2 = _make_sessionmaker(str(tmp_path / "q2.db"))
    ds2, _ = asyncio.run(_seed(sm2, [], source_config=SOURCE))
    assert _run(sm2, lambda s: runs.engine_status(s, ds2)).engine == "TRINO"


def test_direct_run_needs_a_direct_evaluable_rule(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "FUNCTION", "column": "cust_id", "expected": "f"}],
                                 connect=False, source_config=SOURCE))
    with pytest.raises(runs.RunConflictError, match="Trino 연결이 필요"):
        _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))


# ---------------------------------------------------------------------------
# D — 끝에서 끝
# ---------------------------------------------------------------------------

class _FakeSource:
    """원천 대신 — 받은 접속 정보 · 쿼리를 남긴다.

    행 읽기(``direct.scan``)는 정해 둔 행을 읽는 컬럼 순서대로 넣고, 원천 쿼리(``direct.fetch_table`` — 원천 집계 ·
    위반 샘플)는 같은 행을 담은 SQLite 로 실제 계산한다(PostgreSQL 방언: 큰따옴표 인용 · "public" 스키마,
    ``COLLATE "C"`` 는 SQLite 의 ``BINARY``).
    """

    def __init__(self, rows: list[dict], *, truncated_at: int | None = None, error: str | None = None):
        self.rows = rows
        self.truncated_at = truncated_at
        self.error = error
        self.calls: list[dict] = []
        self.queries: list[str] = []

    async def __call__(self, source, sql, evaluator, **kw):
        self.calls.append({"source": dict(source), "sql": sql, "columns": list(evaluator.columns), **kw})
        if self.error:
            raise direct.DirectError(self.error)
        rows = self.rows[:self.truncated_at] if self.truncated_at else self.rows
        for row in rows:
            evaluator.add([row.get(c) for c in evaluator.columns])
        await kw["on_batch"](evaluator.rows)
        return evaluator.rows, self.truncated_at is not None

    async def fetch_table(self, source, sql, *, limit, **_):
        self.queries.append(sql)
        if self.error:
            raise direct.DirectError(self.error)
        db = sqlite3.connect(":memory:")
        db.execute("ATTACH ':memory:' AS public")
        columns = list(dict.fromkeys(k for r in self.rows for k in r)) or ["_"]
        db.execute(f"CREATE TABLE public.orders ({', '.join(chr(34) + c + chr(34) for c in columns)})")
        db.executemany(f"INSERT INTO public.orders VALUES ({', '.join('?' for _ in columns)})",
                       [[str(r.get(c)) if isinstance(r.get(c), (Decimal, date)) else r.get(c) for c in columns]
                        for r in self.rows])
        cur = db.execute(sql.replace('COLLATE "C"', "COLLATE BINARY"))
        return [d[0] for d in cur.description], [list(r) for r in cur.fetchmany(limit)]


def _direct_run(sm, monkeypatch, fake: _FakeSource, ds_id: int) -> int:
    monkeypatch.setattr(direct, "scan", fake)
    monkeypatch.setattr(direct, "fetch_table", fake.fetch_table)
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert run.engine == "DIRECT"
    assert asyncio.run(_work(sm)) is True
    return run.id


ROWS = [
    {"use_yn": "Y", "reg_ymd": "20240101", "bgn_ymd": "20240101", "end_ymd": "20240201"},
    {"use_yn": "N", "reg_ymd": "2024-1-1", "bgn_ymd": "20240301", "end_ymd": "20240201"},
    {"use_yn": "x", "reg_ymd": None, "bgn_ymd": None, "end_ymd": "1"},
]


def test_worker_reads_source_directly_and_catalog_judges(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_yn, r_cmp, r_re, r_fn, r_rows) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "ACCEPTED_VALUES", "column": "use_yn", "expected": "Y,N"},
        {"check_type": "COLUMN_COMPARE", "column": "bgn_ymd", "expected": "<= end_ymd"},
        {"check_type": "REGEX", "column": "reg_ymd", "expected": "^[0-9]{8}$"},
        {"check_type": "FUNCTION", "column": "cust_id", "expected": "memory.default.f", "name": "함수검사"},
        {"check_type": "ROW_COUNT", "expected": "3"},
    ], connect=False, source_config=SOURCE))
    fake = _FakeSource(ROWS)
    run_id = _direct_run(sm, monkeypatch, fake, ds_id)

    # NULL · 허용 값 · 컬럼 비교 · 행 수는 원천이 집계 쿼리 하나로 센다(TODO-097) — 값의 모양(정규식)만 행을 읽는다
    aggregate = fake.queries[0]
    assert aggregate.startswith('SELECT count(*), COALESCE(SUM(CASE WHEN "use_yn" IS NULL THEN 1 ELSE 0 END), 0), ')
    assert """"use_yn" NOT IN ('Y', 'N')""" in aggregate and '"bgn_ymd" COLLATE "C" <= "end_ymd" COLLATE "C"' in aggregate
    assert aggregate.endswith(' FROM "public"."orders"')
    (call,) = fake.calls
    assert call["sql"] == 'SELECT "reg_ymd" FROM "public"."orders"'
    assert call["source"] == {"type": "postgresql", "host": "pg.local", "port": 5432, "database": "sales",
                              "user": "reader", "password": "s3cret", "ssl": False}
    assert call["max_rows"] == 5_000_000

    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "SUCCEEDED" and run.engine == "DIRECT" and run.rules_evaluated == 5
    assert "건너뛴 규칙" in run.error_message and "함수검사" in run.error_message
    results = _results(sm, ds_id)
    assert set(results) == {r_nn, r_yn, r_cmp, r_re, r_rows}
    assert results[r_nn].passed == "true"
    assert results[r_yn].passed == "false" and (results[r_yn].checked_count, results[r_yn].error_count) == (3, 1)
    assert "원천 직접 검사" in results[r_yn].detail
    assert results[r_cmp].passed == "false" and (results[r_cmp].checked_count, results[r_cmp].error_count) == (2, 1)
    assert results[r_re].passed == "false" and (results[r_re].checked_count, results[r_re].error_count) == (2, 1)
    assert results[r_rows].passed == "true"
    # 원천 집계로 센 규칙의 위반 샘플은 원천 방언의 샘플 쿼리로 이어서 읽는다 — 행 읽기 규칙은 읽는 동안 모은다
    import json
    assert json.loads(results[r_yn].failed_samples) == [{**ROWS[2], "reg_ymd": None}]
    assert json.loads(results[r_cmp].failed_samples)[0]["bgn_ymd"] == "20240301"
    assert json.loads(results[r_re].failed_samples)[0]["reg_ymd"] == "2024-1-1"
    assert _run(sm, lambda s: s.get(cm.Dataset, ds_id)).row_count == 3

    # 실행 상세의 쿼리 — 원천에 낸 집계 · 읽기 쿼리, 계획에는 비밀번호가 없다
    client = TestClient(_app(sm))
    queries = client.get(f"/api/v1/quality/runs/{run_id}/queries").json()
    assert [(q["sql"], q["query_id"]) for q in queries] == [(aggregate, None), (call["sql"], None)]
    plan = _run(sm, lambda s: s.get(runs.QualityRun, run_id)).plan_json
    assert "s3cret" not in plan and '"engine": "DIRECT"' in plan


def test_aggregate_failure_reruns_rule_by_rule(tmp_path, monkeypatch) -> None:
    # 원천 집계 쿼리가 실패하면 규칙별로 다시 내 문제 규칙만 실패로 남긴다(Trino 스캔과 같다)
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_bad) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "MIN_VALUE", "column": "amt", "expected": "0"},
    ], connect=False, source_config=SOURCE))
    fake = _FakeSource([{"use_yn": "Y", "amt": None}])
    real = fake.fetch_table

    async def fetch_table(source, sql, **kw):
        if '"amt"' in sql:
            fake.queries.append(sql)
            raise direct.DirectError("column amt does not exist", "USER_ERROR")
        return await real(source, sql, **kw)

    fake.fetch_table = fetch_table
    run_id = _direct_run(sm, monkeypatch, fake, ds_id)
    assert len(fake.queries) == 3, "묶은 집계 1번 + 규칙별 2번"
    results = _results(sm, ds_id)
    assert results[r_nn].passed == "true"
    assert results[r_bad].passed == "false" and "column amt does not exist" in results[r_bad].detail
    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "PARTIAL" and "쿼리가 실패한 규칙" in run.error_message


def test_mysql_aggregate_compares_bytes() -> None:
    # MySQL · MariaDB 의 기본 정렬 규칙은 대소문자를 무시한다 — 워커 · Trino 처럼 바이트로 비교한다
    types = {"use_yn": "char(1)", "name": "varchar(20)", "cust_id": "int unsigned", "doc": "json", "price": "double"}
    agg = rule_direct.aggregate(RuleSpec(1, "ACCEPTED_VALUES", "use_yn", "Y,N"), types, "mysql", ("db", "t"), 5)
    assert agg.exprs[1] == ("COALESCE(SUM(CASE WHEN `use_yn` IS NOT NULL AND CAST(CONVERT(`use_yn` USING utf8mb4) AS BINARY) "
                            "NOT IN (X'59', X'4E') THEN 1 ELSE 0 END), 0)")
    assert agg.sample_sql.startswith("SELECT * FROM `db`.`t` WHERE ") and agg.sample_sql.endswith(" LIMIT 5")
    uq = rule_direct.aggregate(RuleSpec(2, "UNIQUE", "name"), types, "mariadb", ("db", "t"), 5)
    assert uq.exprs == ("count(DISTINCT CAST(CONVERT(`name` USING utf8mb4) AS BINARY))",)
    assert rule_direct.aggregate(RuleSpec(3, "ACCEPTED_VALUES", "cust_id", "1"), types, "mysql", ("db", "t"), 5).exprs[1] \
        .endswith("CAST(CAST(`cust_id` AS CHAR) AS BINARY) NOT IN (X'31') THEN 1 ELSE 0 END), 0)")
    # DB 마다 뜻이 갈리는 규칙은 행을 읽는다(None)
    assert rule_direct.aggregate(RuleSpec(4, "UNIQUE", "doc"), types, "mysql", ("db", "t"), 5) is None
    assert rule_direct.aggregate(RuleSpec(5, "ACCEPTED_VALUES", "price", "1.5"), types, "mysql", ("db", "t"), 5) is None
    assert rule_direct.aggregate(RuleSpec(6, "COLUMN_COMPARE", "name", "< cust_id"), types, "mysql", ("db", "t"), 5) is None
    assert rule_direct.aggregate(RuleSpec(7, "REGEX", "name", "^a"), types, "mysql", ("db", "t"), 5) is None
    assert rule_direct.aggregate(RuleSpec(8, "MIN_VALUE", "name", "0"), types, "mysql", ("db", "t"), 5) is None


def test_type_family() -> None:
    assert [rule_direct.type_family(t) for t in (
        "int(11) unsigned", "decimal(10,2)", "character varying(45)", "enum('a','b')", "timestamp without time zone",
        "jsonb", "year", "bit(1)", "tinyint(1)", "citext")] == [
        "integer", "numeric", "text", "enum", "timestamp without time zone", None, None, None, "integer", None]


def test_truncated_read_is_noted(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_re, r_nn) = asyncio.run(_seed(sm, [{"check_type": "REGEX", "column": "reg_ymd", "expected": "^2"},
                                                 {"check_type": "NOT_NULL", "column": "use_yn"}],
                                                connect=False, source_config=SOURCE))
    run_id = _direct_run(sm, monkeypatch, _FakeSource(ROWS, truncated_at=2), ds_id)
    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "SUCCEEDED" and "앞 2행만 진단" in run.error_message
    results = _results(sm, ds_id)
    assert "앞 2행만 검사" in results[r_re].detail
    # 원천 집계는 상한과 상관없이 테이블 전체를 센다
    assert "앞 2행만" not in results[r_nn].detail and results[r_nn].checked_count == 3


def test_source_read_failure_fails_run_without_results(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"},
                                      {"check_type": "UNIQUE", "column": "cust_id"},
                                      {"check_type": "REGEX", "column": "reg_ymd", "expected": "^2"}],
                                 connect=False, source_config=SOURCE))
    run_id = _direct_run(sm, monkeypatch, _FakeSource([], error="원천에 접속하지 못했습니다: refused"), ds_id)
    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "FAILED" and "원천을 직접 읽어 평가하지 못했습니다" in run.error_message
    assert "refused" in run.error_message and _results(sm, ds_id) == {}


def test_direct_run_without_source_config_fails_at_claim(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}], connect=False))
    monkeypatch.setattr(direct, "scan", _FakeSource([]))
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert asyncio.run(_work(sm)) is False
    failed = _run(sm, lambda s: runs.get_run(s, run.id))
    assert failed.status == "FAILED" and "원천 데이터 소스 'sales-pg' 에 접속 설정이 없습니다" in failed.error_message


# ---------------------------------------------------------------------------
# E — 경로별 지원 (2단계): 커스텀 SQL(Trino), 참조관계(같은 원본) · 커스텀 Python(원천 직접)
# ---------------------------------------------------------------------------

def test_trino_runs_custom_sql_in_dataset_catalog_and_schema(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_sql, r_py) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "CUSTOM_SQL", "expected": "SELECT count(*) FROM orders WHERE amt < 0;", "name": "음수 금액"},
        {"check_type": "CUSTOM_PYTHON", "expected": '{"module": "m", "fn": "f"}', "name": "파이썬"},
    ]))
    fake = _FakeTrino(lambda sql: [7] if "amt < 0" in sql else [100, 0])
    run_id = _start(sm, monkeypatch, fake, ds_id)
    custom = next(c for c in fake.calls if "amt < 0" in c["sql"])
    # 테이블 이름만 적어도 이 데이터셋의 Trino 카탈로그·스키마에서 찾는다
    assert custom["sql"] == "SELECT count(*) FROM orders WHERE amt < 0"
    assert (custom["catalog"], custom["schema"]) == ("pg_sales", "public")
    results = _results(sm, ds_id)
    assert results[r_sql].passed == "false" and results[r_sql].actual_value == "위반 7건"
    assert results[r_nn].passed == "true" and r_py not in results
    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "SUCCEEDED" and "커스텀 Python" in run.error_message and "파이썬" in run.error_message


async def _parent(sm, *, same_source: bool = True) -> int:
    """참조할 데이터셋 — 같은 원본(pg-1) 또는 다른 원본(pg-2)."""
    async with sm() as s:
        if same_source:
            src_id = (await s.execute(select(cm.Datasource.id).where(cm.Datasource.datasource_id == "pg-1"))).scalar_one()
        else:
            other = cm.Datasource(datasource_id="pg-2", name="crm-pg", type="postgresql")
            s.add(other)
            await s.flush()
            src_id = other.id
        d = cm.Dataset(urn="pg-1.public.customers.dataset", name="customers", datasource_id=src_id,
                       qualified_name="pg-1.public.customers")
        s.add(d)
        await s.commit()
        return d.id


def _add_referential(sm, ds_id: int, parent_id: int) -> int:
    async def add(s):
        rule = QualityRule(dataset_id=ds_id, rule_name="고객 참조", check_type="REFERENTIAL", column_name="cust_id",
                           expected_value=f"{parent_id}#id", threshold=100, severity="WARNING", is_active="true")
        s.add(rule)
        await s.flush()
        return rule.id
    return _run(sm, add)


def test_direct_referential_joins_inside_the_same_source(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [], connect=False, source_config=SOURCE))
    parent_id = asyncio.run(_parent(sm))

    rule_id = _add_referential(sm, ds_id, parent_id)
    queries: list[str] = []

    async def fetch_row(source, sql, **_):
        queries.append(sql)
        assert source["password"] == "s3cret"
        return [10, 2]

    monkeypatch.setattr(direct, "fetch_row", fetch_row)
    _direct_run(sm, monkeypatch, _FakeSource([]), ds_id)
    assert queries == [
        'SELECT count(*), sum(CASE WHEN p.k IS NULL THEN 1 ELSE 0 END) FROM "public"."orders" c '
        'LEFT JOIN (SELECT DISTINCT "id" AS k FROM "public"."customers" WHERE "id" IS NOT NULL) p '
        'ON c."cust_id" = p.k WHERE c."cust_id" IS NOT NULL']
    result = _results(sm, ds_id)[rule_id]
    assert result.passed == "false" and (result.checked_count, result.error_count) == (10, 2)
    assert "원천 직접 검사" in result.detail


def test_direct_referential_needs_the_same_source(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}],
                                 connect=False, source_config=SOURCE))
    parent_id = asyncio.run(_parent(sm, same_source=False))

    rule_id = _add_referential(sm, ds_id, parent_id)
    _direct_run(sm, monkeypatch, _FakeSource([{"use_yn": "Y"}]), ds_id)
    result = _results(sm, ds_id)[rule_id]
    assert result.passed == "false" and "같은 원본 데이터 소스" in result.detail


def test_direct_runs_custom_python_plugin(tmp_path, monkeypatch) -> None:
    pytest.importorskip("pandas")
    checks = tmp_path / "custom_checks"
    checks.mkdir()
    (checks / "order_checks.py").write_text(
        "def negative(df, params):\n"
        "    bad = int((df['amt'] < params['min']).sum())\n"
        "    return bad == 0, f'{bad}건', f'최소 {params[\"min\"]} 미만 {bad}건 / {len(df)}행'\n"
        "def broken(df, params):\n"
        "    raise KeyError('nope')\n")
    monkeypatch.chdir(tmp_path)  # direct.checks_dir 기본값 custom_checks — 워커를 실행한 디렉터리 기준
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_ok, r_bad, r_missing, r_sql) = asyncio.run(_seed(sm, [
        {"check_type": "CUSTOM_PYTHON", "expected": '{"module": "order_checks", "fn": "negative", "params": {"min": 0}}'},
        {"check_type": "CUSTOM_PYTHON", "expected": '{"module": "order_checks", "fn": "broken"}'},
        {"check_type": "CUSTOM_PYTHON", "expected": '{"module": "nothing_here", "fn": "f"}'},
        {"check_type": "CUSTOM_SQL", "expected": "SELECT 0", "name": "sql 규칙"},
    ], connect=False, source_config=SOURCE, columns={"use_yn": "char(1)", "amt": "numeric"}))
    fake = _FakeSource([{"use_yn": "Y", "amt": Decimal("10")}, {"use_yn": "N", "amt": Decimal("-1")},
                        {"use_yn": "Y", "amt": None}])
    run_id = _direct_run(sm, monkeypatch, fake, ds_id)

    # 플러그인은 데이터프레임 전체를 받는다 — 스키마의 모든 컬럼을 읽는다
    assert fake.calls[0]["sql"] == 'SELECT "use_yn", "amt" FROM "public"."orders"'
    results = _results(sm, ds_id)
    assert results[r_ok].passed == "false" and results[r_ok].actual_value == "1건"
    assert "최소 0 미만 1건 / 3행" in results[r_ok].detail and "커스텀 Python" in results[r_ok].detail
    assert results[r_bad].passed == "false" and "KeyError" in results[r_bad].detail
    assert results[r_missing].passed == "false" and "플러그인 모듈이 없습니다" in results[r_missing].detail
    assert r_sql not in results
    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "PARTIAL" and "sql 규칙" in run.error_message


def test_engine_status_lists_types_of_the_path(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [], connect=False, source_config=SOURCE))
    direct_types = _run(sm, lambda s: runs.engine_status(s, ds_id)).check_types
    assert "CUSTOM_PYTHON" in direct_types and "REFERENTIAL" in direct_types and "FUNCTION" not in direct_types
    sm2 = _make_sessionmaker(str(tmp_path / "q2.db"))
    ds2, _ = asyncio.run(_seed(sm2, []))
    trino_types = _run(sm2, lambda s: runs.engine_status(s, ds2)).check_types
    # Trino 연결 + 워커가 직접 읽을 수 있는 원본(PostgreSQL) — 커스텀 Python 은 같은 실행에서 원천 직접으로
    assert "CUSTOM_SQL" in trino_types and "CUSTOM_PYTHON" in trino_types
    sm3 = _make_sessionmaker(str(tmp_path / "q3.db"))
    ds3, _ = asyncio.run(_seed(sm3, [], source_type="hive"))
    hive_types = _run(sm3, lambda s: runs.engine_status(s, ds3)).check_types
    assert "CUSTOM_SQL" in hive_types and "CUSTOM_PYTHON" not in hive_types


# ---------------------------------------------------------------------------
# F — 한 실행이 두 경로를 함께 (TODO-095): Trino 연결 + 커스텀 Python
# ---------------------------------------------------------------------------

def _plugin(tmp_path, monkeypatch) -> None:
    checks = tmp_path / "custom_checks"
    checks.mkdir()
    (checks / "order_checks.py").write_text(
        "def negative(df, params):\n"
        "    bad = int((df['amt'] < 0).sum())\n"
        "    return bad == 0, f'{bad}건', f'음수 {bad}건 / {len(df)}행'\n")
    monkeypatch.chdir(tmp_path)


def test_trino_run_evaluates_custom_python_by_reading_source(tmp_path, monkeypatch) -> None:
    _plugin(tmp_path, monkeypatch)
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_py) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "CUSTOM_PYTHON", "expected": '{"module": "order_checks", "fn": "negative"}', "name": "음수"},
    ], source_config=SOURCE, columns={"use_yn": "char(1)", "amt": "numeric"}))
    status = _run(sm, lambda s: runs.engine_status(s, ds_id))
    assert status.engine == "TRINO" and "CUSTOM_PYTHON" in status.check_types
    trino = _FakeTrino(lambda _sql: [3, 0])
    source = _FakeSource([{"use_yn": "Y", "amt": Decimal("10")}, {"use_yn": "N", "amt": Decimal("-1")},
                          {"use_yn": "Y", "amt": None}])
    monkeypatch.setattr(direct, "scan", source)
    run_id = _start(sm, monkeypatch, trino, ds_id)

    # Trino 에는 스캔 하나, 원천에는 커스텀 Python 용 읽기 하나 — 원천 접속 정보는 원본 데이터 소스의 것
    assert len(trino.calls) == 1 and '"pg_sales"."public"."orders"' in trino.calls[0]["sql"]
    assert len(source.calls) == 1 and source.calls[0]["sql"] == 'SELECT "use_yn", "amt" FROM "public"."orders"'
    assert source.calls[0]["source"]["host"] == "pg.local" and source.calls[0]["source"]["password"] == "s3cret"
    results = _results(sm, ds_id)
    assert results[r_nn].passed == "true"
    assert results[r_py].passed == "false" and results[r_py].actual_value == "1건" and "음수 1건 / 3행" in results[r_py].detail
    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "SUCCEEDED" and run.engine == "TRINO" and run.rules_evaluated == 2
    # 진단 쿼리 보기에도 원천 읽기가 Trino 쿼리 뒤에 나온다(쿼리 ID 없음)
    from app.quality import execution
    from app.quality.models import QualityRun

    async def plan(s):
        return execution.run_queries(await s.get(QualityRun, run_id))
    shown = _run(sm, plan)
    assert [q["key"] for q in shown] == ["scan", "python"] and shown[1]["query_id"] is None


def test_trino_run_skips_custom_python_when_source_has_no_connection(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_py) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "CUSTOM_PYTHON", "expected": '{"module": "m", "fn": "f"}', "name": "파이썬"},
    ]))
    source = _FakeSource([])
    monkeypatch.setattr(direct, "scan", source)
    run_id = _start(sm, monkeypatch, _FakeTrino(lambda _sql: [3, 0]), ds_id)
    assert source.calls == [] and r_py not in _results(sm, ds_id)
    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "SUCCEEDED"
    assert "커스텀 Python 을 원천 직접으로 평가하지 못해" in run.error_message and "접속 설정이 없습니다" in run.error_message


def test_trino_run_custom_python_read_failure_keeps_trino_results(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_py) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "CUSTOM_PYTHON", "expected": '{"module": "m", "fn": "f"}', "name": "파이썬"},
    ], source_config=SOURCE))
    monkeypatch.setattr(direct, "scan", _FakeSource([], error="원천에 접속하지 못했습니다: refused"))
    run_id = _start(sm, monkeypatch, _FakeTrino(lambda _sql: [3, 0]), ds_id)
    results = _results(sm, ds_id)
    assert results[r_nn].passed == "true" and r_py not in results
    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "PARTIAL" and "CUSTOM_PYTHON(m.f)" in run.error_message


# ---------------------------------------------------------------------------
# G: 날짜 · 시각 상수(DATE '…' · TIMESTAMP '…'), Oracle · SQL Server 원천 직접 (TODO-095)
# ---------------------------------------------------------------------------

DATE_TYPES = {"paid_at": "timestamp", "d": "date", "amt": "integer"}


def test_date_literals_in_conditions_on_both_paths() -> None:
    text = "paid_at >= date '2024-01-01' AND d IN (DATE '2024-01-01', DATE '2024-02-29') OR paid_at < TIMESTAMP '2024-01-01 10:00:00'"
    assert rule_sql.condition_sql(text, DATE_TYPES) == (
        "\"paid_at\" >= DATE '2024-01-01' AND \"d\" IN (DATE '2024-01-01', DATE '2024-02-29')"
        " OR \"paid_at\" < TIMESTAMP '2024-01-01 10:00:00'")
    assert rule_direct.condition_tree(text, DATE_TYPES) == {"or": [
        {"and": [{"col": "paid_at", "op": ">=", "right": {"date": "2024-01-01"}},
                 {"col": "d", "op": "IN", "values": [{"date": "2024-01-01"}, {"date": "2024-02-29"}]}]},
        {"and": [{"col": "paid_at", "op": "<", "right": {"timestamp": "2024-01-01 10:00:00"}}]},
    ]}
    for bad, why in (("d = DATE '2024-02-30'", "달력에 없는"), ("d = DATE '2024/01/01'", "모양이 틀렸습니다"),
                     ("paid_at = TIMESTAMP '2024-01-01 25:00:00'", "달력에 없는")):
        with pytest.raises(rule_sql.ExpressionError, match=why):
            rule_sql.condition_sql(bad, DATE_TYPES)
        with pytest.raises(rule_sql.ExpressionError, match=why):
            rule_direct.condition_tree(bad, DATE_TYPES)


def test_logical_with_date_literal_and_two_date_columns_on_direct_path() -> None:
    """DATE 상수 조건과 DATE 컬럼끼리 비교가 원천 직접 경로에서도 같은 값을 낸다(날짜끼리 비교가 실행 전체를 깨뜨리지 않는다)."""
    from quality_worker import direct

    def _probe(check, column, expected):
        return rule_direct.probe(RuleSpec(1, check, column, expected), {**DATE_TYPES, "a": "date", "b": "date"})

    probe = _probe("LOGICAL", None, "IF d >= DATE '2024-01-02' THEN d <= paid_at")
    step = {"columns": ["d", "paid_at"], "probes": [{"key": "rule:1", **probe}]}
    rows = [(date(2024, 1, 1), datetime(2024, 1, 5)), (date(2024, 1, 3), datetime(2024, 1, 2, 23)),
            (date(2024, 1, 4), None)]
    results, n = direct.evaluate(step, rows)
    assert n == 3 and results == [{"key": "rule:1", "row": [3, 2, 1]}]
    two_dates = {"columns": ["a", "b"], "probes": [{"key": "rule:2", **_probe("COLUMN_COMPARE", "a", "<= b")}]}
    results, _ = direct.evaluate(two_dates, [(date(2024, 1, 1), date(2024, 1, 2)), (date(2024, 1, 3), date(2024, 1, 2))])
    assert results == [{"key": "rule:2", "row": [2, 2, 1]}]


def test_oracle_and_sqlserver_dialects() -> None:
    assert rule_direct.select_sql("oracle", "S", "T", ["A", "b"]) == 'SELECT "A", "b" FROM "S"."T"'
    assert rule_direct.select_sql("mssql", "dbo", "t", ["a"]) == "SELECT [a] FROM [dbo].[t]"
    assert rule_direct.quote_ident("mssql", "a]b") == "[a]]b]"
    assert rule_direct.limited_select("oracle", "x", "FROM t", 5) == "SELECT x FROM t FETCH FIRST 5 ROWS ONLY"
    assert rule_direct.limited_select("mssql", "x", "FROM t", 5) == "SELECT TOP (5) x FROM t"
    assert rule_direct.limited_select("mysql", "x", "FROM t", 5) == "SELECT x FROM t LIMIT 5"
    assert {"oracle", "mssql"} <= set(rule_direct.DIRECT_SOURCE_TYPES)


def test_direct_source_for_oracle_and_sqlserver() -> None:
    from app.quality import execution

    base = {"host": "db.example", "username": "u", "password": "p"}
    ora = execution._direct_source("oracle", {**base, "service_name": "FREEPDB1", "sid": "XE"})
    assert (ora["port"], ora["service_name"], ora["sid"], ora["database"]) == (1521, "FREEPDB1", "", "")
    assert execution._direct_source("oracle", {**base, "sid": "XE"})["sid"] == "XE"
    with pytest.raises(ValueError, match="Service Name 또는 SID"):
        execution._direct_source("oracle", base)
    ms = execution._direct_source("mssql", {**base, "database": "master"}, "sales")
    assert (ms["port"], ms["database"]) == (1433, "sales"), "데이터셋이 있는 데이터베이스에 접속한다"
    assert execution._direct_source("mssql", base)["database"] == "master"
    assert execution._source_database("mssql", "srv.sales.dbo.orders") == "sales"
    assert execution._source_database("oracle", "srv.S.T") is None


def test_continuity_is_counted_at_oracle_and_sqlserver_sources() -> None:
    """데이터 연속성은 명세가 없어 원천 집계로만 센다 — Oracle · SQL Server 에서도 단위로 자르는 식을 만든다."""
    types = {"d": "date", "ymd": "varchar(8)"}

    def exprs(source_type, column, expected):
        return rule_direct.aggregate(RuleSpec(1, "CONTINUITY", column, expected), types, source_type, ("s", "t"), 5).exprs

    assert exprs("oracle", "d", "DAY")[0] == "count(DISTINCT TRUNC(\"d\", 'DD'))"
    assert exprs("mssql", "d", "MONTH")[1] == "min(DATEADD(month, DATEDIFF(month, 0, [d]), 0))"
    assert "SUBSTR(TRIM(\"ymd\"), 1, 6)" in exprs("oracle", "ymd", "MONTH YYYYMMDD")[2]
    assert "SUBSTRING(LTRIM(RTRIM([ymd])), 1, 6)" in exprs("mssql", "ymd", "MONTH YYYYMMDD")[0]
    # 정렬 규칙이 갈리는 집계는 행을 읽는다
    assert rule_direct.aggregate(RuleSpec(1, "ACCEPTED_VALUES", "ymd", "a,b"), types, "mssql", ("s", "t"), 5) is None
