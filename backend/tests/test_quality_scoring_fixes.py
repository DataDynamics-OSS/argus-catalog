# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 점수 산출 결함 회귀 테스트 (자기완결형, SQLite).

- A: 예전 간이 진단이 남긴 "평가 제외" 자리표시 통과 행은 최신 결과로 보지 않는다(간이 진단은 없앴다 — TODO-095)
- D: 진단 결과 저장(``service.import_results`` — 품질 워커의 정밀 진단) — 빈 목록·rule_id 중복은 스키마 오류,
     비활성 규칙은 거절, 점수는 활성 규칙 전체 기준(이어받기/제외)

임계값 0 · 정확한 건수 판정은 정밀 진단의 판정(rule_sql) 테스트가 본다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_scoring_fixes.py
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.alert.service as alert_service
import app.catalog.models as cm
from app.quality import service
from app.quality.models import (
    DataProfile,
    QualityDefinition,
    QualityJob,
    QualityJobRun,
    QualityResult,
    QualityRule,
    QualityRun,
    QualityScore,
)
from app.quality.schemas import (
    ColumnProfile,
    ResultImportItem,
    ResultsImportRequest,
)

_TABLES = (
    cm.Organization, cm.System, cm.Datasource, cm.Catalog, cm.CatalogConnection,
    cm.DatasourceConfiguration, cm.Dataset,
    cm.DatasetSchema, cm.DatasetProperty, QualityDefinition, QualityRule, QualityResult, QualityScore, DataProfile,
    # 결과 행의 run_id 외래키가 가리키는 표 — SQLite 는 외래키 검사를 켜면 대상 표가 있어야 INSERT 된다
    QualityJob, QualityJobRun, QualityRun,
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
# A — 예전 간이 진단의 자리표시 결과
# ---------------------------------------------------------------------------

def test_legacy_skip_rows_are_not_carried_forward(tmp_path) -> None:
    """예전 간이 진단이 남긴 "평가 제외" 통과 행은 이어받지도, 최신 결과로 보이지도 않는다."""
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_sql) = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL"}, {"check_type": "CUSTOM_SQL", "column": None}]))

    async def legacy(s):
        s.add(QualityResult(rule_id=r_sql, dataset_id=ds_id, passed="false", actual_value="3", detail="batch"))
        await s.flush()
        s.add(QualityResult(rule_id=r_sql, dataset_id=ds_id, passed="true", actual_value="평가 제외",
                            detail="old server"))
    _run(sm, legacy)
    resp = _run(sm, lambda s: service.import_results(s, ds_id, _imp((r_nn, True))))
    sql_res = next(r for r in resp.results if r.rule_id == r_sql)
    assert resp.score == 50.0 and sql_res.passed == "false" and sql_res.carried_forward

    async def check(s):
        latest = {r.rule_id: r for r in await service.get_latest_results(s, ds_id)}
        assert latest[r_sql].passed == "false" and latest[r_sql].detail == "batch"
    _run(sm, check)


def test_latest_results_skip_rules_never_checked(tmp_path) -> None:
    """아직 진단하지 않은 규칙은 최신 결과에 나오지 않는다(예전에는 간이 진단 미지원 유형만 "미평가" 로 보였다)."""
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_re) = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL"}, {"check_type": "REGEX", "expected": "^a"}]))
    _run(sm, lambda s: service.import_results(s, ds_id, _imp((r_nn, True))))

    async def check(s):
        assert [r.rule_id for r in await service.get_latest_results(s, ds_id)] == [r_nn]
    _run(sm, check)


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
