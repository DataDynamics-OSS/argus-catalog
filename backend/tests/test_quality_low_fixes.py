# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 낮음 결함 회귀 테스트 (자기완결형, SQLite — Trino 는 가짜).

- DEF-154: 진단 경로 확인(``/engine``)도 실행과 같은 기준으로 접속 설정을 본다 — 없으면 '불가' 와 그 사유.
- DEF-155: 리포트 엑셀 · PDF 는 화면과 같은 용어(진단 테이블) · 라벨(심각도 · 유형) · 표시 시간대로 적는다.
- DEF-156: 작업 실행의 중지된 데이터셋 실행은 실패와 따로 센다(``runs_cancelled``).
- DEF-157: 규칙이 하나도 없을 때의 409 문구에 Trino 이야기를 섞지 않는다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_low_fixes.py
"""

from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

import app.catalog.models as cm
from app.quality import jobs, report_pdf, reports, runs
from app.quality.models import QualityJob, QualityJobRun, QualityRun
from app.quality.schemas import QualityJobRunReport, QualityJobRunResponse, QualityReportDataset, QualityReportRule
from tests.test_quality_trino_runs import (  # noqa: F401
    _app,
    _make_sessionmaker,
    _no_alerts,
    _run,
    _seed,
)


# ---------------------------------------------------------------------------
# DEF-154 — 경로 확인과 실행의 접속 설정 판단이 같다
# ---------------------------------------------------------------------------

def _drop_configs(sm) -> None:
    async def go(s):
        await s.execute(delete(cm.DatasourceConfiguration))
    _run(sm, go)


def test_engine_unavailable_without_trino_config(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))
    assert _run(sm, lambda s: runs.engine_status(s, ds_id)).available is True
    _drop_configs(sm)

    status = _run(sm, lambda s: runs.engine_status(s, ds_id))
    assert status.available is False
    assert status.reason == "Trino 데이터 소스 'lake-trino' 에 접속 설정이 없습니다."
    with pytest.raises(runs.EngineUnavailableError, match="접속 설정이 없습니다"):
        _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))


def test_engine_unavailable_without_direct_source_config(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}], connect=False))
    status = _run(sm, lambda s: runs.engine_status(s, ds_id))
    assert status.available is False and status.reason == "원천 데이터 소스 'sales-pg' 에 접속 설정이 없습니다."


# ---------------------------------------------------------------------------
# DEF-155 — 리포트 표기는 화면과 같게
# ---------------------------------------------------------------------------

def _report() -> QualityJobRunReport:
    started = datetime(2026, 11, 15, 1, 2, 3, tzinfo=timezone.utc)
    jr = QualityJobRunResponse(id=7, job_id=1, job_name="야간 진단", trigger_type="MANUAL", status="CANCELLED",
                               runs_total=2, runs_succeeded=0, runs_failed=0, runs_cancelled=1,
                               started_at=started, finished_at=started)
    rule = QualityReportRule(dataset_id=1, dataset_name="orders", rule_id=1, rule_name="필수", check_type="NOT_NULL",
                             severity="BREAKING", passed=False, checked_count=10, error_count=1, error_rate=10.0)
    warn = rule.model_copy(update={"rule_id": 2, "check_type": "UNIQUE", "severity": "WARNING", "passed": True})
    ds = QualityReportDataset(dataset_id=1, dataset_name="orders", datasource_name="sales-pg",
                              engine_table="sales-pg.public.orders", run_id=1, status="CANCELLED")
    return QualityJobRunReport(job_run=jr, datasets=[ds], rules=[rule, warn], rules_total=2, failed_rules=1)


def test_display_zone_defaults_to_seoul() -> None:
    assert reports._local(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)) == "2026-01-01 09:00:00"
    assert reports._zone(reports.display_zone("Nowhere/Zone")) == "KST"
    assert reports._local(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc), reports.display_zone("UTC")) \
        == "2026-01-01 00:00:00"


def test_xlsx_uses_screen_labels_and_zone() -> None:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(reports.report_xlsx(_report())))
    summary = {r[0]: r[1] for r in wb["요약"].iter_rows(values_only=True) if r[0]}
    assert summary["시작 (KST)"] == "2026-11-15 10:02:03", "서버 OS 시간대(UTC)가 아니라 화면 기본(KST)"
    assert summary["  실패·일부 실패"] == 0 and summary["  중지"] == 1
    datasets = list(wb["데이터셋별"].iter_rows(values_only=True))
    assert datasets[0][2] == "진단 테이블" and "Trino 테이블" not in datasets[0]
    rules = list(wb["규칙별"].iter_rows(values_only=True))
    assert [(r[2], r[6]) for r in rules[1:]] == [("NULL 없음", "치명"), ("고유값", "경고")]

    # 브라우저가 보낸 시간대를 따른다
    utc = load_workbook(io.BytesIO(reports.report_xlsx(_report(), "UTC")))
    assert {r[0]: r[1] for r in utc["요약"].iter_rows(values_only=True) if r[0]}["시작 (UTC)"] == "2026-11-15 01:02:03"


def test_pdf_uses_screen_labels_and_zone(monkeypatch) -> None:
    seen: list[str] = []
    original = report_pdf._pdf_text

    def record(text):
        out = original(text)
        seen.append(out)
        return out
    monkeypatch.setattr(report_pdf, "_pdf_text", record)
    assert report_pdf.report_pdf(_report()).startswith(b"%PDF-")
    assert "진단 테이블" in seen and "Trino 테이블" not in seen
    assert {"치명", "경고", "NULL 없음", "고유값"} <= set(seen)
    assert not {"BREAKING", "WARNING", "NOT_NULL"} & set(seen)
    assert "시작 (KST)" in seen and "2026-11-15 10:02:03" in seen
    assert "0 / 1" in seen, "실패 / 중지를 따로"


def test_report_download_takes_browser_timezone(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    asyncio.run(_seed(sm, []))

    async def job_run(s):
        job = QualityJob(name="작업", targets_json="[]", schedule_type="NONE")
        s.add(job)
        await s.flush()
        jr = QualityJobRun(job_id=job.id, trigger_type="MANUAL", status="SUCCEEDED",
                           started_at=datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc))
        s.add(jr)
        await s.flush()
        return jr.id
    jr_id = _run(sm, job_run)
    from openpyxl import load_workbook

    client = TestClient(_app(sm))
    res = client.get(f"/api/v1/quality/job-runs/{jr_id}/report.xlsx", params={"tz": "UTC"})
    assert res.status_code == 200
    summary = {r[0]: r[1] for r in load_workbook(io.BytesIO(res.content))["요약"].iter_rows(values_only=True) if r[0]}
    assert summary["시작 (UTC)"] == "2026-11-15 01:00:00"
    assert client.get(f"/api/v1/quality/job-runs/{jr_id}/report.pdf", params={"tz": "Asia/Seoul"}).status_code == 200


# ---------------------------------------------------------------------------
# DEF-156 — 중지는 실패와 따로
# ---------------------------------------------------------------------------

def test_cancelled_runs_counted_separately(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))

    async def seed(s):
        job = QualityJob(name="작업", targets_json=json.dumps([{"type": "dataset", "dataset_id": ds_id}]),
                         schedule_type="NONE")
        s.add(job)
        await s.flush()
        jr = QualityJobRun(job_id=job.id, trigger_type="MANUAL", status="RUNNING", targets_total=1, runs_total=3,
                           cancel_requested_by="alice")
        s.add(jr)
        await s.flush()
        for status in ("CANCELLED", "FAILED", "SUCCEEDED"):
            s.add(QualityRun(dataset_id=ds_id, job_run_id=jr.id, status=status, trigger_type="JOB", rules_total=1))
        return jr.id
    jr_id = _run(sm, seed)

    resp = _run(sm, lambda s: jobs.get_job_run(s, jr_id))
    assert resp.status == "CANCELLED"
    assert (resp.runs_succeeded, resp.runs_failed, resp.runs_cancelled) == (1, 1, 1)
    trend = _run(sm, lambda s: reports.job_trend(s, resp.job_id))
    assert (trend.points[-1].runs_failed, trend.points[-1].runs_cancelled) == (1, 1)


# ---------------------------------------------------------------------------
# DEF-157 — 규칙이 없을 때의 문구
# ---------------------------------------------------------------------------

def test_no_rules_conflict_message_is_plain(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [], connect=False, source_config={"host": "pg.local", "username": "u"}))
    with pytest.raises(runs.RunConflictError) as e:
        _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert "규칙이 없습니다" in str(e.value) and "Trino" not in str(e.value)
