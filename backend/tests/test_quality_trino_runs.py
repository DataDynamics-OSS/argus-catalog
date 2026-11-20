# SPDX-License-Identifier: Apache-2.0
"""품질 진단 실행(Trino, 품질 워커) 테스트 (자기완결형, SQLite — Trino 는 가짜).

워커(D)는 확장 ``extensions/argus-catalog-quality-worker`` 의 실제 코드를 불러 카탈로그의 워커용 API 에
ASGI 로 붙여 돌린다 — 카탈로그와 워커 사이의 약속(계획·보고·결과 제출)을 양쪽 코드로 함께 검증한다.

- A: 규칙 → Trino SQL 변환과 건수 판정 (rule_sql)
- B: 엔진 대상 해석 — QUERY 연결의 remote_name, Trino 데이터 소스, 연결 없음
- C: 대기열 — 요청·중복·취소·응답 없음 판정, 같은 원본 데이터 소스는 동시에 하나만
- D: 워커 — 스캔 한 번으로 평가·건수 저장, 스캔 실패 시 규칙별 재실행, 전부 실패, 취소, 참조관계, 종료 시 되돌리기
- E: Trino REST 클라이언트 — nextUri 따라가기, 오류, 취소
- F: API — 실행 요청·조회·취소·다시 실행, 목록 필터, 워커용 API 의 소유 확인
- G: 품질 지표 — 규칙 유형 대응, 지표를 고른 실행은 그 규칙만 평가, 결과에 실행 ID, 실행별 결과 API

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_trino_runs.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.alert.service as alert_service
import app.catalog.models as cm
from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session
from app.permissions.models import Permission
from app.quality import indicators, rule_sql, runs, trino_client
from app.quality import router as quality_router
from app.quality.models import (
    DataProfile,
    QualityDefinition,
    QualityJob,
    QualityJobRun,
    QualityResult,
    QualityRule,
    QualityRun,
    QualityScore,
    QualityWorker,
)
from app.quality.rule_sql import Compiled, Outcome, RuleSpec, TableRef

# 품질 워커 확장의 소스 — 설치하지 않고 모노레포 경로에서 불러온다
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "argus-catalog-quality-worker" / "src"))
from quality_worker import trino_client as worker_trino  # noqa: E402
from quality_worker.catalog_client import CatalogClient  # noqa: E402
from quality_worker.config import Settings as WorkerSettings  # noqa: E402
from quality_worker.runner import Runner  # noqa: E402

_TABLES = (
    cm.Organization, cm.System, cm.Datasource, cm.Catalog, cm.CatalogConnection,
    cm.DatasourceConfiguration, cm.Dataset, cm.Owner, cm.DatasetSchema, cm.DatasetProperty,
    QualityDefinition, QualityRule, QualityResult, QualityScore, DataProfile, QualityJob, QualityJobRun, QualityRun, QualityWorker,
    Permission,
)
T = TableRef("pg_sales", "public", "orders")


@pytest.fixture(autouse=True)
def _no_alerts(monkeypatch):
    async def fake(_session, _dataset_id, _score, _failed):
        return None

    monkeypatch.setattr(alert_service, "evaluate_quality_alerts", fake)


def _make_sessionmaker(db_path: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async def _init():
        async with engine.begin() as conn:
            for model in _TABLES:
                await conn.run_sync(lambda sc, m=model: m.__table__.create(sc, checkfirst=True))

    asyncio.run(_init())
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed(sm, rules: list[dict], *, connect: bool = True, remote_name: str | None = "pg_sales",
                columns: dict[str, str] | None = None, source_type: str = "postgresql",
                source_config: dict | None = None) -> tuple[int, list[int]]:
    """PostgreSQL 데이터 소스의 데이터셋 + (선택) 그 카탈로그에 QUERY 로 연결된 Trino + 규칙.

    ``source_config`` 를 주면 원천 데이터 소스의 접속 설정으로 저장한다(원천 직접 진단).
    """
    async with sm() as s:
        src = cm.Datasource(datasource_id="pg-1", name="sales-pg", type=source_type)
        trino = cm.Datasource(datasource_id="trino-1", name="lake-trino", type="trino")
        s.add_all([src, trino])
        await s.flush()
        cat = cm.Catalog(catalog_key="pg-1", name="sales", kind="postgresql")
        s.add(cat)
        await s.flush()
        s.add(cm.DatasourceConfiguration(
            datasource_id=trino.id,
            config_json=json.dumps({"host": "trino.local", "port": 8080, "use_ssl": False, "username": "dq"})))
        if source_config is not None:
            s.add(cm.DatasourceConfiguration(datasource_id=src.id, config_json=json.dumps(source_config)))
        if connect:
            s.add(cm.CatalogConnection(catalog_id=cat.id, datasource_id=trino.id, role="QUERY",
                                       remote_name=remote_name))
        d = cm.Dataset(urn="pg-1.public.orders.dataset", name="orders", datasource_id=src.id,
                       catalog_id=cat.id, qualified_name="pg-1.public.orders")
        s.add(d)
        await s.flush()
        for i, (name, typ) in enumerate((columns or {
            "use_yn": "char(1)", "reg_ymd": "varchar(8)", "bgn_ymd": "varchar(8)", "end_ymd": "varchar(8)",
            "cust_id": "integer", "amt": "numeric",
        }).items()):
            s.add(cm.DatasetSchema(dataset_id=d.id, field_path=name, field_type="STRING", native_type=typ,
                                   ordinal=i))
        ids = []
        for i, r in enumerate(rules):
            rule = QualityRule(dataset_id=d.id, rule_name=r.get("name", f"r{i}"), check_type=r["check_type"],
                               column_name=r.get("column"), expected_value=r.get("expected"),
                               threshold=r.get("threshold", 100), severity="WARNING",
                               is_active=r.get("active", "true"))
            s.add(rule)
            await s.flush()
            ids.append(rule.id)
        await s.commit()
        return d.id, ids


def _run(sm, fn):
    async def go():
        async with sm() as s:
            out = await fn(s)
            await s.commit()
            return out
    return asyncio.run(go())


def _spec(check: str, column: str | None = "c", expected: str | None = None, threshold: float = 100.0,
          rule_id: int = 1) -> RuleSpec:
    return RuleSpec(id=rule_id, check_type=check, column_name=column, expected_value=expected,
                    threshold=threshold)


# ---------------------------------------------------------------------------
# A — 규칙 → SQL, 판정
# ---------------------------------------------------------------------------

def test_identifier_and_literal_quoting() -> None:
    assert rule_sql.quote_ident('a"b') == '"a""b"'
    assert rule_sql.string_literal("it's") == "'it''s'"
    assert T.sql == '"pg_sales"."public"."orders"'


def test_unsupported_types_are_left_to_carry_forward() -> None:
    assert rule_sql.compile_rule(_spec("CUSTOM_PYTHON"), T) is None
    # 최신성은 간이 진단을 없애며 정밀 진단으로 옮겼다(TODO-095) — 시각 컬럼의 최댓값
    assert rule_sql.compile_rule(_spec("FRESHNESS"), T).exprs == ('max("c")', 'count("c")')


def test_custom_sql_runs_as_its_own_query() -> None:
    c = rule_sql.compile_rule(_spec("CUSTOM_SQL", None, "-- 반납일 확인\nSELECT count(*) FROM rental WHERE x < 0;"), T)
    assert c.own_sql == "SELECT count(*) FROM rental WHERE x < 0" and c.exprs == ()
    assert rule_sql.read_own(c, [0]).passed and not rule_sql.read_own(c, [3]).passed
    assert rule_sql.read_own(c, [3]).error_count == 3 and rule_sql.read_own(c, [3]).checked_count is None
    assert not rule_sql.read_own(c, ["x"]).passed
    for bad in (None, "UPDATE t SET a = 1", "SELECT 1; DROP TABLE t", "WITH x AS (DELETE FROM t) SELECT 1"):
        assert isinstance(rule_sql.compile_rule(_spec("CUSTOM_SQL", None, bad), T), Outcome)


def test_not_null_counts_and_threshold() -> None:
    c = rule_sql.compile_rule(_spec("NOT_NULL", threshold=99.0), T)
    assert isinstance(c, Compiled) and c.exprs == ('count_if("c" IS NULL)',)
    out = rule_sql.read_scan([c], [1000, 10])[1]
    assert out.passed is True and (out.checked_count, out.error_count) == (1000, 10)
    assert rule_sql.read_scan([c], [1000, 11])[1].passed is False
    # 빈 테이블은 통과 (간이 진단과 같음)
    assert rule_sql.read_scan([c], [0, 0])[1].passed is True


def test_unique_uses_distinct_over_total() -> None:
    c = rule_sql.compile_rule(_spec("UNIQUE"), T)
    out = rule_sql.read_scan([c], [10, 9])[1]
    assert out.passed is False and (out.checked_count, out.error_count) == (10, 1)


def test_min_value_passes_on_extreme_and_counts_violations() -> None:
    c = rule_sql.compile_rule(_spec("MIN_VALUE", expected="0"), T)
    assert c.exprs[2] == 'count_if(TRY_CAST("c" AS double) < 0.0)'
    out = rule_sql.read_scan([c], [5, -3.0, 5, 2])[1]
    assert out.passed is False and out.actual_value == "-3.0" and out.error_count == 2
    assert rule_sql.read_scan([c], [5, 1.0, 5, 0])[1].passed is True
    # 값이 하나도 없으면 실패 (간이 진단과 같음)
    assert rule_sql.read_scan([c], [0, None, 0, 0])[1].passed is False
    bad = rule_sql.compile_rule(_spec("MAX_VALUE", expected="nan"), T)
    assert isinstance(bad, Outcome) and bad.passed is False


def test_accepted_values_ignores_null_and_trims_char() -> None:
    c = rule_sql.compile_rule(_spec("ACCEPTED_VALUES", column="use_yn", expected="Y, N"), T,
                              {"use_yn": "char(1)"})
    assert c.exprs[0] == 'count("use_yn")'
    assert c.exprs[1] == (
        'count_if("use_yn" IS NOT NULL AND rtrim(CAST("use_yn" AS varchar)) NOT IN (\'Y\', \'N\'))')
    out = rule_sql.read_scan([c], [100, 90, 3])[1]
    assert out.passed is False and (out.checked_count, out.error_count) == (90, 3)
    # varchar 는 자르지 않는다
    v = rule_sql.compile_rule(_spec("ACCEPTED_VALUES", expected="Y"), T, {"c": "character varying(10)"})
    assert "rtrim" not in v.exprs[1]
    empty = rule_sql.compile_rule(_spec("ACCEPTED_VALUES", expected=" , "), T)
    assert isinstance(empty, Outcome)


def test_regex_escapes_pattern() -> None:
    c = rule_sql.compile_rule(_spec("REGEX", expected=r"^\d{3}-'x'$"), T)
    assert c.exprs[1].endswith(r"""NOT regexp_like(CAST("c" AS varchar), '^\d{3}-''x''$'))""")
    assert isinstance(rule_sql.compile_rule(_spec("REGEX"), T), Outcome)


def test_function_calls_registered_trino_function() -> None:
    """FUNCTION — Trino 에 미리 등록한 함수 이름만 받아 컬럼 값을 넘긴다. true 가 아니면(NULL 반환 포함) 오류."""
    c = rule_sql.compile_rule(_spec("FUNCTION", expected=" memory.default.Is_Valid_Brno "), T)
    assert c.exprs == (
        'count("c")',
        'count_if("c" IS NOT NULL AND NOT coalesce("memory"."default"."is_valid_brno"("c"), false))')
    out = c.judge(100, [90, 3])
    assert (out.passed, out.checked_count, out.error_count) == (False, 90, 3)
    assert c.judge(100, [90, 0]).passed is True
    assert rule_sql.function_ref("is_ok") == '"is_ok"' and rule_sql.function_ref("dq.is_ok") == '"dq"."is_ok"'
    # 이름만 — 인자·괄호·식·네 마디 이름은 받지 않는다
    for bad in ("", "f(x)", "a.b.c.d", "f; DROP TABLE t", 'f"', "1f", "a..b", "length(c) > 0"):
        with pytest.raises(rule_sql.ExpressionError):
            rule_sql.function_ref(bad)
        assert isinstance(rule_sql.compile_rule(_spec("FUNCTION", expected=bad), T), Outcome)
    with pytest.raises(rule_sql.ExpressionError):
        rule_sql.check_expression("FUNCTION", "f(x)")


def test_date_format_checks_shape_and_calendar() -> None:
    c = rule_sql.compile_rule(_spec("DATE_FORMAT", expected="yyyymmdd"), T, {"c": "varchar(8)"})
    assert "regexp_like(CAST(\"c\" AS varchar), '^\\d{4}\\d{2}\\d{2}$')" in c.exprs[1]
    assert "try(date_parse(CAST(\"c\" AS varchar), '%Y%m%d')) IS NULL" in c.exprs[1]
    assert "날짜 형식 YYYYMMDD" in c.judge(3, [3, 1]).detail
    ym = rule_sql.compile_rule(_spec("DATE_FORMAT", expected="YYYYMM"), T)
    assert "|| ' 01', '%Y%m %d'" in ym.exprs[1], "일이 없으면 1일을 붙여 달을 본다"
    year = rule_sql.compile_rule(_spec("DATE_FORMAT", expected="YYYY"), T)
    assert "date_parse" not in year.exprs[1]
    # 날짜 타입 컬럼은 진단 건수만 세고 오류는 0
    native = rule_sql.compile_rule(_spec("DATE_FORMAT", expected="YYYY-MM-DD"), T, {"c": "timestamp(6)"})
    assert native.exprs == ('count("c")', "0")


def test_date_format_accepts_any_combination_of_words_and_separators() -> None:
    spec = rule_sql.date_format_spec
    assert spec(" yyyy/mm/dd ") == rule_sql.DateFormat("YYYY/MM/DD", r"^\d{4}/\d{2}/\d{2}$", "%Y/%m/%d")
    assert spec("YYYY.MM.DD HH24:MI") == rule_sql.DateFormat(
        "YYYY.MM.DD HH24:MI", r"^\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}$", "%Y.%m.%d %H:%i")
    assert spec("YYYY-MM-DDTHH:MI:SS").parse_format == "%Y-%m-%dT%H:%i:%s", "HH 는 HH24 와 같다"
    assert spec("DD/MM/YYYY").parse_format == "%d/%m/%Y", "순서는 자유"
    assert spec("HH24MISS") == rule_sql.DateFormat("HH24MISS", r"^\d{2}\d{2}\d{2}$", "%H%i%s")
    assert spec("YYYY-MM") == rule_sql.DateFormat("YYYY-MM", r"^\d{4}\-\d{2}$", "%Y-%m %d", " 01")
    # 밀리초(SSS)는 3자리, 초(SS)와 따로 읽는다
    assert spec("YYYY-MM-DD HH24:MI:SS.SSS") == rule_sql.DateFormat(
        "YYYY-MM-DD HH24:MI:SS.SSS", r"^\d{4}\-\d{2}\-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$", "%Y-%m-%d %H:%i:%s.%f")
    assert spec("HH24MISSSSS").parse_format == "%H%i%f%s", "붙여 쓰면 긴 낱말(SSS)부터 — 구분자를 넣어 적는다"
    # 시 바로 뒤의 MM 은 분 — HH:MM:SS 로 적어도 된다
    assert spec("YYYY-MM-DD HH:MM:SS").parse_format == "%Y-%m-%d %H:%i:%s"
    assert spec("YYYY-MM-DD HH:MM:SS.SSS").parse_format == "%Y-%m-%d %H:%i:%s.%f"
    assert spec("HH:MM").parse_format == "%H:%i" and spec("MM/DD HH24").parse_format == "%m/%d %H"
    with pytest.raises(rule_sql.ExpressionError, match="두 번"):
        spec("HH:MM:MI")
    for name in rule_sql.DATE_FORMATS:
        assert spec(name).name == name, "화면이 보여 주는 형식은 모두 읽힌다"
    for bad, why in (("", "날짜 형식이 필요"), ("YYYY-MM-DD-YYYY", "두 번"), ("YYYYMMDD'; DROP", "읽을 수 없습니다"),
                     ("YY-MM-DD", "읽을 수 없습니다"), ("- / .", "하나도 없습니다"), ("YYYY%MM", "읽을 수 없습니다")):
        with pytest.raises(rule_sql.ExpressionError, match=why):
            spec(bad)
        assert isinstance(rule_sql.compile_rule(_spec("DATE_FORMAT", expected=bad), T), Outcome)
    with pytest.raises(rule_sql.ExpressionError):
        rule_sql.check_expression("DATE_FORMAT", "YYMMDD")


def test_column_compare_skips_null_pairs() -> None:
    c = rule_sql.compile_rule(_spec("COLUMN_COMPARE", column="bgn", expected="<= end"), T,
                              {"bgn": "varchar", "end": "varchar"})
    assert c.exprs == ('count_if("bgn" IS NOT NULL AND "end" IS NOT NULL)', 'count_if(NOT ("bgn" <= "end"))')
    assert rule_sql.parse_compare("!= x") == ("<>", "x")
    missing = rule_sql.compile_rule(_spec("COLUMN_COMPARE", column="bgn", expected="<= nope"), T,
                                    {"bgn": "varchar"})
    assert isinstance(missing, Outcome) and "nope" in missing.detail
    assert isinstance(rule_sql.compile_rule(_spec("COLUMN_COMPARE", expected="end"), T), Outcome)


def test_referential_builds_anti_join() -> None:
    parent = TableRef("ora_master", "mst", "customer")
    c = rule_sql.compile_rule(_spec("REFERENTIAL", column="cust_id", expected="9#id"), T, None, (parent, "id"))
    assert c.exprs == () and c.own_sql == (
        'SELECT count(*), count_if(p.k IS NULL) FROM "pg_sales"."public"."orders" c '
        'LEFT JOIN (SELECT DISTINCT "id" AS k FROM "ora_master"."mst"."customer" WHERE "id" IS NOT NULL) p '
        'ON c."cust_id" = p.k WHERE c."cust_id" IS NOT NULL')
    out = rule_sql.read_own(c, [50, 2])
    assert out.passed is False and (out.checked_count, out.error_count) == (50, 2)
    unresolved = rule_sql.compile_rule(_spec("REFERENTIAL", column="cust_id"), T, None, "사유")
    assert isinstance(unresolved, Outcome) and unresolved.detail == "사유"
    assert rule_sql.parse_reference("a.b.dataset#col") == ("a.b.dataset", "col")
    assert rule_sql.parse_reference("nocolumn") is None


def test_scan_sql_puts_all_rules_in_one_select() -> None:
    a = rule_sql.compile_rule(_spec("NOT_NULL", rule_id=1), T)
    b = rule_sql.compile_rule(_spec("ACCEPTED_VALUES", expected="Y,N", rule_id=2), T)
    rows = rule_sql.compile_rule(_spec("ROW_COUNT", column=None, expected="5", rule_id=3), T)
    sql = rule_sql.scan_sql(T, [a, rows, b])
    assert sql.startswith("SELECT count(*), count_if(") and sql.count(" FROM ") == 1
    out = rule_sql.read_scan([a, rows, b], [10, 1, 9, 0])
    assert out[1].passed is False and out[3].passed is True and out[3].checked_count is None
    assert out[2].passed is True and out[2].checked_count == 9


def test_missing_column_fails_without_query() -> None:
    out = rule_sql.compile_rule(_spec("NOT_NULL", column="ghost"), T, {"c": "int"})
    assert isinstance(out, Outcome) and "ghost" in out.detail
    # 대소문자는 가리지 않는다 (Trino 식별자)
    assert isinstance(rule_sql.compile_rule(_spec("NOT_NULL", column="C"), T, {"c": "int"}), Compiled)


# ---------------------------------------------------------------------------
# B — 엔진 대상 해석
# ---------------------------------------------------------------------------

def test_resolve_via_query_connection(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, []))

    async def go(s):
        return await runs.resolve_engine_target(s, await s.get(cm.Dataset, ds_id))

    target = _run(sm, go)
    assert target.datasource_name == "lake-trino" and target.table == TableRef("pg_sales", "public", "orders")


def test_resolve_reports_reason_when_not_connected(tmp_path) -> None:
    # 연결이 없고 원천도 직접 읽을 수 없는 유형이면 진단할 수 없다 (PostgreSQL·MySQL·MariaDB·Oracle·SQL Server 는 원천 직접 진단 — test_quality_direct.py)
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [], connect=False, source_type="hive"))
    status = _run(sm, lambda s: runs.engine_status(s, ds_id))
    assert status.available is False and "Trino" in status.reason and status.workers_alive == 0
    assert "PostgreSQL·MySQL·MariaDB·Oracle·SQL Server" in status.reason


def test_resolve_requires_remote_name(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [], remote_name=None))
    status = _run(sm, lambda s: runs.engine_status(s, ds_id))
    assert status.available is False and "카탈로그 이름" in status.reason


def test_resolve_trino_dataset_uses_own_name(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))

    async def seed():
        async with sm() as s:
            trino = cm.Datasource(datasource_id="trino-9", name="t9", type="trino")
            s.add(trino)
            await s.flush()
            s.add(cm.DatasourceConfiguration(datasource_id=trino.id, config_json=json.dumps({"host": "t9.local"})))
            d = cm.Dataset(urn="u", name="m", datasource_id=trino.id, qualified_name="trino-9.lakehouse.mart.m")
            s.add(d)
            await s.commit()
            return d.id

    ds_id = asyncio.run(seed())
    status = _run(sm, lambda s: runs.engine_status(s, ds_id))
    assert status.available is True and status.engine_table == "lakehouse.mart.m"


# ---------------------------------------------------------------------------
# C — 대기열
# ---------------------------------------------------------------------------

def test_create_run_queues_and_blocks_duplicates(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert run.status == "QUEUED" and run.engine_table == "pg_sales.public.orders" and run.rules_total == 1
    with pytest.raises(runs.RunConflictError):
        _run(sm, lambda s: runs.create_run(s, ds_id, "bob"))


def test_create_run_needs_trino_evaluable_rule(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    # 원본(hive)을 워커가 직접 읽을 수 없으면 커스텀 Python 만 있는 데이터셋은 진단할 규칙이 없다
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "CUSTOM_PYTHON", "expected": '{"module": "m", "fn": "f"}'}],
                                 source_type="hive"))
    with pytest.raises(runs.RunConflictError, match="규칙"):
        _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))


def test_cancel_queued_run_is_immediate(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    cancelled = _run(sm, lambda s: runs.cancel_run(s, run.id, "alice"))
    assert cancelled.status == "CANCELLED" and cancelled.cancel_requested_by == "alice"
    with pytest.raises(runs.RunConflictError):
        _run(sm, lambda s: runs.cancel_run(s, run.id, "alice"))
    # 취소 뒤에는 새 실행을 넣을 수 있다
    assert _run(sm, lambda s: runs.create_run(s, ds_id, "alice")).status == "QUEUED"


def test_stale_running_run_becomes_failed(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert _run(sm, lambda s: runs.claim_next(s, "w1")) == run.id

    async def age(seconds: int):
        async with sm() as s:
            row = await s.get(QualityRun, run.id)
            row.heartbeat_at = runs._now() - timedelta(seconds=seconds)
            await s.commit()

    asyncio.run(age(runs.UNRESPONSIVE_SECONDS + 5))
    seen = _run(sm, lambda s: runs.get_run(s, run.id))
    assert seen.status == "RUNNING" and seen.unresponsive is True
    asyncio.run(age(runs.STALE_SECONDS + 5))
    seen = _run(sm, lambda s: runs.get_run(s, run.id))
    assert seen.status == "FAILED" and "워커" in seen.error_message


def test_claim_runs_one_per_source_datasource(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))

    async def second_dataset() -> int:
        async with sm() as s:
            first = await s.get(cm.Dataset, ds_id)
            d = cm.Dataset(urn="pg-1.public.items.dataset", name="items", datasource_id=first.datasource_id,
                           catalog_id=first.catalog_id, qualified_name="pg-1.public.items")
            s.add(d)
            await s.flush()
            s.add(QualityRule(dataset_id=d.id, rule_name="r", check_type="NOT_NULL", column_name="id",
                              threshold=100, severity="WARNING", is_active="true"))
            await s.commit()
            return d.id

    other_id = asyncio.run(second_dataset())
    first = _run(sm, lambda s: runs.create_run(s, ds_id, "a"))
    second = _run(sm, lambda s: runs.create_run(s, other_id, "a"))
    assert _run(sm, lambda s: runs.claim_next(s, "w1")) == first.id
    # 같은 원본 데이터 소스의 실행이 돌고 있으면 다음 것은 기다린다
    assert _run(sm, lambda s: runs.claim_next(s, "w2")) is None

    async def finish():
        async with sm() as s:
            (await s.get(QualityRun, first.id)).status = "SUCCEEDED"
            await s.commit()

    asyncio.run(finish())
    assert _run(sm, lambda s: runs.claim_next(s, "w2")) == second.id


def test_worker_heartbeat_marks_alive(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    _run(sm, lambda s: runs.worker_heartbeat(s, "w1", "host", None))
    workers = _run(sm, lambda s: runs.list_workers(s))
    assert [(w.worker_id, w.alive) for w in workers] == [("w1", True)]
    assert _run(sm, lambda s: runs.workers_alive(s)) == 1


# ---------------------------------------------------------------------------
# D — 워커
# ---------------------------------------------------------------------------

class _FakeTrino:
    """워커의 ``trino_client.execute`` 대역 — SQL 을 받아 미리 정한 행 또는 오류를 돌려준다."""

    def __init__(self, respond):
        self.respond = respond
        self.calls: list[dict] = []

    async def __call__(self, endpoint, sql, *, source=None, client_tags=(), session=None, on_poll=None,
                       catalog=None, schema=None, **_):
        self.calls.append({"sql": sql, "source": source, "tags": client_tags, "session": session,
                           "url": endpoint.base_url, "user": endpoint.user, "catalog": catalog, "schema": schema})
        query_id = f"q{len(self.calls)}"
        if on_poll is not None and await on_poll(query_id):
            raise worker_trino.TrinoQueryCancelled(query_id)
        rows = self.respond(sql)
        if isinstance(rows, Exception):
            raise rows
        return worker_trino.TrinoResult(columns=[], rows=[rows], query_id=query_id)


def _app(sm, user: TokenUser | None = None) -> FastAPI:
    """품질 API 만 올린 앱. 기본 주체는 관리자 역할의 API 키(품질 워커가 쓰는 것과 같다)."""
    app = FastAPI()
    app.include_router(quality_router.router, prefix="/api/v1")

    async def _session():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: user or TokenUser(
        sub="apikey:1", username="alice", email="", first_name="", last_name="", roles=["argus-admin"], api_key_id=1)
    return app


async def _work(sm, worker_id: str = "w1", during=None) -> bool:
    """실제 워커 코드로 대기 실행 하나를 처리한다 — 카탈로그 API 에는 ASGI 로 붙는다."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(sm)), base_url="http://catalog") as http:
        settings = WorkerSettings(catalog_base_url="http://catalog/api/v1", catalog_api_key="argus_sk_test")
        runner = Runner(settings, CatalogClient(settings, worker_id, "host", client=http))
        if during is None:
            return await runner.run_once()
        return await during(runner)


