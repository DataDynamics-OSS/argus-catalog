# SPDX-License-Identifier: Apache-2.0
"""FRESHNESS 원천 데이터 시간대 회귀 테스트 (QB-T12 / DQ-T06).

시간대 없는 시각 값(MySQL DATETIME 등)을 서버는 프로세스 로컬 시간대(컨테이너 UTC), 품질 배치는 호스트
로컬 시간대(KST)로 해석해 같은 데이터의 경과 시간이 9시간 달랐다. 이제 둘 다 설정된 원천 데이터
시간대(서버 ``quality_source_timezone``, 배치 ``--source-timezone``, 기본 Asia/Seoul)로 해석한다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_source_timezone.py
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.audit.models import AdminAuditLog
from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session
from app.quality import router as quality_router
from app.quality import service
from app.quality.schemas import ColumnProfile, ProfileResponse
from app.settings import service as settings_service
from app.settings.models import CatalogConfiguration

KST = timedelta(hours=9)


# ---------------------------------------------------------------------------
# _parse_timestamp / resolve_timezone
# ---------------------------------------------------------------------------

def test_parse_timestamp_naive_uses_source_timezone() -> None:
    # 기본 Asia/Seoul — 2006-02-23 04:12:08 KST = 2006-02-22 19:12:08 UTC
    expected = datetime(2006, 2, 22, 19, 12, 8, tzinfo=timezone.utc)
    assert service._parse_timestamp("2006-02-23 04:12:08") == expected
    assert service._parse_timestamp("2006-02-23 04:12:08", service.resolve_timezone("Asia/Seoul")) == expected
    # 원천 시간대 UTC
    assert service._parse_timestamp("2006-02-23 04:12:08", timezone.utc) == expected + KST
    # aware 값은 지정 시간대와 무관하게 자기 시간대 유지
    aware = "2026-11-07 10:00:00+00:00"
    assert service._parse_timestamp(aware, service.resolve_timezone("+09:00")) == \
        datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
    assert service._parse_timestamp("abc") is None and service._parse_timestamp(None) is None


def test_parse_timestamp_ignores_process_timezone(monkeypatch) -> None:
    """서버 프로세스 TZ(컨테이너 UTC·KST 등)를 바꿔도 결과가 같다."""
    if not hasattr(time, "tzset"):
        pytest.skip("tzset 미지원 플랫폼")
    expected = datetime(2006, 2, 22, 19, 12, 8, tzinfo=timezone.utc)
    try:
        for tz in ("UTC", "Asia/Seoul", "America/Los_Angeles"):
            monkeypatch.setenv("TZ", tz)
            time.tzset()
            assert service._parse_timestamp("2006-02-23 04:12:08") == expected
    finally:
        monkeypatch.undo()
        time.tzset()


def test_resolve_timezone() -> None:
    assert service.resolve_timezone("UTC") is timezone.utc
    assert service.resolve_timezone(" +09:00 ").utcoffset(None) == KST
    assert service.resolve_timezone("-0330").utcoffset(None) == -timedelta(hours=3, minutes=30)
    assert service.resolve_timezone("Asia/Seoul").utcoffset(datetime(2026, 1, 1)) == KST
    for bad in ("", None, "Mars/Base", "+24:00", "../etc/passwd"):
        with pytest.raises(ValueError):
            service.resolve_timezone(bad)


def test_freshness_rule_respects_source_timezone() -> None:
    now = datetime.now(timezone.utc)
    # 원천 벽시계 값(naive) — KST 로 10시간 전
    naive_kst = (now - timedelta(hours=10)).astimezone(service.resolve_timezone("Asia/Seoul")) \
        .replace(tzinfo=None).isoformat(sep=" ")
    profile = ProfileResponse(
        id=1, dataset_id=1, row_count=1, profiled_at=now,
        columns=[ColumnProfile(column_name="updated_at", column_type="DATETIME", max_value=naive_kst)])
    col_map = {c.column_name: c for c in profile.columns}
    rule = SimpleNamespace(check_type="FRESHNESS", column_name="updated_at", expected_value="12", threshold=100)

    assert service._evaluate_rule(rule, profile, col_map)[:2] == (True, "10.0h")            # 기본 KST
    passed, actual, _ = service._evaluate_rule(rule, profile, col_map, timezone.utc)       # UTC 로 보면
    assert actual == "1.0h"                                                                # 9시간 차이
    passed, actual, _ = service._evaluate_rule(rule, profile, col_map, service.resolve_timezone("-05:00"))
    assert actual == "0.0h"                                                                # 미래 → 0


# ---------------------------------------------------------------------------
# 설정 — 시드 기본값, GET/PUT /quality/settings, run_quality_check 반영
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


def test_settings_endpoints_and_check_uses_setting(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    base = "/api/v1/quality/settings"
    admin = _client(sm, admin=True)

    # 행이 없으면 기본값
    assert admin.get(base).json() == {"source_timezone": "Asia/Seoul"}

    async def tz_now():
        async with sm() as s:
            return await service.get_source_timezone(s)

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
            return await service.get_source_timezone(s)

    assert asyncio.run(corrupt()).utcoffset(datetime(2026, 1, 1)) == KST
