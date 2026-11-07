# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 잔여 결함 회귀 테스트 (자기완결형, SQLite·가짜 원천 엔진).

- DQ-T09: check_type·severity 허용 목록, threshold 0~100 (생성·수정)
- DQ-T18: 없는 데이터셋 검사 실행/규칙 생성/주기 설정 → 404, 규칙 수정 rule_name ""/null → 422
- DQ-T02: 프로파일 접속 URL 이스케이프·SSL·타임아웃, 식별자 인용, 폴백 표시(fallback)
- DQ-T06: FRESHNESS — 시각 컬럼 지정 시 데이터 최신값 기준

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_remaining_fixes.py
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.catalog.models as cm
from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session
from app.permissions import router as perm
from app.quality import router as quality_router
from app.quality import service
from app.quality.models import DataProfile, QualityResult, QualityRule, QualityScore
from app.quality.schemas import ColumnProfile, ProfileResponse, QualityRuleCreate, QualityRuleUpdate

_TABLES = (
    cm.Organization, cm.System, cm.Datasource, cm.DatasourceConfiguration, cm.Dataset,
    cm.DatasetSchema, cm.DatasetProperty, QualityRule, QualityResult, QualityScore, DataProfile,
)


def _make_sessionmaker(db_path: str):
    # 파일 DB + NullPool — TestClient 의 이벤트 루프에서도 새 연결을 열 수 있게
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


async def _seed(sm, *, ds_type="postgresql", config=None, qualified_name="1.shop.customers",
                fields=(("id", "INTEGER"),)) -> int:
    async with sm() as s:
        ds = cm.Datasource(datasource_id="ds-1", name="ds", type=ds_type)
        s.add(ds)
        await s.flush()
        s.add(cm.DatasourceConfiguration(datasource_id=ds.id, config_json=json.dumps(config or {})))
        d = cm.Dataset(urn="urn:1", name="customers", datasource_id=ds.id, qualified_name=qualified_name)
        s.add(d)
        await s.flush()
        for i, (name, typ) in enumerate(fields):
            s.add(cm.DatasetSchema(dataset_id=d.id, field_path=name, field_type=typ, ordinal=i))
        await s.commit()
        return d.id


# ---------------------------------------------------------------------------
# DQ-T09 — 스키마 검증
# ---------------------------------------------------------------------------

def test_rule_create_validation() -> None:
    base = {"dataset_id": 1, "rule_name": "r"}
    for ct in ("NOT_NULL", "UNIQUE", "MIN_VALUE", "MAX_VALUE", "ROW_COUNT", "FRESHNESS",
               "ACCEPTED_VALUES", "REGEX", "CUSTOM_SQL", "CUSTOM_PYTHON"):
        QualityRuleCreate(**base, check_type=ct)
    for sev in ("BREAKING", "WARNING", "INFO"):
        QualityRuleCreate(**base, check_type="NOT_NULL", severity=sev)
    QualityRuleCreate(**base, check_type="NOT_NULL", threshold=0)
    QualityRuleCreate(**base, check_type="NOT_NULL", threshold=100)
    for bad in ({"check_type": "FOO"}, {"check_type": "not_null"},
                {"check_type": "NOT_NULL", "severity": "CRITICAL"},
                {"check_type": "NOT_NULL", "threshold": 101},
                {"check_type": "NOT_NULL", "threshold": -1}):
        with pytest.raises(ValidationError):
            QualityRuleCreate(**base, **bad)


def test_rule_update_validation() -> None:
    # 생략은 미변경
    assert QualityRuleUpdate(expected_value="10").model_dump(exclude_unset=True) == {"expected_value": "10"}
    QualityRuleUpdate(check_type="CUSTOM_SQL", severity="INFO", threshold=50, is_active="false")
    for bad in ({"rule_name": ""}, {"rule_name": None}, {"check_type": "FOO"}, {"check_type": None},
                {"severity": "X"}, {"severity": None}, {"threshold": 150}, {"is_active": "yes"},
                {"is_active": None}):
        with pytest.raises(ValidationError):
            QualityRuleUpdate(**bad)
    # 선택 필드(column_name/expected_value)는 null 로 비울 수 있다
    QualityRuleUpdate(column_name=None, expected_value=None)


# ---------------------------------------------------------------------------
# DQ-T18 — HTTP 상태 코드
# ---------------------------------------------------------------------------