def _start(sm, monkeypatch, fake, ds_id: int) -> int:
    monkeypatch.setattr(worker_trino, "execute", fake)
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert asyncio.run(_work(sm)) is True
    return run.id


def _results(sm, ds_id: int) -> dict[int, QualityResult]:
    async def go(s):
        rows = (await s.execute(select(QualityResult).where(QualityResult.dataset_id == ds_id))).scalars().all()
        return {r.rule_id: r for r in rows}
    return _run(sm, go)


def test_worker_evaluates_rules_in_one_scan(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_yn, r_date, r_seq, r_sql) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "ACCEPTED_VALUES", "column": "use_yn", "expected": "Y,N"},
        {"check_type": "DATE_FORMAT", "column": "reg_ymd", "expected": "YYYYMMDD"},
        {"check_type": "COLUMN_COMPARE", "column": "bgn_ymd", "expected": "<= end_ymd"},
        {"check_type": "CUSTOM_PYTHON", "expected": '{"module": "m", "fn": "f"}'},
    ]))
    # count(*), NOT_NULL(nulls), ACCEPTED(checked, errors), DATE(checked, errors), COMPARE(checked, errors)
    fake = _FakeTrino(lambda _sql: [1000, 0, 1000, 4, 990, 1, 800, 0])
    run_id = _start(sm, monkeypatch, fake, ds_id)

    # 진단 쿼리는 하나 — 뒤의 쿼리는 실패한 규칙의 위반 샘플(LIMIT 5, TODO-095 4단계)
    samples = [c for c in fake.calls[1:] if c["sql"].endswith("LIMIT 5")]
    assert len(fake.calls) == 1 + len(samples) and len(samples) == 2, "테이블은 한 번만 읽는다"
    call = fake.calls[0]
    assert call["sql"].startswith('SELECT count(*), ') and call["sql"].endswith('FROM "pg_sales"."public"."orders"')
    assert call["source"] == "argus-dq" and call["tags"] == ("argus-dq",)
    assert call["session"] == {"query_max_execution_time": "30m"}
    assert call["url"] == "http://trino.local:8080" and call["user"] == "dq"

    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "SUCCEEDED" and run.rules_total == 5 and run.rules_evaluated == 4
    assert (run.checked_count, run.error_count) == (1000 + 1000 + 990 + 800, 5)
    assert run.error_rate == round(5 / 3790 * 100, 4)
    assert run.query_ids == ["q1"] and run.progress_done == run.progress_total == 1
    # 점수는 이 실행에서 평가한 규칙 기준 — CUSTOM_SQL 은 평가하지 않아 빠진다 → 4개 중 2개 통과
    assert run.score == 50.0

    results = _results(sm, ds_id)
    assert set(results) == {r_nn, r_yn, r_date, r_seq}
    assert results[r_yn].passed == "false" and (results[r_yn].checked_count, results[r_yn].error_count) == (1000, 4)
    assert results[r_date].passed == "false" and results[r_seq].passed == "true"
    assert r_sql not in results

    client = TestClient(_app(sm))
    queries = client.get(f"/api/v1/quality/runs/{run_id}/queries").json()
    assert [(q["sql"], q["query_id"], q["retry"]) for q in queries] == [(call["sql"], "q1", False)]
    shown = queries[0]["formatted_sql"]
    assert shown.startswith("SELECT\n  count(*),  -- 전체 행 수\n") and "-- NOT_NULL(use_yn)" in shown
    # 주석·줄바꿈을 걷어내면 워커가 낸 쿼리와 같다
    plain = " ".join(line.split("  -- ")[0].strip() for line in shown.splitlines())
    assert plain == call["sql"]
    assert client.get("/api/v1/quality/runs/999999/queries").status_code == 404


