# SPDX-License-Identifier: Apache-2.0
"""변경 요청 취소 — 상태 검사(라우터, 스텁) + 적용 전 어느 단계에서든 취소 반영(워크플로우).

예전에는(IT CHG-T19)
- 라우터가 상태를 보지 않아 이미 끝난 CR(APPLIED·CANCELLED 등)의 취소가 종료된 워크플로우에
  Signal 을 보내다 HTTP 500 이었다.
- 워크플로우가 결재 대기 중에만 취소 플래그를 봐서, 결재선 없는 CR 이나 승인 후 적용 예정
  시각을 기다리는 CR 의 취소는 202 로 받고 무시돼 그대로 APPLIED 가 됐다.

워크플로우 부분은 Temporal 시간 건너뛰기 테스트 서버가 필요하다 — 받을 수 없는 환경이면 건너뛴다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_cr_cancel_state
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from temporalio import activity
from temporalio.service import RPCError, RPCStatusCode

from app.change_mgmt import router as cr_router
from app.change_mgmt import service, temporal_client
from app.change_mgmt.activities import ApplyInput, NotifyInput
from app.change_mgmt.workflow import ChangeApprovalWorkflow, DecisionSignal, WorkflowInput
from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session

# ---------------------------------------------------------------------------
# 라우터 — 상태 검사
# ---------------------------------------------------------------------------

def test_cancel_router_state() -> None:
    def cr(i, status, wf):
        return SimpleNamespace(id=i, status=status, workflow_id=wf, requested_by="alice")

    crs = {
        1: cr(1, "APPLIED", "wf-1"), 2: cr(2, "CANCELLED", "wf-2"), 3: cr(3, "REJECTED", "wf-3"),
        4: cr(4, "SUBMITTED", "wf-4"), 5: cr(5, "APPROVING", "wf-5"), 6: cr(6, "APPROVED", "wf-6"),
        7: cr(7, "APPROVING", "wf-gone"), 8: cr(8, "APPROVING", "wf-down"), 9: cr(9, "DRAFT", None),
        10: cr(10, "DRAFT", None),   # 취소 직전에 상신된 경우(조건부 UPDATE 0건)
        11: cr(11, "CANCELLED", None),  # 상신 전에 취소된 CR — 워크플로우 없음
    }
    drafts: list[int] = []
    connected = {"v": True}
    sent: list[str] = []

    async def get_cr(_s, cr_id):
        return crs.get(cr_id)

    async def cancel_cr(workflow_id):
        if workflow_id == "wf-gone":
            raise RPCError("workflow execution already completed", RPCStatusCode.NOT_FOUND, b"")
        if workflow_id == "wf-down":
            raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
        sent.append(workflow_id)

    async def cancel_draft_cr(_s, cr_id):
        if cr_id == 10:
            return False
        drafts.append(cr_id)
        return True

    class _Sess:
        async def commit(self):
            pass

    async def _session():
        yield _Sess()

    async def ensure_connected():   # 실제 재연결 시도 없이 연결 상태만 흉내
        return connected["v"]

    saved = (service.get_cr, service.cancel_cr, service.cancel_draft_cr, temporal_client.is_connected,
             temporal_client.ensure_connected)
    service.get_cr, service.cancel_cr, service.cancel_draft_cr = get_cr, cancel_cr, cancel_draft_cr
    temporal_client.is_connected = lambda: connected["v"]
    temporal_client.ensure_connected = ensure_connected
    try:
        app = FastAPI()
        app.include_router(cr_router.router, prefix="/api/v1")
        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_current_user] = lambda: TokenUser(sub="alice", username="alice", roles=["argus-user"])
        c = TestClient(app)

        def cancel(i):
            r = c.post(f"/api/v1/changes/{i}/cancel")
            return r.status_code, r.json().get("detail")

        for i, st in ((1, "APPLIED"), (2, "CANCELLED"), (3, "REJECTED")):
            assert cancel(i) == (409, f"취소할 수 없는 상태입니다 (현재={st})."), i
        assert sent == []                      # 종료된 CR 에는 Signal 을 보내지 않는다
        for i in (4, 5, 6):
            assert cancel(i)[0] == 202, i
        assert sent == ["wf-4", "wf-5", "wf-6"]
        assert cancel(7) == (409, "취소할 수 없는 상태입니다 — 워크플로우가 이미 종료되었습니다.")
        assert cancel(8) == (503, "Temporal 워크플로우 엔진 호출에 실패했습니다.")
        # DRAFT(워크플로우 없음) — DB 에서 바로 취소(예전 404, CHG-T12). Temporal 없어도 된다
        connected["v"] = False
        r = c.post("/api/v1/changes/9/cancel")
        assert (r.status_code, r.json()) == (202, {"accepted": True, "status": "CANCELLED"}) and drafts == [9]
        assert cancel(10) == (409, "변경 요청 상태가 바뀌었습니다. 새로고침 후 다시 시도하세요.")
        assert cancel(5)[0] == 503             # 상신 후 CR 은 Temporal 필요
        # 상신 전에 취소된 CR 재취소 — 409 (예전 404 "…워크플로우…찾을 수 없습니다", CHG-T12). Temporal 불필요
        assert cancel(11) == (409, "취소할 수 없는 상태입니다 (현재=CANCELLED).")
        connected["v"] = True
        assert cancel(999)[0] == 404
    finally:
        (service.get_cr, service.cancel_cr, service.cancel_draft_cr, temporal_client.is_connected,
         temporal_client.ensure_connected) = saved


# ---------------------------------------------------------------------------
# 서비스 — DRAFT 취소는 조건부 UPDATE (in-memory SQLite)
# ---------------------------------------------------------------------------

async def _run_cancel_draft_db() -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.catalog.models as cm
    from app.change_mgmt import models as chm

    def with_fk_targets(tables):
        seen, todo = {}, list(tables)
        while todo:
            t = todo.pop()
            if t.name not in seen:
                seen[t.name] = t
                todo.extend(fk.column.table for fk in t.foreign_keys)
        return list(seen.values())

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(
            sc, tables=with_fk_targets([chm.ChangeRequest.__table__])))
    make_db = async_sessionmaker(engine, expire_on_commit=False)
    async with make_db() as db:
        ds_src = cm.Datasource(datasource_id="m", name="m", type="mysql")
        db.add(ds_src)
        await db.flush()
        ds = cm.Dataset(urn="m.db.t.dataset", name="db.t", datasource_id=ds_src.id, origin="DEV",
                        qualified_name="m.db.t", status="active")
        db.add(ds)
        await db.flush()
        base = dict(dataset_id=ds.id, change_type="COSMETIC", rollback_plan="r",
                    business_justification="b", requested_by="alice")
        draft = chm.ChangeRequest(cr_code="CR-1", title="d", status="DRAFT", **base)
        submitted = chm.ChangeRequest(cr_code="CR-2", title="s", status="SUBMITTED", workflow_id="wf", **base)
        db.add_all([draft, submitted])
        await db.commit()

        assert await service.cancel_draft_cr(db, draft.id) is True
        await db.commit()
        assert await service.cancel_draft_cr(db, submitted.id) is False   # 상신됨 — 그대로
        assert await service.cancel_draft_cr(db, draft.id) is False       # 이미 취소됨
        await db.commit()
        status = {i: (await db.execute(select(chm.ChangeRequest.status).where(chm.ChangeRequest.id == i))).scalar()
                  for i in (draft.id, submitted.id)}
        assert status == {draft.id: "CANCELLED", submitted.id: "SUBMITTED"}
    await engine.dispose()


def test_cancel_draft_db() -> None:
    asyncio.run(_run_cancel_draft_db())


# ---------------------------------------------------------------------------
# 워크플로우 — 적용 전 어느 단계에서든 취소 반영
# ---------------------------------------------------------------------------

_log: list[tuple] = []


@activity.defn(name="analyze_impact")
async def _analyze_impact(cr_id: int) -> None:
    _log.append(("analyze", cr_id))


@activity.defn(name="update_cr_status")
async def _update_cr_status(cr_id: int, status: str) -> None:
    _log.append(("status", cr_id, status))


@activity.defn(name="record_decision")
async def _record_decision(cr_id: int, step: int, decision: str, comment, by) -> None:
    _log.append(("decision", cr_id, step, decision))


@activity.defn(name="apply_change")
async def _apply_change(inp: ApplyInput) -> None:
    _log.append(("apply", inp.cr_id))


@activity.defn(name="send_notifications")
async def _send_notifications(inp: NotifyInput) -> None:
    _log.append(("notify", inp.cr_id, inp.stage))


async def _run_workflow_cases() -> None:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    try:
        env = await WorkflowEnvironment.start_time_skipping()
    except Exception as e:  # 테스트 서버 바이너리를 받을 수 없는 환경
        pytest.skip(f"Temporal 테스트 서버 사용 불가: {e}")
    async with env:
        tq = f"cr-cancel-{uuid.uuid4()}"
        acts = [_analyze_impact, _update_cr_status, _record_decision, _apply_change, _send_notifications]
        async with Worker(env.client, task_queue=tq, workflows=[ChangeApprovalWorkflow], activities=acts):

            async def start(cr_id, chain, scheduled=None):
                return await env.client.start_workflow(
                    ChangeApprovalWorkflow.run,
                    WorkflowInput(cr_id=cr_id, approver_chain=chain, scheduled_at_iso=scheduled),
                    id=f"cr-{cr_id}-{uuid.uuid4()}", task_queue=tq,
                )

            # A) 승인 후 적용 예정 시각(3일 뒤) 대기 중 취소 → CANCELLED, 적용 안 함
            sched = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
            h = await start(1, ["admin"], sched)
            await h.signal(ChangeApprovalWorkflow.submit_decision, DecisionSignal(1, "APPROVED", None, "admin"))
            for _ in range(100):  # APPROVED 상태(대기 진입)까지
                if ("status", 1, "APPROVED") in _log:
                    break
                await asyncio.sleep(0.05)
            await h.signal(ChangeApprovalWorkflow.cancel)
            assert await h.result() == "CANCELLED"
            assert ("apply", 1) not in _log and ("status", 1, "CANCELLED") in _log

            # B) 결재선 없는 CR — 시작과 동시에 취소해도 반영
            h = await start(2, [], sched)
            await h.signal(ChangeApprovalWorkflow.cancel)
            assert await h.result() == "CANCELLED"
            assert ("apply", 2) not in _log

            # C) 취소 없으면 예정 시각 뒤 적용(시간 건너뛰기) — 기존 동작 유지
            h = await start(3, ["admin"], sched)
            await h.signal(ChangeApprovalWorkflow.submit_decision, DecisionSignal(1, "APPROVED", None, "admin"))
            assert await h.result() == "APPLIED"
            assert ("apply", 3) in _log
            assert ("notify", 3, "T_MINUS_1") in _log


def test_cancel_workflow_before_apply() -> None:
    asyncio.run(_run_workflow_cases())


if __name__ == "__main__":
    test_cancel_router_state()
    test_cancel_draft_db()
    test_cancel_workflow_before_apply()
    print("test_cr_cancel_state OK")
