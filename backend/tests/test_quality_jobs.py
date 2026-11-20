# SPDX-License-Identifier: Apache-2.0
"""품질 진단 작업(``app.quality.jobs``) 테스트 (자기완결형, SQLite — Trino 는 가짜, 워커는 실제 확장 코드).

- A: 주기 — 다음 실행 시각(매일·매주·매월·N시간), 검증
- B: 대상 — 카탈로그·스키마·데이터셋을 데이터셋으로 풀기, 미리 보기
- C: 실행 — 대기열에 넣기, 규칙 없는 데이터셋·넣지 못한 데이터셋, 집계·마감, 겹침, 취소
- D: 스케줄러 — 주기가 된 작업만, 한 번만, 이전 실행이 안 끝났으면 건너뜀
- E: API — 등록·수정·삭제·지금 실행·이력

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_jobs.py
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.catalog.models as cm
from app.quality import jobs, runs
from app.quality.models import QualityDefinition, QualityJob, QualityJobRun, QualityRule, QualityRun
from app.quality.schemas import QualityJobTarget, QualityJobWrite
from tests.test_quality_trino_runs import (  # noqa: F401 — _no_alerts 는 autouse fixture
    _api,
    _FakeTrino,
    _make_sessionmaker,
    _no_alerts,
    _run,
    _seed,
    _work,
    worker_trino,
)

UTC = timezone.utc


def _job(**kw) -> QualityJob:
    base = {"name": "j", "enabled": True, "schedule_type": "DAILY", "schedule_time": "02:30",
            "timezone": "Asia/Seoul", "targets_json": "[]"}
    return QualityJob(**{**base, **kw})


def _write(**kw) -> QualityJobWrite:
    base = {"name": "야간 진단", "targets": [{"type": "catalog", "catalog_id": 1}],
            "schedule_type": "DAILY", "schedule_time": "02:30"}
    return QualityJobWrite(**{**base, **kw})


# ---------------------------------------------------------------------------
# A — 주기
# ---------------------------------------------------------------------------

def test_next_run_daily_uses_job_timezone() -> None:
    # 2026-11-14 10:00 KST = 01:00 UTC. 02:30 KST 는 이미 지났으므로 다음 날 02:30 KST = 11-14 17:30 UTC
    now = datetime(2026, 11, 14, 1, 0, tzinfo=UTC)
    assert jobs.next_run_after(_job(), now) == datetime(2026, 11, 14, 17, 30, tzinfo=UTC)
    # 02:00 KST 에는 같은 날 02:30 KST
    assert jobs.next_run_after(_job(), datetime(2026, 11, 13, 17, 0, tzinfo=UTC)) == datetime(
        2026, 11, 13, 17, 30, tzinfo=UTC)
    # 정각에 걸리면 다음 날
    assert jobs.next_run_after(_job(), datetime(2026, 11, 13, 17, 30, tzinfo=UTC)) == datetime(
        2026, 11, 14, 17, 30, tzinfo=UTC)


def test_next_run_weekly_monthly_hourly_and_off() -> None:
    now = datetime(2026, 11, 14, 1, 0, tzinfo=UTC)  # 토요일 10:00 KST
    weekly = _job(schedule_type="WEEKLY", schedule_day=0, schedule_time="09:00")  # 월요일
    assert jobs.next_run_after(weekly, now) == datetime(2026, 11, 16, 0, 0, tzinfo=UTC)
    same_day_later = _job(schedule_type="WEEKLY", schedule_day=5, schedule_time="11:00")  # 토요일 11:00
    assert jobs.next_run_after(same_day_later, now) == datetime(2026, 11, 14, 2, 0, tzinfo=UTC)
    same_day_passed = _job(schedule_type="WEEKLY", schedule_day=5, schedule_time="09:00")
    assert jobs.next_run_after(same_day_passed, now) == datetime(2026, 11, 21, 0, 0, tzinfo=UTC)

    monthly = _job(schedule_type="MONTHLY", schedule_day=31, schedule_time="00:10")
    # 11월은 30일까지 — 말일 00:10 KST = 11-29 15:10 UTC
    assert jobs.next_run_after(monthly, now) == datetime(2026, 11, 29, 15, 10, tzinfo=UTC)
    passed = _job(schedule_type="MONTHLY", schedule_day=1, schedule_time="00:10")
    assert jobs.next_run_after(passed, datetime(2026, 12, 20, 0, 0, tzinfo=UTC)) == datetime(
        2026, 12, 31, 15, 10, tzinfo=UTC), "해가 넘어간다"

    hourly = _job(schedule_type="HOURLY", schedule_interval_hours=6, schedule_time=None)
    assert jobs.next_run_after(hourly, now) == now + timedelta(hours=6)
    assert jobs.next_run_after(_job(schedule_type="NONE"), now) is None
    assert jobs.next_run_after(_job(enabled=False), now) is None
    # 시간대 없는 값(SQLite)은 UTC 로 본다
    assert jobs.next_run_after(hourly, now.replace(tzinfo=None)) == now + timedelta(hours=6)


@pytest.mark.parametrize("kw", [
    {"schedule_type": "YEARLY"},
    {"schedule_time": "2:30"},
    {"schedule_time": "24:00"},
    {"schedule_type": "WEEKLY", "schedule_day": 7},
    {"schedule_type": "WEEKLY"},
    {"schedule_type": "MONTHLY", "schedule_day": 0},
    {"schedule_type": "HOURLY", "schedule_interval_hours": 0},
    {"schedule_type": "HOURLY"},
    {"timezone": "Mars/Olympus"},
])
def test_validate_schedule_rejects(kw) -> None:
    with pytest.raises(ValueError):
        jobs.validate_schedule(_write(**kw))


def test_validate_schedule_accepts() -> None:
    jobs.validate_schedule(_write())
    jobs.validate_schedule(_write(schedule_type="NONE", schedule_time=None))
    jobs.validate_schedule(_write(schedule_type="WEEKLY", schedule_day=6))
    jobs.validate_schedule(_write(schedule_type="HOURLY", schedule_interval_hours=168, schedule_time=None))


# ---------------------------------------------------------------------------
# B — 대상
# ---------------------------------------------------------------------------

async def _more(sm, tables: dict[str, list[dict] | None]) -> dict[str, int]:
    """_seed 가 만든 카탈로그에 데이터셋을 더한다. 값은 규칙 목록(None 이면 규칙 없음). 이름은 ``schema.table``."""
    async with sm() as s:
        base = (await s.execute(select(cm.Dataset))).scalars().first()
        out = {}
        for qualified, rules in tables.items():
            d = cm.Dataset(urn=f"pg-1.{qualified}.dataset", name=qualified.split(".")[-1],
                           datasource_id=base.datasource_id, catalog_id=base.catalog_id,
                           qualified_name=f"pg-1.{qualified}")
            s.add(d)
            await s.flush()
            s.add(cm.DatasetSchema(dataset_id=d.id, field_path="use_yn", field_type="STRING",
                                   native_type="char(1)", ordinal=0))
            for i, r in enumerate(rules or []):
                s.add(QualityRule(dataset_id=d.id, rule_name=f"r{i}", check_type=r["check_type"],
                                  column_name=r.get("column"), expected_value=r.get("expected"),
                                  threshold=100, severity="WARNING", is_active="true"))
            out[qualified] = d.id
        await s.commit()
        return out


def _setup(tmp_path):
    """orders(필수값 규칙) + public.customers(필수값·코드) + audit.logs(규칙 없음) + audit.events(코드)."""
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    orders_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    ids = asyncio.run(_more(sm, {
        "public.customers": [{"check_type": "NOT_NULL", "column": "use_yn"},
                             {"check_type": "ACCEPTED_VALUES", "column": "use_yn", "expected": "Y,N"}],
        "audit.logs": None,
        "audit.events": [{"check_type": "ACCEPTED_VALUES", "column": "use_yn", "expected": "Y,N"}],
    }))
    catalog_id = _run(sm, lambda s: s.scalar(select(cm.Dataset.catalog_id).where(cm.Dataset.id == orders_id)))
    return sm, catalog_id, {"public.orders": orders_id, **ids}


def _t(**kw) -> QualityJobTarget:
    return QualityJobTarget(**kw)


def test_resolve_targets_and_preview(tmp_path) -> None:
    sm, cat, ids = _setup(tmp_path)
    resolve = lambda targets: _run(sm, lambda s: jobs.resolve_dataset_ids(s, targets))  # noqa: E731
    assert resolve([_t(type="catalog", catalog_id=cat)]) == sorted(ids.values())
    assert resolve([_t(type="schema", catalog_id=cat, schema_name="audit")]) == sorted(
        [ids["audit.logs"], ids["audit.events"]])
    assert resolve([_t(type="dataset", dataset_id=ids["public.orders"]),
                    _t(type="schema", catalog_id=cat, schema_name="audit"),
                    _t(type="dataset", dataset_id=ids["audit.logs"])]) == sorted(
        [ids["public.orders"], ids["audit.logs"], ids["audit.events"]]), "겹쳐도 한 번"
    assert resolve([_t(type="catalog", catalog_id=999), _t(type="dataset", dataset_id=999)]) == []

    # 원본에서 사라진 데이터셋은 뺀다
    async def remove(s):
        (await s.get(cm.Dataset, ids["audit.events"])).status = "removed"
    _run(sm, remove)
    assert ids["audit.events"] not in resolve([_t(type="catalog", catalog_id=cat)])

    whole = [_t(type="catalog", catalog_id=cat)]
    p = _run(sm, lambda s: jobs.preview(s, whole, []))
    assert (p.datasets_total, p.datasets_with_rules) == (3, 2)
    p = _run(sm, lambda s: jobs.preview(s, whole, ["YN"]))
    assert (p.datasets_total, p.datasets_with_rules) == (3, 1)

    schemas = _run(sm, lambda s: jobs.list_schemas(s, cat))
    assert [(o.schema_name, o.datasets) for o in schemas] == [("audit", 1), ("public", 2)]


# ---------------------------------------------------------------------------
# C — 실행, 집계·마감
# ---------------------------------------------------------------------------

def _create(sm, cat, **kw):
    req = _write(targets=[{"type": "catalog", "catalog_id": cat}], **kw)
    return _run(sm, lambda s: jobs.create_job(s, req, "alice"))


def _drain(sm, monkeypatch, respond) -> int:
    """대기열이 빌 때까지 워커를 돌린다. 처리한 실행 수를 돌려준다."""
    monkeypatch.setattr(worker_trino, "execute", _FakeTrino(respond))
    done = 0
    while asyncio.run(_work(sm)):
        done += 1
        assert done < 50
    return done


def test_launch_queues_runs_and_settles_to_succeeded(tmp_path, monkeypatch) -> None:
    sm, cat, ids = _setup(tmp_path)
    job = _create(sm, cat, indicators=["REQUIRED"])
    assert job.indicators == ["REQUIRED"] and job.next_run_at is not None and job.last_run is None
    assert [t.label for t in job.targets] == ["sales"]

    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert started.status == "RUNNING" and started.trigger_type == "MANUAL" and started.requested_by == "bob"
    assert (started.targets_total, started.runs_total, started.runs_active) == (4, 2, 2)
    assert (started.no_rules, started.skipped_total) == (2, 0), "필수값 규칙이 없는 데이터셋은 진단하지 않는다"

    queued = _run(sm, lambda s: runs.list_runs(s, job_run_id=started.id))
    assert {r.dataset_id for r in queued} == {ids["public.orders"], ids["public.customers"]}
    assert all(r.indicators == ["REQUIRED"] and r.trigger_type == "MANUAL" and r.job_name == "야간 진단"
               and r.job_id == job.id for r in queued)

    # 이전 실행이 끝나지 않았다 — 수동은 거절
    with pytest.raises(jobs.JobConflictError):
        _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))

    assert _drain(sm, monkeypatch, lambda _sql: [100, 3]) == 2
    done = _run(sm, lambda s: jobs.get_job_run(s, started.id))
    assert done.status == "SUCCEEDED" and done.finished_at is not None and done.message is None
    assert (done.runs_succeeded, done.runs_failed, done.runs_active) == (2, 0, 0)
    assert (done.checked_count, done.error_count, done.error_rate) == (200, 6, 3.0)
    assert _run(sm, lambda s: jobs.get_job(s, job.id)).last_run.id == started.id
    # 데이터셋 실행이 여럿이면 합계·평균만 — 한 실행을 가리키지 않는다
    assert (done.run_id, done.dataset_name) == (None, None)
    # 규칙 수는 그 실행이 진단한 규칙만 — 고르지 않은 지표의 규칙은 분모에 넣지 않는다(DEF-149)
    assert (done.rules_total, done.rules_evaluated, done.progress_done, done.progress_total) == (2, 2, 2, 2)
    assert done.score is not None


def test_launch_records_datasets_it_could_not_start(tmp_path, monkeypatch) -> None:
    sm, cat, ids = _setup(tmp_path)
    job = _create(sm, cat)
    # customers 는 이미 진단 중 — 배치는 그 데이터셋을 넣지 못한다
    _run(sm, lambda s: runs.create_run(s, ids["public.customers"], "alice"))
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert (started.targets_total, started.runs_total, started.no_rules, started.skipped_total) == (4, 2, 1, 1)
    assert [(i.dataset_id, i.name) for i in started.skipped] == [(ids["public.customers"], "customers")]
    assert "이미" in started.skipped[0].reason

    # 스캔: orders [건수, NULL], customers [건수, NULL, 코드 진단, 코드 오류], events [건수, 코드 진단, 코드 오류]
    assert _drain(sm, monkeypatch, lambda sql: [10, 0, 10, 1] if "IN (" in sql and "IS NULL" in sql
                  else [10, 10, 1] if "IN (" in sql else [10, 0]) == 3
    done = _run(sm, lambda s: jobs.get_job_run(s, started.id))
    assert done.status == "PARTIAL", "모두 성공해도 넣지 못한 데이터셋이 있으면 일부 실패"
    assert done.message == "실행하지 못한 데이터셋 1개" and (done.runs_succeeded, done.runs_failed) == (2, 0)


def test_launch_with_nothing_to_run_fails_immediately(tmp_path) -> None:
    sm, cat, ids = _setup(tmp_path)
    job = _create(sm, cat, indicators=["REFERENCE"])
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert started.status == "FAILED" and started.runs_total == 0 and started.finished_at is not None
    assert started.message == "선택한 진단 기준의 활성 규칙이 있는 데이터셋이 없습니다."

    async def empty(s):
        row = await s.get(QualityJob, job.id)
        row.targets_json = json.dumps([{"type": "catalog", "catalog_id": 999}])
    _run(sm, empty)
    assert _run(sm, lambda s: jobs.run_now(s, job.id, "bob")).message == "진단 대상 데이터셋이 없습니다."


def test_failed_runs_make_job_run_partial_or_failed(tmp_path, monkeypatch) -> None:
    sm, cat, ids = _setup(tmp_path)
    job = _create(sm, cat, indicators=["REQUIRED"])
    first = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    boom = worker_trino.TrinoQueryError("table not found")
    assert _drain(sm, monkeypatch, lambda sql: boom if "customers" in sql else [10, 0]) == 2
    done = _run(sm, lambda s: jobs.get_job_run(s, first.id))
    assert done.status == "PARTIAL" and (done.runs_succeeded, done.runs_failed) == (1, 1)
    assert done.message == "실패·일부 실패 1개" and (done.checked_count, done.error_count) == (10, 0)

    second = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert _drain(sm, monkeypatch, lambda _sql: boom) == 2
    done = _run(sm, lambda s: jobs.get_job_run(s, second.id))
    assert done.status == "FAILED" and (done.runs_succeeded, done.runs_failed) == (0, 2)
    assert done.checked_count is None


def test_cancel_job_run_cancels_its_runs(tmp_path) -> None:
    sm, cat, ids = _setup(tmp_path)
    job = _create(sm, cat)
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    cancelled = _run(sm, lambda s: jobs.cancel_job_run(s, started.id, "carol"))
    assert cancelled.status == "CANCELLED" and cancelled.cancel_requested_by == "carol"
    # 중지한 데이터셋 실행은 실패와 따로 센다(DEF-156)
    assert (cancelled.runs_succeeded, cancelled.runs_failed, cancelled.runs_cancelled) == (0, 0, 3)
    children = _run(sm, lambda s: runs.list_runs(s, job_run_id=started.id))
    assert {r.status for r in children} == {"CANCELLED"}
    with pytest.raises(jobs.JobConflictError):
        _run(sm, lambda s: jobs.cancel_job_run(s, started.id, "carol"))
    with pytest.raises(jobs.JobNotFoundError):
        _run(sm, lambda s: jobs.cancel_job_run(s, 999, "carol"))


def test_delete_job_keeps_runs_and_blocks_while_running(tmp_path) -> None:
    sm, cat, ids = _setup(tmp_path)
    job = _create(sm, cat)
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    with pytest.raises(jobs.JobConflictError):
        _run(sm, lambda s: jobs.delete_job(s, job.id, "alice"))
    _run(sm, lambda s: jobs.cancel_job_run(s, started.id, "alice"))
    _run(sm, lambda s: jobs.delete_job(s, job.id, "alice"))

    async def left(s):
        job_runs = (await s.execute(select(QualityJobRun))).scalars().all()
        children = (await s.execute(select(QualityRun))).scalars().all()
        return len(job_runs), [r.job_run_id for r in children]
    assert _run(sm, left) == (0, [None, None, None]), "작업 이력은 지워지고 데이터셋 실행은 연결만 끊긴다"


# ---------------------------------------------------------------------------
# D — 스케줄러
# ---------------------------------------------------------------------------

def _set_next(sm, job_id: int, when: datetime | None) -> None:
    async def go(s):
        (await s.get(QualityJob, job_id)).next_run_at = when
    _run(sm, go)


def _job_runs(sm, job_id: int) -> list[QualityJobRun]:
    async def go(s):
        return list((await s.execute(
            select(QualityJobRun).where(QualityJobRun.job_id == job_id).order_by(QualityJobRun.id))).scalars().all())
    return _run(sm, go)


def test_tick_runs_due_jobs_once_and_moves_next_run(tmp_path, monkeypatch) -> None:
    sm, cat, ids = _setup(tmp_path)
    due = _create(sm, cat, name="due", indicators=["REQUIRED"])
    later = _create(sm, cat, name="later")
    off = _create(sm, cat, name="off", enabled=False)
    manual = _create(sm, cat, name="manual", schedule_type="NONE", schedule_time=None)
    assert off.next_run_at is None and manual.next_run_at is None
    past = datetime.now(UTC) - timedelta(minutes=5)
    _set_next(sm, due.id, past)

    assert asyncio.run(jobs.tick(sm)) == 1
    rows = _job_runs(sm, due.id)
    assert [(r.trigger_type, r.status, r.runs_total, r.requested_by) for r in rows] == [("SCHEDULE", "RUNNING", 2, None)]
    assert all(not _job_runs(sm, j.id) for j in (later, off, manual))
    moved = runs._as_utc(_run(sm, lambda s: s.scalar(select(QualityJob.next_run_at).where(QualityJob.id == due.id))))
    assert moved > datetime.now(UTC), "다음 실행 시각이 앞으로 옮겨진다"
    assert asyncio.run(jobs.tick(sm)) == 0, "같은 차례를 두 번 실행하지 않는다"

    # 이전 실행이 끝나지 않은 채 다음 차례가 오면 건너뛴 이력만 남긴다
    _set_next(sm, due.id, past)
    assert asyncio.run(jobs.tick(sm)) == 1
    rows = _job_runs(sm, due.id)
    assert [r.status for r in rows] == ["RUNNING", "SKIPPED"] and "건너뛰었습니다" in rows[1].message
    assert len(_run(sm, lambda s: runs.list_runs(s, limit=100))) == 2, "건너뛴 차례는 실행을 넣지 않는다"

    # 워커가 끝내면 다음 tick 이 작업 실행을 마감한다
    assert _drain(sm, monkeypatch, lambda _sql: [10, 0]) == 2
    assert asyncio.run(jobs.tick(sm)) == 0
    assert [r.status for r in _job_runs(sm, due.id)] == ["SUCCEEDED", "SKIPPED"]


# ---------------------------------------------------------------------------
# E — API
# ---------------------------------------------------------------------------

def test_api_job_lifecycle(tmp_path, monkeypatch) -> None:
    sm, cat, ids = _setup(tmp_path)
    client = _api(sm)
    body = {
        "name": "주간 진단", "description": " 매주 월요일 ",
        "targets": [{"type": "schema", "catalog_id": cat, "schema_name": "public"},
                    {"type": "dataset", "dataset_id": ids["audit.events"]},
                    {"type": "schema", "catalog_id": cat, "schema_name": "public"}],
        "indicators": ["yn", "REQUIRED"], "schedule_type": "WEEKLY", "schedule_day": 0, "schedule_time": "06:00",
    }
    created = client.post("/api/v1/quality/jobs", json=body)
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["indicators"] == ["REQUIRED", "YN"] and job["description"] == "매주 월요일"
    assert [(t["type"], t["label"]) for t in job["targets"]] == [("schema", "sales.public"), ("dataset", "events")]
    assert job["created_by"] == "alice" and job["next_run_at"] and job["timezone"] == "Asia/Seoul"
    job_id = job["id"]

    assert client.post("/api/v1/quality/jobs", json={**body, "name": "주간 진단"}).status_code == 409
    for bad in ({"targets": []}, {"indicators": ["NOPE"]}, {"schedule_time": "6"}, {"name": "  "},
                {"targets": [{"type": "catalog", "catalog_id": 999}]},
                {"targets": [{"type": "schema", "catalog_id": cat}]},
                {"targets": [{"type": "table", "dataset_id": 1}]}):
        assert client.post("/api/v1/quality/jobs", json={**body, "name": "x", **bad}).status_code == 422, bad

    assert [j["id"] for j in client.get("/api/v1/quality/jobs").json()] == [job_id]
    assert client.get(f"/api/v1/quality/jobs/{job_id}").json()["name"] == "주간 진단"
    assert client.get("/api/v1/quality/jobs/999").status_code == 404
    assert client.get("/api/v1/quality/jobs/schemas", params={"catalog_id": cat}).json() == [
        {"schema_name": "audit", "datasets": 2}, {"schema_name": "public", "datasets": 2}]
    assert client.post("/api/v1/quality/jobs/preview", json={
        "targets": body["targets"], "indicators": ["YN"]}).json() == {"datasets_total": 3, "datasets_with_rules": 2}

    updated = client.put(f"/api/v1/quality/jobs/{job_id}", json={
        **body, "name": "수동 진단", "schedule_type": "NONE", "indicators": []})
    assert updated.status_code == 200 and updated.json()["next_run_at"] is None
    assert updated.json()["indicators"] == [] and updated.json()["schedule_time"] is None
    assert client.put("/api/v1/quality/jobs/999", json=body).status_code == 404

    started = client.post(f"/api/v1/quality/jobs/{job_id}/run")
    assert started.status_code == 202 and started.json()["runs_total"] == 3 and started.json()["job_name"] == "수동 진단"
    job_run_id = started.json()["id"]
    assert client.post(f"/api/v1/quality/jobs/{job_id}/run").status_code == 409
    assert client.delete(f"/api/v1/quality/jobs/{job_id}").status_code == 409
    assert len(client.get("/api/v1/quality/runs", params={"job_run_id": job_run_id}).json()) == 3

    monkeypatch.setattr(worker_trino, "execute", _FakeTrino(
        lambda sql: [10, 0, 10, 1] if "IN (" in sql and "IS NULL" in sql else [10, 10, 1] if "IN (" in sql else [10, 0]))
    while asyncio.run(_work(sm)):
        pass
    history = client.get("/api/v1/quality/job-runs", params={"job_id": job_id}).json()
    assert [(h["id"], h["status"], h["runs_succeeded"]) for h in history] == [(job_run_id, "SUCCEEDED", 3)]
    assert client.get(f"/api/v1/quality/job-runs/{job_run_id}").json()["error_count"] == 2
    assert client.get("/api/v1/quality/job-runs/999").status_code == 404
    assert client.post(f"/api/v1/quality/job-runs/{job_run_id}/cancel").status_code == 409
    assert client.get("/api/v1/quality/jobs").json()[0]["last_run"]["status"] == "SUCCEEDED"

    assert client.delete(f"/api/v1/quality/jobs/{job_id}").status_code == 204
    assert client.get("/api/v1/quality/jobs").json() == []
    assert client.get("/api/v1/quality/job-runs").json() == []


# ---------------------------------------------------------------------------
# F — 리포트
# ---------------------------------------------------------------------------

def _scan(sql: str):
    """customers(필수값+코드) / events(코드) / orders(필수값) 의 스캔 결과."""
    if "customers" in sql:
        return [100, 4, 96, 6]      # 건수, NULL 4, 코드 진단 96, 코드 오류 6
    if "events" in sql:
        return worker_trino.TrinoQueryError("permission denied")
    return [50, 0]                  # orders: 건수 50, NULL 0


def test_report_groups_by_area_indicator_and_dataset(tmp_path, monkeypatch) -> None:
    from app.quality import reports

    sm, cat, ids = _setup(tmp_path)
    job = _create(sm, cat)
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert _drain(sm, monkeypatch, _scan) == 3
    report = _run(sm, lambda s: reports.job_run_report(s, started.id))

    assert report.job_run.status == "PARTIAL" and report.job_run.no_rules == 1
    assert (report.job_run.checked_count, report.job_run.error_count) == (246, 10)
    assert (report.rules_total, report.failed_rules) == (3, 2)
    assert [(g.key, g.name, g.rules, g.failed_rules, g.checked_count, g.error_count, g.error_rate)
            for g in report.areas] == [
        ("COMPLETENESS", "완결성", 2, 1, 150, 4, round(4 / 150 * 100, 4)),
        ("VALIDITY", "유효성", 1, 1, 96, 6, 6.25),
    ]
    assert [(g.key, g.area_name, g.checked_count, g.error_count) for g in report.indicators] == [
        ("REQUIRED", "완결성", 150, 4), ("YN", "유효성", 96, 6)]
    # 오류율 높은 순, 건수 없는 실패는 맨 뒤
    assert [(d.dataset_name, d.status, d.rules, d.failed_rules, d.error_rate) for d in report.datasets] == [
        ("customers", "SUCCEEDED", 2, 2, round(10 / 196 * 100, 4)),
        ("orders", "SUCCEEDED", 1, 0, 0.0),
        ("events", "FAILED", 0, 0, None),
    ]
    assert report.datasets[2].error_message and report.datasets[0].error_message is None
    assert [(r.dataset_name, r.indicator, r.passed, r.checked_count, r.error_count) for r in report.rules] == [
        ("orders", "REQUIRED", True, 50, 0), ("customers", "REQUIRED", False, 100, 4),
        ("customers", "YN", False, 96, 6)]

    with pytest.raises(jobs.JobNotFoundError):
        _run(sm, lambda s: reports.job_run_report(s, 999))


def test_report_counts_only_results_of_that_job_run(tmp_path, monkeypatch) -> None:
    from app.quality import reports

    sm, cat, ids = _setup(tmp_path)
    job = _create(sm, cat, indicators=["REQUIRED"])
    first = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert _drain(sm, monkeypatch, lambda _sql: [10, 1]) == 2
    # 배치와 무관한 직접 실행 — 리포트에 섞이지 않는다
    _run(sm, lambda s: runs.create_run(s, ids["public.orders"], "alice"))
    assert _drain(sm, monkeypatch, lambda _sql: [10, 9]) == 1
    second = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert _drain(sm, monkeypatch, lambda _sql: [10, 3]) == 2

    r1 = _run(sm, lambda s: reports.job_run_report(s, first.id))
    r2 = _run(sm, lambda s: reports.job_run_report(s, second.id))
    assert [(g.key, g.checked_count, g.error_count) for g in r1.indicators] == [("REQUIRED", 20, 2)]
    assert [(g.key, g.checked_count, g.error_count) for g in r2.indicators] == [("REQUIRED", 20, 6)]
    # 직전 회차 — 첫 회차에는 없고, 둘째 회차는 첫 회차의 숫자를 준다(작업과 무관한 실행은 세지 않는다)
    assert r1.previous is None
    assert (r2.previous.job_run_id, r2.previous.checked_count, r2.previous.error_count, r2.previous.error_rate) == (
        first.id, 20, 2, 10.0)
    assert (r2.previous.rules_total, r2.previous.failed_rules) == (r1.rules_total, r1.failed_rules)

    # 추이 — 오래된 것부터, 건너뛴 차례·실행 중은 뺀다
    async def add_skipped(s):
        s.add(QualityJobRun(job_id=job.id, trigger_type="SCHEDULE", status="SKIPPED"))
    _run(sm, add_skipped)
    trend = _run(sm, lambda s: reports.job_trend(s, job.id))
    assert trend.job_name == "야간 진단"
    assert [(p.job_run_id, p.status, p.error_rate, p.indicators) for p in trend.points] == [
        (first.id, "SUCCEEDED", 10.0, {"REQUIRED": 10.0}), (second.id, "SUCCEEDED", 30.0, {"REQUIRED": 30.0})]
    assert [p.job_run_id for p in _run(sm, lambda s: reports.job_trend(s, job.id, 1)).points] == [second.id]
    with pytest.raises(jobs.JobNotFoundError):
        _run(sm, lambda s: reports.job_trend(s, 999))


def test_api_report_trend_and_xlsx(tmp_path, monkeypatch) -> None:
    import io

    from openpyxl import load_workbook

    sm, cat, ids = _setup(tmp_path)
    client = _api(sm)
    job = _create(sm, cat)
    # customers 는 이미 진단 중 — 시작하지 못한 데이터셋으로 남는다
    _run(sm, lambda s: runs.create_run(s, ids["public.customers"], "alice"))
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert _drain(sm, monkeypatch, _scan) == 3

    body = client.get(f"/api/v1/quality/job-runs/{started.id}/report").json()
    assert body["job_run"]["skipped_total"] == 1 and [d["dataset_name"] for d in body["datasets"]] == ["orders", "events"]
    assert client.get("/api/v1/quality/job-runs/999/report").status_code == 404
    trend = client.get(f"/api/v1/quality/jobs/{job.id}/trend").json()
    assert [p["job_run_id"] for p in trend["points"]] == [started.id]
    assert client.get("/api/v1/quality/jobs/999/trend").status_code == 404

    res = client.get(f"/api/v1/quality/job-runs/{started.id}/report.xlsx")
    assert res.status_code == 200 and "spreadsheetml" in res.headers["content-type"]
    assert f"quality-report-{started.id}.xlsx" in res.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(res.content))
    assert wb.sheetnames == ["요약", "진단 기준별", "AI-Ready 지표별", "데이터셋별", "규칙별"]
    summary = {row[0]: row[1] for row in wb["요약"].iter_rows(values_only=True) if row[0]}
    assert summary["품질 진단 작업"] == "야간 진단" and summary["상태"] == "일부 실패"
    assert summary["진단한 데이터셋"] == 2 and summary["시작하지 못한 데이터셋"] == 1
    assert summary["customers"].startswith("이 데이터셋의 진단이 이미"), "시작하지 못한 데이터셋과 사유"
    assert [row[:2] for row in wb["진단 기준별"].iter_rows(values_only=True)] == [("영역", "진단 기준"), ("완결성", "필수값")]
    # 같은 결과를 AI-Ready 품질 지표로 — 지표 합계 행, 그 뒤에 진단 기준 행
    assert [row[:3] for row in wb["AI-Ready 지표별"].iter_rows(values_only=True)] == [
        ("AI-Ready 지표", "세부 지표", "진단 기준"), ("완전성", "(합계)", None), ("완전성", "필수 속성 충족도", "필수값")]
    assert [g["name"] for g in body["ai_ready_areas"]] == ["완전성"]
    assert body["ai_ready_indicators"][0]["ai_ready_detail"] == "필수 속성 충족도"
    datasets = list(wb["데이터셋별"].iter_rows(values_only=True))
    assert [(r[0], r[3]) for r in datasets[1:]] == [("orders", "완료"), ("events", "실패")]
    rules = list(wb["규칙별"].iter_rows(values_only=True))
    assert len(rules) == 2 and rules[1][0] == "orders" and rules[1][7] == "통과"
    assert client.get("/api/v1/quality/job-runs/999/report.xlsx").status_code == 404


def test_api_report_pdf(tmp_path, monkeypatch) -> None:
    from app.quality import report_pdf, reports

    sm, cat, ids = _setup(tmp_path)
    client = _api(sm)
    job = _create(sm, cat)
    _run(sm, lambda s: runs.create_run(s, ids["public.customers"], "alice"))  # 시작하지 못한 데이터셋
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert _drain(sm, monkeypatch, _scan) == 3

    res = client.get(f"/api/v1/quality/job-runs/{started.id}/report.pdf")
    assert res.status_code == 200 and res.headers["content-type"] == "application/pdf"
    assert f"quality-report-{started.id}.pdf" in res.headers["content-disposition"]
    assert res.content.startswith(b"%PDF-") and res.content.rstrip().endswith(b"%%EOF")
    assert b"NotoSansKR" in res.content, "한글 글꼴을 문서에 넣는다"
    assert client.get("/api/v1/quality/job-runs/999/report.pdf").status_code == 404

    # 규칙이 많으면 실패한 규칙부터 RULE_ROWS 건만 싣는다 — 줄 수만큼 쪽이 늘지 않는다
    report = _run(sm, lambda s: reports.job_run_report(s, started.id))
    passed = report.rules[0].model_copy(update={"passed": True, "rule_name": "통과 <b>&</b> 규칙"})
    failed = passed.model_copy(update={"passed": False, "detail": "값 " * 200})
    monkeypatch.setattr(report_pdf, "RULE_ROWS", 30)
    small = report_pdf.report_pdf(report.model_copy(update={"rules": [passed] * 30}))
    capped = report_pdf.report_pdf(report.model_copy(update={"rules": [passed] * 500 + [failed]}))
    pages = lambda pdf: pdf.count(b"/Type /Page\n") or pdf.count(b"/Type /Page ")  # noqa: E731
    assert pages(small) >= 1 and pages(capped) <= pages(small) + 2

    # 평가한 규칙·데이터셋이 없는 실행도 문서가 나온다
    empty = report.model_copy(update={"areas": [], "indicators": [], "datasets": [], "rules": []})
    assert report_pdf.report_pdf(empty).startswith(b"%PDF-")


def test_pdf_text_has_glyphs_for_quality_symbols() -> None:
    """PDF 글꼴(Noto Sans KR)에 없는 기호는 ASCII 로 바꿔 찍는다 — "기대 ≥ 0.0" 이 빈 네모 · "기대 0.0" 이 되지 않게(DEF-134).
    품질 코드(진단 결과 문구 · 기준)에 나오는 한글 밖 기호가 모두 글꼴에 있거나 바꿀 목록에 있는지도 본다."""
    import ast
    import re
    from pathlib import Path

    from reportlab.pdfbase.ttfonts import TTFont

    from app.quality import report_pdf

    font = TTFont("t", str(report_pdf._FONT_DIR / "NotoSansKR-Regular.ttf")).face.charToGlyph
    assert report_pdf._pdf_text("기대 ≥ 0.0, 값 ≤ 9 · a → b") == "기대 >= 0.0, 값 <= 9 · a -> b"
    assert report_pdf._pdf_text(None) == ""
    for ch in report_pdf._pdf_text("".join(report_pdf._NO_GLYPH_CHARS)):
        assert ord(ch) in font, ch
    # 주석 · 독스트링은 찍히지 않으므로 문자열 상수만 본다
    used: set[str] = set()
    for path in Path(report_pdf.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                used.update(re.findall(r"[^\x00-\x7f\uac00-\ud7a3\u3131-\u318e]", n.value))
    missing = sorted(c for c in used if ord(c) not in font and c not in report_pdf._NO_GLYPH_CHARS)
    assert not missing, f"PDF 글꼴에 없는 기호: {missing}"


def test_xlsx_times_use_server_local_time(monkeypatch) -> None:
    import time

    from app.quality import reports

    monkeypatch.setenv("TZ", "Asia/Seoul")
    time.tzset()
    try:
        assert reports._local(datetime(2026, 11, 14, 17, 30, tzinfo=UTC)) == "2026-11-15 02:30:00"
        assert reports._local(datetime(2026, 11, 14, 17, 30)) == "2026-11-15 02:30:00", "시간대 없는 값은 UTC 로 본다"
        assert reports._local(None) == "" and reports._zone() == "KST"
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_domain_decides_which_indicator_a_rule_counts_for(tmp_path, monkeypatch) -> None:
    """같은 유형(허용 값 목록)이라도 도메인이 다르면 다른 지표 — 미리보기·실행·리포트가 모두 그렇게 센다."""
    from app.quality import reports

    sm, cat, ids = _setup(tmp_path)
    whole = [_t(type="catalog", catalog_id=cat)]
    count = lambda keys: _run(sm, lambda s: jobs.preview(s, whole, keys)).datasets_with_rules  # noqa: E731
    assert (count(["YN"]), count(["CODE"])) == (2, 0), "use_yn 은 이름으로 여부"

    async def _mark_code(s):
        rule = (await s.execute(select(QualityRule).where(
            QualityRule.dataset_id == ids["public.customers"], QualityRule.check_type == "ACCEPTED_VALUES"))).scalar_one()
        rule.domain = "CODE"
        await s.commit()
    _run(sm, _mark_code)
    assert (count(["YN"]), count(["CODE"])) == (1, 1), "적어 둔 도메인이 이름보다 먼저"

    # 코드만 고른 배치 — customers 의 코드 규칙만 쿼리에 들어간다
    job = _create(sm, cat, indicators=["CODE"])
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert started.runs_total == 1
    assert _drain(sm, monkeypatch, lambda sql: [100, 96, 6]) == 1
    report = _run(sm, lambda s: reports.job_run_report(s, started.id))
    assert [(g.key, g.name, g.checked_count, g.error_count) for g in report.indicators] == [("CODE", "코드", 96, 6)]
    trend = _run(sm, lambda s: reports.job_trend(s, job.id))
    assert trend.points[-1].indicators == {"CODE": 6.25}


def test_rules_overview_lists_what_each_indicator_checks(tmp_path) -> None:
    """등록 화면 — 대상 데이터셋이 지표마다 가진 규칙(컬럼·기준). 규칙이 있는 지표만, 정해진 순서로."""
    sm, cat, ids = _setup(tmp_path)

    async def _add(s):
        s.add(QualityRule(dataset_id=ids["public.customers"], rule_name="가입일", check_type="DATE_FORMAT",
                          column_name="join_ymd", expected_value="YYYY/MM/DD", threshold=100, severity="WARNING",
                          is_active="true"))
        s.add(QualityRule(dataset_id=ids["public.customers"], rule_name="꺼 둔 규칙", check_type="DATE_FORMAT",
                          column_name="x", expected_value="YYYY", threshold=100, severity="WARNING", is_active="false"))
        s.add(QualityRule(dataset_id=ids["public.customers"], rule_name="배치 전용", check_type="CUSTOM_SQL",
                          expected_value="select 0", threshold=100, severity="WARNING", is_active="true"))
        await s.commit()
    _run(sm, _add)

    whole = [_t(type="catalog", catalog_id=cat)]
    overview = _run(sm, lambda s: jobs.rules_overview(s, whole))
    assert [(g.indicator, g.rules_total, g.datasets, g.truncated) for g in overview] == [
        ("REQUIRED", 2, 2, False), ("DATE", 1, 1, False), ("YN", 2, 2, False)]
    date = overview[1].rules[0]
    assert (date.dataset_name, date.column_name, date.check_type, date.expected_value) == (
        "customers", "join_ymd", "DATE_FORMAT", "YYYY/MM/DD")
    # 대상을 좁히면 그 데이터셋의 규칙만
    audit = _run(sm, lambda s: jobs.rules_overview(s, [_t(type="schema", catalog_id=cat, schema_name="audit")]))
    assert [(g.indicator, g.rules_total) for g in audit] == [("YN", 1)]
    assert _run(sm, lambda s: jobs.rules_overview(s, [])) == []

    # 많으면 앞의 일부만 싣고 개수는 그대로
    jobs_limit = jobs.RULES_PER_INDICATOR
    try:
        jobs.RULES_PER_INDICATOR = 1
        cut = _run(sm, lambda s: jobs.rules_overview(s, whole))
        assert [(g.indicator, g.rules_total, len(g.rules), g.truncated) for g in cut] == [
            ("REQUIRED", 2, 1, True), ("DATE", 1, 1, False), ("YN", 2, 1, True)]
    finally:
        jobs.RULES_PER_INDICATOR = jobs_limit

    client = _api(sm)
    body = client.post("/api/v1/quality/jobs/rules", json={"targets": [{"type": "catalog", "catalog_id": cat}]})
    assert body.status_code == 200 and [g["indicator"] for g in body.json()] == ["REQUIRED", "DATE", "YN"]


def test_job_measures_only_rules_of_chosen_definitions(tmp_path, monkeypatch) -> None:
    """품질 진단 작업이 미리 정의한 품질 지표를 고르면 그 지표가 할당된 규칙만 진단한다."""
    sm, cat, ids = _setup(tmp_path)

    async def _define(s):
        d = QualityDefinition(name="여부 Y/N", category="YN", check_type="ACCEPTED_VALUES", expected_value="Y,N",
                              threshold=100, severity="WARNING")
        unused = QualityDefinition(name="안 쓰는 지표", category="REQUIRED", check_type="NOT_NULL",
                                   threshold=100, severity="WARNING")
        s.add_all([d, unused])
        await s.flush()
        # customers 의 여부 규칙만 지표에 할당한다 — events 의 같은 규칙은 직접 만든 규칙으로 남는다
        rule = (await s.execute(select(QualityRule).where(
            QualityRule.dataset_id == ids["public.customers"], QualityRule.check_type == "ACCEPTED_VALUES"))).scalar_one()
        rule.definition_id = d.id
        await s.commit()
        return d.id, unused.id
    yn_id, unused_id = _run(sm, _define)

    whole = [_t(type="catalog", catalog_id=cat)]
    p = _run(sm, lambda s: jobs.preview(s, whole, [], [yn_id]))
    assert (p.datasets_total, p.datasets_with_rules) == (4, 1)

    grouped = _run(sm, lambda s: jobs.rules_overview(s, whole, "definition"))
    assert [(g.definition_id, g.rules_total, g.datasets) for g in grouped] == [(yn_id, 1, 1), (None, 3, 3)]

    with pytest.raises(ValueError, match="없는 품질 지표"):
        _create(sm, cat, name="없는 지표", definition_ids=[9999])
    job = _create(sm, cat, definition_ids=[yn_id])
    assert job.definition_ids == [yn_id] and job.definitions == ["여부 Y/N"] and job.indicators == []

    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert (started.runs_total, started.no_rules) == (1, 3) and started.definitions == ["여부 Y/N"]
    queued = _run(sm, lambda s: runs.list_runs(s, job_run_id=started.id))
    assert [(r.dataset_id, r.definition_ids, r.definitions) for r in queued] == [
        (ids["public.customers"], [yn_id], ["여부 Y/N"])]

    assert _drain(sm, monkeypatch, lambda _sql: [100, 100, 4]) == 1
    run = _run(sm, lambda s: runs.get_run(s, queued[0].id))
    # 고르지 않은 규칙은 분모에도 넣지 않는다(DEF-149)
    assert run.status == "SUCCEEDED" and (run.rules_total, run.rules_evaluated) == (1, 1)
    assert (run.checked_count, run.error_count) == (100, 4)
    # 작업 이력 한 줄 — 데이터셋 실행이 하나뿐이면 그 실행·데이터셋·규칙 수·품질 점수를 함께 준다
    done = _run(sm, lambda s: jobs.get_job_run(s, started.id))
    assert (done.run_id, done.dataset_id, done.dataset_name) == (run.id, ids["public.customers"], "customers")
    assert (done.rules_total, done.rules_evaluated, done.score) == (1, 1, run.score)
    # 품질 점수는 이 실행에서 평가한 규칙만 센다 — 고르지 않은 규칙의 지난 결과는 넣지 않는다 (DEF-088)
    assert run.score == 0.0, "평가한 규칙 1개가 실패"
    assert done.engine_table == run.engine_table and done.progress_total == done.progress_done == 1
    # 다시 실행도 같은 지표로
    again = _run(sm, lambda s: runs.retry_run(s, run.id, "bob"))
    assert again.definition_ids == [yn_id]

    # 지표가 할당된 규칙이 없으면 바로 끝난다
    empty = _create(sm, cat, name="빈 작업", definition_ids=[unused_id])
    nothing = _run(sm, lambda s: jobs.run_now(s, empty.id, "bob"))
    assert nothing.status == "FAILED" and "품질 지표가 할당된" in nothing.message

    # 지표를 지워도 이력에는 무엇을 골랐는지 남는다
    async def _drop(s):
        await s.delete(await s.get(QualityDefinition, yn_id))
        await s.commit()
    _run(sm, _drop)
    assert _run(sm, lambda s: jobs.get_job_run(s, started.id)).definitions == [f"삭제된 지표 #{yn_id}"]



# ---------------------------------------------------------------------------
# F — 프로파일도 대기열에 (TODO-095): include_profile
# ---------------------------------------------------------------------------

def _profile_or_scan(sql: str, *, fail_profile: bool = False):
    """프로파일 쿼리(고유값 수 포함) · 최빈값 쿼리 · 진단 스캔에 맞는 한 행."""
    if "GROUP BY" in sql:
        return ["Y", 6]
    if "count(DISTINCT" in sql:
        # 모든 컬럼의 통계가 들어갈 만큼 — [행 수, 컬럼마다 비-NULL · 고유 · 최소 · 최대(· 평균) …]
        return worker_trino.TrinoQueryError("boom", error_type="INTERNAL_ERROR") if fail_profile else [10] + [2] * 40
    return [10, 0]


def test_job_with_profile_queues_profiles_for_every_target(tmp_path, monkeypatch) -> None:
    sm, cat, ids = _setup(tmp_path)
    job = _create(sm, cat, indicators=["REQUIRED"], include_profile=True)
    assert job.include_profile is True
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    # 진단은 필수값 규칙이 있는 2개, 프로파일은 규칙이 없는 데이터셋까지 4개
    assert (started.runs_total, started.profiles_total, started.no_rules) == (2, 4, 2)
    queued = _run(sm, lambda s: runs.list_runs(s, job_run_id=started.id, limit=50))
    assert sorted(r.kind for r in queued) == ["CHECK", "CHECK", "PROFILE", "PROFILE", "PROFILE", "PROFILE"]

    assert _drain(sm, monkeypatch, _profile_or_scan) == 6
    done = _run(sm, lambda s: jobs.get_job_run(s, started.id))
    assert done.status == "SUCCEEDED" and done.message is None
    assert (done.runs_succeeded, done.runs_failed, done.profiles_succeeded, done.profiles_failed) == (2, 0, 4, 0)
    assert (done.checked_count, done.error_count) == (20, 0)
    # 리포트에는 진단 실행만
    from app.quality import reports
    report = _run(sm, lambda s: reports.job_run_report(s, started.id))
    assert len(report.datasets) == 2


def test_job_profile_failure_makes_job_run_partial(tmp_path, monkeypatch) -> None:
    sm, cat, ids = _setup(tmp_path)
    job = _create(sm, cat, indicators=["REQUIRED"], include_profile=True)
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert _drain(sm, monkeypatch, lambda sql: _profile_or_scan(sql, fail_profile=True)) == 6
    done = _run(sm, lambda s: jobs.get_job_run(s, started.id))
    assert done.status == "PARTIAL" and done.message == "프로파일 실패 4개"
    assert (done.runs_succeeded, done.runs_failed, done.profiles_failed) == (2, 0, 4)


def test_job_of_datasets_without_rules_still_profiles(tmp_path, monkeypatch) -> None:
    sm, cat, ids = _setup(tmp_path)
    req = _write(targets=[{"type": "dataset", "dataset_id": ids["audit.logs"]}], include_profile=True)
    job = _run(sm, lambda s: jobs.create_job(s, req, "alice"))
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert started.status == "RUNNING" and (started.runs_total, started.profiles_total) == (0, 1)
    assert _drain(sm, monkeypatch, _profile_or_scan) == 1
    done = _run(sm, lambda s: jobs.get_job_run(s, started.id))
    assert done.status == "SUCCEEDED" and (done.runs_succeeded, done.profiles_succeeded) == (0, 1)


def test_check_and_profile_of_a_dataset_can_wait_together(tmp_path) -> None:
    sm, cat, ids = _setup(tmp_path)
    orders = ids["public.orders"]
    _run(sm, lambda s: runs.create_run(s, orders, "alice", kind=runs.PROFILE_KIND))
    assert _run(sm, lambda s: runs.create_run(s, orders, "alice")).kind == "CHECK"
    with pytest.raises(runs.RunConflictError, match="프로파일"):
        _run(sm, lambda s: runs.create_run(s, orders, "alice", kind=runs.PROFILE_KIND))
    with pytest.raises(runs.RunConflictError, match="진단"):
        _run(sm, lambda s: runs.create_run(s, orders, "alice"))