def test_worker_falls_back_to_per_rule_queries(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_re) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "REGEX", "column": "reg_ymd", "expected": "(["},
    ]))

    def respond(sql: str):
        if "regexp_like" in sql:
            return worker_trino.TrinoQueryError("Invalid regular expression", query_id="bad", error_type="USER_ERROR")
        return [10, 0]

    fake = _FakeTrino(respond)
    run_id = _start(sm, monkeypatch, fake, ds_id)
    assert len(fake.calls) == 3, "스캔 1 + 규칙별 2"
    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "PARTIAL" and "REGEX(reg_ymd)" in run.error_message
    results = _results(sm, ds_id)
    assert results[r_nn].passed == "true" and results[r_nn].checked_count == 10
    assert results[r_re].passed == "false" and "Invalid regular expression" in results[r_re].detail
    assert results[r_re].checked_count is None

    # 진단 쿼리 — 스캔 + 다시 낸 규칙별 쿼리, 워커가 낸 SQL·ID 와 순서대로 맞는다
    queries = TestClient(_app(sm)).get(f"/api/v1/quality/runs/{run_id}/queries").json()
    assert [q["sql"] for q in queries] == [c["sql"] for c in fake.calls]
    assert [q["query_id"] for q in queries] == ["q1", "q2", "q3"]
    assert [q["retry"] for q in queries] == [False, True, True]


