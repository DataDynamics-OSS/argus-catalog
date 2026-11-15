# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 메뉴 리뷰에서 나온 결함의 회귀 테스트 (DEF-090 ~ DEF-099).

- DEF-090 계획 단계의 예외 하나가 대기열을 막지 않는다
- DEF-091 메뉴 API 는 명시 허용형, 데이터셋 품질 탭 API 는 그대로
- DEF-092 워커 API 는 API 키만, 워커 ID 는 처음 쓴 키의 것
- DEF-093 같은 데이터셋의 다른 진단 중에는 미룸/거절, 대기 만료, 실행 0개는 실패, 취소 뒤 완료는 완료
- DEF-094 진단하지 못한 규칙은 결과를 남기지 않는다(규칙이 잘못된 오류만 실패로 저장)
- DEF-096 N시간 주기는 예정 시각 기준, 주기를 안 고치면 다음 실행을 다시 세지 않는다
- DEF-098 엑셀의 글자는 수식이 되지 않는다
- DEF-099 리포트는 진단 당시의 규칙 분류로 집계한다
"""

from __future__ import annotations

import asyncio
import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.auth import TokenUser
from app.quality import execution, jobs, reports, runs
from app.quality.models import (
    QualityJob,
    QualityJobRun,
    QualityResult,
    QualityRule,
    QualityRun,
    QualityWorker,
)
from tests.test_quality_jobs import _drain, _job, _job_runs, _set_next, _setup, _write
from tests.test_quality_trino_runs import (  # noqa: F401 — _no_alerts 는 autouse fixture
    _api,
    _app,
    _FakeTrino,
    _make_sessionmaker,
    _no_alerts,
    _results,
    _run,
    _seed,
    _start,
    worker_trino,
)

UTC = timezone.utc


def _single(sm, dataset_id: int, **kw):
    """데이터셋 하나짜리 작업."""
    req = _write(targets=[{"type": "dataset", "dataset_id": dataset_id}], **kw)
    return _run(sm, lambda s: jobs.create_job(s, req, "alice"))


# ---------------------------------------------------------------------------
# DEF-090
# ---------------------------------------------------------------------------

def test_claim_fails_only_the_run_it_cannot_plan(tmp_path, monkeypatch) -> None:
    sm, _cat, ids = _setup(tmp_path)
    first = _run(sm, lambda s: runs.create_run(s, ids["public.orders"], "alice"))
    second = _run(sm, lambda s: runs.create_run(s, ids["public.customers"], "alice"))
    real = execution._snapshot

    async def poisoned(session, run):
        if run.id == first.id:
            raise RuntimeError("복호화 실패")
        return await real(session, run)

    monkeypatch.setattr(execution, "_snapshot", poisoned)
    job = _run(sm, lambda s: execution.claim(s, "w1", None, None))
    assert job is not None and job["run_id"] == second.id, "뒤의 실행은 그대로 넘겨준다"
    bad = _run(sm, lambda s: runs.get_run(s, first.id))
    assert bad.status == "FAILED" and "실행 계획을 만들지 못했습니다" in bad.error_message
    assert "RuntimeError" in bad.error_message


def test_reference_id_that_is_not_a_plain_number_is_reported_not_raised(tmp_path) -> None:
    sm, _cat, _ids = _setup(tmp_path)
    for expected in ("²#id", "99999999999#id"):
        out = _run(sm, lambda s, e=expected: execution._resolve_reference(s, e, 1))
        assert isinstance(out, str) and "찾을 수 없습니다" in out


# ---------------------------------------------------------------------------
# DEF-091 · DEF-092
# ---------------------------------------------------------------------------

def _user(**kw) -> TokenUser:
    base = {"sub": "u9", "username": "bob", "email": "", "first_name": "", "last_name": "", "roles": ["argus-user"]}
    return TokenUser(**{**base, **kw})


def test_menu_api_needs_explicit_grant_but_dataset_tab_api_stays_open(tmp_path) -> None:
    sm, _cat, ids = _setup(tmp_path)
    ds = ids["public.orders"]
    plain = TestClient(_app(sm, _user()))
    base = "/api/v1/quality"
    # 관리 > 데이터 품질 메뉴의 API — 권한 설정이 없으면 관리자만
    for path in ("/jobs", "/job-runs", "/runs", "/runs/status", "/workers", "/job-runs/1/report",
                 "/job-runs/1/report.xlsx", "/job-runs/1/report.pdf", "/jobs/1/trend", "/runs/1/queries"):
        assert plain.get(base + path).status_code == 403, path
    body = _write(targets=[{"type": "dataset", "dataset_id": ds}]).model_dump()
    assert plain.post(f"{base}/jobs", json=body).status_code == 403
    assert plain.post(f"{base}/jobs/1/run").status_code == 403
    assert plain.delete(f"{base}/jobs/1").status_code == 403
    assert plain.post(f"{base}/job-runs/1/cancel").status_code == 403
    # 데이터셋 품질 탭이 쓰는 API 는 지금까지처럼 열려 있다
    assert plain.get(f"{base}/datasets/{ds}/runs").status_code == 200
    assert plain.get(f"{base}/datasets/{ds}/engine").status_code == 200
    assert plain.get(f"{base}/definitions").status_code == 200
    # 관리자는 그대로
    assert _api(sm).get(f"{base}/jobs").status_code == 200


def test_worker_api_takes_api_keys_only_and_binds_worker_id(tmp_path) -> None:
    sm, _cat, ids = _setup(tmp_path)
    base = "/api/v1/quality/worker"
    run = _run(sm, lambda s: runs.create_run(s, ids["public.orders"], "alice"))

    # 관리자라도 브라우저 세션(JWT)으로는 Trino 접속 정보를 받지 못한다 — 대기 실행도 그대로다
    browser = TestClient(_app(sm, _user(roles=["argus-admin"])))
    denied = browser.post(f"{base}/claim", json={"worker_id": "x"})
    assert denied.status_code == 403 and "API 키" in denied.json()["detail"]
    assert _run(sm, lambda s: runs.get_run(s, run.id)).status == "QUEUED"
    # 관리자 역할이 아닌 API 키도 안 된다
    assert TestClient(_app(sm, _user(api_key_id=7, sub="apikey:7"))).post(
        f"{base}/claim", json={"worker_id": "x"}).status_code == 403

    key1 = _api(sm)  # apikey:1
    job = key1.post(f"{base}/claim", json={"worker_id": "w1"}).json()["job"]
    assert job["run_id"] == run.id
    assert _run(sm, lambda s: s.scalar(select(QualityWorker.owner).where(QualityWorker.worker_id == "w1"))) == "apikey:1"

    # 다른 API 키가 같은 워커 ID 를 쓰면 거절 — 실행을 끝내거나 가로채지 못한다
    key2 = TestClient(_app(sm, _user(roles=["argus-admin"], api_key_id=2, sub="apikey:2")))
    assert key2.post(f"{base}/heartbeat", json={"worker_id": "w1", "run_id": run.id}).status_code == 403
    assert key2.post(f"{base}/runs/{run.id}/finish", json={"worker_id": "w1", "outcome": "FAILED"}).status_code == 403
    assert key2.delete(f"{base}/w1").status_code == 403
    assert _run(sm, lambda s: runs.get_run(s, run.id)).status == "RUNNING"
    assert key1.post(f"{base}/heartbeat", json={"worker_id": "w1", "run_id": run.id}).json()["active"] is True

    # 오래 소식이 없는 워커의 ID 는 넘겨준다 — API 키를 새로 발급해 같은 ID 로 다시 띄울 수 있어야 한다
    async def silence(s):
        (await s.get(QualityWorker, "w1")).heartbeat_at = runs._now() - timedelta(seconds=runs.STALE_SECONDS + 5)
    _run(sm, silence)
    assert key2.post(f"{base}/heartbeat", json={"worker_id": "w1"}).status_code == 200
    assert key1.post(f"{base}/heartbeat", json={"worker_id": "w1"}).status_code == 403


# ---------------------------------------------------------------------------
# DEF-093
# ---------------------------------------------------------------------------

def test_job_waits_while_another_diagnosis_uses_its_dataset(tmp_path, monkeypatch) -> None:
    sm, _cat, ids = _setup(tmp_path)
    ds = ids["public.customers"]
    job = _single(sm, ds, name="b")
    planned = datetime.now(UTC) - timedelta(minutes=5)
    _set_next(sm, job.id, planned)
    # 품질 탭에서 수동 진단이 대기 중이다
    _run(sm, lambda s: runs.create_run(s, ds, "alice"))

    # 예약 실행은 실패 이력을 남기지 않고 미룬다 — 예정 시각은 그대로
    assert asyncio.run(jobs.tick(sm)) == 0
    assert _job_runs(sm, job.id) == []
    kept = runs._as_utc(_run(sm, lambda s: s.scalar(select(QualityJob.next_run_at).where(QualityJob.id == job.id))))
    assert abs((kept - planned).total_seconds()) < 1
    # 수동 실행은 바로 알린다
    with pytest.raises(jobs.JobConflictError, match="다른 진단"):
        _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))

    # 그 진단이 끝나면 다음 tick 에 실행한다
    assert _drain(sm, monkeypatch, lambda _sql: [10, 0, 10, 0]) == 1
    assert asyncio.run(jobs.tick(sm)) == 1
    assert [r.status for r in _job_runs(sm, job.id)] == ["RUNNING"]


def test_queued_run_expires_only_when_no_worker_is_alive(tmp_path) -> None:
    sm, _cat, ids = _setup(tmp_path)
    run = _run(sm, lambda s: runs.create_run(s, ids["public.orders"], "alice"))

    async def age(s):
        (await s.get(QualityRun, run.id)).queued_at = runs._now() - timedelta(seconds=runs.QUEUE_TIMEOUT_SECONDS + 60)
    _run(sm, age)
    # 워커가 살아 있으면 대기가 길어도 기다린다
    _run(sm, lambda s: runs.worker_heartbeat(s, "w1", "h", None))
    assert _run(sm, lambda s: runs.get_run(s, run.id)).status == "QUEUED"
    # 워커가 없으면 실패로 끝낸다 — 작업 실행이 끝없이 "실행 중" 으로 남지 않는다
    _run(sm, lambda s: s.execute(delete(QualityWorker)))
    seen = _run(sm, lambda s: runs.get_run(s, run.id))
    assert seen.status == "FAILED" and "품질 워커가 없어" in seen.error_message
    # 같은 데이터셋의 새 진단을 막지 않는다
    assert _run(sm, lambda s: runs.create_run(s, ids["public.orders"], "alice")).status == "QUEUED"


def test_job_run_without_runs_fails_and_job_without_target_is_disabled(tmp_path) -> None:
    sm, _cat, ids = _setup(tmp_path)
    ds = ids["public.orders"]
    job = _single(sm, ds, name="one")
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    assert started.status == "RUNNING"
    # 실행 중에 데이터셋이 지워져 진단 실행이 함께 사라졌다
    _run(sm, lambda s: s.execute(delete(QualityRun).where(QualityRun.job_run_id == started.id)))
    done = _run(sm, lambda s: jobs.get_job_run(s, started.id))
    assert done.status == "FAILED" and "남아 있지 않습니다" in done.message

    # 대상이 사라진 예약 작업은 한 번 실패를 남기고 꺼진다 — 주기마다 실패가 쌓이지 않는다
    async def remove(s):
        from app.catalog.models import Dataset
        (await s.get(Dataset, ds)).status = "removed"
    _run(sm, remove)
    _set_next(sm, job.id, datetime.now(UTC) - timedelta(minutes=1))
    assert asyncio.run(jobs.tick(sm)) == 1
    last = _job_runs(sm, job.id)[-1]
    assert last.status == "FAILED" and "사용 안 함" in last.message
    row = _run(sm, lambda s: s.get(QualityJob, job.id))
    assert row.enabled is False and row.next_run_at is None
    assert asyncio.run(jobs.tick(sm)) == 0


def test_cancel_request_after_everything_finished_keeps_real_status(tmp_path, monkeypatch) -> None:
    sm, _cat, ids = _setup(tmp_path)
    job = _single(sm, ids["public.orders"], name="one")
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))

    # 취소 요청이 워커의 완료보다 늦게 반영된 경우 — 진단 실행은 이미 성공으로 끝났다
    assert _drain(sm, monkeypatch, lambda _sql: [10, 0]) == 1

    async def late_cancel(s):
        (await s.get(QualityJobRun, started.id)).cancel_requested_by = "bob"
    _run(sm, late_cancel)
    done = _run(sm, lambda s: jobs.get_job_run(s, started.id))
    assert done.status == "SUCCEEDED", "결과가 반영된 실행을 중지됨으로 적지 않는다"


# ---------------------------------------------------------------------------
# DEF-094
# ---------------------------------------------------------------------------

def test_query_that_could_not_run_leaves_no_failed_result(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r_nn, r_re) = asyncio.run(_seed(sm, [
        {"check_type": "NOT_NULL", "column": "use_yn"},
        {"check_type": "REGEX", "column": "reg_ymd", "expected": "^[0-9]{8}$"},
    ]))

    def respond(sql: str):
        if "regexp_like" in sql:  # 시간 초과 — 규칙의 잘못이 아니다
            return worker_trino.TrinoQueryError(
                "Query exceeded the maximum execution time limit", query_id="slow",
                error_name="EXCEEDED_TIME_LIMIT", error_type="INSUFFICIENT_RESOURCES")
        return [10, 0]

    run_id = _start(sm, monkeypatch, _FakeTrino(respond), ds_id)
    run = _run(sm, lambda s: runs.get_run(s, run_id))
    assert run.status == "PARTIAL" and "진단하지 못한 규칙(직전 결과 유지)" in run.error_message
    assert "REGEX(reg_ymd)" in run.error_message
    results = _results(sm, ds_id)
    assert results[r_nn].passed == "true"
    assert r_re not in results, "진단하지 못한 규칙에 실패 결과를 남기지 않는다 — 점수·품질 상태·알림에 섞이지 않게"
    assert (run.rules_evaluated, run.score) == (1, 100.0)


# ---------------------------------------------------------------------------
# DEF-096
# ---------------------------------------------------------------------------

def test_hourly_schedule_steps_from_planned_time() -> None:
    job = _job(schedule_type="HOURLY", schedule_interval_hours=2, schedule_time=None)
    planned = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
    # 스케줄러가 40초 늦게 돌아도 다음 차례는 12:00 정각
    assert jobs.next_run_after(job, planned + timedelta(seconds=40), planned) == datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    # 오래 멈춰 있었으면 밀린 차례는 건너뛰고 같은 격자의 다음 시각
    assert jobs.next_run_after(job, planned + timedelta(hours=5, minutes=20), planned) == datetime(
        2026, 11, 14, 16, 0, tzinfo=UTC)
    # 예정 시각 없이(새 작업·주기 변경)는 지금부터 N 시간
    now = datetime(2026, 11, 14, 10, 7, tzinfo=UTC)
    assert jobs.next_run_after(job, now) == now + timedelta(hours=2)


def test_editing_a_job_keeps_next_run_unless_schedule_changes(tmp_path) -> None:
    sm, _cat, ids = _setup(tmp_path)
    ds = ids["public.orders"]
    kw = {"schedule_type": "HOURLY", "schedule_interval_hours": 168, "schedule_time": None}
    job = _single(sm, ds, name="weekly", **kw)
    soon = datetime.now(UTC) + timedelta(hours=3)
    _set_next(sm, job.id, soon)

    def save(**changes):
        req = _write(targets=[{"type": "dataset", "dataset_id": ds}], name="weekly", **{**kw, **changes})
        out = _run(sm, lambda s: jobs.update_job(s, job.id, req, "alice"))
        return runs._as_utc(out.next_run_at)

    assert abs((save(description="설명만 고침") - soon).total_seconds()) < 1, "주기를 안 고치면 그대로"
    assert save(schedule_interval_hours=24) > soon + timedelta(hours=12), "주기를 고치면 지금부터 다시"
    assert save(schedule_interval_hours=24, enabled=False) is None


# ---------------------------------------------------------------------------
# DEF-098 · DEF-099
# ---------------------------------------------------------------------------

def _report_of_one_run(tmp_path, monkeypatch):
    sm, _cat, ids = _setup(tmp_path)
    job = _single(sm, ids["public.customers"], name="one")
    started = _run(sm, lambda s: jobs.run_now(s, job.id, "bob"))
    # customers: 필수값(NOT_NULL) + 여부(ACCEPTED_VALUES) — 건수, NULL 4, 여부 진단 96, 여부 오류 6
    assert _drain(sm, monkeypatch, lambda _sql: [100, 4, 96, 6]) == 1
    return sm, ids["public.customers"], started.id


def test_xlsx_text_never_becomes_a_formula(tmp_path, monkeypatch) -> None:
    from openpyxl import load_workbook

    sm, _ds, job_run_id = _report_of_one_run(tmp_path, monkeypatch)
    report = _run(sm, lambda s: reports.job_run_report(s, job_run_id))
    evil = report.rules[0].model_copy(update={"rule_name": '=HYPERLINK("http://x","y")', "detail": "-1+1"})
    wb = load_workbook(io.BytesIO(reports.report_xlsx(report.model_copy(update={"rules": [evil]}))))
    row = list(wb["규칙별"].iter_rows(min_row=2, max_row=2))[0]
    assert (row[1].value, row[1].data_type) == ('=HYPERLINK("http://x","y")', "s")
    assert (row[11].value, row[11].data_type) == ("-1+1", "s")


def test_report_keeps_classification_of_the_time_of_the_run(tmp_path, monkeypatch) -> None:
    sm, ds, job_run_id = _report_of_one_run(tmp_path, monkeypatch)
    before = _run(sm, lambda s: reports.job_run_report(s, job_run_id))
    assert [(g.key, g.checked_count, g.error_count) for g in before.indicators] == [("REQUIRED", 100, 4), ("YN", 96, 6)]
    assert before.rules_missing == 0
    job_id = before.job_run.job_id
    trend_before = _run(sm, lambda s: reports.job_trend(s, job_id)).points[0].indicators

    # 지표의 기준 방식을 바꾼 것처럼 — 여부 규칙이 정규식(번호)이 되고 이름도 바뀐다
    async def change(s):
        rule = (await s.execute(select(QualityRule).where(
            QualityRule.dataset_id == ds, QualityRule.check_type == "ACCEPTED_VALUES"))).scalar_one()
        rule.check_type, rule.expected_value, rule.rule_name = "REGEX", "^[YN]$", "바뀐 이름"
        return rule.id
    changed_id = _run(sm, change)

    after = _run(sm, lambda s: reports.job_run_report(s, job_run_id))
    assert [(g.key, g.checked_count, g.error_count) for g in after.indicators] == [
        ("REQUIRED", 100, 4), ("YN", 96, 6)], "지난 회차는 진단 당시의 분류 그대로"
    row = next(r for r in after.rules if r.rule_id == changed_id)
    assert (row.check_type, row.indicator) == ("ACCEPTED_VALUES", "YN") and row.rule_name != "바뀐 이름"
    assert _run(sm, lambda s: reports.job_trend(s, job_id)).points[0].indicators == trend_before

    # 규칙을 지우면 결과도 지워진다 — 요약(실행에 저장된 건수)과 표가 그만큼 다르다는 것을 알린다
    _run(sm, lambda s: s.execute(delete(QualityResult).where(QualityResult.rule_id == changed_id)))
    gone = _run(sm, lambda s: reports.job_run_report(s, job_run_id))
    assert (gone.rules_total, gone.rules_missing) == (1, 1)
