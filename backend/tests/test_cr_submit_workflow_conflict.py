# SPDX-License-Identifier: Apache-2.0
"""DEF-187 — 코드가 재사용된 CR 의 상신: 고아 워크플로우 정리, 그 밖의 충돌은 409 (500 아님).

CR 코드는 "올해 가장 큰 번호 + 1" 이라 마지막 CR 이 데이터셋 삭제로 연쇄 삭제되면 번호를 다시 쓴다.
지워진 CR 의 워크플로우(``cr-<코드>``)가 실행 중으로 남아 있으면 예전에는 ``start_workflow`` 의
``WorkflowAlreadyStartedError`` 가 처리되지 않아 HTTP 500 이었다.

- 실제 Temporal 테스트 서버: 워커 없는 큐의 고아 워크플로우(취소 Signal 만 받은 채 실행 중)를 끝내고
  새로 기동, 이 CR 자신 · 살아 있는 다른 CR 의 워크플로우는 끝내지 않고 ``WorkflowIdConflictError``
- 스텁: 주인 판별 분기, 라우터의 409, 삭제 경로의 강제 종료

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_cr_submit_workflow_conflict
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.change_mgmt import router as cr_router
from app.change_mgmt import service, temporal_client
from app.change_mgmt.workflow import ChangeApprovalWorkflow, WorkflowInput
from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session
from tests.test_change_mgmt_remaining_fixes import _cr, _make_db, _seed_dataset

# ---------------------------------------------------------------------------
# 실제 Temporal 테스트 서버 — 이력의 기동 입력으로 주인을 판별한다
# ---------------------------------------------------------------------------

async def _run_real_server(monkeypatch) -> None:
    from temporalio.testing import WorkflowEnvironment

    try:
        env = await WorkflowEnvironment.start_time_skipping()
    except Exception as e:  # 테스트 서버 바이너리를 받을 수 없는 환경
        pytest.skip(f"Temporal 테스트 서버 사용 불가: {e}")
    engine, factory = await _make_db()
    tq = f"cr-conflict-{uuid.uuid4()}"   # 워커 없는 큐 — 워크플로우는 실행 중으로 남는다
    monkeypatch.setattr(temporal_client, "get_client", lambda: env.client)
    monkeypatch.setattr(temporal_client, "TASK_QUEUE", tq)

    async def start(code: str, cr_id: int):
        h = await env.client.start_workflow(
            ChangeApprovalWorkflow.run, WorkflowInput(cr_id=cr_id, approver_chain=["carol"]),
            id=f"cr-{code}", task_queue=tq)
        # 실행(run)을 고정한 핸들 — 같은 ID 로 새 실행이 생겨도 이 실행의 상태를 본다
        return env.client.get_workflow_handle(h.id, run_id=h.result_run_id)

    async def status(handle):
        return (await handle.describe()).status

    try:
        async with env, factory() as db:
            ds = await _seed_dataset(db)
            suffix = uuid.uuid4().hex[:8]

            # 1) 고아 — 삭제된 CR(id 9999)의 워크플로우. 취소 Signal 은 워커가 없어 처리되지 않는다
            code = f"CR-ORPHAN-{suffix}"
            orphan = await start(code, 9999)
            await orphan.signal("cancel")
            assert await status(orphan) == WorkflowExecutionStatus.RUNNING
            cr = _cr(ds, code)
            db.add(cr)
            await db.commit()
            assert await service.submit_cr(db, cr, ["EMAIL"]) == f"cr-{code}"
            assert (cr.status, cr.workflow_id) == ("SUBMITTED", f"cr-{code}")
            assert await status(orphan) == WorkflowExecutionStatus.TERMINATED
            desc = await env.client.get_workflow_handle(f"cr-{code}").describe()
            assert desc.status == WorkflowExecutionStatus.RUNNING and desc.run_id != orphan.run_id
            current = env.client.get_workflow_handle(f"cr-{code}", run_id=desc.run_id)
            assert await service._running_workflow_owner(env.client, f"cr-{code}") == (desc.run_id, cr.id)

            # 2) 이 CR 자신의 워크플로우(동시 상신) — 끝내지 않고 409 안내
            with pytest.raises(service.WorkflowIdConflictError, match="이미 상신 처리 중"):
                await service.submit_cr(db, cr, ["EMAIL"])
            assert await status(current) == WorkflowExecutionStatus.RUNNING

            # 3) 살아 있는 다른 CR 의 워크플로우 — 끝내지 않고 409 안내
            other = _cr(ds, f"CR-ALIVE-{suffix}", "APPROVING")
            code3 = f"CR-TAKEN-{suffix}"
            cr3 = _cr(ds, code3)
            db.add_all([other, cr3])
            await db.commit()
            taken = await start(code3, other.id)
            with pytest.raises(service.WorkflowIdConflictError, match=f"cr-{code3}"):
                await service.submit_cr(db, cr3, ["EMAIL"])
            assert await status(taken) == WorkflowExecutionStatus.RUNNING
            assert (cr3.status, cr3.workflow_id) == ("DRAFT", None)

            # 4) 끝난 워크플로우의 ID 는 그대로 다시 쓴다(정리 없음)
            await taken.terminate()
            assert await service.submit_cr(db, cr3, ["EMAIL"]) == f"cr-{code3}"

            # 삭제 경로 — 취소 Signal 이 아니라 강제 종료라 워커 없이도 끝난다
            monkeypatch.setattr(temporal_client, "is_connected", lambda: True)
            assert await service.cancel_workflows_best_effort([f"cr-{code}", "cr-없는-워크플로우"]) == 1
            assert await status(current) == WorkflowExecutionStatus.TERMINATED
    finally:
        await engine.dispose()


def test_submit_clears_orphan_workflow_only(monkeypatch) -> None:
    asyncio.run(_run_real_server(monkeypatch))


# ---------------------------------------------------------------------------
# 스텁 — 주인 판별 분기 (테스트 서버 없이도 돈다)
# ---------------------------------------------------------------------------

class _Client:
    """``start_workflow`` 가 처음 ``conflicts`` 번은 이미 실행 중 오류를 내는 대역."""

    def __init__(self, conflicts: int = 1) -> None:
        self.conflicts = conflicts
        self.started: list[str] = []
        self.terminated: list[tuple[str, str | None, str | None]] = []

    async def start_workflow(self, _run, _input, *, id, task_queue):
        if self.conflicts > 0:
            self.conflicts -= 1
            raise WorkflowAlreadyStartedError(id, "ChangeApprovalWorkflow")
        self.started.append(id)

    def get_workflow_handle(self, workflow_id, *, run_id=None):
        async def terminate(*, reason=None):
            self.terminated.append((workflow_id, run_id, reason))

        return SimpleNamespace(terminate=terminate)


async def _run_branches(monkeypatch) -> None:
    engine, factory = await _make_db()
    try:
        async with factory() as db:
            ds = await _seed_dataset(db)
            cr, other = _cr(ds, "CR-1"), _cr(ds, "CR-2", "APPROVING", "cr-CR-2")
            db.add_all([cr, other])
            await db.commit()

            async def submit(owner, conflicts=1):
                client = _Client(conflicts)

                async def running_owner(_client, _wf):
                    return owner

                monkeypatch.setattr(temporal_client, "get_client", lambda: client)
                monkeypatch.setattr(service, "_running_workflow_owner", running_owner)
                cr.status, cr.workflow_id = "DRAFT", None
                try:
                    await service.submit_cr(db, cr, ["EMAIL"])
                except service.WorkflowIdConflictError as e:
                    return client, str(e)
                return client, None

            # 삭제된 CR 의 워크플로우 — 확인한 그 실행(run)만 끝내고 새로 기동
            client, err = await submit(("run-1", 9999))
            assert err is None and client.started == ["cr-CR-1"]
            assert client.terminated == [("cr-CR-1", "run-1", service.ORPHAN_TERMINATE_REASON)]
            assert (cr.status, cr.workflow_id) == ("SUBMITTED", "cr-CR-1")

            # 그 사이 끝난 워크플로우 — 정리 없이 다시 기동
            client, err = await submit(None)
            assert err is None and client.started == ["cr-CR-1"] and client.terminated == []

            # 이 CR 자신 · 살아 있는 다른 CR · 주인을 알 수 없음 — 끝내지 않고 안내, CR 은 DRAFT 그대로
            for owner, text in ((("run-1", cr.id), "이미 상신 처리 중"),
                                (("run-1", other.id), "이미 실행 중이라 상신할 수 없습니다"),
                                (("run-1", None), "이미 실행 중이라 상신할 수 없습니다")):
                client, err = await submit(owner)
                assert text in err, (owner, err)
                assert client.terminated == [] and client.started == []
                assert (cr.status, cr.workflow_id) == ("DRAFT", None)

            # 정리한 직후 다른 요청이 먼저 기동 — 다시 충돌하면 안내(500 아님)
            client, err = await submit(("run-1", 9999), conflicts=2)
            assert "이미 상신 처리 중" in err and client.started == []
    finally:
        await engine.dispose()


def test_submit_conflict_branches(monkeypatch) -> None:
    asyncio.run(_run_branches(monkeypatch))


def test_submit_conflict_is_409(monkeypatch) -> None:
    cr = SimpleNamespace(id=1, cr_code="CR-1", requested_by="alice", status="DRAFT", workflow_id=None, dataset_id=10)
    rolled = []

    async def get_cr(_s, _id):
        return cr

    async def submit_cr(_s, _cr, _ch):
        raise service.WorkflowIdConflictError("이미 상신 처리 중인 변경 요청입니다.")

    async def list_steps(_s, _cr_id):
        return [SimpleNamespace(approver="carol")]

    async def connected():
        return True

    class _Sess:
        async def commit(self):
            raise AssertionError("실패한 상신은 커밋하지 않는다")

        async def rollback(self):
            rolled.append(True)

    async def _session():
        yield _Sess()

    monkeypatch.setattr(service, "get_cr", get_cr)
    monkeypatch.setattr(service, "submit_cr", submit_cr)
    monkeypatch.setattr(service, "list_approval_steps", list_steps)
    monkeypatch.setattr(temporal_client, "ensure_connected", connected)

    app = FastAPI()
    app.include_router(cr_router.router, prefix="/api/v1")
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: TokenUser(sub="alice", username="alice", roles=["argus-user"])
    r = TestClient(app).post("/api/v1/changes/1/submit", json=["EMAIL"])
    assert (r.status_code, r.json()["detail"]) == (409, "이미 상신 처리 중인 변경 요청입니다.")
    assert rolled == [True] and cr.status == "DRAFT"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