def test_worker_fails_without_results_when_every_query_fails(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "ACCEPTED_VALUES", "column": "use_yn", "expected": "Y"},
    ]))
    fake = _FakeTrino(lambda _sql: worker_trino.TrinoQueryError("Table 'pg_sales.public.orders' does not exist"))
    run_id = _start(sm, monkeypatch, fake, ds_id)
    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "FAILED" and "does not exist" in run.error_message
    assert run.checked_count is None and run.rules_evaluated == 0
    assert _results(sm, ds_id) == {}, "접속·테이블 문제를 품질 실패로 남기지 않는다"


def test_worker_reports_connection_failure(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    fake = _FakeTrino(lambda _sql: httpx.ConnectError("refused"))
    run_id = _start(sm, monkeypatch, fake, ds_id)
    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "FAILED" and "접속" in run.error_message


def test_worker_stops_when_cancel_requested(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))

    class _CancelDuringQuery(_FakeTrino):
        async def __call__(self, endpoint, sql, **kwargs):
            async with sm() as s:
                run = (await s.execute(select(QualityRun))).scalars().first()
                run.cancel_requested_by = "alice"
                await s.commit()
            return await super().__call__(endpoint, sql, **kwargs)

    fake = _CancelDuringQuery(lambda _sql: [10, 0])
    monkeypatch.setattr(worker_trino, "execute", fake)
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    asyncio.run(_work(sm))
    seen = _run(sm, lambda s: runs.get_run(s, run.id))
    assert seen.status == "CANCELLED" and _results(sm, ds_id) == {}


