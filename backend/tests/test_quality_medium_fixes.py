# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 중간 결함 회귀 테스트 (자기완결형, SQLite — Trino 는 가짜).

- DEF-148: 지표를 진단 범위로 고른 작업을 지표 응답(``jobs``)이 알리고, 지운 지표는 작업 응답의 ``deleted_definition_ids``.
- DEF-149: 실행의 ``rules_total`` 은 그 실행이 진단하는 규칙(고른 지표 · 진단 기준 안)만 센다 — 작업 이력 · 리포트가 같다.
- DEF-150: 마지막 진단이 평가하지 않아 이어받은 결과는 최신 결과 API 가 ``carried_forward`` 로 표시한다.
  업스트림 경고의 점수는 최신 점수 기록(소수 한 자리)이다.
- DEF-151: *다시 실행* 은 원래 실행의 종류(진단 · 프로파일)와 범위(품질 지표)를 그대로 쓴다.
- DEF-152: 원천 직접 경로(Trino 연결 없음) 데이터셋의 진단 쿼리 미리보기는 원천 방언 쿼리와 그 경로의 안내를 보인다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_medium_fixes.py
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
from sqlalchemy import text, update

from app.quality import definitions, jobs, reports, runs, service
from app.quality.models import (
    QualityDefinition,
    QualityJob,
    QualityJobRun,
    QualityRule,
    QualityRun,
    QualityScore,
)
from app.quality.schemas import QualitySqlPreviewRequest
from tests.test_quality_trino_runs import (  # noqa: F401
    _app,
    _FakeTrino,
    _make_sessionmaker,
    _no_alerts,
    _run,
    _seed,
    _work,
    worker_trino,
)

_RULES = [
    {"check_type": "NOT_NULL", "column": "use_yn"},
    {"check_type": "NOT_NULL", "column": "reg_ymd"},
    {"check_type": "NOT_NULL", "column": "cust_id"},   # 지표 없이 직접 만든 규칙
]


def _define(sm, rule_ids: list[int], name: str = "필수값") -> int:
    """지표 하나를 만들어 규칙들에 할당한다."""
    async def go(s):
        d = QualityDefinition(name=name, category="REQUIRED", check_type="NOT_NULL", threshold=100.0, severity="WARNING")
        s.add(d)
        await s.flush()
        await s.execute(update(QualityRule).where(QualityRule.id.in_(rule_ids)).values(definition_id=d.id))
        return d.id
    return _run(sm, go)


# ---------------------------------------------------------------------------
# DEF-149 — 규칙 수는 그 실행이 진단하는 규칙만
# ---------------------------------------------------------------------------

def test_rules_total_counts_only_rules_in_scope(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r1, r2, _r3) = asyncio.run(_seed(sm, _RULES))
    def_id = _define(sm, [r1, r2])

    # 작업 실행처럼 묶어 둔다 — 리포트와 작업 이력이 같은 수를 보이는지
    async def job_run(s):
        job = QualityJob(name="필수값 작업", targets_json=json.dumps([{"type": "dataset", "dataset_id": ds_id}]),
                         definition_ids=str(def_id), schedule_type="NONE")
        s.add(job)
        await s.flush()
        jr = QualityJobRun(job_id=job.id, trigger_type="MANUAL", status="RUNNING", definition_ids=str(def_id),
                           targets_total=1, runs_total=1)
        s.add(jr)
        await s.flush()
        return jr.id
    job_run_id = _run(sm, job_run)

    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice", definition_ids=[def_id], job_run_id=job_run_id))
    assert run.rules_total == 2, "지표 없이 직접 만든 규칙은 이 실행이 진단하지 않는다 — 분모에 넣지 않는다"

    monkeypatch.setattr(worker_trino, "execute", _FakeTrino(lambda _sql: [100, 0, 0]))
    assert asyncio.run(_work(sm)) is True
    done = _run(sm, lambda s: runs.get_run(s, run.id))
    assert (done.status, done.rules_total, done.rules_evaluated) == ("SUCCEEDED", 2, 2)

    report = _run(sm, lambda s: reports.job_run_report(s, job_run_id))
    assert report.job_run.rules_total == report.rules_total == 2, "한 응답 안의 규칙 수가 같다"

    # 다시 실행도 같은 범위 · 같은 분모
    again = _run(sm, lambda s: runs.retry_run(s, run.id, "bob"))
    assert again.rules_total == 2 and again.definition_ids == [def_id]


