# SPDX-License-Identifier: Apache-2.0
"""소비자 ACK 집계·미확인 에스컬레이션 (DEF-004, IT CHG-T10).

- ACK 현황은 통지 로그 행이 아니라 소비자 단위로 센다(한 통지라도 ACK 하면 확인).
- 적용 직전 미확인 소비자가 있으면 요청자·결재자에게 변경 통지 채널로 알리고 ``stage=ESCALATION`` 이력을 남긴다.
  통지가 꺼져 있으면 발송 없이 ``SKIPPED`` 이력. 모두 확인했으면 아무것도 남기지 않는다.
- 워크플로우는 적용 예정 시각이 있는 CR 만 적용 직전에 에스컬레이션하고, 적용은 막지 않는다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_cr_ack_escalation.py
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import activity

import app.catalog.models as cm
import app.settings.notify as notify_mod
import app.settings.service as settings_service
from app.change_mgmt import activities as acts
from app.change_mgmt import models as chm
from app.change_mgmt.activities import ApplyInput, NotifyInput
from app.change_mgmt.workflow import ChangeApprovalWorkflow, DecisionSignal, WorkflowInput
from app.usermgr.models import ArgusUser
from tests.test_cr_notification_ack import _with_fk_targets


def _setup(monkeypatch, *, enabled="true", channel="slack"):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(acts, "async_session_factory", factory)

    async def cfg(_session, category):
        assert category == "change"
        return {"change_notify_enabled": enabled, "change_notify_channel": channel}

    sent: list[str] = []

    async def fake_notify(_session, text, **_k):
        sent.append(text)
        return True

    monkeypatch.setattr(settings_service, "get_config_by_category", cfg)
    monkeypatch.setattr(notify_mod, "send_notify_db", fake_notify)

    async def seed(acked: set[str]) -> int:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sc: cm.Base.metadata.create_all(sc, tables=_with_fk_targets(
                [chm.NotificationLog.__table__, chm.ApprovalStep.__table__, ArgusUser.__table__])))
        async with factory() as db:
            src = cm.Datasource(datasource_id="m", name="m", type="mysql")
            db.add(src)
            await db.flush()
            ds = cm.Dataset(urn="m.db.t.dataset", name="db.t", datasource_id=src.id, origin="DEV",
                            qualified_name="m.db.t", status="active")
            db.add(ds)
            await db.flush()
            cr = chm.ChangeRequest(cr_code="CR-1", title="컬럼 삭제", status="APPROVED", dataset_id=ds.id,
                                   change_type="BREAKING", rollback_plan="r", business_justification="b",
                                   requested_by="kim", scheduled_at=datetime.now(timezone.utc))
            db.add(cr)
            await db.flush()
            for order, who in ((1, "lee"), (2, "kim")):  # 요청자가 결재자이기도 — 한 번만 받는다
                db.add(chm.ApprovalStep(cr_id=cr.id, step_order=order, approver=who))
            for name in ("ETL", "대시보드", "ML"):
                c = chm.Consumer(dataset_id=ds.id, consumer_name=name)
                db.add(c)
                await db.flush()
                for stage in ("T_MINUS_7", "T_MINUS_1"):
                    for ch in ("EMAIL", "WEBHOOK"):
                        ack = datetime.now(timezone.utc) if name in acked and stage == "T_MINUS_1" and ch == "WEBHOOK" else None
                        db.add(chm.NotificationLog(cr_id=cr.id, consumer_id=c.id, channel=ch, stage=stage,
                                                   status="ACKED" if ack else "SENT", acked_at=ack))
            await db.commit()
            return cr.id

    async def escalation_logs(cr_id):
        async with factory() as db:
            return (await db.execute(select(chm.NotificationLog).where(
                chm.NotificationLog.cr_id == cr_id, chm.NotificationLog.stage == "ESCALATION",
            ).order_by(chm.NotificationLog.id))).scalars().all()

    return seed, escalation_logs, sent


def test_summary_counts_consumers_not_rows(monkeypatch) -> None:
    seed, _, _ = _setup(monkeypatch)
    cr_id = asyncio.run(seed({"ETL"}))
    s = asyncio.run(acts.wait_acks_summary(cr_id))
    assert s == {"total": 3, "acked": 1, "pending": 2, "pending_consumers": ["ML", "대시보드"]}


def test_escalates_to_requester_and_approvers(monkeypatch) -> None:
    seed, logs, sent = _setup(monkeypatch)
    cr_id = asyncio.run(seed({"ETL"}))
    s = asyncio.run(acts.escalate_unacked_consumers(cr_id))
    assert s["pending"] == 2
    rows = asyncio.run(logs(cr_id))
    assert [(r.recipient, r.channel, r.status) for r in rows] == [("kim", "SLACK", "SENT"), ("lee", "SLACK", "SENT")]
    assert len(sent) == 1 and "미확인 소비자 2곳" in sent[0] and "ML, 대시보드" in sent[0]


def test_escalation_rows_fit_notification_response(monkeypatch) -> None:
    """에스컬레이션 이력이 이력 조회 응답 스키마를 통과한다 (DEF-028 — 예전에는 500)."""
    from app.change_mgmt.schemas import NotificationLogResponse, NotificationStage

    assert NotificationStage(acts.ESCALATION_STAGE) is NotificationStage.ESCALATION
    seed, logs, _ = _setup(monkeypatch)
    cr_id = asyncio.run(seed({"ETL"}))
    asyncio.run(acts.escalate_unacked_consumers(cr_id))
    out = [NotificationLogResponse.model_validate(r) for r in asyncio.run(logs(cr_id))]
    assert [(o.stage, o.recipient) for o in out] == [(NotificationStage.ESCALATION, "kim"),
                                                     (NotificationStage.ESCALATION, "lee")]


def test_all_acked_records_nothing(monkeypatch) -> None:
    seed, logs, sent = _setup(monkeypatch)
    cr_id = asyncio.run(seed({"ETL", "대시보드", "ML"}))
    assert asyncio.run(acts.escalate_unacked_consumers(cr_id))["pending"] == 0
    assert asyncio.run(logs(cr_id)) == [] and sent == []


def test_notify_off_records_skipped(monkeypatch) -> None:
    seed, logs, sent = _setup(monkeypatch, enabled="false")
    cr_id = asyncio.run(seed(set()))
    asyncio.run(acts.escalate_unacked_consumers(cr_id))
    assert [r.status for r in asyncio.run(logs(cr_id))] == ["SKIPPED", "SKIPPED"] and sent == []


def test_email_without_user_address_is_failed(monkeypatch) -> None:
    seed, logs, _ = _setup(monkeypatch, channel="email")
    cr_id = asyncio.run(seed(set()))
    asyncio.run(acts.escalate_unacked_consumers(cr_id))
    rows = asyncio.run(logs(cr_id))
    assert [(r.recipient, r.status) for r in rows] == [("kim", "FAILED"), ("lee", "FAILED")]
    assert "이메일" in rows[0].error


# ---------------------------------------------------------------------------
# 워크플로우 — 예정 시각이 있는 CR 만 적용 직전 에스컬레이션, 적용은 계속
# ---------------------------------------------------------------------------

_log: list[tuple] = []


@activity.defn(name="analyze_impact")
async def _analyze(cr_id: int) -> None:
    _log.append(("analyze", cr_id))


@activity.defn(name="update_cr_status")
async def _status(cr_id: int, status: str) -> None:
    _log.append(("status", cr_id, status))


@activity.defn(name="record_decision")
async def _decision(cr_id: int, step: int, decision: str, comment, by) -> None:
    _log.append(("decision", cr_id, step))


@activity.defn(name="apply_change")
async def _apply(inp: ApplyInput) -> None:
    _log.append(("apply", inp.cr_id))


@activity.defn(name="send_notifications")
async def _notify(inp: NotifyInput) -> None:
    _log.append(("notify", inp.cr_id, inp.stage))


@activity.defn(name="escalate_unacked_consumers")
async def _escalate(cr_id: int) -> dict:
    _log.append(("escalate", cr_id))
    return {"total": 2, "acked": 0, "pending": 2, "pending_consumers": ["a", "b"]}


async def _workflow_cases() -> None:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    try:
        env = await WorkflowEnvironment.start_time_skipping()
    except Exception as e:  # 테스트 서버 바이너리를 받을 수 없는 환경
        pytest.skip(f"Temporal 테스트 서버 사용 불가: {e}")
    async with env:
        tq = f"cr-ack-{uuid.uuid4()}"
        async with Worker(env.client, task_queue=tq, workflows=[ChangeApprovalWorkflow],
                          activities=[_analyze, _status, _decision, _apply, _notify, _escalate]):
            async def run(cr_id, scheduled):
                h = await env.client.start_workflow(
                    ChangeApprovalWorkflow.run,
                    WorkflowInput(cr_id=cr_id, approver_chain=["admin"], scheduled_at_iso=scheduled),
                    id=f"cr-{cr_id}-{uuid.uuid4()}", task_queue=tq)
                await h.signal(ChangeApprovalWorkflow.submit_decision, DecisionSignal(1, "APPROVED", None, "admin"))
                return await h.result()

            sched = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
            assert await run(1, sched) == "APPLIED"
            mine = [e for e in _log if e[1] == 1]
            assert mine.index(("notify", 1, "T_MINUS_1")) < mine.index(("escalate", 1)) < mine.index(("apply", 1))

            # 예정 시각 없음 — 사전 통지가 없으니 에스컬레이션도 없다
            assert await run(2, None) == "APPLIED"
            assert ("escalate", 2) not in _log and ("apply", 2) in _log


def test_workflow_escalates_before_apply_without_blocking() -> None:
    asyncio.run(_workflow_cases())