def test_worker_runs_referential_as_separate_query(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, []))

    async def add():
        async with sm() as s:
            child = await s.get(cm.Dataset, ds_id)
            parent = cm.Dataset(urn="pg-1.public.customer.dataset", name="customer",
                                datasource_id=child.datasource_id, catalog_id=child.catalog_id,
                                qualified_name="pg-1.public.customer")
            s.add(parent)
            await s.flush()
            ok = QualityRule(dataset_id=ds_id, rule_name="fk", check_type="REFERENTIAL", column_name="cust_id",
                             expected_value="pg-1.public.customer.dataset#id", threshold=100,
                             severity="WARNING", is_active="true")
            lost = QualityRule(dataset_id=ds_id, rule_name="fk2", check_type="REFERENTIAL", column_name="cust_id",
                               expected_value="no.such.dataset#id", threshold=100,
                               severity="WARNING", is_active="true")
            s.add_all([ok, lost])
            await s.commit()
            return ok.id, lost.id

    r_ok, r_lost = asyncio.run(add())
    fake = _FakeTrino(lambda _sql: [40, 3])
    run_id = _start(sm, monkeypatch, fake, ds_id)
    # 진단 쿼리 하나 + 위반이 있어 그 위반 샘플 쿼리 하나
    assert len(fake.calls) == 2 and '"pg_sales"."public"."customer"' in fake.calls[0]["sql"]
    assert fake.calls[1]["sql"].endswith("AND p.k IS NULL LIMIT 5")
    results = _results(sm, ds_id)
    assert results[r_ok].passed == "false" and (results[r_ok].checked_count, results[r_ok].error_count) == (40, 3)
    assert results[r_lost].passed == "false" and "찾을 수 없습니다" in results[r_lost].detail
    assert _run(sm, lambda s: runs.get_run(s, run_id)).status == "SUCCEEDED"


def test_worker_requeues_on_shutdown(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))

    async def hang(*_a, **_k):
        await asyncio.sleep(60)

    monkeypatch.setattr(worker_trino, "execute", hang)
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))

    async def stop_midway(runner):
        job = asyncio.create_task(runner.run_once())
        await asyncio.sleep(0.3)
        assert runner.current_run_id == run.id
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job

    asyncio.run(_work(sm, during=stop_midway))
    seen = _run(sm, lambda s: runs.get_run(s, run.id))
    assert seen.status == "QUEUED" and seen.worker_id is None


def test_worker_drops_run_taken_away_by_catalog(tmp_path, monkeypatch) -> None:
    """실행이 카탈로그에서 다른 상태로 바뀌면(응답 없음 처리 등) 워커는 쿼리를 멈추고 결과를 내지 않는다."""
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))

    class _TakenAway(_FakeTrino):
        async def __call__(self, endpoint, sql, **kwargs):
            async with sm() as s:
                run = (await s.execute(select(QualityRun))).scalars().first()
                run.status = "FAILED"
                await s.commit()
            return await super().__call__(endpoint, sql, **kwargs)

    monkeypatch.setattr(worker_trino, "execute", _TakenAway(lambda _sql: [10, 0]))
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert asyncio.run(_work(sm)) is True
    assert _run(sm, lambda s: runs.get_run(s, run.id)).status == "FAILED"
    assert _results(sm, ds_id) == {}


def test_plan_is_frozen_at_claim(tmp_path, monkeypatch) -> None:
    """실행 도중 규칙이 바뀌어도 낸 쿼리와 같은 기준으로 판정한다(계획을 실행 행에 남긴다)."""
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_min,) = asyncio.run(_seed(sm, [{"check_type": "MIN_VALUE", "column": "amt", "expected": "0"}]))

    class _EditDuringQuery(_FakeTrino):
        async def __call__(self, endpoint, sql, **kwargs):
            async with sm() as s:
                rule = await s.get(QualityRule, r_min)
                rule.check_type, rule.expected_value = "NOT_NULL", None
                await s.commit()
            return await super().__call__(endpoint, sql, **kwargs)

    # count(*), min, checked, errors — MIN_VALUE 의 모양
    run_id = _start(sm, monkeypatch, _EditDuringQuery(lambda _sql: [10, -5.0, 10, 2]), ds_id)
    assert _run(sm, lambda s: runs.get_run(s, run_id)).status == "SUCCEEDED"
    result = _results(sm, ds_id)[r_min]
    assert result.passed == "false" and (result.checked_count, result.error_count) == (10, 2)


# ---------------------------------------------------------------------------
# E — Trino REST 클라이언트
# ---------------------------------------------------------------------------

_EP = trino_client.TrinoEndpoint(base_url="http://trino.local:8080", user="dq")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_client_follows_next_uri_and_sends_headers() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen.update(request.headers)
            seen["body"] = request.content.decode()
            return httpx.Response(200, json={"id": "q1", "nextUri": "http://trino.local:8080/v1/statement/q1/1"})
        if request.url.path.endswith("/1"):
            return httpx.Response(200, json={
                "id": "q1", "columns": [{"name": "n"}], "data": [[1]],
                "nextUri": "http://trino.local:8080/v1/statement/q1/2"})
        return httpx.Response(200, json={"id": "q1", "data": [[2]]})

    async def go():
        async with _client(handler) as client:
            return await trino_client.execute(
                _EP, "SELECT 1", source="argus-dq", client_tags=("argus-dq",),
                session={"query_max_execution_time": "30m"}, client=client)

    result = asyncio.run(go())
    assert result.rows == [[1], [2]] and result.columns == ["n"] and result.query_id == "q1"
    assert seen["x-trino-user"] == "dq" and seen["x-trino-source"] == "argus-dq"
    assert seen["x-trino-client-tags"] == "argus-dq"
    assert seen["x-trino-session"] == "query_max_execution_time=30m" and seen["body"] == "SELECT 1"


def test_client_raises_query_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "q9", "error": {"message": "line 1: bad", "errorName": "SYNTAX_ERROR"}})

    async def go():
        async with _client(handler) as client:
            await trino_client.execute(_EP, "SELEC", client=client)

    with pytest.raises(trino_client.TrinoQueryError) as info:
        asyncio.run(go())
    assert info.value.query_id == "q9" and info.value.error_name == "SYNTAX_ERROR"


def test_client_cancels_when_poll_hook_asks() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json={"id": "q1", "nextUri": "http://trino.local:8080/v1/statement/q1/1"})

    async def stop(_query_id):
        return True

    async def go():
        async with _client(handler) as client:
            await trino_client.execute(_EP, "SELECT 1", on_poll=stop, client=client)

    with pytest.raises(trino_client.TrinoQueryCancelled):
        asyncio.run(go())
    assert methods == ["POST", "DELETE"]


# ---------------------------------------------------------------------------
# F — API
# ---------------------------------------------------------------------------

def _api(sm) -> TestClient:
    return TestClient(_app(sm))