def test_rules_total_without_scope_counts_all_active_rules(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [*_RULES, {"check_type": "NOT_NULL", "column": "amt", "active": "false"}]))
    run = _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert run.rules_total == 3, "비활성 규칙은 넣지 않는다"


# ---------------------------------------------------------------------------
# DEF-151 — 다시 실행은 원래의 종류와 범위로
# ---------------------------------------------------------------------------

def test_retry_keeps_profile_kind(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, _RULES))
    profile = _run(sm, lambda s: runs.create_run(s, ds_id, "alice", kind=runs.PROFILE_KIND))

    async def finish(s):
        row = await s.get(QualityRun, profile.id)
        row.status = "SUCCEEDED"
    _run(sm, finish)

    again = _run(sm, lambda s: runs.retry_run(s, profile.id, "bob"))
    assert again.kind == runs.PROFILE_KIND and again.rules_total == 0 and again.definition_ids == []


def test_retry_keeps_definition_scope(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r1, _r2, _r3) = asyncio.run(_seed(sm, _RULES))
    def_id = _define(sm, [r1])
    first = _run(sm, lambda s: runs.create_run(s, ds_id, "alice", definition_ids=[def_id]))

    async def finish(s):
        row = await s.get(QualityRun, first.id)
        row.status = "FAILED"
    _run(sm, finish)

    again = _run(sm, lambda s: runs.retry_run(s, first.id, "bob"))
    assert (again.kind, again.definition_ids, again.rules_total) == (runs.CHECK_KIND, [def_id], 1)


# ---------------------------------------------------------------------------
# DEF-150 — 이어받은 결과 표시, 업스트림 점수
# ---------------------------------------------------------------------------

def test_latest_results_mark_carried_forward(tmp_path, monkeypatch) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r1, r2, r3) = asyncio.run(_seed(sm, _RULES))
    def_id = _define(sm, [r1, r2])

    monkeypatch.setattr(worker_trino, "execute", _FakeTrino(lambda _sql: [100, 0, 0, 5]))
    _run(sm, lambda s: runs.create_run(s, ds_id, "alice"))
    assert asyncio.run(_work(sm)) is True
    first = {r.rule_id: r for r in _run(sm, lambda s: service.get_latest_results(s, ds_id))}
    assert not any(r.carried_forward for r in first.values()), "모두 마지막 진단의 결과"

    monkeypatch.setattr(worker_trino, "execute", _FakeTrino(lambda _sql: [100, 0, 0]))
    _run(sm, lambda s: runs.create_run(s, ds_id, "alice", definition_ids=[def_id]))
    assert asyncio.run(_work(sm)) is True
    latest = {r.rule_id: r for r in _run(sm, lambda s: service.get_latest_results(s, ds_id))}
    assert (latest[r1].carried_forward, latest[r2].carried_forward) == (False, False)
    assert latest[r3].carried_forward is True and latest[r3].passed == "false", "직접 만든 규칙은 첫 실행의 결과를 이어받았다"
    assert latest[r3].run_id == first[r3].run_id


def test_upstream_badge_uses_recorded_score(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, _RULES))

    async def go(s):
        await s.execute(text(
            "CREATE TABLE argus_dataset_lineage (id INTEGER PRIMARY KEY, source_dataset_id INTEGER, "
            "target_dataset_id INTEGER, relation_type VARCHAR(50))"))
        import app.catalog.models as cm
        target = cm.Dataset(urn="pg-1.public.view.dataset", name="view", datasource_id=1, catalog_id=1,
                            qualified_name="pg-1.public.view")
        s.add(target)
        await s.flush()
        await s.execute(text(
            "INSERT INTO argus_dataset_lineage (source_dataset_id, target_dataset_id, relation_type) "
            "VALUES (:s, :t, 'READ')"), {"s": ds_id, "t": target.id})
        source = await s.get(cm.Dataset, ds_id)
        source.quality_status, source.quality_score = "BAD", 33   # 데이터셋 행은 정수로 반올림해 둔다
        s.add(QualityScore(dataset_id=ds_id, score=33.3, total_rules=3, passed_rules=1, warning_rules=0,
                           failed_rules=2))
        return target.id
    target_id = _run(sm, go)

    client = TestClient(_app(sm))
    rows = client.get(f"/api/v1/quality/datasets/{target_id}/upstream-quality").json()
    assert [(r["id"], r["quality_score"]) for r in rows] == [(ds_id, 33.3)], "품질 탭 · 점수 이력과 같은 소수 한 자리"


