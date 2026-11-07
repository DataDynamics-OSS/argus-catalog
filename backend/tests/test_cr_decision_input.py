# SPDX-License-Identifier: Apache-2.0
"""결재 결정값 — ``PENDING`` 은 422 (자기완결형, 스키마 + 라우터 스텁).

예전에는 ``ApprovalDecisionRequest.decision`` 이 ``Decision`` enum 전체를 받아 ``PENDING`` 이
202 로 접수됐다. 워크플로우는 반려가 아니면 다음 단계로 넘어가는데 DB 의 단계는 PENDING 으로
남아, 이후 승인 Signal 을 워크플로우가 받지 못하고 CR 이 APPROVING 에서 멈췄다(IT CHG-T18).

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_cr_decision_input
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.change_mgmt import router as cr_router
from app.change_mgmt import schemas, service, temporal_client
from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session
from app.permissions import router as perm


def test_decision_input_schema() -> None:
    for d in ("APPROVED", "REJECTED", "DELEGATED"):
        assert schemas.ApprovalDecisionRequest(decision=d).decision.value == d
    for bad in ("PENDING", "OK", "", None):
        with pytest.raises(ValidationError):
            schemas.ApprovalDecisionRequest(decision=bad)
    # 응답(단계 상태)에는 여전히 PENDING 이 있다
    assert schemas.Decision.PENDING.value == "PENDING"


def test_decision_pending_rejected_before_signal() -> None:
    signals: list[str] = []

    async def get_cr(_s, _id):
        return SimpleNamespace(id=1, status="APPROVING", workflow_id="wf-1")

    async def list_steps(_s, _id):
        return [SimpleNamespace(step_order=1, approver="admin", decision="PENDING")]

    async def signal(_wf, _step, decision, _c, _by):
        signals.append(decision)

    async def allow(*_a, **_k):
        return True

    async def _session():
        yield SimpleNamespace()

    saved = (service.get_cr, service.list_approval_steps, service.submit_decision_signal,
             temporal_client.is_connected, perm.check_feature)
    service.get_cr, service.list_approval_steps, service.submit_decision_signal = get_cr, list_steps, signal
    temporal_client.is_connected = lambda: True
    perm.check_feature = allow
    try:
        app = FastAPI()
        app.include_router(cr_router.router, prefix="/api/v1")
        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_current_user] = lambda: TokenUser(sub="admin", username="admin", roles=["argus-user"])
        c = TestClient(app)
        assert c.post("/api/v1/changes/1/decisions", json={"decision": "PENDING"}).status_code == 422
        assert signals == []   # 워크플로우에 아무것도 보내지 않는다
        assert c.post("/api/v1/changes/1/decisions", json={"decision": "APPROVED"}).status_code == 202
        assert signals == ["APPROVED"]
    finally:
        (service.get_cr, service.list_approval_steps, service.submit_decision_signal,
         temporal_client.is_connected, perm.check_feature) = saved


if __name__ == "__main__":
    test_decision_input_schema()
    test_decision_pending_rejected_before_signal()
    print("test_cr_decision_input OK")