def test_missing_dataset_and_update_status_codes(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id = asyncio.run(_seed(sm))

    async def allow(*_a, **_k):
        return True

    async def _session():
        async with sm() as s:
            yield s

    monkeypatch.setattr(perm, "check_feature", allow)
    app = FastAPI()
    app.include_router(quality_router.router, prefix="/api/v1")
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: TokenUser(sub="u", username="u", roles=["argus-user"])
    c = TestClient(app)
    base = "/api/v1/quality"

    # 없는 데이터셋 — 검사 실행(500 아님)·규칙 생성·주기 설정(409 아님) 모두 404
    r = c.post(f"{base}/datasets/9999/check")
    assert r.status_code == 404 and r.json()["detail"] == "데이터셋을 찾을 수 없습니다."
    r = c.post(f"{base}/rules", json={"dataset_id": 9999, "rule_name": "r", "check_type": "NOT_NULL"})
    assert r.status_code == 404 and r.json()["detail"] == "데이터셋을 찾을 수 없습니다."
    r = c.put(f"{base}/datasets/9999/schedule", json={"schedule": "daily"})
    assert r.status_code == 404
    # 지원하지 않는 주기는 기존대로 422
    assert c.put(f"{base}/datasets/9999/schedule", json={"schedule": "yearly"}).status_code == 422

    # 지원하지 않는 검증 유형·범위 밖 임계값 → 422
    assert c.post(f"{base}/rules", json={"dataset_id": ds_id, "rule_name": "r",
                                        "check_type": "FOO"}).status_code == 422
    assert c.post(f"{base}/rules", json={"dataset_id": ds_id, "rule_name": "r",
                                        "check_type": "NOT_NULL", "threshold": 200}).status_code == 422

    # 정상 생성·주기 설정
    r = c.post(f"{base}/rules", json={"dataset_id": ds_id, "rule_name": "r", "check_type": "ROW_COUNT",
                                      "expected_value": "0"})
    assert r.status_code == 201, r.text
    rule_id = r.json()["id"]
    assert c.put(f"{base}/datasets/{ds_id}/schedule", json={"schedule": "daily"}).json() == {"schedule": "daily"}

    # 규칙 수정 — rule_name ""(예전 허용)·null(예전 409) 모두 422
    assert c.put(f"{base}/rules/{rule_id}", json={"rule_name": ""}).status_code == 422
    assert c.put(f"{base}/rules/{rule_id}", json={"rule_name": None}).status_code == 422
    r = c.put(f"{base}/rules/{rule_id}", json={"expected_value": "10"})
    assert r.status_code == 200 and r.json()["rule_name"] == "r"


# ---------------------------------------------------------------------------
# DQ-T02 — 프로파일 접속·식별자·폴백
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value

    def first(self):
        return (1, 9)

    def fetchall(self):
        return [("a", 3)]


class _FakeConn:
    def __init__(self, sqls):
        self.sqls = sqls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, stmt):
        self.sqls.append(str(stmt))
        return _FakeResult(10)


class _FakeEngine:
    def __init__(self, sqls, fail):
        self.sqls, self.fail = sqls, fail

    def connect(self):
        if self.fail:
            raise ConnectionRefusedError("refused")
        return _FakeConn(self.sqls)

    async def dispose(self):
        pass


def _patch_engine(monkeypatch, fail=False):
    captured: dict = {"sqls": []}

    def fake_create(url, **kw):
        captured["url"] = url
        captured["kw"] = kw
        return _FakeEngine(captured["sqls"], fail)

    import sqlalchemy.ext.asyncio as sa_async

    monkeypatch.setattr(sa_async, "create_async_engine", fake_create)
    return captured


def test_profile_pg_url_ssl_timeout_and_quoted_identifiers(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "p.db"))
    ds_id = asyncio.run(_seed(
        sm, config={"host": "db.local", "database": "shop", "username": "u@x",
                    "password": "p@ss/w#rd:1", "ssl_enabled": True},
        qualified_name="1.Sales.Order Items",
        fields=(("id", "INTEGER"), ('we"ird; DROP TABLE x', "VARCHAR"))))
    cap = _patch_engine(monkeypatch)

    async def run():
        async with sm() as s:
            return await service.profile_dataset(s, ds_id)

    prof = asyncio.run(run())
    url = cap["url"]
    # 비밀번호·사용자명 특수문자가 그대로 보존되고(이스케이프), PG 기본 포트 5432
    assert url.username == "u@x" and url.password == "p@ss/w#rd:1"
    assert url.host == "db.local" and url.port == 5432 and url.database == "shop"
    assert "p%40ss%2Fw%23rd" in url.render_as_string(hide_password=False)
    ca = cap["kw"]["connect_args"]
    assert ca["timeout"] == 10 and ca["command_timeout"] == 120 and ca["ssl"] == "require"
    # 식별자 인용 — 스키마·테이블·컬럼 모두 큰따옴표, 내부 따옴표는 이중화
    assert cap["sqls"][0] == 'SELECT COUNT(*) FROM "Sales"."Order Items"'
    assert any('"we""ird; DROP TABLE x" IS NULL' in q for q in cap["sqls"])
    assert all("DROP TABLE x IS" not in q for q in cap["sqls"])
    assert prof.fallback is False and prof.row_count == 10


