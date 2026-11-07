# SPDX-License-Identifier: Apache-2.0
"""Temporal 장애 — 상신 503(500 아님) + 기동 후 지연 재연결 (IT CHG-T16, 자기완결형 스텁).

예전에는
- 연결을 맺은 뒤 Temporal 이 내려가면 ``start_workflow`` 의 RPC 오류가 처리되지 않아 HTTP 500.
- 백엔드가 Temporal 없이 기동되면 기동 시 한 번만 연결해, 재시작 전까지 계속 503.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_cr_temporal_down
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from temporalio.service import RPCError, RPCStatusCode

from app.change_mgmt import router as cr_router
from app.change_mgmt import service, temporal_client
from app.core.auth import TokenUser, get_current_user
from app.core.config import settings
from app.core.database import get_session


def test_submit_rpc_error_is_503(monkeypatch) -> None:
    cr = SimpleNamespace(id=1, cr_code="CR-1", requested_by="alice", status="DRAFT", workflow_id=None)
    rolled = []

    async def get_cr(_s, _id):
        return cr

    async def submit_cr(_s, _cr, _ch):
        raise RPCError("connection refused", RPCStatusCode.UNAVAILABLE, b"")

    class _Sess:
        async def commit(self):
            raise AssertionError("실패한 상신은 커밋하지 않는다")

        async def rollback(self):
            rolled.append(True)

    async def _session():
        yield _Sess()

    async def connected():
        return True

    monkeypatch.setattr(service, "get_cr", get_cr)
    monkeypatch.setattr(service, "submit_cr", submit_cr)
    monkeypatch.setattr(temporal_client, "ensure_connected", connected)

    app = FastAPI()
    app.include_router(cr_router.router, prefix="/api/v1")
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: TokenUser(sub="alice", username="alice", roles=["argus-user"])
    r = TestClient(app).post("/api/v1/changes/1/submit", json=["IN_APP"])
    assert (r.status_code, r.json()["detail"]) == (503, "Temporal 워크플로우 엔진 호출에 실패했습니다.")
    assert rolled == [True] and cr.status == "DRAFT"


def test_lazy_reconnect(monkeypatch) -> None:
    """기동 때 연결 실패 → 다음 요청에서 재연결(간격 제한), temporal.enabled=false 면 시도하지 않음."""
    attempts: list[int] = []
    up = {"v": False}

    class _Client:
        pass

    async def fake_connect(target, namespace=None):
        attempts.append(1)
        if not up["v"]:
            raise RuntimeError("connection refused")
        return _Client()

    monkeypatch.setattr(temporal_client, "_client", None)
    monkeypatch.setattr(temporal_client, "_last_attempt", None)
    monkeypatch.setattr(temporal_client.Client, "connect", staticmethod(fake_connect))
    monkeypatch.setattr(settings, "temporal_enabled", True)
    clock = {"t": 1000.0}
    monkeypatch.setattr(temporal_client.time, "monotonic", lambda: clock["t"])

    async def run():
        assert await temporal_client.ensure_connected() is False      # 1회 시도, 실패
        assert await temporal_client.ensure_connected() is False      # 간격 안 — 시도 안 함
        assert len(attempts) == 1
        up["v"] = True
        clock["t"] += temporal_client.RECONNECT_INTERVAL_SECONDS + 1
        assert await temporal_client.ensure_connected() is True       # 재연결
        assert len(attempts) == 2 and temporal_client.is_connected()
        assert await temporal_client.ensure_connected() is True       # 이미 연결 — 시도 안 함
        assert len(attempts) == 2

    asyncio.run(run())

    # 비활성화면 연결을 시도하지 않는다
    monkeypatch.setattr(temporal_client, "_client", None)
    monkeypatch.setattr(temporal_client, "_last_attempt", None)
    monkeypatch.setattr(settings, "temporal_enabled", False)
    assert asyncio.run(temporal_client.ensure_connected()) is False
    assert len(attempts) == 2


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
