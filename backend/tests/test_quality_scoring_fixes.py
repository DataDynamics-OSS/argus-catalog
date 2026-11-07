# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 점수 산출 결함 회귀 테스트 (자기완결형, SQLite).

- A: 서버가 평가할 수 없는 유형(ACCEPTED_VALUES·REGEX·CUSTOM_SQL·CUSTOM_PYTHON)은 새 결과를 남기지 않고
     직전(배치) 결과를 이어받는다 — 배치 FAIL 을 서버 검증이 통과로 덮어쓰지 않음. 이전 결과 없으면 점수 제외.
- B: threshold 0 이 100 으로 바뀌지 않음
- C: NOT_NULL/UNIQUE 판정은 반올림된 비율이 아니라 정확한 건수로 (30만 행 중 NULL 1건 → 임계값 100 실패)
- D: 결과 반입 — 빈 목록·rule_id 중복 422, 비활성 규칙 400, 점수는 활성 규칙 전체 기준(이어받기/제외)

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_scoring_fixes.py
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.alert.service as alert_service
import app.catalog.models as cm
from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session
from app.permissions import router as perm
from app.quality import router as quality_router
from app.quality import service
from app.quality.models import DataProfile, QualityResult, QualityRule, QualityScore
from app.quality.schemas import (
    ColumnProfile,
    ProfileResponse,
    ResultImportItem,
    ResultsImportRequest,
)

_TABLES = (
    cm.Organization, cm.System, cm.Datasource, cm.DatasourceConfiguration, cm.Dataset,
    cm.DatasetSchema, cm.DatasetProperty, QualityRule, QualityResult, QualityScore, DataProfile,
)


@pytest.fixture(autouse=True)
def _no_alerts(monkeypatch):
    calls: list = []

    async def fake(_session, dataset_id, score, failed):
        calls.append((dataset_id, score, failed))

    monkeypatch.setattr(alert_service, "evaluate_quality_alerts", fake)
    return calls


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


async def _seed(sm, rules: list[dict]) -> tuple[int, list[int]]:
    """데이터셋 + 프로파일(col: 10행, NULL 0) + 규칙들."""
    async with sm() as s:
        ds = cm.Datasource(datasource_id="ds-1", name="ds", type="postgresql")
        s.add(ds)
        await s.flush()
        d = cm.Dataset(urn="urn:1", name="t", datasource_id=ds.id, qualified_name="1.s.t")
        s.add(d)
        await s.flush()
        cols = [ColumnProfile(column_name="col", column_type="VARCHAR", total_count=10,
                              null_count=0, unique_count=10, unique_percent=100.0).model_dump()]
        s.add(DataProfile(dataset_id=d.id, row_count=10, profile_json=json.dumps(cols)))
        ids = []
        for i, r in enumerate(rules):
            rule = QualityRule(dataset_id=d.id, rule_name=r.get("name", f"r{i}"),
                               check_type=r["check_type"], column_name=r.get("column", "col"),
                               expected_value=r.get("expected"), threshold=r.get("threshold", 100),
                               severity=r.get("severity", "WARNING"), is_active=r.get("active", "true"))
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


def _imp(*items) -> ResultsImportRequest:
    return ResultsImportRequest(results=[ResultImportItem(rule_id=i, passed=p, actual_value="x",
                                                          detail="batch") for i, p in items])


# ---------------------------------------------------------------------------
# A — 서버 검증이 배치 결과를 덮어쓰지 않음
# ---------------------------------------------------------------------------

def test_server_check_keeps_batch_failure(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_sql) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL"}, {"check_type": "CUSTOM_SQL", "column": None, "severity": "BREAKING"},
    ]))

    # 배치 반입: NOT_NULL 통과, CUSTOM_SQL 실패 → 50점
    resp = _run(sm, lambda s: service.import_results(s, ds_id, _imp((r_nn, True), (r_sql, False))))
    assert resp.score == 50.0 and resp.failed == 1

    # 서버 검증(수동/주기) — CUSTOM_SQL 은 새로 평가하지 않고 배치 FAIL 을 이어받는다
    resp = _run(sm, lambda s: service.run_quality_check(s, ds_id))
    assert resp.score == 50.0 and resp.total_rules == 2 and resp.passed == 1 and resp.failed == 1
    sql_res = next(r for r in resp.results if r.rule_id == r_sql)
    assert sql_res.passed == "false" and sql_res.carried_forward is True and sql_res.detail == "batch"
    assert sql_res.checked_at is not None and sql_res.id is not None
    nn_res = next(r for r in resp.results if r.rule_id == r_nn)
    assert nn_res.carried_forward is False and nn_res.passed == "true"

    async def check(s):
        rows = (await s.execute(select(QualityResult).where(QualityResult.rule_id == r_sql))).scalars().all()
        assert len(rows) == 1  # 서버 검증은 CUSTOM_SQL 결과 행을 새로 남기지 않음
        latest = await service.get_latest_results(s, ds_id)
        assert next(r for r in latest if r.rule_id == r_sql).passed == "false"
        d = (await s.execute(select(cm.Dataset).where(cm.Dataset.id == ds_id))).scalar_one()
        assert float(d.quality_score) == 50.0 and d.quality_status == "BAD"  # BREAKING 실패 유지
    _run(sm, check)