def test_profile_mysql_url_and_backtick_identifiers(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "m.db"))
    ds_id = asyncio.run(_seed(
        sm, ds_type="mysql", config={"host": "my.local", "port": 3307, "username": "root",
                                     "password": "a@b"},
        qualified_name="sakila.ren`tal", fields=(("return date", "DATETIME"),)))
    cap = _patch_engine(monkeypatch)

    async def run():
        async with sm() as s:
            return await service.profile_dataset(s, ds_id)

    asyncio.run(run())
    url = cap["url"]
    assert url.password == "a@b" and url.port == 3307 and url.database == "sakila"
    assert url.query["charset"] == "utf8mb4"
    ca = cap["kw"]["connect_args"]
    assert ca == {"connect_timeout": 10}   # SSL 꺼짐 → ssl 인자 없음
    assert cap["sqls"][0] == "SELECT COUNT(*) FROM `ren``tal`"
    assert any("`return date` IS NULL" in q for q in cap["sqls"])


def test_profile_fallback_is_flagged(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "f.db"))
    ds_id = asyncio.run(_seed(sm, config={"host": "db.local", "username": "u", "password": "p"}))
    _patch_engine(monkeypatch, fail=True)

    async def run():
        async with sm() as s:
            prof = await service.profile_dataset(s, ds_id)
            await s.commit()
            latest = await service.get_latest_profile(s, ds_id)
            return prof, latest

    prof, latest = asyncio.run(run())
    # 폴백은 유지하되(행 수 0, 스키마 컬럼만) 응답에 표시한다
    assert prof.fallback is True and prof.row_count == 0
    assert [c.column_name for c in prof.columns] == ["id"]
    assert "연결이 거부" in prof.fallback_reason
    assert latest.fallback is False   # 조회 응답은 하위 호환 기본값

    # 접속 설정 자체가 잘못된 경우(호스트 없음)도 폴백 + 사유
    async def run2():
        async with sm() as s:
            cfg = (await s.execute(cm.DatasourceConfiguration.__table__.select())).first()
            await s.execute(cm.DatasourceConfiguration.__table__.update()
                            .where(cm.DatasourceConfiguration.__table__.c.id == cfg.id)
                            .values(config_json="{}"))
            return await service.profile_dataset(s, ds_id)

    prof2 = asyncio.run(run2())
    assert prof2.fallback is True and "호스트" in prof2.fallback_reason


# ---------------------------------------------------------------------------
# DQ-T06 — FRESHNESS
# ---------------------------------------------------------------------------

def test_freshness_uses_column_max_timestamp() -> None:
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()
    # 시간대 없는 값은 원천 데이터 시간대(기본 Asia/Seoul)의 시각 — 서버 로컬 시간대와 무관
    old = (now - timedelta(hours=72)).astimezone(ZoneInfo("Asia/Seoul")).replace(tzinfo=None).isoformat(sep=" ")
    profile = ProfileResponse(
        id=1, dataset_id=1, row_count=5, profiled_at=now,
        columns=[ColumnProfile(column_name="updated_at", column_type="TIMESTAMP", max_value=recent),
                 ColumnProfile(column_name="created_at", column_type="DATETIME", max_value=old),
                 ColumnProfile(column_name="name", column_type="VARCHAR", max_value="zzz")])
    col_map = {c.column_name: c for c in profile.columns}

    def ev(column, expected="24"):
        rule = SimpleNamespace(check_type="FRESHNESS", column_name=column, expected_value=expected,
                               threshold=100)
        return service._evaluate_rule(rule, profile, col_map)

    passed, actual, _ = ev("updated_at")
    assert passed and actual == "2.0h"
    passed, actual, _ = ev("created_at")
    assert not passed and actual == "72.0h"      # 프로파일 직후여도 데이터가 오래되면 실패
    assert ev("name")[0] is False                # 시각으로 해석 불가
    assert ev("missing")[0] is False
    # 컬럼 미지정 — 프로파일 경과 시간(하위 호환)
    passed, actual, _ = ev(None)
    assert passed and actual == "0.0h"
    assert ev(None, "abc")[0] is False
