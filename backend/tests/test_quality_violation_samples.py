# SPDX-License-Identifier: Apache-2.0
"""위반 샘플(품질 워커) 테스트 — TODO-095 4단계 (자기완결형, SQLite — Trino · 원천은 가짜).

- A: 규칙별 샘플 쿼리 — 위반 조건으로 행 전체 몇 개, 샘플이 없는 유형(행 수 · 날짜 타입 컬럼의 날짜 형식 · 커스텀 SQL)
- B: Trino 경로 — 결과 제출 응답의 샘플 쿼리(실패 규칙만), 워커가 이어서 내 붙임, 다른 워커의 제출 거부
- C: 원천 직접 경로 — 읽는 동안 모은 위반 행(NULL · 중복 · 허용 값), 통과한 규칙은 남기지 않음, 참조관계는 이어서
- D: 저장 형식 — 컬럼 · 값 길이 상한

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_violation_samples.py
"""

from __future__ import annotations

import asyncio
import importlib
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.quality import execution, rule_sql, runs
from app.quality.models import QualityResult
from app.quality.rule_sql import RuleSpec, TableRef
from tests.test_quality_trino_runs import (  # noqa: F401 — _no_alerts 는 autouse 픽스처
    _app,
    _make_sessionmaker,
    _no_alerts,
    _run,
    _seed,
    _work,
    worker_trino,
)

# 품질 워커 소스는 test_quality_trino_runs 가 sys.path 에 넣는다 — 그 뒤에 불러온다
worker_direct = importlib.import_module("quality_worker.direct")

T = TableRef("pg_sales", "public", "orders")
TYPES = {"use_yn": "char(1)", "cust_id": "integer", "reg_dt": "timestamp", "bgn": "varchar(8)", "end": "varchar(8)"}


def _compile(check: str, column: str | None = "use_yn", expected: str | None = None, reference=None):
    return rule_sql.compile_rule(RuleSpec(1, check, column, expected), T, TYPES, reference)


# ---------------------------------------------------------------- A: 샘플 쿼리

def test_sample_sql_selects_violating_rows() -> None:
    table = '"pg_sales"."public"."orders"'
    assert _compile("NOT_NULL").sample_sql == f'SELECT * FROM {table} WHERE "use_yn" IS NULL LIMIT 5'
    accepted = _compile("ACCEPTED_VALUES", expected="Y,N").sample_sql
    assert accepted == (f'SELECT * FROM {table} WHERE "use_yn" IS NOT NULL AND rtrim(CAST("use_yn" AS varchar)) '
                        "NOT IN ('Y', 'N') LIMIT 5")
    unique = _compile("UNIQUE", "cust_id").sample_sql
    assert unique == (f'SELECT * FROM {table} WHERE "cust_id" IN (SELECT "cust_id" FROM {table} WHERE "cust_id" IS NOT NULL '
                      'GROUP BY "cust_id" HAVING count(*) > 1) ORDER BY "cust_id" LIMIT 5')
    assert _compile("COLUMN_COMPARE", "bgn", "<= end").sample_sql.endswith('WHERE NOT ("bgn" <= "end") LIMIT 5')
    assert _compile("MIN_VALUE", "cust_id", "10").sample_sql.endswith('WHERE TRY_CAST("cust_id" AS double) < 10.0 LIMIT 5')
    ref = _compile("REFERENTIAL", "cust_id", reference=(TableRef("pg_sales", "public", "customers"), "id")).sample_sql
    assert ref.startswith(f"SELECT c.* FROM {table} c LEFT JOIN (SELECT DISTINCT \"id\" AS k FROM ")
    assert ref.endswith('WHERE c."cust_id" IS NOT NULL AND p.k IS NULL LIMIT 5')
    # 위반 행이 따로 없는 유형
    assert _compile("ROW_COUNT", None, "10").sample_sql is None
    assert _compile("DATE_FORMAT", "reg_dt", "YYYY-MM-DD").sample_sql is None, "날짜 타입은 DBMS 가 보장 — 오류 0"
    assert _compile("CUSTOM_SQL", None, "SELECT 0").sample_sql is None


# ---------------------------------------------------------------- B: Trino 경로

class _Fake:
    """Trino 대역 — SQL 에 따라 (컬럼, 행 목록) 또는 오류."""

    def __init__(self, respond):
        self.respond = respond
        self.calls: list[dict] = []

    async def __call__(self, endpoint, sql, *, on_poll=None, catalog=None, schema=None, **_):
        self.calls.append({"sql": sql, "catalog": catalog, "schema": schema})
        if on_poll is not None:
            await on_poll(f"q{len(self.calls)}")
        got = self.respond(sql)
        if isinstance(got, Exception):
            raise got
        columns, rows = got
        return worker_trino.TrinoResult(columns=columns, rows=rows, query_id=f"q{len(self.calls)}")


def _results(sm, ds_id: int) -> dict[int, QualityResult]:
    async def go(s):
        rows = (await s.execute(select(QualityResult).where(QualityResult.dataset_id == ds_id))).scalars().all()
        return {r.rule_id: r for r in rows}
    return _run(sm, go)


