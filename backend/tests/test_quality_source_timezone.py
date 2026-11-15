# SPDX-License-Identifier: Apache-2.0
"""최신성(FRESHNESS) 규칙의 시각 해석 · 원천 데이터 시간대 테스트 (QB-T12 / DQ-T06, TODO-095).

최신성은 정밀 진단이 평가한다 — Trino · 원천 직접 두 경로 모두 시각 컬럼의 최댓값을 집계하고, 판정(``rule_sql.judge_freshness``)
은 결과를 받은 시각 기준 경과 시간을 잰다. 시간대 없는 시각 값(MySQL DATETIME 등)은 서버 프로세스의 로컬 시간대가 아니라
설정된 원천 데이터 시간대(``quality_source_timezone``, 기본 Asia/Seoul)로 해석한다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_source_timezone.py
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.audit.models import AdminAuditLog
from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session
from app.quality import execution, rule_direct, rule_sql, service
from app.quality import router as quality_router
from app.quality.rule_sql import Compiled, Outcome, RuleSpec, TableRef
from app.settings import service as settings_service
from app.settings.models import CatalogConfiguration

KST = timedelta(hours=9)


# ---------------------------------------------------------------------------
# parse_timestamp / resolve_timezone
# ---------------------------------------------------------------------------

def test_parse_timestamp_naive_uses_source_timezone() -> None:
    # 기본 Asia/Seoul — 2006-02-23 04:12:08 KST = 2006-02-22 19:12:08 UTC
    expected = datetime(2006, 2, 22, 19, 12, 8, tzinfo=timezone.utc)
    parse = rule_sql.parse_timestamp
    assert parse("2006-02-23 04:12:08") == expected
    assert parse("2006-02-23 04:12:08", rule_sql.resolve_timezone("Asia/Seoul")) == expected
    assert parse("2006-02-23 04:12:08", timezone.utc) == expected + KST  # 원천 시간대 UTC
    # aware 값은 지정 시간대와 무관하게 자기 시간대 유지
    assert parse("2026-11-07 10:00:00+00:00", rule_sql.resolve_timezone("+09:00")) == \
        datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
    # Trino 의 결과 모양 — 밀리초, 시간대 이름이 붙은 timestamp with time zone, date
    assert parse("2006-02-23 04:12:08.000") == expected
    assert parse("2026-11-07 10:00:00.000 UTC") == datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
    assert parse("2026-11-07 19:00:00.000 Asia/Seoul") == datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
    assert parse("2006-02-23") == datetime(2006, 2, 22, 15, 0, tzinfo=timezone.utc)
    for bad in ("abc", None, "", "2026-11-07 10:00:00 Mars/Base", 12345):
        assert parse(bad) is None


def test_parse_timestamp_ignores_process_timezone(monkeypatch) -> None:
    """서버 프로세스 TZ(컨테이너 UTC·KST 등)를 바꿔도 결과가 같다."""
    if not hasattr(time, "tzset"):
        pytest.skip("tzset 미지원 플랫폼")
    expected = datetime(2006, 2, 22, 19, 12, 8, tzinfo=timezone.utc)
    try:
        for tz in ("UTC", "Asia/Seoul", "America/Los_Angeles"):
            monkeypatch.setenv("TZ", tz)
            time.tzset()
            assert rule_sql.parse_timestamp("2006-02-23 04:12:08") == expected
    finally:
        monkeypatch.undo()
        time.tzset()


def test_resolve_timezone() -> None:
    assert service.resolve_timezone is rule_sql.resolve_timezone
    assert rule_sql.resolve_timezone("UTC") is timezone.utc
    assert rule_sql.resolve_timezone(" +09:00 ").utcoffset(None) == KST
    assert rule_sql.resolve_timezone("-0330").utcoffset(None) == -timedelta(hours=3, minutes=30)
    assert rule_sql.resolve_timezone("Asia/Seoul").utcoffset(datetime(2026, 1, 1)) == KST
    for bad in ("", None, "Mars/Base", "+24:00", "../etc/passwd"):
        with pytest.raises(ValueError):
            rule_sql.resolve_timezone(bad)
    # 설정값 → tzinfo: 비었거나 해석할 수 없으면 기본(Asia/Seoul)
    for value in (None, "", "Nope/Zone"):
        assert rule_sql.source_tzinfo(value).utcoffset(datetime(2026, 1, 1)) == KST
    assert rule_sql.source_tzinfo("UTC") is timezone.utc


# ---------------------------------------------------------------------------
# 최신성 규칙 — 두 경로의 SQL 과 판정
# ---------------------------------------------------------------------------

TABLE = TableRef("pg", "public", "orders")
TYPES = {"updated_at": "timestamp", "id": "integer"}


def _fresh(expected="12", column="updated_at", tz=None) -> Compiled | Outcome:
    return rule_sql.compile_rule(RuleSpec(1, "FRESHNESS", column, expected), TABLE, TYPES, source_timezone=tz)


def test_freshness_compiles_to_max_and_count_on_both_paths() -> None:
    compiled = _fresh()
    assert isinstance(compiled, Compiled)
    assert compiled.exprs == ('max("updated_at")', 'count("updated_at")') and compiled.sample_sql is None
    agg = rule_direct.aggregate(RuleSpec(1, "FRESHNESS", "updated_at", "12"), TYPES, "mysql", ("shop", "orders"), 5)
    assert agg.exprs == ("max(`updated_at`)", "count(`updated_at`)")
    assert "FRESHNESS" in rule_direct.DIRECT_CHECK_TYPES and "FRESHNESS" not in rule_direct.PROBE_TYPES
    assert "FRESHNESS" in rule_sql.TRINO_PATH_TYPES


def test_freshness_rule_errors() -> None:
    assert _fresh(column=None) == Outcome(False, "N/A", "최신성 검사는 적재 · 수정 시각 컬럼을 지정해야 합니다")
    assert "스키마에" in _fresh(column="nope").detail
    for bad in ("abc", "-1", "nan"):
        assert "잘못된 기대값" in _fresh(expected=bad).detail
    with pytest.raises(rule_sql.ExpressionError):
        rule_sql.check_expression("FRESHNESS", "abc")
    rule_sql.check_expression("FRESHNESS", None)  # 비면 24시간


def test_freshness_judge_respects_source_timezone() -> None:
    now = datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc)
    naive_kst = "2026-11-14 02:00:00"  # KST 02:00 = UTC 전날 17:00 → 10시간 전
    judge = rule_sql.judge_freshness("updated_at", 12, rule_sql.source_tzinfo(None), "범위", now=lambda: now)
    out = judge(100, [naive_kst, 100])
    assert (out.passed, out.actual_value, out.checked_count) == (True, "10.0h", 100)
    assert "updated_at 최댓값 2026-11-14 02:00:00" in out.detail and "허용 12시간" in out.detail
    utc = rule_sql.judge_freshness("updated_at", 12, timezone.utc, "범위", now=lambda: now)
    assert utc(100, [naive_kst, 100]).actual_value == "1.0h"  # UTC 로 보면 9시간 차이
    minus5 = rule_sql.judge_freshness("updated_at", 12, rule_sql.resolve_timezone("-05:00"), "범위", now=lambda: now)
    assert minus5(100, [naive_kst, 100]).actual_value == "0.0h"  # 미래 → 0
    late = rule_sql.judge_freshness("updated_at", 5, None, "범위", now=lambda: now)
    assert late(100, [naive_kst, 100]).passed is False
    assert late(0, [None, 0]).passed is False and "값이 없어" in late(0, [None, 0]).detail
    assert "해석할 수 없습니다" in late(1, ["not-a-time", 1]).detail


def test_plan_carries_source_timezone_into_judge() -> None:
    """계획의 바탕(snapshot)에 남긴 시간대로 판정한다 — 설정을 나중에 바꿔도 그 실행은 그대로."""
    snapshot = {"engine": "TRINO", "table": ["pg", "public", "orders"], "types": TYPES,
                "rules": [{"id": 1, "check_type": "FRESHNESS", "column_name": "updated_at", "expected_value": "1"}],
                "source_timezone": "UTC"}
    plan = execution.compile_snapshot(snapshot)
    (compiled,) = plan.scan
    just_now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    assert compiled.judge(1, [just_now, 1]).passed is True  # UTC 로 해석 — KST 로 보면 9시간 전이라 실패
    plan_kst = execution.compile_snapshot({**snapshot, "source_timezone": None})
    assert plan_kst.scan[0].judge(1, [just_now, 1]).passed is False
    direct = execution.compile_snapshot({**snapshot, "engine": "DIRECT", "source_type": "postgresql"})
    assert [c.exprs for c in direct.aggregate] == [('max("updated_at")', 'count("updated_at")')]


# ---------------------------------------------------------------------------
# 설정 — 시드 기본값, GET/PUT /quality/settings
# ---------------------------------------------------------------------------

def test_seed_default_is_asia_seoul() -> None:
    seeded = {cat: {k: v for k, v, _ in items} for cat, items in settings_service._all_defaults()}
    assert seeded["quality"] == {"quality_source_timezone": "Asia/Seoul"}
    assert service.DEFAULT_SOURCE_TIMEZONE == "Asia/Seoul"


def _make_sessionmaker(db_path: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)

    async def _init():
        async with engine.begin() as conn:
            for model in (CatalogConfiguration, AdminAuditLog):
                await conn.run_sync(lambda sc, m=model: m.__table__.create(sc, checkfirst=True))

    asyncio.run(_init())
    return async_sessionmaker(engine, expire_on_commit=False)


def _client(sm, *, admin: bool) -> TestClient:
    async def _session():
        async with sm() as s:
            yield s

    app = FastAPI()
    app.include_router(quality_router.router, prefix="/api/v1")
    app.dependency_overrides[get_session] = _session
    roles = ["argus-admin", "argus-user"] if admin else ["argus-user"]
    app.dependency_overrides[get_current_user] = lambda: TokenUser(sub="u", username="u", roles=roles)
    return TestClient(app)


def test_settings_endpoints(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    base = "/api/v1/quality/settings"
    admin = _client(sm, admin=True)

    # 행이 없으면 기본값
    assert admin.get(base).json() == {"source_timezone": "Asia/Seoul"}

    async def tz_now():
        async with sm() as s:
            return rule_sql.source_tzinfo(await service.get_source_timezone_name(s))

    assert asyncio.run(tz_now()).utcoffset(datetime(2026, 1, 1)) == KST

    # 일반 사용자는 저장 불가
    assert _client(sm, admin=False).put(base, json={"source_timezone": "UTC"}).status_code == 403
    # 잘못된 시간대 422, 저장 안 됨
    r = admin.put(base, json={"source_timezone": "Mars/Base"})
    assert r.status_code == 422 and "알 수 없는 시간대" in r.json()["detail"]
    # 저장 → 조회·서비스 반영
    r = admin.put(base, json={"source_timezone": " UTC "})
    assert r.status_code == 200 and r.json() == {"source_timezone": "UTC"}
    assert admin.get(base).json() == {"source_timezone": "UTC"}
    assert asyncio.run(tz_now()) is timezone.utc

    # DB 에 잘못된 값이 들어가 있어도 기본값으로 동작(검사 실패 아님)
    async def corrupt():
        async with sm() as s:
            await settings_service.update_config(s, "quality", {"quality_source_timezone": "Nope/Zone"})
            return rule_sql.source_tzinfo(await service.get_source_timezone_name(s))

    assert asyncio.run(corrupt()).utcoffset(datetime(2026, 1, 1)) == KST