# ---------------------------------------------------------------------------
# DEF-148 — 지표 삭제가 작업에 주는 영향
# ---------------------------------------------------------------------------

def test_definition_lists_jobs_using_it_and_job_flags_deleted(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (r1, r2, _r3) = asyncio.run(_seed(sm, _RULES))
    only_id = _define(sm, [r1], name="필수값 A")
    other_id = _define(sm, [r2], name="필수값 B")
    target = json.dumps([{"type": "dataset", "dataset_id": ds_id}])

    async def make_jobs(s):
        s.add_all([
            QualityJob(name="A 만", targets_json=target, definition_ids=str(only_id), schedule_type="NONE"),
            QualityJob(name="A 와 B", targets_json=target, definition_ids=f"{only_id},{other_id}", schedule_type="NONE"),
            QualityJob(name="전부", targets_json=target, definition_ids=None, schedule_type="NONE"),
        ])
    _run(sm, make_jobs)

    listed = {d.id: d for d in _run(sm, definitions.list_definitions)}
    assert [(j.name, j.only) for j in listed[only_id].jobs] == [("A 만", True), ("A 와 B", False)]
    assert [j.name for j in listed[other_id].jobs] == ["A 와 B"], "전부 진단하는 작업은 지표를 고르지 않았다"

    _run(sm, lambda s: definitions.delete_definition(s, only_id))
    by_name = {j.name: j for j in _run(sm, jobs.list_jobs)}
    assert by_name["A 만"].deleted_definition_ids == [only_id]
    assert by_name["A 만"].definitions == [f"삭제된 지표 #{only_id}"]
    assert by_name["A 와 B"].deleted_definition_ids == [only_id]
    assert by_name["전부"].deleted_definition_ids == []


# ---------------------------------------------------------------------------
# DEF-152 — 원천 직접 경로의 진단 쿼리 미리보기
# ---------------------------------------------------------------------------

def _preview(sm, **kw):
    return _run(sm, lambda s: definitions.preview_sql(s, QualitySqlPreviewRequest(**kw)))


def test_preview_on_direct_path_uses_source_dialect(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, [], connect=False, source_type="mysql"))

    agg = _preview(sm, check_type="NOT_NULL", column_name="use_yn", dataset_id=ds_id)
    assert (agg.engine, agg.source_type, agg.mode, agg.actual_table) == ("DIRECT", "mysql", "AGGREGATE", True)
    assert "FROM `public`.`orders`" in agg.sql and "`use_yn` IS NULL" in agg.sql
    assert '"catalog"' not in agg.sql and "Trino" not in (agg.note or "")
    assert agg.table == "sales-pg.public.orders" and agg.judgement

    # 원천에서 집계하지 않는 규칙 — 워커가 행을 읽는 쿼리
    read = _preview(sm, check_type="REGEX", column_name="reg_ymd", expected_value=r"^\d{8}$", dataset_id=ds_id)
    assert read.mode == "READ" and read.sql == "SELECT `reg_ymd` FROM `public`.`orders`"
    assert "품질 워커" in read.note

    # Trino 에서만 평가하는 유형은 이 경로에서 건너뛴다고 알린다
    fn = _preview(sm, check_type="FUNCTION", column_name="use_yn", expected_value="dq.is_ok", dataset_id=ds_id)
    assert fn.sql is None and fn.engine == "DIRECT" and "건너뜁니다" in fn.note

    # 원천 유형이 PostgreSQL 이면 큰따옴표 — Trino 카탈로그 없이 스키마.테이블
    sm2 = _make_sessionmaker(str(tmp_path / "q2.db"))
    pg_id, _ = asyncio.run(_seed(sm2, [], connect=False))
    pg = _preview(sm2, check_type="NOT_NULL", column_name="use_yn", dataset_id=pg_id)
    assert pg.engine == "DIRECT" and pg.sql.endswith('FROM "public"."orders"')


def test_preview_on_trino_path_is_unchanged(tmp_path) -> None:
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, _ = asyncio.run(_seed(sm, []))
    p = _preview(sm, check_type="NOT_NULL", column_name="use_yn", dataset_id=ds_id)
    assert p.engine == "TRINO" and p.mode is None and 'FROM "pg_sales"."public"."orders"' in p.sql