def test_trino_run_fetches_samples_for_failed_rules(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_ok, r_rows) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "NOT_NULL", "column": "cust_id"},
        {"check_type": "ROW_COUNT", "expected": "999999"},
    ]))

    def respond(sql: str):
        if "LIMIT 5" in sql:
            assert '"use_yn" IS NULL' in sql, "통과한 규칙 · 샘플이 없는 규칙(행 수)은 샘플을 읽지 않는다"
            return ["use_yn", "cust_id", "memo"], [[None, 7, "x" * 500], [None, 9, None]]
        return [], [[100, 2, 0]]  # count(*), use_yn NULL 2건, cust_id NULL 0건

    fake = _Fake(respond)
    monkeypatch.setattr(worker_trino, "execute", fake)
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert asyncio.run(_work(sm)) is True

    assert len(fake.calls) == 2
    sample_call = fake.calls[1]
    assert (sample_call["catalog"], sample_call["schema"]) == ("pg_sales", "public")
    results = _results(sm, ds_id)
    samples = json.loads(results[r_nn].failed_samples)
    assert samples == [{"use_yn": None, "cust_id": "7", "memo": "x" * 200}, {"use_yn": None, "cust_id": "9", "memo": None}]
    assert results[r_ok].failed_samples is None and results[r_rows].failed_samples is None
    assert results[r_rows].passed == "false", "행 수 규칙은 실패했지만 위반 행이 없다"
    # 화면의 결과 API 에도 나온다
    client = TestClient(_app(sm))
    shown = {r["rule_id"]: r for r in client.get(f"/api/v1/quality/datasets/{ds_id}/results").json()}
    assert shown[r_nn]["failed_samples"][0]["cust_id"] == "7"
    assert _run(sm, lambda s: runs.get_run(s, run.id)).status == "SUCCEEDED"


def test_sample_query_failure_keeps_run_result(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn,) = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    monkeypatch.setattr(worker_trino, "execute", _Fake(
        lambda sql: worker_trino.TrinoQueryError("boom") if "LIMIT 5" in sql else ([], [[10, 1]])))
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert asyncio.run(_work(sm)) is True
    assert _run(sm, lambda s: runs.get_run(s, run.id)).status == "SUCCEEDED"
    result = _results(sm, ds_id)[r_nn]
    assert result.passed == "false" and result.failed_samples is None


def test_samples_only_from_the_worker_that_finished_the_run(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn,) = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    monkeypatch.setattr(worker_trino, "execute", _Fake(lambda sql: ([], []) if "LIMIT 5" in sql else ([], [[10, 1]])))
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert asyncio.run(_work(sm)) is True
    client = TestClient(_app(sm))
    body = {"worker_id": "other", "results": [{"key": f"rule:{r_nn}", "columns": ["a"], "rows": [["1"]]}]}
    assert client.post("/api/v1/quality/worker/heartbeat", json={"worker_id": "other"}).status_code == 200
    assert client.post(f"/api/v1/quality/worker/runs/{run.id}/samples", json=body).status_code == 409
    body["worker_id"] = "w1"
    assert client.post(f"/api/v1/quality/worker/runs/{run.id}/samples", json=body).json() == {"attached": 1}
    assert json.loads(_results(sm, ds_id)[r_nn].failed_samples) == [{"a": "1"}]
    # 이미 샘플이 있으면 덮지 않는다
    body["results"][0]["rows"] = [["2"]]
    assert client.post(f"/api/v1/quality/worker/runs/{run.id}/samples", json=body).json() == {"attached": 0}


# ---------------------------------------------------------------- C: 원천 직접 경로

def test_direct_run_collects_violating_rows_while_reading(tmp_path, monkeypatch) -> None:
    # 행을 읽어 판정하는 규칙(정규식 등)은 읽는 동안 위반 행을 모은다
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_re, r_ok) = asyncio.run(_seed(sm, [
        {"check_type": "REGEX", "column": "use_yn", "expected": "^[YN]$"},
        {"check_type": "REGEX", "column": "reg_ymd", "expected": "^2"},
    ], connect=False, source_type="mysql", source_config={"host": "db.local", "port": 3306}))
    data = [("Y", "2024"), ("X", "2024"), ("y", "2024"), ("N", "2025")]

    async def fake_scan(source, sql, evaluator, *, max_rows, **_):
        for values in data:
            row = {"use_yn": values[0], "reg_ymd": values[1]}
            evaluator.add([row[c] for c in evaluator.columns])
        return len(data), False

    monkeypatch.setattr(worker_direct, "scan", fake_scan)
    _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert asyncio.run(_work(sm)) is True
    results = _results(sm, ds_id)
    assert json.loads(results[r_re].failed_samples) == [{"use_yn": "X", "reg_ymd": "2024"}, {"use_yn": "y", "reg_ymd": "2024"}]
    assert results[r_ok].passed == "true" and results[r_ok].failed_samples is None