def test_api_run_lifecycle(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    client = _api(sm)

    engine = client.get(f"/api/v1/quality/datasets/{ds_id}/engine").json()
    assert engine["available"] is True and engine["engine_table"] == "pg_sales.public.orders"

    created = client.post(f"/api/v1/quality/datasets/{ds_id}/runs")
    assert created.status_code == 202 and created.json()["status"] == "QUEUED"
    assert created.json()["requested_by"] == "alice"
    run_id = created.json()["id"]
    assert client.post(f"/api/v1/quality/datasets/{ds_id}/runs").status_code == 409

    assert [r["id"] for r in client.get(f"/api/v1/quality/datasets/{ds_id}/runs").json()] == [run_id]
    assert [r["id"] for r in client.get("/api/v1/quality/runs", params={"status": "QUEUED"}).json()] == [run_id]
    assert client.get(f"/api/v1/quality/runs/{run_id}").json()["status"] == "QUEUED"
    assert client.post(f"/api/v1/quality/runs/{run_id}/cancel").json()["status"] == "CANCELLED"
    assert client.post(f"/api/v1/quality/runs/{run_id}/cancel").status_code == 409
    assert client.get("/api/v1/quality/runs/999").status_code == 404
    assert client.get("/api/v1/quality/datasets/999/engine").status_code == 404
    assert client.get("/api/v1/quality/workers").json() == []

    # 다시 실행 — 끝난 실행의 데이터셋으로 새 실행을 만든다
    again = client.post(f"/api/v1/quality/runs/{run_id}/retry")
    assert again.status_code == 202 and again.json()["id"] != run_id and again.json()["status"] == "QUEUED"
    assert client.post(f"/api/v1/quality/runs/{again.json()['id']}/retry").status_code == 409, "끝나지 않은 실행"
    assert client.post("/api/v1/quality/runs/999/retry").status_code == 404


def test_api_run_list_filters_and_status(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    client = _api(sm)
    first = client.post(f"/api/v1/quality/datasets/{ds_id}/runs").json()["id"]
    client.post(f"/api/v1/quality/runs/{first}/cancel")
    second = client.post(f"/api/v1/quality/datasets/{ds_id}/runs").json()["id"]

    def ids(**params):
        return [r["id"] for r in client.get("/api/v1/quality/runs", params=params).json()]

    assert ids() == [second, first]
    assert ids(status="ACTIVE") == [second] and ids(status="CANCELLED") == [first] and ids(status="PROBLEM") == []
    row = client.get("/api/v1/quality/runs").json()[0]
    assert (row["dataset_name"], row["datasource_name"], row["engine_datasource_name"]) == (
        "orders", "sales-pg", "lake-trino")
    assert ids(datasource_id=row["datasource_id"]) == [second, first] and ids(datasource_id=9999) == []
    assert ids(since="2999-01-01T00:00:00Z") == [] and ids(limit=1, cursor=1) == [first]

    assert client.get("/api/v1/quality/runs/status").json() == {
        "workers_alive": 0, "running": 0, "queued": 1, "workers": []}


def test_worker_api_checks_ownership(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn,) = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    client = _api(sm)
    base = "/api/v1/quality/worker"
    assert client.post(f"{base}/claim", json={"worker_id": "w1", "hostname": "h", "version": "1"}).json() == {"job": None}
    status = client.get("/api/v1/quality/runs/status").json()
    assert status["workers_alive"] == 1 and status["workers"][0]["version"] == "1"

    run_id = client.post(f"/api/v1/quality/datasets/{ds_id}/runs").json()["id"]
    job = client.post(f"{base}/claim", json={"worker_id": "w1"}).json()["job"]
    assert job["run_id"] == run_id and job["table"] == "pg_sales.public.orders"
    assert job["trino"] == {"base_url": "http://trino.local:8080", "user": "dq", "password": None}
    assert [s["key"] for s in job["steps"]] == ["scan"] and job["steps"][0]["fallback"] == []
    assert client.post(f"{base}/claim", json={"worker_id": "w2"}).json() == {"job": None}

    # 다른 워커는 보고·제출하지 못한다
    beat = {"worker_id": "w2", "run_id": run_id, "progress_done": 1}
    assert client.post(f"{base}/heartbeat", json=beat).json() == {"cancel": False, "active": False}
    done = {"worker_id": "w2", "results": [{"key": "scan", "row": [10, 0]}]}
    assert client.post(f"{base}/runs/{run_id}/complete", json=done).status_code == 409
    assert client.post(f"{base}/runs/{run_id}/finish", json={"worker_id": "w2", "outcome": "FAILED"}).status_code == 409

    beat["worker_id"] = "w1"
    assert client.post(f"{base}/heartbeat", json=beat).json() == {"cancel": False, "active": True}
    client.post(f"/api/v1/quality/runs/{run_id}/cancel")
    assert client.post(f"{base}/heartbeat", json=beat).json()["cancel"] is True
    # 취소 요청된 실행은 워커가 종료하며 되돌려도 대기열로 가지 않는다
    ended = client.post(f"{base}/runs/{run_id}/finish", json={"worker_id": "w1", "outcome": "REQUEUE"})
    assert ended.json()["status"] == "CANCELLED"
    assert client.post(f"{base}/runs/{run_id}/complete", json={**done, "worker_id": "w1"}).status_code == 409

    assert client.delete(f"{base}/w1").status_code == 204
    assert [w["worker_id"] for w in client.get("/api/v1/quality/workers").json()] == ["w2"]
    assert _results(sm, ds_id) == {} and r_nn


def test_api_rejects_run_without_trino_connection(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}], connect=False,
                                 source_type="hive"))
    resp = _api(sm).post(f"/api/v1/quality/datasets/{ds_id}/runs")
    assert resp.status_code == 409 and "Trino" in resp.json()["detail"]


def test_new_check_types_are_accepted_and_wait_for_a_run(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, []))
    client = _api(sm)
    for check, expected in (("DATE_FORMAT", "YYYYMMDD"), ("COLUMN_COMPARE", "<= end_ymd"),
                            ("REFERENTIAL", "1#id")):
        resp = client.post("/api/v1/quality/rules", json={
            "dataset_id": ds_id, "rule_name": check, "check_type": check, "column_name": "reg_ymd",
            "expected_value": expected})
        assert resp.status_code == 201, resp.text
    # 아직 진단하지 않은 규칙은 결과가 없다(예전에는 간이 진단이 평가하지 못한다는 "미평가" 항목이 나왔다)
    assert client.get(f"/api/v1/quality/datasets/{ds_id}/results").json() == []


# ---------------------------------------------------------------------------
# G — 품질 지표, 실행별 결과
# ---------------------------------------------------------------------------

def test_every_trino_check_type_belongs_to_one_indicator() -> None:
    covered = {t for i in indicators.INDICATORS for t in i.check_types}
    assert covered == set(rule_sql.TRINO_CHECK_TYPES), "Trino 유형마다 지표가 있어야 한다"
    assert len({i.key for i in indicators.INDICATORS}) == len(indicators.INDICATORS)
    assert {i.area for i in indicators.INDICATORS} <= indicators.AREAS.keys()
    # 관리지침의 진단 기준 16개(완결성 4 · 유효성 7 · 정합성 5) + 기준 밖의 문자열 형식·건수·더미 값·연속성·다양성·여러 컬럼 키
    per_area = {a: sum(1 for i in indicators.INDICATORS if i.area == a) for a in indicators.AREAS}
    assert per_area == {"COMPLETENESS": 4, "VALIDITY": 7, "CONSISTENCY": 5, "OTHER": 6}
    # AI-Ready 품질 지표(가이드라인 §2.3) — 건수만 빠지고 6개 지표가 모두 쓰인다
    assert [i.key for i in indicators.INDICATORS if i.ai_ready is None] == ["VOLUME"]
    assert {i.ai_ready[0] for i in indicators.INDICATORS if i.ai_ready} == set(indicators.AI_READY_AREAS)
    assert all(i.domain in (None, i.key) for i in indicators.INDICATORS), "도메인은 분류의 키와 같다"
    # 같은 유형이 지표 여럿에 걸치면 도메인이 가른다 — (유형, 도메인) 은 지표 하나
    pairs = [(t, i.domain) for i in indicators.INDICATORS for t in i.check_types]
    assert len(pairs) == len(set(pairs))
    for check, domains in indicators.DOMAINS.items():
        assert {i.domain for i in indicators.INDICATORS if check in i.check_types} == set(domains)
    assert indicators.indicator_of("NOT_NULL").key == "REQUIRED"
    assert indicators.indicator_of("CUSTOM_SQL") is None and indicators.indicator_of(None) is None


