# SPDX-License-Identifier: Apache-2.0
"""품질 사용 흐름 결함 회귀 테스트 (자기완결형, SQLite).

- TODO-095: 데이터셋 품질 탭의 예전 진단 주기(데이터셋 속성)는 기동할 때 그 데이터셋 하나를 대상으로 하는 품질 진단
  작업으로 옮기고 속성을 지운다(예전 DEF-053 의 주기 설정 · 예약 실패 표시는 주기 스케줄러와 함께 없앴다).
- DEF-056: 규칙을 지우면 남은 규칙의 최신 결과로 최신 점수·데이터셋 품질 상태를 다시 계산한다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_flow_fixes.py
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import app.catalog.models as cm
from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session
from app.permissions import router as perm
from app.quality import jobs, service
from app.quality import router as quality_router
from app.quality.models import QualityJob, QualityScore
from app.quality.schemas import ColumnProfile
from tests.test_quality_scoring_fixes import (  # noqa: F401
    _imp,
    _make_sessionmaker,
    _no_alerts,
    _run,
    _seed,
)


def _client(sm, monkeypatch) -> TestClient:
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
    return TestClient(app)


async def _set_type(s, ds_id, db_type):
    d = await s.get(cm.Dataset, ds_id)
    src = await s.get(cm.Datasource, d.datasource_id)
    src.type = db_type


async def _prop(s, ds_id, key):
    return (await s.execute(select(cm.DatasetProperty.property_value).where(
        cm.DatasetProperty.dataset_id == ds_id, cm.DatasetProperty.property_key == key,
    ))).scalar_one_or_none()


@pytest.fixture
def source(monkeypatch):
    """원천 프로파일 SQL 대역 — state["fail"] 이면 접속 실패."""
    state = {"fail": False, "calls": 0}

    async def fake(_session, _dataset, _fields):
        state["calls"] += 1
        if state["fail"]:
            raise ConnectionError("connection refused")
        return 10, [ColumnProfile(column_name="col", column_type="VARCHAR", total_count=10,
                                  null_count=0, unique_count=10, unique_percent=100.0)]

    monkeypatch.setattr(service, "_profile_via_source_db", fake)
    return state


# ---------------------------------------------------------------------------
# TODO-095 — 예전 진단 주기를 품질 진단 작업으로
# ---------------------------------------------------------------------------

def _add_props(sm, ds_id, **props):
    async def go(s):
        for key, value in props.items():
            s.add(cm.DatasetProperty(dataset_id=ds_id, property_key=key, property_value=value))
    _run(sm, go)


def test_legacy_schedules_become_quality_jobs(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL"}]))
    _run(sm, lambda s: _own(s, ds_id, "bob"))
    _add_props(sm, ds_id, **{jobs.LEGACY_SCHEDULE_PROPERTY: "daily",
                             jobs.LEGACY_SCHEDULE_FAILURE_PROPERTY: '{"at": "2026-01-01T00:00:00+00:00", "reason": "x"}'})
    # 같은 이름의 작업이 이미 있으면 데이터셋 ID 를 붙인다
    _run(sm, lambda s: _job(s, "t 주기 진단"))

    assert asyncio.run(jobs.migrate_dataset_schedules(sm)) == 1
    job = _run(sm, lambda s: s.scalar(select(QualityJob).where(QualityJob.name != "t 주기 진단")))
    assert job.name == f"t 주기 진단 (#{ds_id})" and job.created_by == "bob"
    assert (job.schedule_type, job.schedule_time, job.schedule_day, job.enabled) == ("DAILY", "03:00", None, True)
    assert json.loads(job.targets_json) == [{"type": "dataset", "dataset_id": ds_id}]
    assert job.indicators is None and job.definition_ids is None and job.next_run_at is not None
    assert "매일 03:00" in job.description
    left = _run(sm, lambda s: s.scalar(select(func.count()).select_from(cm.DatasetProperty)))
    assert left == 0, "주기 · 실패 기록 속성은 지운다"
    # 다시 돌려도 옮길 것이 없다
    assert asyncio.run(jobs.migrate_dataset_schedules(sm)) == 0


@pytest.mark.parametrize(("value", "expected"), [
    ("hourly", ("HOURLY", None, None, 1)), ("weekly", ("WEEKLY", "03:00", 0, None)), ("yearly", None),
])
def test_legacy_schedule_mapping(tmp_path, value, expected) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL"}]))
    _add_props(sm, ds_id, **{jobs.LEGACY_SCHEDULE_PROPERTY: value})
    assert asyncio.run(jobs.migrate_dataset_schedules(sm)) == (0 if expected is None else 1)
    job = _run(sm, lambda s: s.scalar(select(QualityJob)))
    if expected is None:
        assert job is None, "알 수 없는 값은 옮기지 않고 지운다"
    else:
        assert (job.schedule_type, job.schedule_time, job.schedule_day, job.schedule_interval_hours) == expected
        assert job.created_by == "system"
        assert job.include_profile is True, "예전 주기 진단처럼 회차마다 프로파일도 새로 뜬다"
    assert _run(sm, lambda s: s.scalar(select(func.count()).select_from(cm.DatasetProperty))) == 0


async def _own(s, ds_id, owner):
    (await s.get(cm.Dataset, ds_id)).created_by = owner


async def _job(s, name):
    s.add(QualityJob(name=name, targets_json="[]", schedule_type="NONE"))


# ---------------------------------------------------------------------------
# DEF-056 — 규칙 삭제 후 점수·상태 재계산
# ---------------------------------------------------------------------------

async def _scores(s, ds_id):
    return (await s.execute(select(func.count()).select_from(QualityScore)
                            .where(QualityScore.dataset_id == ds_id))).scalar()


def test_delete_rule_recomputes_score_and_status(tmp_path, _no_alerts) -> None:  # noqa: F811
    alerts = _no_alerts
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_ok, r_bad) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL"}, {"check_type": "CUSTOM_SQL", "column": None, "severity": "BREAKING"},
    ]))
    resp = _run(sm, lambda s: service.import_results(s, ds_id, _imp((r_ok, True), (r_bad, False))))
    assert resp.score == 50.0
    ds = _run(sm, lambda s: s.get(cm.Dataset, ds_id))
    assert ds.quality_status == "BAD"
    before = _run(sm, lambda s: service.get_latest_score(s, ds_id))
    n_alerts = len(alerts)

    assert _run(sm, lambda s: service.delete_rule(s, r_bad)) is True

    after = _run(sm, lambda s: service.get_latest_score(s, ds_id))
    assert float(after.score) == 100.0 and after.total_rules == 1 and after.failed_rules == 0
    assert after.id == before.id and after.scored_at == before.scored_at  # 이력 행 추가 없음
    assert _run(sm, lambda s: _scores(s, ds_id)) == 1
    ds = _run(sm, lambda s: s.get(cm.Dataset, ds_id))
    assert ds.quality_status == "GOOD" and float(ds.quality_score) == 100.0
    assert len(alerts) == n_alerts  # 재계산은 알림을 다시 만들지 않는다


def test_delete_rule_without_score_leaves_status(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r1,) = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL"}]))
    assert _run(sm, lambda s: service.delete_rule(s, r1)) is True
    assert _run(sm, lambda s: _scores(s, ds_id)) == 0
    ds = _run(sm, lambda s: s.get(cm.Dataset, ds_id))
    assert ds.quality_status is None
