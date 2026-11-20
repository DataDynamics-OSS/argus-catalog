# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: F811 — env 는 test_ai_ready_check 의 픽스처
"""AI-Ready 알림 — 최소 요건 미충족 · 신고 접수 · 처리 기한 임박/넘김 (TODO-096). 픽스처는 test_ai_ready_check 와 같다.

- AI_READY_UNMET: 미충족 조건이 있으면 알림, 같은 데이터셋엔 다시 안 함, 충족하면 자동 해결(무시한 알림도), 판정 못 하는 조건은 미충족 아님
- REPORT_RECEIVED: 댓글을 오류 신고 · 개선 요청으로 남기거나 분류를 바꾸면 알림, 분류 · 범위 필터, 수신자에 데이터셋 생성자
- REPORT_DUE: 임박(경고) → 넘김(치명, 임박 알림은 해결), 처리 끝나면 바로 해결, 0 이면 넘김만
- 트리거 설정 검증

실행:
    cd backend
    pytest tests/test_ai_ready_alerts.py
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.alert.models import AlertNotification, AlertRule, LineageAlert
from app.alert.schemas import normalize_trigger_config
from app.catalog import ai_ready_alerts
from app.comments.models import Comment
from tests.test_ai_ready_check import V1, _fill_everything, env  # noqa: F401 — env 는 픽스처
from tests.test_ai_ready_feedback import _post

NOW = datetime.now(timezone.utc)


def _setup(run, *rules: tuple[str, str, dict]):
    """알림 표를 만들고 규칙을 넣는다 — (트리거, 범위, 설정)."""

    async def _go(db):
        conn = await db.connection()
        await conn.run_sync(lambda c: [t.__table__.create(c, checkfirst=True)
                                       for t in (AlertRule, LineageAlert, AlertNotification)])
        for i, (trigger, scope, cfg) in enumerate(rules):
            db.add(AlertRule(rule_name=f"r{i}", scope_type=scope, scope_id=None, trigger_type=trigger,
                             trigger_config=json.dumps(cfg), channels="IN_APP", notify_owners="true",
                             subscribers="ops", is_active="true"))
    run(_go)


def _call(run, fn, *args):
    out = {}

    async def _go(db):
        out["v"] = await fn(db, *args)
    run(_go)
    return out.get("v")


def _alerts(run, trigger: str | None = None) -> list[LineageAlert]:
    async def q(db):
        stmt = select(LineageAlert).order_by(LineageAlert.id)
        if trigger:
            stmt = stmt.where(LineageAlert.alert_type == trigger)
        return list((await db.execute(stmt)).scalars().all())
    return _call(run, q)


def _recipients(run, alert_id: int) -> set[str]:
    async def q(db):
        return set((await db.execute(select(AlertNotification.recipient)
                                     .where(AlertNotification.alert_id == alert_id))).scalars().all())
    return _call(run, q)


def test_unmet_alerts_dedupe_and_auto_resolve(env) -> None:
    c, _who, ids, run = env
    _setup(run, ("AI_READY_UNMET", "ALL", {}), ("AI_READY_UNMET", "ALL", {"conditions": ["missing"]}))

    assert _call(run, ai_ready_alerts.evaluate_unmet_alerts) == {"created": 2, "resolved": 0}
    alerts = _alerts(run)
    assert {a.source_dataset_id for a in alerts} == {ids["bare"], ids["other"]}, "제거된 데이터셋은 빼고, 결측률만 보는 규칙은 프로파일이 없어 판정 못 함"
    a = next(x for x in alerts if x.source_dataset_id == ids["bare"])
    assert a.severity == "WARNING" and "필수 메타데이터" in a.change_summary and "db.bare" in a.change_summary
    assert _recipients(run, a.id) == {"ops", "alice"}, "구독자 + 데이터셋 생성자"

    assert _call(run, ai_ready_alerts.evaluate_unmet_alerts) == {"created": 0, "resolved": 0}, "열린 동안 다시 알리지 않음"

    async def _dismiss(db):
        await db.execute(update(LineageAlert).where(LineageAlert.id == a.id).values(status="DISMISSED"))
    run(_dismiss)
    assert _call(run, ai_ready_alerts.evaluate_unmet_alerts)["created"] == 0, "무시해도 다시 알리지 않음"

    run(_fill_everything(ids["bare"], ids["src"]))
    assert _call(run, ai_ready_alerts.evaluate_unmet_alerts) == {"created": 0, "resolved": 1}
    resolved = next(x for x in _alerts(run) if x.id == a.id)
    assert (resolved.status, resolved.resolved_by) == ("RESOLVED", "system"), "요건을 충족하면 해결(무시한 것도)"

    async def _break(db):
        from app.catalog.models import Dataset
        await db.execute(update(Dataset).where(Dataset.id == ids["bare"]).values(license=None))
    run(_break)
    assert _call(run, ai_ready_alerts.evaluate_unmet_alerts)["created"] == 1, "해결 뒤 다시 미충족이면 새 알림"


def test_report_received(env) -> None:
    c, who, ids, run = env
    _setup(run, ("REPORT_RECEIVED", "ALL", {"categories": ["bug"]}), ("REPORT_RECEIVED", "DATASET", {}))

    async def _scope(db):  # 두 번째 규칙은 데이터셋 other 만
        await db.execute(update(AlertRule).where(AlertRule.rule_name == "r1").values(scope_id=ids["other"]))
    run(_scope)

    bug = _post(c, ids["bare"], "bug")
    (a,) = _alerts(run, "REPORT_RECEIVED")
    assert a.severity == "INFO" and a.source_dataset_id == ids["bare"]
    assert a.change_summary.startswith("오류 신고 접수: db.bare — alice: 값이 이상합니다 (기한 ")
    assert json.loads(a.change_detail)["comment_id"] == bug["id"]

    _post(c, ids["bare"], "suggestion")
    _post(c, ids["bare"], "general")
    assert len(_alerts(run, "REPORT_RECEIVED")) == 1, "개선 요청은 r0 분류 밖, r1 범위 밖 / 일반 댓글은 신고 아님"

    gen = _post(c, ids["other"], "general")
    c.put(f"{V1}/comments/{gen['id']}", json={"content": "<p>x</p>", "category": "suggestion"})
    c.put(f"{V1}/comments/{gen['id']}", json={"content": "<p>y</p>", "category": "suggestion"})
    assert [x.rule_id for x in _alerts(run, "REPORT_RECEIVED")][1:] != [], "분류를 바꿔 접수되면 알림"
    assert len(_alerts(run, "REPORT_RECEIVED")) == 2, "이미 접수된 신고를 고쳐도 다시 알리지 않음"


def test_report_due_stages(env) -> None:
    c, _who, ids, run = env
    _setup(run, ("REPORT_DUE", "ALL", {"days_before": 3}), ("REPORT_DUE", "ALL", {"days_before": 0}))
    bug = _post(c, ids["bare"], "bug")
    _post(c, ids["bare"], "suggestion")  # 기한 10일 — 아직 아님

    def due_in(days: float):
        async def _go(db):
            await db.execute(update(Comment).where(Comment.id == bug["id"])
                             .values(due_at=datetime.now(timezone.utc) + timedelta(days=days)))
        run(_go)

    assert _call(run, ai_ready_alerts.evaluate_due_alerts)["created"] == 0
    due_in(2.5)
    assert _call(run, ai_ready_alerts.evaluate_due_alerts) == {"created": 1, "resolved": 0}, "0 인 규칙은 넘김만"
    (soon,) = _alerts(run, "REPORT_DUE")
    assert soon.severity == "WARNING" and "처리 기한 임박(3일 남음)" in soon.change_summary and f"#{bug['id']}" in soon.change_summary
    assert _call(run, ai_ready_alerts.evaluate_due_alerts)["created"] == 0

    due_in(-1.2)
    assert _call(run, ai_ready_alerts.evaluate_due_alerts) == {"created": 2, "resolved": 1}, "두 규칙 넘김 + 임박 해결"
    alerts = _alerts(run, "REPORT_DUE")
    assert alerts[0].status == "RESOLVED"
    assert {a.severity for a in alerts[1:]} == {"BREAKING"} and "처리 기한 넘김(1일)" in alerts[1].change_summary

    r = c.put(f"{V1}/comments/{bug['id']}/status", json={"status": "RESOLVED", "resolution": "고쳤습니다"})
    assert r.status_code == 200, r.text
    assert all(a.status == "RESOLVED" and a.resolved_by == "system" for a in _alerts(run, "REPORT_DUE")), \
        "처리하면 그 자리에서 해결(DEF-140)"
    assert _call(run, ai_ready_alerts.evaluate_due_alerts) == {"created": 0, "resolved": 0}, "점검에 남은 것 없음"


def test_due_text_matches_screen() -> None:
    """DEF-144 — 남은 일수는 화면(``OpenDueText``)처럼 올림, 기한 날짜는 표시 시간대(KST)."""
    now = datetime(2026, 11, 15, 3, 0, tzinfo=timezone.utc)
    assert ai_ready_alerts._days_left(now + timedelta(days=0.99), now) == 1, "0.99일 남으면 D-1(전에는 '오늘')"
    assert ai_ready_alerts._days_left(now + timedelta(days=2.5), now) == 3
    assert ai_ready_alerts._days_left(now, now) == 0
    # KST 2026-11-25 08:00 = UTC 2026-11-24 23:00 — UTC 날짜로 적으면 하루 이르다
    assert ai_ready_alerts._due_date(datetime(2026, 11, 24, 23, 0, tzinfo=timezone.utc)) == "2026-11-25"
    assert ai_ready_alerts._due_date(datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc)) == "2026-11-24"


def test_due_soon_alert_uses_ceil_and_kst_date(env) -> None:
    c, _who, ids, run = env
    _setup(run, ("REPORT_DUE", "ALL", {"days_before": 2}))
    bug = _post(c, ids["bare"], "bug")
    now = datetime(2026, 11, 24, 0, 0, tzinfo=timezone.utc)
    due = datetime(2026, 11, 24, 23, 45, tzinfo=timezone.utc)   # 0.99일 남음, KST 11-25 08:45

    async def _go(db):
        await db.execute(update(Comment).where(Comment.id == bug["id"]).values(due_at=due))
    run(_go)
    assert _call(run, ai_ready_alerts.evaluate_due_alerts, now)["created"] == 1
    (a,) = _alerts(run, "REPORT_DUE")
    assert "처리 기한 임박(1일 남음)" in a.change_summary and "(기한 2026-11-25)" in a.change_summary, a.change_summary


def test_run_checks_without_rules(env) -> None:
    _c, _who, _ids, run = env
    _setup(run)
    assert _call(run, ai_ready_alerts.run_checks) == {"unmet": {"created": 0, "resolved": 0},
                                                      "due": {"created": 0, "resolved": 0}}


def test_trigger_config_validation() -> None:
    assert json.loads(normalize_trigger_config("AI_READY_UNMET", {"conditions": ["missing", "format"]})) == \
        {"conditions": ["missing", "format"]}
    assert json.loads(normalize_trigger_config("REPORT_DUE", {})) == {}
    assert json.loads(normalize_trigger_config("REPORT_DUE", {"days_before": 0})) == {"days_before": 0}
    for trigger, cfg in [("AI_READY_UNMET", {"conditions": ["speed"]}), ("AI_READY_UNMET", {"conditions": []}),
                         ("REPORT_RECEIVED", {"categories": ["feature"]}), ("REPORT_DUE", {"days_before": 31}),
                         ("REPORT_DUE", {"days_before": True})]:
        with pytest.raises(ValueError):
            normalize_trigger_config(trigger, cfg)
    assert asyncio.iscoroutinefunction(ai_ready_alerts.alert_loop)