def test_indicator_follows_domain_then_column_name_then_default() -> None:
    of = indicators.indicator_of
    # 적어 둔 도메인이 먼저
    assert of("ACCEPTED_VALUES", "YN", "grade_cd").key == "YN"
    assert of("MIN_VALUE", "rate", "amt").key == "RATE", "대소문자를 가리지 않는다"
    # 없으면 컬럼 이름의 끝 단어
    assert of("ACCEPTED_VALUES", None, "use_yn").key == "YN"
    assert of("ACCEPTED_VALUES", None, "grade_cd").key == "CODE"
    assert of("MAX_VALUE", None, "TOTAL_AMT").key == "AMOUNT"
    assert of("MIN_VALUE", None, "orderCnt").key == "QUANTITY"
    assert of("MAX_VALUE", None, "tax_rt").key == "RATE"
    # 그래도 모르면 유형의 기본 도메인
    assert of("ACCEPTED_VALUES", None, "grade").key == "CODE"
    assert of("MIN_VALUE", None, "x").key == "QUANTITY"
    assert of("COLUMN_COMPARE", None, "bgn_ymd").key == "TIME_ORDER"
    assert of("COLUMN_COMPARE", "SEQUENCE", "rcpt_ymd").key == "SEQUENCE"
    # 그 유형에 없는 도메인(유형을 바꾼 뒤 남은 값)은 무시한다
    assert of("MIN_VALUE", "YN", "use_yn").key == "QUANTITY"
    assert indicators.resolve_domain("NOT_NULL", "YN", "use_yn") is None

    assert indicators.validate_domain("ACCEPTED_VALUES", " yn ") == "YN"
    assert indicators.validate_domain("ACCEPTED_VALUES", "") is None
    with pytest.raises(ValueError, match="도메인이 아닙니다"):
        indicators.validate_domain("ACCEPTED_VALUES", "RATE")
    with pytest.raises(ValueError, match="도메인을 정하지 않습니다"):
        indicators.validate_domain("NOT_NULL", "YN")

    # 정규식은 어느 분류에나 쓸 수 있다 — 적지 않으면 번호, 컬럼 이름으로 추정하지 않는다
    assert of("REGEX", None, "tel_no").key == "NUMBER" and of("REGEX", None, "use_yn").key == "NUMBER"
    assert [of("REGEX", d).key for d in ("DATE", "code", "RATE", "PATTERN")] == ["DATE", "CODE", "RATE", "PATTERN"]
    assert of("REGEX", "TIME_ORDER").key == "NUMBER", "정규식을 쓰지 않는 분류는 무시"
    assert of("DATE_FORMAT").key == "DATE" and of("DATE_FORMAT", "DATE", "x").key == "DATE"
    assert indicators.validate_domain("REGEX", "pattern") == "PATTERN"
    assert indicators.wants(["DATE"], "REGEX", "DATE") and not indicators.wants(["DATE"], "REGEX", None, "reg_ymd")

    assert indicators.wants([], "MIN_VALUE", None, "amt") and indicators.wants([], "CUSTOM_SQL") and not indicators.wants(["AMOUNT"], "CUSTOM_SQL")
    assert indicators.wants(["AMOUNT"], "MIN_VALUE", None, "amt")
    assert not indicators.wants(["RATE"], "MIN_VALUE", None, "amt")


def test_indicator_keys_normalize_and_round_trip() -> None:
    assert indicators.normalize(None) == [] and indicators.normalize([]) == []
    assert indicators.normalize(["reference", " REQUIRED ", "REQUIRED"]) == ["REQUIRED", "REFERENCE"], "정해진 순서"
    with pytest.raises(ValueError, match="NOPE"):
        indicators.normalize(["REQUIRED", "NOPE"])
    assert indicators.dump([]) is None and indicators.load(None) == []
    assert indicators.load(indicators.dump(["REQUIRED", "RATE"])) == ["REQUIRED", "RATE"]
    assert indicators.load("REQUIRED,GONE") == ["REQUIRED"], "없어진 키는 버린다"
    # 지표가 묶여 있던 때의 키는 지금의 키로 푼다
    assert indicators.load("REQUIRED,RANGE,ORDER,FORMAT") == [
        "REQUIRED", "NUMBER", "AMOUNT", "QUANTITY", "RATE", "TIME_ORDER", "SEQUENCE"]
    assert indicators.normalize(["range"]) == ["AMOUNT", "QUANTITY", "RATE"]
    assert indicators.check_types_for([]) is None
    assert indicators.check_types_for(["RATE", "DATE"]) == {"MIN_VALUE", "MAX_VALUE", "DATE_FORMAT", "REGEX", "FUNCTION"}
    assert indicators.check_types_for(["REQUIRED"]) == {"NOT_NULL"}


def test_compile_broken_text_and_whitespace() -> None:
    c = rule_sql.compile_rule(_spec("BROKEN_TEXT", column="nm"), T, {"nm": "varchar"})
    assert c.exprs[0] == 'count("nm")' and "regexp_like" in c.exprs[1] and "\ufffd" in c.exprs[1]
    out = c.judge(10, [10, 2])
    assert not out.passed and (out.checked_count, out.error_count) == (10, 2)

    w = rule_sql.compile_rule(_spec("WHITESPACE", column="nm"), T, {"nm": "varchar"})
    assert w.exprs[1].count("regexp_like") == 1 and w.judge(5, [5, 0]).passed
    extra = rule_sql.compile_rule(_spec("WHITESPACE", column="nm", expected="#$ ]#"), T, {"nm": "varchar"})
    assert extra.exprs[1].count("regexp_like") == 2 and "'[#$\\]]'" in extra.exprs[1], "금지 문자는 문자 클래스로, 겹친 것은 한 번"
    assert rule_sql.extra_characters_pattern("  ") is None
    assert isinstance(rule_sql.compile_rule(_spec("BROKEN_TEXT", column=None), T), Outcome)


def test_compile_logical_accepts_only_fixed_condition_shapes() -> None:
    types = {"cancel_yn": "char(1)", "cancel_ymd": "varchar", "qty": "integer"}
    c = rule_sql.compile_rule(
        _spec("LOGICAL", column=None, expected="if cancel_yn = 'Y' and qty != 0 then cancel_ymd is not null"), T, types)
    when = '"cancel_yn" = \'Y\' AND "qty" <> 0'
    assert c.exprs == (f"count_if({when})", f'count_if(({when}) AND NOT ("cancel_ymd" IS NOT NULL))')
    out = c.judge(100, [40, 3])
    assert not out.passed and (out.checked_count, out.error_count) == (40, 3)

    sql = rule_sql.condition_sql("cancel_yn NOT IN ('Y', 'N') OR qty >= cancel_ymd", types)
    assert sql == '"cancel_yn" NOT IN (\'Y\', \'N\') OR "qty" >= "cancel_ymd"'
    assert rule_sql.condition_sql("\"qty\" is null", types) == '"qty" IS NULL'
    assert rule_sql.condition_sql("cancel_yn = 'it''s'", types) == '"cancel_yn" = \'it\'\'s\''

    for bad, why in (
        ("cancel_yn = 'Y'", "IF"),                                        # IF … THEN 이 아님
        ("IF cancel_yn = 'Y'; DROP TABLE t THEN qty > 0", "읽을 수 없는"),  # 정해진 낱말 밖
        ("IF (cancel_yn = 'Y') THEN qty > 0", "컬럼 이름으로 시작"),        # 괄호
        ("IF cancel_yn = 'Y' THEN qty > (SELECT 1)", "값"),               # 부분 쿼리
        ("IF nope = 1 THEN qty > 0", "'nope' 컬럼이 없습니다"),
        ("IF cancel_yn = 'Y' THEN qty", "컬럼 뒤에는"),
        ("IF cancel_yn = 'Y' qty > 0 THEN qty > 0", "AND 또는 OR"),
        ("IF cancel_yn IN ('Y' THEN qty > 0", "닫는 괄호"),
    ):
        out = rule_sql.compile_rule(_spec("LOGICAL", column=None, expected=bad), T, types)
        assert isinstance(out, Outcome) and not out.passed and why in out.detail, (bad, out.detail)


def test_compile_formula_takes_columns_numbers_and_arithmetic_only() -> None:
    types = {"total": "decimal(10,2)", "supply": "decimal(10,2)", "vat": "decimal(10,2)", "qty": "integer"}
    c = rule_sql.compile_rule(_spec("FORMULA", column="total", expected="supply + vat"), T, types)
    assert c.exprs == (
        'count_if("total" IS NOT NULL AND try("supply" + "vat") IS NOT NULL)',
        'count_if("total" <> try("supply" + "vat"))')
    assert c.judge(9, [8, 0]).passed and c.judge(9, [8, 1]).error_count == 1

    loose = rule_sql.compile_rule(_spec("FORMULA", column="total", expected="(supply + vat) * -1.5 / qty ~ 0.01"), T, types)
    assert loose.exprs[1] == (
        'count_if(abs(CAST("total" AS double) - CAST(try(( "supply" + "vat" ) * - 1.5 / "qty") AS double)) > 0.01)')

    for bad, why in (
        ("", "계산식이 필요"), ("supply +", "끝나지 않았습니다"), ("(supply + vat", "끝나지 않았습니다"),
        ("supply vat", "연산자"), ("supply + 'x'", "컬럼 이름이나 숫자"), ("supply + nope", "'nope' 컬럼이 없습니다"),
        ("supply; DROP TABLE t", "읽을 수 없는"), ("supply + (SELECT 1)", "'SELECT' 컬럼이 없습니다"),
    ):
        out = rule_sql.compile_rule(_spec("FORMULA", column="total", expected=bad), T, types)
        assert isinstance(out, Outcome) and why in out.detail, (bad, out.detail)
    # 저장 전 검사는 모양만 본다(컬럼이 있는지는 진단할 때)
    rule_sql.check_expression("FORMULA", "anything + 1")
    rule_sql.check_expression("NOT_NULL", None)
    with pytest.raises(rule_sql.ExpressionError):
        rule_sql.check_expression("LOGICAL", "cancel_yn = 'Y'")


