# SPDX-License-Identifier: Apache-2.0
"""변경 관리 남은 결함 수정 (자기완결형, in-memory SQLite + 스텁 — 실제 Temporal·Postgres 불필요).

- CHG-T25: 시간대 없는 적용 예정 시각/처리 기한은 UTC 로 본다(오프셋이 있으면 그대로)
- CHG-T26: CR 코드는 올해 최대 일련번호 + 1, 코드 충돌(동시 생성) 시 다음 번호로 재시도
- CHG-T26: 데이터셋·데이터 소스 삭제 시 진행 중 CR 워크플로우에 best-effort 취소 Signal,
           액티비티는 없는(삭제된) CR 을 오류 없이 건너뜀
- CHG-T14: 위임(DELEGATED)은 대결자 필수(422), 요청의 ``delegated_to`` 를 기록(결정자 아님)

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_change_mgmt_remaining_fixes
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import activity
from temporalio.service import RPCError, RPCStatusCode

import app.catalog.models as cm
from app.catalog import service as catalog_service
from app.catalog.schemas import DatasourceCreate
from app.change_mgmt import activities as acts
from app.change_mgmt import models as chm
from app.change_mgmt import router as cr_router
from app.change_mgmt import schemas, service, temporal_client
from app.change_mgmt.workflow import ChangeApprovalWorkflow, DecisionSignal, WorkflowInput
from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session
from app.permissions import router as perm

YEAR = datetime.now(timezone.utc).year


def _with_fk_targets(tables):
    seen, todo = {}, list(tables)
    while todo:
        t = todo.pop()
        if t.name not in seen:
            seen[t.name] = t
            todo.extend(fk.column.table for fk in t.foreign_keys)
    return list(seen.values())


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")   # 데이터셋 삭제 → CR 연쇄 삭제(ON DELETE CASCADE)
        cur.close()

    tables = _with_fk_targets([chm.ApprovalStep.__table__, chm.NotificationLog.__table__,
                               chm.ChangeReferrer.__table__, cm.DatasetSchema.__table__])
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(sc, tables=tables))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_dataset(db, name="db.t") -> int:
    src = cm.Datasource(datasource_id=f"m-{name}", name=f"m-{name}", type="mysql")
    db.add(src)
    await db.flush()
    ds = cm.Dataset(urn=f"{src.datasource_id}.{name}.dataset", name=name, datasource_id=src.id,
                    origin="DEV", qualified_name=f"m.{name}", status="active")
    db.add(ds)
    await db.flush()
    return ds.id


def _cr(dataset_id, code, status="DRAFT", workflow_id=None):
    return chm.ChangeRequest(cr_code=code, title=code, status=status, workflow_id=workflow_id,
                             dataset_id=dataset_id, change_type="COSMETIC", rollback_plan="r",
                             business_justification="b", requested_by="alice")


def _create_input(dataset_id, **kw):
    return schemas.ChangeRequestCreate(title="t", dataset_id=dataset_id, change_type="COSMETIC",
                                       rollback_plan="r", business_justification="b", **kw)


# ---------------------------------------------------------------------------
# CHG-T25 — 시간대
# ---------------------------------------------------------------------------

def test_naive_datetimes_are_utc() -> None:
    c = _create_input(1, scheduled_at="2026-11-08T09:00",
                      approval_chain=[{"step_order": 1, "approver": "a", "due_at": "2026-11-09T00:00"}])
    assert c.scheduled_at == datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)
    assert c.approval_chain[0].due_at == datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    # UI 는 이제 toISOString()(…Z) — KST 09:00 은 00:00Z, 오프셋을 붙여 보내도 같은 순간
    kst = _create_input(1, scheduled_at="2026-11-08T09:00:00+09:00").scheduled_at
    assert kst == _create_input(1, scheduled_at="2026-11-08T00:00:00.000Z").scheduled_at
    assert kst.utcoffset() == timedelta(hours=9)
    assert _create_input(1).scheduled_at is None


# ---------------------------------------------------------------------------
# CHG-T26 — CR 코드
# ---------------------------------------------------------------------------

async def _run_cr_code() -> None:
    engine, factory = await _make_db()
    async with factory() as db:
        ds = await _seed_dataset(db)
        # 0002 는 연쇄 삭제돼 빠짐 — 건수(2)+1 = 0003 은 이미 있다. 다른 해·형식 코드는 무시
        db.add_all([_cr(ds, f"CR-{YEAR}-0001"), _cr(ds, f"CR-{YEAR}-0003"),
                    _cr(ds, f"CR-{YEAR - 1}-0042"), _cr(ds, f"CR-{YEAR}-X")])
        await db.commit()
        assert await service._next_cr_code(db) == f"CR-{YEAR}-0004"
        cr = await service.create_cr(db, _create_input(ds), "alice")
        await db.commit()
        assert cr.cr_code == f"CR-{YEAR}-0004"

        # 동시 생성 흉내 — 처음 두 번은 이미 있는 코드를 고른다 → 다음 번호로 재시도해 성공
        real, picks = service._next_cr_code, []

        async def racy(session):
            picks.append(None)
            if len(picks) <= 2:
                return f"CR-{YEAR}-0003"
            return await real(session)

        service._next_cr_code = racy
        try:
            cr2 = await service.create_cr(db, _create_input(ds, approval_chain=[{"step_order": 1, "approver": "bob"}]), "alice")
            await db.commit()
        finally:
            service._next_cr_code = real
        assert len(picks) == 3 and cr2.cr_code == f"CR-{YEAR}-0005"
        steps = await service.list_approval_steps(db, cr2.id)
        assert [(s.cr_id, s.approver) for s in steps] == [(cr2.id, "bob")]
        assert await db.scalar(select(func.count()).select_from(chm.ChangeRequest)) == 6

        # 끝까지 충돌이면 IntegrityError 그대로(전역 핸들러 409)
        async def always_dup(_s):
            return f"CR-{YEAR}-0001"

        service._next_cr_code = always_dup
        try:
            from sqlalchemy.exc import IntegrityError
            with pytest.raises(IntegrityError):
                await service.create_cr(db, _create_input(ds), "alice")
        finally:
            service._next_cr_code = real
    await engine.dispose()


def test_cr_code_from_max_sequence_and_retry() -> None:
    asyncio.run(_run_cr_code())


# ---------------------------------------------------------------------------
# CHG-T26 — 삭제 시 워크플로우 취소 + 액티비티의 없는 CR 허용
# ---------------------------------------------------------------------------

async def _run_delete_cancels() -> None:
    sent: list[str] = []
    mode = {"connected": True, "fail": False}

    async def cancel_cr(wf):
        if mode["fail"]:
            raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
        sent.append(wf)

    saved = (service.cancel_cr, temporal_client.is_connected)
    service.cancel_cr = cancel_cr
    temporal_client.is_connected = lambda: mode["connected"]
    engine, factory = await _make_db()
    try:
        async with factory() as db:
            ds1, ds2, ds3 = await _seed_dataset(db, "a"), await _seed_dataset(db, "b"), await _seed_dataset(db, "c")
            db.add_all([
                _cr(ds1, "CR-1", "APPROVING", "wf-approving"), _cr(ds1, "CR-2", "APPROVED", "wf-approved"),
                _cr(ds1, "CR-3", "SUBMITTED", "wf-submitted"), _cr(ds1, "CR-4", "DRAFT"),
                _cr(ds1, "CR-5", "APPLIED", "wf-applied"), _cr(ds1, "CR-6", "CANCELLED", "wf-cancelled"),
                _cr(ds2, "CR-7", "APPROVING", "wf-other"), _cr(ds3, "CR-8", "APPROVING", "wf-down"),
            ])
            await db.commit()

            assert await catalog_service.delete_dataset(db, ds1)
            assert sorted(sent) == ["wf-approved", "wf-approving", "wf-submitted"]
            left = (await db.execute(select(chm.ChangeRequest.cr_code))).scalars().all()
            assert sorted(left) == ["CR-7", "CR-8"]   # 연쇄 삭제 확인(다른 데이터셋 CR 은 그대로)

            # Temporal 미연결 — 삭제는 성공, Signal 없음
            sent.clear()
            mode["connected"] = False
            assert await catalog_service.delete_dataset(db, ds3)
            assert sent == []
            # Signal 실패 — 삭제는 성공
            mode["connected"], mode["fail"] = True, True
            assert await catalog_service.delete_dataset(db, ds2)
            assert await db.scalar(select(func.count()).select_from(chm.ChangeRequest)) == 0
            assert await service.in_flight_workflow_ids(db, []) == []
    finally:
        service.cancel_cr, temporal_client.is_connected = saved
        await engine.dispose()


def test_delete_dataset_cancels_in_flight_workflows() -> None:
    asyncio.run(_run_delete_cancels())


def test_delete_datasource_cancels_in_flight_workflows() -> None:
    """데이터 소스 삭제(force) 경로 — 소속 데이터셋 CR 의 워크플로우를 커밋 후 취소."""
    from tests.test_datasource_stage2 import _make_session as _ds_session

    sent: list[str] = []

    async def cancel_cr(wf):
        sent.append(wf)

    async def run():
        make_db = await _ds_session()
        async with make_db() as db:
            ds = await catalog_service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
            d = cm.Dataset(urn="m.db.t.dataset", name="db.t", datasource_id=ds.id, origin="DEV",
                           qualified_name="m.db.t", status="active")
            db.add(d)
            await db.flush()
            db.add_all([_cr(d.id, "CR-1", "APPROVING", "wf-1"), _cr(d.id, "CR-2", "REJECTED", "wf-2")])
            await db.commit()
            assert await catalog_service.delete_datasource(db, ds.id, force=True)

    saved = (service.cancel_cr, temporal_client.is_connected)
    service.cancel_cr = cancel_cr
    temporal_client.is_connected = lambda: True
    try:
        asyncio.run(run())
    finally:
        service.cancel_cr, temporal_client.is_connected = saved
    assert sent == ["wf-1"]


async def _run_activities_missing_cr() -> None:
    engine, factory = await _make_db()

    @asynccontextmanager
    async def _factory():
        async with factory() as s:
            yield s

    saved = acts.async_session_factory
    acts.async_session_factory = _factory
    try:
        # 모두 예외 없이 건너뛴다 — 예전에는 ValueError 로 재시도 끝에 워크플로우 실패
        report = await acts.analyze_impact(999)
        assert report.affected_consumer_count == 0 and report.risk_score == 0.0
        assert await acts.record_decision(999, 1, "APPROVED", None, "admin") is None
        assert await acts.update_cr_status(999, "CANCELLED") is None
        assert await acts.send_notifications(acts.NotifyInput(cr_id=999, stage="APPLIED", channels=["EMAIL"])) == 0
        assert await acts.apply_change(acts.ApplyInput(cr_id=999)) is None

        # CHG-T14 — 위임은 요청의 대결자를 기록(결정자 아님), 대결자 없는 예전 Signal 은 비움
        async with factory() as db:
            ds = await _seed_dataset(db)
            cr = _cr(ds, "CR-1", "APPROVING", "wf")
            db.add(cr)
            await db.flush()
            db.add_all([chm.ApprovalStep(cr_id=cr.id, step_order=i, approver="admin", decision="PENDING")
                        for i in (1, 2)])
            await db.commit()
            cr_id = cr.id
        await acts.record_decision(cr_id, 1, "DELEGATED", "부재", "admin", "bob")
        await acts.record_decision(cr_id, 2, "DELEGATED", None, "admin")
        async with factory() as db:
            rows = (await db.execute(select(chm.ApprovalStep.step_order, chm.ApprovalStep.decision,
                                            chm.ApprovalStep.delegated_to)
                                     .where(chm.ApprovalStep.cr_id == cr_id)
                                     .order_by(chm.ApprovalStep.step_order))).all()
        assert [tuple(r) for r in rows] == [(1, "DELEGATED", "bob"), (2, "DELEGATED", None)]
    finally:
        acts.async_session_factory = saved
        await engine.dispose()


def test_activities_tolerate_missing_cr_and_record_delegate() -> None:
    asyncio.run(_run_activities_missing_cr())


# ---------------------------------------------------------------------------
# CHG-T14 — 위임 요청 검증(라우터)
# ---------------------------------------------------------------------------

def test_delegate_requires_target() -> None:
    assert schemas.ApprovalDecisionRequest(decision="DELEGATED", delegated_to=" bob ").delegated_to == "bob"
    assert schemas.ApprovalDecisionRequest(decision="DELEGATED", delegated_to="  ").delegated_to is None
    # 위임이 아니면 대결자 값은 버린다
    assert schemas.ApprovalDecisionRequest(decision="APPROVED", delegated_to="bob").delegated_to is None

    signals: list[tuple] = []

    async def get_cr(_s, _id):
        return SimpleNamespace(id=1, status="APPROVING", workflow_id="wf-1")

    async def list_steps(_s, _id):
        return [SimpleNamespace(step_order=1, approver="admin", decision="PENDING")]

    async def signal(_wf, _step, decision, _c, by, **kw):
        signals.append((decision, by, kw))

    async def unknown(_s, names):
        return [n for n in names if n == "ghost"]

    async def allow(*_a, **_k):
        return True

    async def _session():
        yield SimpleNamespace()

    saved = (service.get_cr, service.list_approval_steps, service.submit_decision_signal,
             service.unknown_approvers, temporal_client.is_connected, perm.check_feature)
    service.get_cr, service.list_approval_steps, service.submit_decision_signal = get_cr, list_steps, signal
    service.unknown_approvers = unknown
    temporal_client.is_connected = lambda: True
    perm.check_feature = allow
    try:
        app = FastAPI()
        app.include_router(cr_router.router, prefix="/api/v1")
        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_current_user] = lambda: TokenUser(sub="admin", username="admin", roles=["argus-user"])
        c = TestClient(app)

        def post(body):
            return c.post("/api/v1/changes/1/decisions", json=body).status_code

        assert post({"decision": "DELEGATED"}) == 422
        assert post({"decision": "DELEGATED", "delegated_to": "  "}) == 422
        assert post({"decision": "DELEGATED", "delegated_to": "ghost"}) == 422
        assert signals == []
        assert post({"decision": "DELEGATED", "delegated_to": "bob", "comment": "출장"}) == 202
        assert post({"decision": "APPROVED", "delegated_to": "bob"}) == 202
        # 위임만 대결자를 넘기고, 결정자(by)는 인증 사용자 그대로
        assert signals == [("DELEGATED", "admin", {"delegated_to": "bob"}), ("APPROVED", "admin", {})]
    finally:
        (service.get_cr, service.list_approval_steps, service.submit_decision_signal,
         service.unknown_approvers, temporal_client.is_connected, perm.check_feature) = saved


# ---------------------------------------------------------------------------
# CHG-T14 — 워크플로우가 대결자를 record_decision 에 넘김 (Temporal 테스트 서버 없으면 건너뜀)
# ---------------------------------------------------------------------------

_log: list[tuple] = []


@activity.defn(name="analyze_impact")
async def _analyze_impact(cr_id: int) -> None:
    pass


@activity.defn(name="update_cr_status")
async def _update_cr_status(cr_id: int, status: str) -> None:
    _log.append(("status", cr_id, status))


@activity.defn(name="record_decision")
async def _record_decision(cr_id: int, step: int, decision: str, comment, by, delegated_to=None) -> None:
    _log.append(("decision", step, decision, by, delegated_to))


@activity.defn(name="apply_change")
async def _apply_change(inp: acts.ApplyInput) -> None:
    _log.append(("apply", inp.cr_id))


@activity.defn(name="send_notifications")
async def _send_notifications(inp: acts.NotifyInput) -> None:
    pass


async def _run_workflow_delegate() -> None:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    try:
        env = await WorkflowEnvironment.start_time_skipping()
    except Exception as e:  # 테스트 서버 바이너리를 받을 수 없는 환경
        pytest.skip(f"Temporal 테스트 서버 사용 불가: {e}")
    async with env:
        tq = f"cr-delegate-{uuid.uuid4()}"
        activities = [_analyze_impact, _update_cr_status, _record_decision, _apply_change, _send_notifications]
        async with Worker(env.client, task_queue=tq, workflows=[ChangeApprovalWorkflow], activities=activities):
            h = await env.client.start_workflow(
                ChangeApprovalWorkflow.run, WorkflowInput(cr_id=1, approver_chain=["admin", "mjkim"]),
                id=f"cr-delegate-{uuid.uuid4()}", task_queue=tq,
            )
            await h.signal(ChangeApprovalWorkflow.submit_decision,
                           DecisionSignal(1, "DELEGATED", "출장", "admin", delegated_to="bob"))
            await h.signal(ChangeApprovalWorkflow.submit_decision, DecisionSignal(2, "APPROVED", None, "mjkim"))
            assert await h.result() == "APPLIED"   # 대결자 라우팅은 아직 없음 — 위임은 다음 단계로
    assert ("decision", 1, "DELEGATED", "admin", "bob") in _log
    assert ("decision", 2, "APPROVED", "mjkim", None) in _log


def test_workflow_passes_delegate() -> None:
    asyncio.run(_run_workflow_delegate())


if __name__ == "__main__":
    test_naive_datetimes_are_utc()
    test_cr_code_from_max_sequence_and_retry()
    test_delete_dataset_cancels_in_flight_workflows()
    test_delete_datasource_cancels_in_flight_workflows()
    test_activities_tolerate_missing_cr_and_record_delegate()
    test_delegate_requires_target()
    test_workflow_passes_delegate()
    print("test_change_mgmt_remaining_fixes OK")
