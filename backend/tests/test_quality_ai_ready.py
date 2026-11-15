# SPDX-License-Identifier: Apache-2.0
"""AI-Ready 품질 지표(TODO-096 5단계) 테스트 — 더미 값 빈도(DUMMY_VALUE), 데이터 연속성(CONTINUITY), AI-Ready 묶음.

- A: Trino 경로 — 규칙 → 집계 식, 판정
- B: 원천 직접 경로 — 원천 집계 식(PostgreSQL · MySQL), 행 읽기 명세(워커 계산)
- C: 지표 — 진단 기준 ↔ AI-Ready 지표, 품질 지표 정의 검증

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_ai_ready.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.quality import indicators, rule_direct, rule_sql
from app.quality.rule_sql import Compiled, ExpressionError, Outcome, RuleSpec, TableRef
from app.quality.schemas import QualityDefinitionWrite, QualityRuleCreate

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "argus-catalog-quality-worker" / "src"))
from quality_worker import direct  # noqa: E402

T = TableRef("pg_sales", "public", "orders")
TYPES = {"amt": "bigint", "name": "varchar(20)", "base_ymd": "varchar(8)", "base_ym": "varchar(7)",
         "reg_dt": "date", "paid_at": "timestamp(3)", "flag": "boolean"}


def _spec(check: str, column: str | None, expected: str | None = None, threshold: float = 100.0) -> RuleSpec:
    return RuleSpec(id=1, check_type=check, column_name=column, expected_value=expected, threshold=threshold)


def _compile(check: str, column: str | None, expected: str | None = None, threshold: float = 100.0):
    return rule_sql.compile_rule(_spec(check, column, expected, threshold), T, TYPES)


# ---------------------------------------------------------------------------
# A — Trino 경로
# ---------------------------------------------------------------------------

def test_dummy_value_sql_per_column_type() -> None:
    num = _compile("DUMMY_VALUE", "amt")
    assert isinstance(num, Compiled)
    # 숫자 컬럼은 숫자 모양 더미 값만 — 'N/A' 같은 문자열은 숫자 컬럼에 들어갈 수 없다
    # 00000000 은 문자열에서만 더미 값 — 숫자로는 0 이라 정상 값 0 을 세게 된다
    assert num.exprs[1] == 'count_if("amt" IN (999999, 9999999, 99999999, 999999999, -999, -9999, -99999))'
    assert rule_sql.dummy_numbers(["0", "007", "-0.5", "0.0", "-01"]) == ["0", "-0.5", "0.0"]
    text = _compile("DUMMY_VALUE", "name", "N/A, 없음 ,it's")
    assert "IN ('N/A', '없음', 'it''s')" in text.exprs[1] and text.sample_sql
    # 숫자 컬럼에 문자 더미 값만 적으면 규칙 오류
    bad = _compile("DUMMY_VALUE", "amt", "N/A,없음")
    assert isinstance(bad, Outcome) and not bad.passed and "숫자 더미 값" in bad.detail


def test_dummy_value_judge_uses_threshold() -> None:
    strict = _compile("DUMMY_VALUE", "name")
    out = strict.judge(100, [80, 4])
    assert (out.passed, out.checked_count, out.error_count, out.actual_value) == (False, 80, 4, "4건 (5.00%)")
    assert strict.judge(100, [80, 0]).passed
    loose = _compile("DUMMY_VALUE", "name", threshold=95.0)
    assert loose.judge(100, [80, 4]).passed, "정상 95% 이상이면 통과"
    assert not loose.judge(100, [80, 5]).passed
    assert strict.judge(0, [0, 0]).passed, "진단할 값이 없으면 통과"


def test_continuity_spec_parsing() -> None:
    assert rule_sql.continuity_spec("day") == rule_sql.Continuity("DAY", "day")
    c = rule_sql.continuity_spec("DAY YYYYMMDD")
    assert (c.prefix_len, c.prefix_format) == (8, "%Y%m%d")
    c = rule_sql.continuity_spec("MONTH YYYY-MM-DD")
    assert (c.prefix_len, c.prefix_format) == (7, "%Y-%m"), "단위까지만 잘라 같은 달을 묶는다"
    c = rule_sql.continuity_spec("HOUR YYYY-MM-DD HH24:MI:SS")
    assert (c.prefix_len, c.prefix_format, c.like) == (13, "%Y-%m-%d %H", "____-__-__ __:__:__")
    for bad in (None, "", "WEEK", "DAY DD/MM/YYYY", "DAY YYYYMM"):
        with pytest.raises(ExpressionError):
            rule_sql.continuity_spec(bad)


def test_continuity_sql_for_temporal_and_string_dates() -> None:
    temporal = _compile("CONTINUITY", "paid_at", "DAY")
    key = "date_trunc('day', CAST(\"paid_at\" AS timestamp))"
    assert temporal.exprs == (f"count(DISTINCT {key})", f"min({key})", f"max({key})")
    text = _compile("CONTINUITY", "base_ymd", "MONTH YYYYMMDD")
    assert "regexp_like" in text.exprs[0] and "substr(" in text.exprs[0] and ", 1, 6)" in text.exprs[0]
    # 문자열 컬럼인데 형식을 적지 않음 · 단위가 틀림 → 규칙 오류
    for column, expected in (("base_ymd", "DAY"), ("paid_at", "WEEK")):
        out = _compile("CONTINUITY", column, expected)
        assert isinstance(out, Outcome) and not out.passed


def test_continuity_judge_counts_missing_periods() -> None:
    day = _compile("CONTINUITY", "reg_dt", "DAY")
    # 1월 1일 ~ 1월 10일 = 10일 중 8일에 값이 있음 → 빈 날 2개
    out = day.judge(500, [8, date(2024, 1, 1), datetime(2024, 1, 10, 0, 0)])
    assert (out.passed, out.checked_count, out.error_count, out.actual_value) == (False, 10, 2, "빈 일 2개")
    assert "2024-01-01 ~ 2024-01-10" in out.detail
    assert day.judge(500, [10, "2024-01-01", "2024-01-10"]).passed, "ISO 문자열(워커가 날짜를 문자열로 준다)"
    loose = _compile("CONTINUITY", "reg_dt", "DAY", threshold=80.0)
    assert loose.judge(500, [8, "2024-01-01", "2024-01-10"]).passed
    month = _compile("CONTINUITY", "base_ymd", "MONTH YYYYMMDD")
    out = month.judge(50, [11, "202301", "202412"])
    assert (out.checked_count, out.error_count) == (24, 13)
    hour = _compile("CONTINUITY", "paid_at", "HOUR")
    assert hour.judge(9, [3, "2024-01-01T22:00:00", "2024-01-02T01:00:00"]).error_count == 1
    year = _compile("CONTINUITY", "paid_at", "YEAR")
    assert year.judge(9, [3, "2020-01-01 00:00:00", "2024-01-01 00:00:00"]).error_count == 2
    assert day.judge(0, [0, None, None]).passed, "값이 없으면 진단할 기간이 없다"
    unreadable = month.judge(5, [2, "20xx01", "202412"])
    assert not unreadable.passed and "날짜로 읽을 수 없습니다" in unreadable.detail


def test_continuity_periods() -> None:
    a, b = datetime(2023, 11, 30, 23), datetime(2024, 2, 1, 1)
    assert rule_sql.continuity_periods(a, b, "YEAR") == 2
    assert rule_sql.continuity_periods(a, b, "MONTH") == 4
    assert rule_sql.continuity_periods(a, b, "DAY") == 64
    assert rule_sql.continuity_periods(a, b, "HOUR") == 63 * 24 + 1 - 23 + 1


# ---------------------------------------------------------------------------
# B — 원천 직접 경로
# ---------------------------------------------------------------------------

def _agg(check: str, column: str, expected: str | None, source: str, types: dict[str, str]):
    return rule_direct.aggregate(_spec(check, column, expected), types, source, ("sales", "orders"), 5)


def test_direct_aggregate_dummy_value() -> None:
    num = _agg("DUMMY_VALUE", "amt", "999999,N/A", "postgresql", {"amt": "bigint"})
    assert '"amt" IN (999999)' in num.exprs[1], "숫자 컬럼은 숫자 더미 값만"
    text = _agg("DUMMY_VALUE", "name", "N/A,-", "mysql", {"name": "varchar(20)"})
    # MySQL 문자열은 바이트로 비교한다(TODO-097 — 대소문자 · 악센트를 가리지 않는 정렬 규칙을 피함)
    assert "`name` IS NOT NULL" in text.exprs[1] and "X'4E2F41', X'2D'" in text.exprs[1] and text.sample_sql
    assert _agg("DUMMY_VALUE", "amt", "N/A", "postgresql", {"amt": "integer"}) is None, "compile_rule 이 먼저 규칙 오류"
    assert rule_direct.aggregate(_spec("DUMMY_VALUE", "reg_dt"), {"reg_dt": "date"}, "postgresql",
                                 ("s", "t"), 5) is None, "날짜 등은 워커가 읽는다"


def test_direct_aggregate_continuity_per_dialect() -> None:
    pg = _agg("CONTINUITY", "reg_dt", "MONTH", "postgresql", {"reg_dt": "date"})
    assert pg.exprs[0] == "count(DISTINCT date_trunc('month', \"reg_dt\"))"
    my = _agg("CONTINUITY", "paid_at", "HOUR", "mysql", {"paid_at": "datetime"})
    assert my.exprs[1] == "min(DATE_FORMAT(`paid_at`, '%Y-%m-%d %H:00:00'))"
    text = _agg("CONTINUITY", "base_ymd", "DAY YYYYMMDD", "mariadb", {"base_ymd": "varchar(8)"})
    assert text.exprs[2] == "max(CASE WHEN TRIM(`base_ymd`) LIKE '________' THEN SUBSTRING(TRIM(`base_ymd`) FROM 1 FOR 8) END)"
    with pytest.raises(ValueError, match="날짜 · 시각 타입이 아닙니다"):
        _agg("CONTINUITY", "base_ymd", "DAY", "postgresql", {"base_ymd": "varchar(8)"})
    assert "CONTINUITY" in rule_direct.DIRECT_CHECK_TYPES and "CONTINUITY" not in rule_direct.PROBE_TYPES


def test_direct_probe_dummy_value_with_worker() -> None:
    probe = rule_direct.probe(_spec("DUMMY_VALUE", "flag", "999999,N/A"), {"flag": "boolean"})
    assert probe["forbid"] == [r"^(?:999999(?:\.0+)?|N/A)$"]
    step = {"columns": ["flag"], "char_columns": [], "probes": [{"key": "k", **probe}]}
    rows = [("999999",), ("999999.00",), ("N/A",), ("N/A x",), (None,), ("1999999",)]
    (result,), _ = direct.evaluate(step, rows)
    assert result["row"][1:] == [5, 3], "비-NULL 5개 중 더미 값 3개(소수 꼬리 허용, 부분 일치는 아님)"
    num = rule_direct.probe(_spec("DUMMY_VALUE", "x", "N/A,-999"), {"x": "double"})
    assert num["forbid"] == [r"^(?:\-999(?:\.0+)?)$"], "숫자 컬럼은 숫자 더미 값만"
    with pytest.raises(ExpressionError):
        rule_direct.probe(_spec("DUMMY_VALUE", "x", "N/A"), {"x": "double"})


# ---------------------------------------------------------------------------
# C — 지표, 정의
# ---------------------------------------------------------------------------

def test_ai_ready_mapping() -> None:
    assert indicators.ai_ready_of("DUMMY") == ("AI_VALIDITY", "통계적 타당성")
    assert indicators.ai_ready_of("CONTINUITY") == ("AI_TIMELINESS", "데이터 연속성")
    assert indicators.ai_ready_of("REFERENCE") == ("AI_CONSISTENCY", "참조 무결성")
    assert indicators.ai_ready_of("VOLUME") is None and indicators.ai_ready_of("NOPE") is None
    assert indicators.indicator_of("DUMMY_VALUE").key == "DUMMY"
    assert indicators.indicator_of("CONTINUITY").key == "CONTINUITY"
    definable = {i.key for i in indicators.definable()}
    assert {"DUMMY", "CONTINUITY"} <= definable
    assert indicators.suggests("CONTINUITY", "base_ymd")


def test_definition_and_rule_validation() -> None:
    d = QualityDefinitionWrite(name="더미", category="DUMMY", threshold=99.0)
    assert (d.check_type, d.expected_value) == ("DUMMY_VALUE", None)
    assert QualityDefinitionWrite(name="연속", category="CONTINUITY", expected_value="day yyyymmdd").check_type == "CONTINUITY"
    for bad in (None, "WEEK", "DAY DD-MM-YYYY"):
        with pytest.raises(ValidationError):
            QualityDefinitionWrite(name="연속", category="CONTINUITY", expected_value=bad)
    with pytest.raises(ValidationError):
        QualityRuleCreate(dataset_id=1, rule_name="r", check_type="CONTINUITY", column_name="base_ymd",
                          expected_value="FORTNIGHT")
    assert QualityRuleCreate(dataset_id=1, rule_name="r", check_type="DUMMY_VALUE", column_name="amt").expected_value is None