def test_server_check_without_prior_result_excludes_rule(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_re, r_av) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL"}, {"check_type": "REGEX", "expected": "^a"},
        {"check_type": "ACCEPTED_VALUES", "expected": '["a"]'},
    ]))
    resp = _run(sm, lambda s: service.run_quality_check(s, ds_id))
    # 평가 가능한 규칙(NOT_NULL)만 분모 — 100점, 1/1
    assert resp.score == 100.0 and resp.total_rules == 1 and resp.passed == 1 and resp.failed == 0
    skipped = [r for r in resp.results if r.rule_id in (r_re, r_av)]
    assert len(skipped) == 2
    assert all(r.passed is None and r.id is None and r.checked_at is None for r in skipped)
    assert all("점수에서 제외" in r.detail for r in skipped)

    async def check(s):
        rows = (await s.execute(select(QualityResult).where(QualityResult.rule_id != r_nn))).scalars().all()
        assert rows == []  # 미평가 규칙은 결과 행을 남기지 않음
        latest = {r.rule_id: r for r in await service.get_latest_results(s, ds_id)}
        assert latest[r_nn].passed == "true"
        # 조회에서도 미평가 항목으로 보인다 (UI 상태 아이콘 "미평가")
        assert latest[r_re].passed is None and latest[r_av].passed is None and latest[r_re].id is None
    _run(sm, check)


def test_legacy_skip_rows_are_not_carried_forward(tmp_path) -> None:
    """예전 서버 검증이 남긴 "평가 제외" 통과 행은 이어받지 않는다."""
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_sql,) = asyncio.run(_seed(sm, [{"check_type": "CUSTOM_SQL", "column": None}]))

    async def legacy(s):
        s.add(QualityResult(rule_id=r_sql, dataset_id=ds_id, passed="false", actual_value="3", detail="batch"))
        await s.flush()
        s.add(QualityResult(rule_id=r_sql, dataset_id=ds_id, passed="true", actual_value="평가 제외",
                            detail="old server"))
    _run(sm, legacy)
    resp = _run(sm, lambda s: service.run_quality_check(s, ds_id))
    assert resp.score == 0.0 and resp.results[0].passed == "false" and resp.results[0].carried_forward

    async def check(s):
        latest = await service.get_latest_results(s, ds_id)
        assert latest[0].passed == "false" and latest[0].detail == "batch"
    _run(sm, check)


def test_nothing_evaluable_keeps_100(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "CUSTOM_PYTHON", "column": None}]))
    resp = _run(sm, lambda s: service.run_quality_check(s, ds_id))
    assert resp.score == 100.0 and resp.total_rules == 0 and resp.results[0].passed is None


# ---------------------------------------------------------------------------
# B·C — 임계값 0, 정확한 건수 판정
# ---------------------------------------------------------------------------

def _profile(cols: list[ColumnProfile], rows: int) -> tuple[ProfileResponse, dict]:
    p = ProfileResponse(id=1, dataset_id=1, row_count=rows, profiled_at=datetime.now(timezone.utc), columns=cols)
    return p, {c.column_name: c for c in cols}


def _rule(check_type, threshold):
    return SimpleNamespace(check_type=check_type, column_name="c", expected_value=None, threshold=threshold)


def test_threshold_zero_is_not_100() -> None:
    # 전부 NULL·고유값 0 — 임계값 0 이면 통과해야 한다 (예전: 0 → 100 으로 바뀌어 실패)
    p, cm_ = _profile([ColumnProfile(column_name="c", column_type="INT", total_count=5, null_count=5,
                                     null_percent=100.0, unique_count=0, unique_percent=0.0)], 5)
    assert service._evaluate_rule(_rule("NOT_NULL", Decimal("0.00")), p, cm_)[0] is True
    assert service._evaluate_rule(_rule("UNIQUE", Decimal("0")), p, cm_)[0] is True
    assert service._evaluate_rule(_rule("NOT_NULL", 0), p, cm_)[0] is True
    # None 은 기본 100
    assert service._evaluate_rule(_rule("NOT_NULL", None), p, cm_)[0] is False


