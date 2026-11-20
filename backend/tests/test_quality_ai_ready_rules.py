# SPDX-License-Identifier: Apache-2.0
"""AI-Ready 품질 지표 — 남은 진단 유형(TODO-096 남은 일 2) 테스트.

데이터 · 라벨 다양성(DIVERSITY), 여러 컬럼 키 유일성(COMPOSITE_UNIQUE), 허용 값의 표준 코드 그룹 연결, 연속성의 주(WEEK) 단위.

- A: Trino 경로 — 규칙 → 쿼리, 판정
- B: 원천 직접 경로 — DB 방언(PostgreSQL · MySQL · Oracle · SQL Server)
- C: 지표 · 정의 · 이름
- D: 끝에서 끝 — 표준 코드 그룹을 풀어 실행에 남기기, 실제 워커 코드로 Trino · 원천 직접 실행

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_ai_ready_rules.py
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.quality import definitions, execution, indicators, rule_direct, rule_sql
from app.quality.reports import check_type_label
from app.quality.rule_sql import Compiled, ExpressionError, Outcome, RuleSpec, TableRef
from app.quality.schemas import QualityDefinitionWrite, QualityRuleCreate, QualitySqlPreviewRequest
from app.standard.models import CodeGroup, CodeValue, StandardDictionary
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "argus-catalog-quality-worker" / "src"))
from quality_worker import direct  # noqa: E402

T = TableRef("pg_sales", "public", "orders")
TYPES = {"name": "varchar(20)", "base_ymd": "varchar(8)", "paid_at": "timestamp(3)", "cust_id": "integer"}


def _spec(check: str, column: str | None, expected: str | None = None, threshold: float = 100.0) -> RuleSpec:
    return RuleSpec(id=1, check_type=check, column_name=column, expected_value=expected, threshold=threshold)


def _compile(check: str, column: str | None, expected: str | None = None, threshold: float = 100.0):
    return rule_sql.compile_rule(_spec(check, column, expected, threshold), T, TYPES)


# ---------------------------------------------------------------------------
# A — Trino 경로
# ---------------------------------------------------------------------------

def test_diversity_sql_and_judge() -> None:
    c = _compile("DIVERSITY", "name")
    assert isinstance(c, Compiled) and c.exprs == () and c.sample_sql is None
    assert c.own_sql == ('SELECT COALESCE(SUM(n), 0), MAX(n), MIN(n), COUNT(*) FROM (SELECT count(*) AS n '
                         'FROM "pg_sales"."public"."orders" WHERE "name" IS NOT NULL GROUP BY "name") g')
    # 기본 상한 90% — 최빈값 95건 / 100건이면 상한을 넘친 5건이 오류
    out = c.judge(0, [100, 95, 1, 3])
    assert (out.passed, out.checked_count, out.error_count, out.actual_value) == (False, 100, 5, "최빈값 95.0%")
    assert "최다/최소 빈도 95.0:1" in out.detail and "고유값 3개" in out.detail
    assert c.judge(0, [100, 90, 2, 6]).passed, "상한과 같으면 통과"
    assert c.judge(0, [0, None, None, 0]).passed, "값이 없으면 진단할 분포가 없다"
    strict = _compile("DIVERSITY", "name", "60%")
    assert not strict.judge(0, [3, 2, 1, 2]).passed and strict.judge(0, [3, 2, 1, 2]).error_count == 1
    assert rule_sql.diversity_limit(None) == rule_sql.DEFAULT_DIVERSITY_MAX
    for bad in ("0", "101", "abc", "nan"):
        assert isinstance(_compile("DIVERSITY", "name", bad), Outcome)
        with pytest.raises(ExpressionError):
            rule_sql.check_expression("DIVERSITY", bad)


def test_composite_unique_sql_and_judge() -> None:
    c = _compile("COMPOSITE_UNIQUE", None, '["base_ymd", "name"]')
    assert isinstance(c, Compiled) and c.label == "COMPOSITE_UNIQUE(base_ymd, name)"
    assert c.own_sql == (
        'SELECT COALESCE(SUM(n), 0), COALESCE(SUM(CASE WHEN n > 1 THEN n ELSE 0 END), 0) FROM (SELECT count(*) AS n '
        'FROM "pg_sales"."public"."orders" WHERE "base_ymd" IS NOT NULL AND "name" IS NOT NULL '
        'GROUP BY "base_ymd", "name") g')
    assert c.sample_sql.startswith(
        'SELECT c.* FROM "pg_sales"."public"."orders" c JOIN (SELECT "base_ymd" AS k0, "name" AS k1')
    assert c.sample_sql.endswith(
        'ON c."base_ymd" = d.k0 AND c."name" = d.k1 ORDER BY c."base_ymd", c."name" LIMIT 5')
    out = c.judge(0, [10, 4])
    assert (out.passed, out.checked_count, out.error_count) == (False, 10, 4) and "키 (base_ymd, name)" in out.detail
    loose = _compile("COMPOSITE_UNIQUE", None, "base_ymd,name", threshold=60.0)
    assert loose.judge(0, [10, 4]).passed, "겹치지 않는 행 60% 이상"
    missing = _compile("COMPOSITE_UNIQUE", None, '["base_ymd", "nope"]')
    assert isinstance(missing, Outcome) and "nope" in missing.detail
    for bad in (None, '["name"]', '["name", "NAME"]'):
        with pytest.raises(ExpressionError):
            rule_sql.composite_columns(bad)
    assert "COMPOSITE_UNIQUE" in rule_sql.TABLE_LEVEL_TYPES
    rule = QualityRuleCreate(dataset_id=1, rule_name="r", check_type="COMPOSITE_UNIQUE", column_name="name",
                             expected_value='["base_ymd","name"]')
    assert rule.column_name is None, "테이블 수준 — 키 컬럼은 기대값의 목록"


def test_code_group_reference() -> None:
    ref = rule_sql.code_group_expected(12)
    assert rule_sql.code_group_ref(ref) == 12 and rule_sql.code_group_ref('{"code_group_id": 3}') == 3
    for other in (None, "", '["Y","N"]', "Y,N", '{"code_group_id": "3"}', '{"code_group_id": true}', "{oops"):
        assert rule_sql.code_group_ref(other) is None
    # 진단 요청 때 풀지 못한 연결 — 그룹이 지워졌거나 사용 중인 값이 없다
    out = _compile("ACCEPTED_VALUES", "name", ref)
    assert isinstance(out, Outcome) and not out.passed and "표준 코드 그룹 #12" in out.detail
    rule_sql.check_expression("ACCEPTED_VALUES", ref)
    with pytest.raises(ExpressionError):
        rule_sql.check_expression("ACCEPTED_VALUES", '{"group": 1}')
    d = QualityDefinitionWrite(name="성별 코드", category="CODE", check_type="ACCEPTED_VALUES", expected_value=ref)
    assert d.expected_value == ref
    # 값이 많은 코드 그룹 — 판정 설명에는 앞의 10개만
    many = _compile("ACCEPTED_VALUES", "name", ",".join(f"C{i:02d}" for i in range(25)))
    assert "외 15개" in many.judge(0, [5, 1]).detail


def test_continuity_week() -> None:
    assert rule_sql.continuity_spec("week") == rule_sql.Continuity("WEEK", "week")
    c = rule_sql.continuity_spec("WEEK YYYY-MM-DD")
    assert (c.prefix_len, c.prefix_format) == (10, "%Y-%m-%d"), "주는 일까지 잘라 날짜로 읽는다"
    with pytest.raises(ExpressionError):
        rule_sql.continuity_spec("WEEK YYYYMM")
    temporal = _compile("CONTINUITY", "paid_at", "WEEK")
    assert temporal.exprs[0] == "count(DISTINCT date_trunc('week', CAST(\"paid_at\" AS timestamp)))"
    text = _compile("CONTINUITY", "base_ymd", "WEEK YYYYMMDD")
    assert ("date_trunc('week', try(date_parse(substr(CAST(\"base_ymd\" AS varchar), 1, 8), '%Y%m%d')))"
            in text.exprs[0])
    # 2024-01-01(월) ~ 2024-01-29(월) = 5주, 그 가운데 3주에 값 → 빈 주 2개 (Trino 는 시각을 문자열로 돌려준다)
    out = text.judge(0, [3, "2024-01-01 00:00:00.000", "2024-01-29 00:00:00.000"])
    assert (out.passed, out.checked_count, out.error_count, out.actual_value) == (False, 5, 2, "빈 주 2개")
    # 잘리지 않은 값이 와도 같은 주로 센다 — 일요일(2024-01-07)은 1일(월)의 주
    assert rule_sql.continuity_periods(datetime(2024, 1, 7), datetime(2024, 1, 8), "WEEK") == 2
    assert rule_sql.continuity_periods(datetime(2024, 1, 1), datetime(2024, 1, 7, 23), "WEEK") == 1
    assert rule_sql.continuity_periods(datetime(2023, 12, 31), datetime(2024, 1, 1), "WEEK") == 2


# ---------------------------------------------------------------------------
# B — 원천 직접 경로
# ---------------------------------------------------------------------------

def test_grouped_rules_direct_dialects() -> None:
    types = {"name": "varchar(20)", "cust_id": "integer"}
    my_sql, my_sample = rule_direct.composite_unique_sql("mysql", ("sales", "orders"), '["name","cust_id"]', types, 5)
    # MySQL 문자열은 바이트로 묶는다(기본 정렬 규칙이 대소문자를 무시) — 숫자는 그대로
    assert "GROUP BY CAST(CONVERT(`name` USING utf8mb4) AS BINARY), `cust_id`) g" in my_sql
    assert "ON CAST(CONVERT(c.`name` USING utf8mb4) AS BINARY) = d.k0 AND c.`cust_id` = d.k1" in my_sample
    assert my_sample.endswith(" LIMIT 5")
    ms_sql, ms_sample = rule_direct.composite_unique_sql("mssql", ("dbo", "orders"), '["name","cust_id"]', types, 5)
    assert ms_sample.startswith("SELECT TOP (5) c.* FROM [dbo].[orders] c JOIN")
    # SQL Server 문자열도 값의 바이트로 묶는다 — 기본 정렬 규칙은 대소문자를, 모든 정렬 규칙의 = · GROUP BY 는 끝 공백을 무시한다
    assert "GROUP BY CAST([name] AS VARBINARY(MAX)), [cust_id]) g" in ms_sql
    assert "ON CAST(c.[name] AS VARBINARY(MAX)) = d.k0 AND c.[cust_id] = d.k1" in ms_sample
    assert "GROUP BY CAST([name] AS VARBINARY(MAX))) g" in rule_direct.diversity_sql("mssql", ("dbo", "orders"), "name", types)
    assert "GROUP BY [cust_id]) g" in rule_direct.diversity_sql("mssql", ("dbo", "orders"), "cust_id", types)
    _, ora_sample = rule_direct.composite_unique_sql("oracle", ("S", "ORDERS"), '["name","cust_id"]', types, 5)
    assert ora_sample.endswith("FETCH FIRST 5 ROWS ONLY")
    assert rule_direct.diversity_sql("postgresql", ("sales", "orders"), "name", types) == (
        'SELECT COALESCE(SUM(n), 0), MAX(n), MIN(n), COUNT(*) FROM (SELECT count(*) AS n FROM "sales"."orders" '
        'WHERE "name" IS NOT NULL GROUP BY "name") g')
    with pytest.raises(ExpressionError):
        rule_direct.diversity_sql("postgresql", ("s", "t"), "nope", types)
    for t in ("DIVERSITY", "COMPOSITE_UNIQUE"):
        assert t in rule_direct.DIRECT_CHECK_TYPES and t not in rule_direct.PROBE_TYPES


def test_direct_continuity_week_per_dialect() -> None:
    keys = {
        "postgresql": "date_trunc('week', \"reg_dt\")",
        "mysql": "DATE_SUB(DATE(`reg_dt`), INTERVAL WEEKDAY(`reg_dt`) DAY)",
        "oracle": "TRUNC(\"reg_dt\", 'IW')",
        "mssql": "DATEADD(day, DATEDIFF(day, 0, [reg_dt]) / 7 * 7, 0)",
    }
    for source, key in keys.items():
        agg = rule_direct.aggregate(_spec("CONTINUITY", "reg_dt", "WEEK"), {"reg_dt": "date"}, source, ("s", "t"), 5)
        assert agg.exprs[0] == f"count(DISTINCT {key})"
    with pytest.raises(ValueError, match=r"주\(WEEK\)"):
        rule_direct.aggregate(_spec("CONTINUITY", "base_ymd", "WEEK YYYYMMDD"), {"base_ymd": "varchar(8)"},
                              "postgresql", ("s", "t"), 5)


# ---------------------------------------------------------------------------
# C — 지표 · 정의 · 이름
# ---------------------------------------------------------------------------

def test_new_indicators_and_definitions() -> None:
    assert indicators.indicator_of("DIVERSITY").key == "DIVERSITY"
    assert indicators.indicator_of("COMPOSITE_UNIQUE").key == "COMPOSITE_KEY"
    assert indicators.ai_ready_of("DIVERSITY") == ("AI_COMPLETENESS", "데이터 · 라벨 다양성")
    assert indicators.ai_ready_of("COMPOSITE_KEY") == ("AI_UNIQUENESS", "데이터 유일성")
    definable = {i.key for i in indicators.definable()}
    assert "DIVERSITY" in definable and "COMPOSITE_KEY" not in definable, "여러 컬럼 키는 테이블마다 달라 직접 만든다"
    assert indicators.suggests("DIVERSITY", "gender_cd")
    assert QualityDefinitionWrite(name="쏠림", category="DIVERSITY").check_type == "DIVERSITY"
    with pytest.raises(ValidationError):
        QualityDefinitionWrite(name="쏠림", category="DIVERSITY", expected_value="150")
    assert QualityDefinitionWrite(name="주", category="CONTINUITY", expected_value="week").expected_value == "week"
    assert any(d[2] == "DIVERSITY" for d in definitions.DEFAULTS)
    assert check_type_label("DIVERSITY") == "값 분포 다양성"
    assert check_type_label("COMPOSITE_UNIQUE") == "여러 컬럼 키 고유값"


# ---------------------------------------------------------------------------
# D — 끝에서 끝
# ---------------------------------------------------------------------------

def _with_standards(sm) -> int:
    """표준 사전 · 코드 그룹(Y · N 사용, X 사용 안 함)을 만들고 그룹 번호를 돌려준다."""
    async def go(s):
        conn = await s.connection()
        for model in (StandardDictionary, CodeGroup, CodeValue):
            await conn.run_sync(lambda sc, m=model: m.__table__.create(sc, checkfirst=True))
        dic = StandardDictionary(dict_name="기관 표준")
        s.add(dic)
        await s.flush()
        group = CodeGroup(dictionary_id=dic.id, group_name="사용 여부")
        s.add(group)
        await s.flush()
        s.add_all([CodeValue(code_group_id=group.id, code_value="N", code_name="아니오", sort_order=2),
                   CodeValue(code_group_id=group.id, code_value="Y", code_name="예", sort_order=1),
                   CodeValue(code_group_id=group.id, code_value="X", code_name="폐지", sort_order=3, is_active="false")])
        await s.flush()
        return group.id
    return _run(sm, go)


def test_code_group_resolved_into_the_run(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    gid = _with_standards(sm)
    ds_id, (r_code, r_lost, r_div, r_key) = asyncio.run(_seed(sm, [
        {"check_type": "ACCEPTED_VALUES", "column": "use_yn", "expected": rule_sql.code_group_expected(gid)},
        {"check_type": "ACCEPTED_VALUES", "column": "use_yn", "expected": rule_sql.code_group_expected(gid + 100)},
        {"check_type": "DIVERSITY", "column": "use_yn", "expected": "80"},
        {"check_type": "COMPOSITE_UNIQUE", "expected": '["reg_ymd", "cust_id"]'},
    ]))
    options = _run(sm, lambda s: definitions.list_code_groups(s))
    assert [(o.id, o.name, o.dictionary_name, o.value_count, o.sample_values) for o in options] == [
        (gid, "사용 여부", "기관 표준", 2, ["Y", "N"])]
    api = TestClient(_app(sm)).get("/api/v1/quality/code-groups")
    assert api.status_code == 200 and [g["id"] for g in api.json()] == [gid]

    def respond(sql: str):
        if "MIN(n)" in sql:
            return [100, 85, 15, 2]          # 다양성 — 최빈값 85%
        if "n > 1" in sql:
            return [100, 6]                  # 여러 컬럼 키 — 겹치는 행 6
        if sql.endswith("LIMIT 5"):
            return [1, 2]                    # 위반 샘플
        return [100, 100, 3]                 # 스캔 — 행 수, 허용 값(진단, 오류)

    fake = _FakeTrino(respond)
    _start(sm, monkeypatch, fake, ds_id)
    scan = next(c["sql"] for c in fake.calls if c["sql"].startswith("SELECT count(*), "))
    assert "NOT IN ('Y', 'N')" in scan, "사용 중인 코드 값만, 정렬 순서대로"
    results = _results(sm, ds_id)
    assert results[r_code].passed == "false" and (results[r_code].checked_count, results[r_code].error_count) == (100, 3)
    assert results[r_lost].passed == "false" and f"#{gid + 100}" in results[r_lost].detail
    assert results[r_div].passed == "false" and results[r_div].error_count == 5 and "상한 80%" in results[r_div].detail
    assert results[r_key].passed == "false" and (results[r_key].checked_count, results[r_key].error_count) == (100, 6)

    # 미리보기도 진단처럼 코드 값으로 푼다
    preview = _run(sm, lambda s: definitions.preview_sql(s, QualitySqlPreviewRequest(
        check_type="ACCEPTED_VALUES", expected_value=rule_sql.code_group_expected(gid), column_name="use_yn")))
    assert "NOT IN ('Y', 'N')" in preview.sql
    resolved = _run(sm, lambda s: execution.resolve_code_group(s, "REGEX", rule_sql.code_group_expected(gid)))
    assert resolved == rule_sql.code_group_expected(gid), "허용 값이 아닌 유형은 그대로"


SOURCE = {"host": "pg.local", "port": 5432, "database": "sales", "username": "reader", "password": "s3cret"}


def test_direct_grouped_rules_run_on_the_source(tmp_path, monkeypatch) -> None:
    """원천 직접 — 묶음 집계 쿼리를 원천(여기서는 SQLite 로 흉내)에 그대로 내 같은 판정을 얻는다."""
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_div, r_key) = asyncio.run(_seed(sm, [
        {"check_type": "DIVERSITY", "column": "use_yn"},
        {"check_type": "COMPOSITE_UNIQUE", "expected": '["reg_ymd", "cust_id"]'},
    ], connect=False, source_config=SOURCE, columns={"use_yn": "char(1)", "reg_ymd": "varchar(8)", "cust_id": "integer"}))
    rows = [("Y", "20240101", 1), ("Y", "20240101", 1), ("Y", "20240102", 1), ("N", "20240101", 2),
            ("Y", None, 3), (None, "20240103", 3)]
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH ':memory:' AS public")
    db.execute('CREATE TABLE public.orders ("use_yn", "reg_ymd", "cust_id")')
    db.executemany("INSERT INTO public.orders VALUES (?, ?, ?)", rows)
    queries: list[str] = []

    async def fetch_row(_source, sql, **_):
        queries.append(sql)
        return list(db.execute(sql).fetchone())

    async def fetch_table(_source, sql, *, limit, **_):
        cur = db.execute(sql)
        return [d[0] for d in cur.description], [list(r) for r in cur.fetchmany(limit)]

    monkeypatch.setattr(direct, "fetch_row", fetch_row)
    monkeypatch.setattr(direct, "fetch_table", fetch_table)
    run = _run(sm, lambda s: execution.runs.create_run(s, ds_id, "alice"))
    assert run.engine == "DIRECT"
    assert asyncio.run(_work(sm)) is True
    assert len(queries) == 2 and all(" g" in q for q in queries)
    results = _results(sm, ds_id)
    # use_yn: Y 4 · N 1 (NULL 제외 5) → 최빈값 80% ≤ 90% 통과
    assert results[r_div].passed == "true" and results[r_div].checked_count == 5
    # (reg_ymd, cust_id): (20240101, 1) 이 두 번 — NULL 이 든 행은 빼고 5행 중 2행이 겹침
    assert results[r_key].passed == "false" and (results[r_key].checked_count, results[r_key].error_count) == (5, 2)
    samples = results[r_key].failed_samples
    assert samples and '"20240101"' in samples