def test_run_with_indicators_evaluates_only_those_rules(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_yn, r_date) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "ACCEPTED_VALUES", "column": "use_yn", "expected": "Y,N"},
        {"check_type": "DATE_FORMAT", "column": "reg_ymd", "expected": "YYYYMMDD"},
    ]))
    monkeypatch.setattr(worker_trino, "execute", _FakeTrino(lambda _sql: [100, 0, 100, 3, 90, 0]))

    # 전체 실행 — 결과 세 건에 실행 ID 가 남는다
    first = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert first.indicators == [] and first.job_run_id is None
    assert asyncio.run(_work(sm)) is True
    assert {r.run_id for r in _results(sm, ds_id).values()} == {first.id}

    # 필수값·날짜만 — ACCEPTED_VALUES 는 쿼리에 없고 직전 결과를 이어받는다
    fake = _FakeTrino(lambda _sql: [100, 5, 90, 0])
    monkeypatch.setattr(worker_trino, "execute", fake)
    second = _run(sm, lambda s: runs.create_run(s, ds_id, "alice", indicators=["date", "REQUIRED"]))
    assert second.indicators == ["REQUIRED", "DATE"]
    assert asyncio.run(_work(sm)) is True
    assert "IN (" not in fake.calls[0]["sql"], "고르지 않은 지표의 규칙은 쿼리에 넣지 않는다"

    done = _run(sm, lambda s: runs.get_run(s, second.id))
    # 분모는 이 실행이 진단하는 규칙만(DEF-149)
    assert done.status == "SUCCEEDED" and done.rules_total == 2 and done.rules_evaluated == 2
    assert (done.checked_count, done.error_count) == (190, 5)

    async def by_run(s):
        rows = (await s.execute(select(QualityResult).order_by(QualityResult.id))).scalars().all()
        return [(r.rule_id, r.run_id) for r in rows]
    assert _run(sm, by_run) == [
        (r_nn, first.id), (r_yn, first.id), (r_date, first.id), (r_nn, second.id), (r_date, second.id)]

    # 다시 실행은 같은 지표를 이어받는다
    again = _run(sm, lambda s: runs.retry_run(s, second.id, "bob"))
    assert again.indicators == ["REQUIRED", "DATE"]


def test_create_run_rejects_unknown_or_empty_indicators(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    with pytest.raises(ValueError):
        _run(sm, lambda s: runs.create_run(s, ds_id, "alice", indicators=["NOPE"]))
    with pytest.raises(runs.RunConflictError, match="선택한 진단 기준"):
        _run(sm, lambda s: runs.create_run(s, ds_id, "alice", indicators=["REFERENCE"]))


def test_api_indicators_and_run_results(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_yn) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "ACCEPTED_VALUES", "column": "use_yn", "expected": "Y,N"},
    ]))
    client = _api(sm)

    listed = client.get("/api/v1/quality/indicators").json()
    assert [i["key"] for i in listed] == [i.key for i in indicators.INDICATORS]
    assert next(i for i in listed if i["key"] == "REQUIRED") == {
        "key": "REQUIRED", "name": "필수값", "area": "COMPLETENESS", "area_name": "완결성",
        "check_types": ["NOT_NULL"], "description": "반드시 있어야 하는 값이 비어 있는지(NULL)", "domain": None,
        "definable": True, "ai_ready": "AI_COMPLETENESS", "ai_ready_name": "완전성", "ai_ready_detail": "필수 속성 충족도"}
    assert next(i for i in listed if i["key"] == "VOLUME")["ai_ready"] is None
    assert next(i for i in listed if i["key"] == "YN")["domain"] == "YN"
    assert not next(i for i in listed if i["key"] == "LOGICAL")["definable"], "테이블마다 다른 기준은 지표로 미리 정하지 않는다"

    assert client.post(f"/api/v1/quality/datasets/{ds_id}/runs", json={"indicators": ["NOPE"]}).status_code == 422
    assert client.post(f"/api/v1/quality/datasets/{ds_id}/runs", json={"indicators": ["DATE"]}).status_code == 409
    created = client.post(f"/api/v1/quality/datasets/{ds_id}/runs", json={"indicators": ["YN"]})
    assert created.status_code == 202 and created.json()["indicators"] == ["YN"]
    run_id = created.json()["id"]
    assert client.get(f"/api/v1/quality/runs/{run_id}/results").json() == [], "아직 실행 전"

    monkeypatch.setattr(worker_trino, "execute", _FakeTrino(lambda _sql: [50, 50, 2]))
    assert asyncio.run(_work(sm)) is True
    results = client.get(f"/api/v1/quality/runs/{run_id}/results").json()
    assert [(r["rule_id"], r["indicator"], r["run_id"], r["checked_count"], r["error_count"]) for r in results] == [
        (r_yn, "YN", run_id, 50, 2)]
    assert client.get("/api/v1/quality/runs/999/results").status_code == 404
    # 품질 탭의 최근 결과에도 지표가 붙는다 — 평가한 적 없는 필수값 규칙은 결과가 없다
    latest = client.get(f"/api/v1/quality/datasets/{ds_id}/results").json()
    assert [(r["rule_id"], r["indicator"]) for r in latest] == [(r_yn, "YN")]


def test_api_rule_domain_and_expression_checks(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    client = _api(sm)
    base = {"dataset_id": ds_id, "rule_name": "등급", "check_type": "ACCEPTED_VALUES",
            "column_name": "use_yn", "expected_value": "A,B"}

    # 도메인을 적지 않으면 컬럼 이름으로 — use_yn → 여부
    auto = client.post("/api/v1/quality/rules", json=base).json()
    assert (auto["domain"], auto["indicator"]) == (None, "YN")
    # 적으면 그 도메인이 이긴다
    coded = client.post("/api/v1/quality/rules", json={**base, "domain": "code"}).json()
    assert (coded["domain"], coded["indicator"]) == ("CODE", "CODE")
    assert client.post("/api/v1/quality/rules", json={**base, "domain": "RATE"}).status_code == 422
    assert client.post("/api/v1/quality/rules", json={
        **base, "check_type": "NOT_NULL", "domain": "YN"}).status_code == 422

    rule = f"/api/v1/quality/rules/{coded['id']}"
    assert client.put(rule, json={"domain": "RATE"}).status_code == 422
    assert client.put(rule, json={"domain": ""}).json()["domain"] is None, "빈 값은 자동"
    assert client.put(rule, json={"domain": "CODE"}).json()["indicator"] == "CODE"
    # 유형을 바꿔 맞지 않게 된 도메인은 자동으로 되돌린다
    moved = client.put(rule, json={"check_type": "MAX_VALUE", "column_name": "total_amt", "expected_value": "10"}).json()
    assert (moved["domain"], moved["indicator"]) == (None, "AMOUNT")

    # 논리관계·계산식은 저장할 때 모양을 본다
    logical = {"dataset_id": ds_id, "rule_name": "해지일", "check_type": "LOGICAL"}
    assert client.post("/api/v1/quality/rules", json={**logical, "expected_value": "cancel_yn = 'Y'"}).status_code == 422
    ok = client.post("/api/v1/quality/rules", json={
        **logical, "expected_value": "IF cancel_yn = 'Y' THEN cancel_ymd IS NOT NULL"})
    assert ok.status_code == 201 and ok.json()["indicator"] == "LOGICAL"
    assert client.put(f"/api/v1/quality/rules/{ok.json()['id']}", json={"expected_value": "IF x THEN y"}).status_code == 422
    assert client.post("/api/v1/quality/rules", json={
        "dataset_id": ds_id, "rule_name": "합계", "check_type": "FORMULA", "column_name": "total",
        "expected_value": "supply +"}).status_code == 422