def test_exact_counts_decide_not_null_and_unique() -> None:
    n = 300_000
    p, cm_ = _profile([ColumnProfile(column_name="c", column_type="INT", total_count=n, null_count=1,
                                     null_percent=0.0, unique_count=n - 1, unique_percent=100.0)], n)
    passed, actual, detail = service._evaluate_rule(_rule("NOT_NULL", Decimal("100.00")), p, cm_)
    assert passed is False and actual == "100.0%" and "NULL 1건" in detail
    passed, _, detail = service._evaluate_rule(_rule("UNIQUE", Decimal("100.00")), p, cm_)
    assert passed is False and "고유 299,999개" in detail
    # 임계값 99.99 는 통과 (정확 비교 — 299999/300000 = 99.99967%)
    assert service._evaluate_rule(_rule("NOT_NULL", Decimal("99.99")), p, cm_)[0] is True
    # 경계값 정확히 같으면 통과 (199/200 = 99.5%)
    p2, cm2 = _profile([ColumnProfile(column_name="c", column_type="INT", total_count=200, null_count=1,
                                      unique_count=199)], 200)
    assert service._evaluate_rule(_rule("NOT_NULL", Decimal("99.50")), p2, cm2)[0] is True
    assert service._evaluate_rule(_rule("UNIQUE", Decimal("99.50")), p2, cm2)[0] is True
    assert service._evaluate_rule(_rule("UNIQUE", Decimal("99.51")), p2, cm2)[0] is False


def test_empty_table_semantics_unchanged() -> None:
    p, cm_ = _profile([ColumnProfile(column_name="c", column_type="INT", total_count=0)], 0)
    assert service._evaluate_rule(_rule("NOT_NULL", Decimal("100")), p, cm_)[0] is True
    assert service._evaluate_rule(_rule("UNIQUE", Decimal("100")), p, cm_)[0] is False
    # 건수 없이 비율만 있는 반입 프로파일 — 비율로 판정
    p, cm_ = _profile([ColumnProfile(column_name="c", column_type="INT", null_percent=10.0,
                                     unique_percent=95.0)], 10)
    assert service._evaluate_rule(_rule("NOT_NULL", Decimal("90")), p, cm_)[0] is True
    assert service._evaluate_rule(_rule("UNIQUE", Decimal("96")), p, cm_)[0] is False


# ---------------------------------------------------------------------------
# D — 결과 반입 점수
# ---------------------------------------------------------------------------

def test_import_request_validation() -> None:
    with pytest.raises(ValidationError):
        ResultsImportRequest(results=[])
    with pytest.raises(ValidationError):
        ResultsImportRequest()
    with pytest.raises(ValidationError):
        _imp((1, True), (1, False))


def test_import_scores_over_active_rules(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r1, r2, r3, r_off) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL"}, {"check_type": "UNIQUE"}, {"check_type": "REGEX", "expected": "x"},
        {"check_type": "NOT_NULL", "active": "false"},
    ]))
    # 1차 반입: r1 실패, r2 통과 — r3 은 이전 결과 없음 → 제외 → 50점
    resp = _run(sm, lambda s: service.import_results(s, ds_id, _imp((r1, False), (r2, True))))
    assert (resp.score, resp.total_rules, resp.passed, resp.failed) == (50.0, 2, 1, 1)
    r3_res = next(r for r in resp.results if r.rule_id == r3)
    assert r3_res.passed is None and "점수에서 제외" in r3_res.detail

    # 2차 반입: r2 만 통과 — r1 의 직전 FAIL 을 이어받음(예전엔 1/1 = 100점)
    resp = _run(sm, lambda s: service.import_results(s, ds_id, _imp((r2, True))))
    assert (resp.score, resp.total_rules, resp.passed, resp.failed) == (50.0, 2, 1, 1)
    r1_res = next(r for r in resp.results if r.rule_id == r1)
    assert r1_res.passed == "false" and r1_res.carried_forward is True

    # 비활성 규칙 결과는 거절 (ValueError → 400), 아무것도 저장 안 됨
    async def inactive(s):
        with pytest.raises(ValueError, match="inactive"):
            await service.import_results(s, ds_id, _imp((r2, True), (r_off, True)))
    _run(sm, inactive)

    async def check(s):
        scores = (await s.execute(select(QualityScore).where(QualityScore.dataset_id == ds_id))).scalars().all()
        assert len(scores) == 2
    _run(sm, check)


def test_import_api_status_codes(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r1, r_off) = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL"},
                                                {"check_type": "NOT_NULL", "active": "false"}]))

    async def allow(*_a, **_k):
        return True

    async def _session():
        async with sm() as s:
            yield s

    monkeypatch.setattr(perm, "check_feature", allow)
    app = FastAPI()
    app.include_router(quality_router.router, prefix="/api/v1")
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: TokenUser(
        sub="u", username="u", roles=["argus-admin", "argus-user"])
    c = TestClient(app)
    url = f"/api/v1/quality/datasets/{ds_id}/results/import"

    assert c.post(url, json={"results": []}).status_code == 422
    assert c.post(url, json={}).status_code == 422
    dup = {"results": [{"rule_id": r1, "passed": True}, {"rule_id": r1, "passed": False}]}
    assert c.post(url, json=dup).status_code == 422
    assert c.post(url, json={"results": [{"rule_id": r_off, "passed": True}]}).status_code == 400
    r = c.post(url, json={"results": [{"rule_id": r1, "passed": False}]})
    assert r.status_code == 200, r.text
    assert r.json()["score"] == 0.0 and r.json()["total_rules"] == 1