def test_direct_aggregate_samples_follow_up_in_source_dialect(tmp_path, monkeypatch) -> None:
    # 원천이 집계로 센 규칙(NULL · 고유값 · 허용 값)의 위반 행은 실행 뒤 원천 방언의 샘플 쿼리로 읽는다
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_uq, r_yn, r_ok) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "UNIQUE", "column": "cust_id"},
        {"check_type": "ACCEPTED_VALUES", "column": "use_yn", "expected": "Y,N"},
        {"check_type": "NOT_NULL", "column": "cust_id"},
    ], connect=False, source_type="mysql", source_config={"host": "db.local", "port": 3306}))
    sqls: list[str] = []

    async def fake_table(source, sql, *, limit, **_):
        sqls.append(sql)
        if sql.endswith("LIMIT 5"):
            return ["use_yn", "cust_id"], [[None, 2]] if "IS NULL" in sql else [["X", 3]]
        # 행 6 · NULL 2 · 고유 5 · 진단 4 위반 1 · NULL 0
        return [], [[6, 2, 5, 4, 1, 0]]

    monkeypatch.setattr(worker_direct, "fetch_table", fake_table)
    _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert asyncio.run(_work(sm)) is True
    assert len(sqls) == 4, "집계 1번 + 실패한 규칙 3개의 샘플"
    assert sqls[0].startswith("SELECT count(*), COALESCE(SUM(CASE WHEN `use_yn` IS NULL") and "FROM `public`.`orders`" in sqls[0]
    assert "X'59', X'4E'" in sqls[0]
    assert any("GROUP BY `cust_id` HAVING count(*) > 1" in q for q in sqls[1:])
    results = _results(sm, ds_id)
    assert json.loads(results[r_nn].failed_samples) == [{"use_yn": None, "cust_id": "2"}]
    assert json.loads(results[r_yn].failed_samples) == [{"use_yn": "X", "cust_id": "3"}]
    assert results[r_uq].passed == "false" and results[r_uq].failed_samples
    assert results[r_ok].passed == "true" and results[r_ok].failed_samples is None


def test_direct_referential_samples_follow_up(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_ref,) = asyncio.run(_seed(sm, [{"check_type": "REFERENTIAL", "column": "cust_id", "expected": "ID#id"}],
                                        connect=False, source_type="mysql", source_config={"host": "db.local", "port": 3306}))

    async def parent(s):
        from app.catalog import models as cm
        child = await s.get(cm.Dataset, ds_id)
        p = cm.Dataset(urn="pg-1.public.customers.dataset", name="customers", datasource_id=child.datasource_id,
                       catalog_id=child.catalog_id, qualified_name="pg-1.public.customers")
        s.add(p)
        await s.flush()
        rule = await s.get(__import__("app.quality.models", fromlist=["QualityRule"]).QualityRule, r_ref)
        rule.expected_value = f"{p.id}#id"
    _run(sm, parent)
    sqls: list[str] = []

    async def fake_table(source, sql, *, limit, **_):
        sqls.append(sql)
        if "LIMIT 5" in sql:
            return ["cust_id", "name"], [[99, "ghost"]]
        return [], [[10, 1]]

    monkeypatch.setattr(worker_direct, "fetch_table", fake_table)
    _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert asyncio.run(_work(sm)) is True
    assert sqls[1] == ("SELECT c.* FROM `public`.`orders` c LEFT JOIN (SELECT DISTINCT `id` AS k FROM `public`.`customers` "
                       "WHERE `id` IS NOT NULL) p ON c.`cust_id` = p.k WHERE c.`cust_id` IS NOT NULL AND p.k IS NULL LIMIT 5")
    assert json.loads(_results(sm, ds_id)[r_ref].failed_samples) == [{"cust_id": "99", "name": "ghost"}]


def test_probe_keeps_only_requested_samples() -> None:
    probe = worker_direct.Probe({"key": "rule:1", "kind": "nulls", "column": "a", "samples": 2})
    for v in (None, 1, None, None):
        probe.add({"a": v, "b": v})
    assert probe.values() == [3] and len(probe.samples) == 2
    quiet = worker_direct.Probe({"key": "rule:2", "kind": "nulls", "column": "a"})
    quiet.add({"a": None})
    assert quiet.samples == [], "예전 카탈로그(samples 없음)에는 모으지 않는다"


# ---------------------------------------------------------------- D: 저장 형식

def test_sample_rows_limits_columns_and_values() -> None:
    columns = [f"c{i}" for i in range(40)]
    rows = execution.sample_rows(columns, [list(range(40))] * 8)
    assert len(rows) == 5 and len(rows[0]) == 30 and rows[0]["c3"] == "3"
    assert execution.sample_rows(["a"], []) is None and execution.sample_rows(None, None) is None
    assert execution.sample_rows(["t"], [["2006-02-15 21:30:53.000 UTC"]]) == [{"t": "2006-02-15 21:30:53+00:00"}]
