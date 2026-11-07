# SPDX-License-Identifier: Apache-2.0
"""결재 결정은 결재 진행 중(APPROVING)인 CR 에만 — 그 밖의 상태(DRAFT·종료)는 409, 없는 CR 만 404 (자기완결형, DB·Temporal 스텁).

예전에는 반려·취소로 끝난 CR 에 남은 PENDING 단계를 현재 차례로 보고 종료된 워크플로우에
Signal 을 보내다 HTTP 500 이 났다(IT CHG-T17).

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_cr_decision_state
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from temporalio.service import RPCError, RPCStatusCode

from app.change_mgmt import router as cr_router
from app.change_mgmt import service, temporal_client
from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session
from app.permissions import router as perm


def _step(order, approver, decision="PENDING"):
    return SimpleNamespace(step_order=order, approver=approver, decision=decision)


def test_cr_decision_state() -> None:
    crs = {
        # 반려: 1단계 REJECTED, 2단계 PENDING 이 남는다
        1: (SimpleNamespace(id=1, status="REJECTED", workflow_id="wf-1"), [_step(1, "admin", "REJECTED"), _step(2, "mjkim")]),
        # 취소: 1단계 PENDING 이 남는다
        2: (SimpleNamespace(id=2, status="CANCELLED", workflow_id="wf-2"), [_step(1, "mjkim")]),
        # 적용 완료: 모든 단계 결정됨 → 409 (예전 400)
        3: (SimpleNamespace(id=3, status="APPLIED", workflow_id="wf-3"), [_step(1, "mjkim", "APPROVED")]),
        # 결재 중
        4: (SimpleNamespace(id=4, status="APPROVING", workflow_id="wf-4"), [_step(1, "mjkim")]),
        # 결재 중이지만 워크플로우는 이미 종료(DB 갱신 전 틈)
        5: (SimpleNamespace(id=5, status="APPROVING", workflow_id="wf-gone"), [_step(1, "mjkim")]),
        # 결재 중, Temporal 장애
        6: (SimpleNamespace(id=6, status="APPROVING", workflow_id="wf-down"), [_step(1, "mjkim")]),
        # 상신 전: 워크플로우 없음, 1단계 PENDING
        8: (SimpleNamespace(id=8, status="DRAFT", workflow_id=None), [_step(1, "mjkim")]),
    }
    signals: list[tuple[str, int, str]] = []

    async def get_cr(_s, cr_id):
        return crs[cr_id][0] if cr_id in crs else None

    async def list_approval_steps(_s, cr_id):
        return crs[cr_id][1]

    async def submit_decision_signal(workflow_id, step_order, decision, _comment, _by):
        if workflow_id == "wf-gone":
            raise RPCError("workflow execution already completed", RPCStatusCode.NOT_FOUND, b"")
        if workflow_id == "wf-down":
            raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
        signals.append((workflow_id, step_order, decision))

    async def allow(*_a, **_k):
        return True

    async def _session():
        yield SimpleNamespace()

    saved = (service.get_cr, service.list_approval_steps, service.submit_decision_signal,
             temporal_client.is_connected, perm.check_feature)
    service.get_cr, service.list_approval_steps = get_cr, list_approval_steps
    service.submit_decision_signal = submit_decision_signal
    temporal_client.is_connected = lambda: True
    perm.check_feature = allow
    try:
        app = FastAPI()
        app.include_router(cr_router.router, prefix="/api/v1")
        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_current_user] = lambda: TokenUser(sub="mjkim", username="mjkim", roles=["argus-user"])
        c = TestClient(app)

        def decide(cr_id, decision="APPROVED"):
            r = c.post(f"/api/v1/changes/{cr_id}/decisions", json={"decision": decision})
            return r.status_code, r.json().get("detail")

        msg = "결재할 수 없는 상태입니다 — 결재 진행 중(APPROVING)인 변경 요청만 결재할 수 있습니다 (현재={})."
        assert decide(1) == (409, msg.format("REJECTED"))
        assert decide(2) == (409, msg.format("CANCELLED"))
        # 적용 완료 — 예전에는 400 "결재 대기 중인 단계가 없습니다." 였다. 있는 CR 의 비결재 상태는 모두 409
        assert decide(3) == (409, msg.format("APPLIED"))
        # 상신 전(DRAFT, 워크플로우 없음) — 예전에는 404 "…워크플로우을(를) 찾을 수 없습니다." 였다(CHG-T17)
        assert decide(8) == (409, msg.format("DRAFT"))
        assert signals == []   # 종료된 CR 에는 Signal 을 보내지 않는다

        assert decide(4)[0] == 202
        assert signals == [("wf-4", 1, "APPROVED")]

        assert decide(5) == (409, "결재를 받을 수 없는 상태입니다 — 워크플로우가 이미 종료되었습니다.")
        assert decide(6) == (503, "Temporal 워크플로우 엔진 호출에 실패했습니다.")
        assert decide(999) == (404, "변경 요청을(를) 찾을 수 없습니다.")   # 없는 CR 만 404

        # 결재 차례가 아닌 사용자 — 기존대로 403 (상태 검사 통과 후)
        crs[7] = (SimpleNamespace(id=7, status="APPROVING", workflow_id="wf-7"), [_step(1, "admin")])
        assert decide(7) == (403, "현재 결재 차례가 아닙니다.")
    finally:
        (service.get_cr, service.list_approval_steps, service.submit_decision_signal,
         temporal_client.is_connected, perm.check_feature) = saved


if __name__ == "__main__":
    test_cr_decision_state()
    print("test_cr_decision_state OK")
