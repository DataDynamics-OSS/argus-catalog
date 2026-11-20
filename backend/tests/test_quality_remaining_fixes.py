# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 잔여 결함 회귀 테스트 (자기완결형, SQLite·가짜 원천 엔진).

- DQ-T09: check_type·severity 허용 목록, threshold 0~100 (생성·수정)
- DQ-T18: 없는 데이터셋 규칙 생성 → 404, 규칙 수정 rule_name ""/null → 422
  (간이 진단 · 주기 설정 · 카탈로그 서버의 원천 프로파일(DQ-T02)은 없앴다 — TODO-095. 최신성(DQ-T06)은
  tests/test_quality_source_timezone.py)

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_remaining_fixes.py
"""

from __future__ import annotations

import asyncio
import json

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
from app.quality.models import (
    DataProfile,
    QualityDefinition,
    QualityResult,
    QualityRule,
    QualityScore,
)
from app.quality.schemas import QualityRuleCreate, QualityRuleUpdate

_TABLES = (
    cm.Organization, cm.System, cm.Datasource, cm.Catalog, cm.CatalogConnection,
    cm.DatasourceConfiguration, cm.Dataset, cm.Owner,
    cm.DatasetSchema, cm.DatasetProperty, QualityDefinition, QualityRule, QualityResult, QualityScore, DataProfile,
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
    for ct in ("NOT_NULL", "UNIQUE", "MIN_VALUE", "MAX_VALUE", "ROW_COUNT",
               "ACCEPTED_VALUES", "REGEX"):
        QualityRuleCreate(**base, check_type=ct)
    # 최신성은 시각 컬럼이 있어야 하고 기대값(허용 경과 시간)은 숫자
    QualityRuleCreate(**base, check_type="FRESHNESS", column_name="updated_at")
    for column, expected in ((None, None), ("updated_at", "하루")):
        with pytest.raises(ValidationError):
            QualityRuleCreate(**base, check_type="FRESHNESS", column_name=column, expected_value=expected)
    # 커스텀 SQL·Python 은 저장할 때 모양을 본다(조회 쿼리 하나, 플러그인 설정 JSON)
    QualityRuleCreate(**base, check_type="CUSTOM_SQL", expected_value="SELECT count(*) FROM t WHERE x < 0")
    QualityRuleCreate(**base, check_type="CUSTOM_PYTHON", expected_value='{"module": "m", "fn": "f"}')
    for ct, expected in (("CUSTOM_SQL", None), ("CUSTOM_SQL", "DELETE FROM t"), ("CUSTOM_SQL", "SELECT 1; SELECT 2"),
                         ("CUSTOM_PYTHON", "{}"), ("CUSTOM_PYTHON", '{"module": "../x", "fn": "f"}')):
        with pytest.raises(ValidationError):
            QualityRuleCreate(**base, check_type=ct, expected_value=expected)
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

    # 일반 사용자 u 가 소유한 데이터셋 — 품질 변경은 소유자 또는 관리자만
    async def own():
        async with sm() as s:
            (await s.get(cm.Dataset, ds_id)).created_by = "u"
            await s.commit()
    asyncio.run(own())

    monkeypatch.setattr(perm, "check_feature", allow)
    app = FastAPI()
    app.include_router(quality_router.router, prefix="/api/v1")
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: TokenUser(sub="u", username="u", roles=["argus-user"])
    c = TestClient(app)
    base = "/api/v1/quality"

    # 없는 데이터셋 — 규칙 생성(409 아님) 404. 예전 간이 진단 · 주기 API 는 없다
    r = c.post(f"{base}/rules", json={"dataset_id": 9999, "rule_name": "r", "check_type": "NOT_NULL"})
    assert r.status_code == 404 and r.json()["detail"] == "데이터셋을 찾을 수 없습니다."
    for method, path in (("POST", f"/datasets/{ds_id}/check"), ("PUT", f"/datasets/{ds_id}/schedule"),
                         ("GET", f"/datasets/{ds_id}/schedule"), ("POST", f"/datasets/{ds_id}/profile"),
                         ("POST", f"/datasets/{ds_id}/results/import"), ("POST", f"/datasets/{ds_id}/profile/import")):
        assert c.request(method, base + path, json={}).status_code in (404, 405), (method, path)

    # 지원하지 않는 진단 유형·범위 밖 임계값 → 422
    assert c.post(f"{base}/rules", json={"dataset_id": ds_id, "rule_name": "r",
                                        "check_type": "FOO"}).status_code == 422
    assert c.post(f"{base}/rules", json={"dataset_id": ds_id, "rule_name": "r",
                                        "check_type": "NOT_NULL", "threshold": 200}).status_code == 422

    # 최신성은 시각 컬럼이 있어야 한다
    r = c.post(f"{base}/rules", json={"dataset_id": ds_id, "rule_name": "f", "check_type": "FRESHNESS",
                                      "expected_value": "24"})
    assert r.status_code == 422 and "시각 컬럼" in r.text

    # 정상 생성
    r = c.post(f"{base}/rules", json={"dataset_id": ds_id, "rule_name": "r", "check_type": "ROW_COUNT",
                                      "expected_value": "0"})
    assert r.status_code == 201, r.text
    rule_id = r.json()["id"]

    # 규칙 수정 — rule_name ""(예전 허용)·null(예전 409) 모두 422
    assert c.put(f"{base}/rules/{rule_id}", json={"rule_name": ""}).status_code == 422
    assert c.put(f"{base}/rules/{rule_id}", json={"rule_name": None}).status_code == 422
    r = c.put(f"{base}/rules/{rule_id}", json={"expected_value": "10"})
    assert r.status_code == 200 and r.json()["rule_name"] == "r"
