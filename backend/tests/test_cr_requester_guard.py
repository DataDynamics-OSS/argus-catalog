# SPDX-License-Identifier: Apache-2.0
"""변경 요청 상신·취소는 요청자 본인 또는 관리자만 (자기완결형 — DB·Temporal 없이 서비스 스텁).

예전에는 두 엔드포인트가 로그인만 확인해 누구나 타인 CR 을 상신·취소할 수 있었다(IT CHG-T20).

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_cr_requester_guard
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.change_mgmt import router as cr_router
from app.change_mgmt import service, temporal_client
from app.core.auth import TokenUser, get_current_user
from app.core.config import settings
from app.core.database import get_session

OWNER_ONLY = "변경 요청의 소유자 또는 관리자만 수행할 수 있습니다."


def _tu(name: str, admin: bool = False) -> TokenUser:
    return TokenUser(sub=name, username=name, roles=[settings.auth_keycloak_admin_role] if admin else ["argus-user"])


def test_cr_requester_guard() -> None:
    crs = {
        1: SimpleNamespace(id=1, cr_code="CR-2026-0001", requested_by="alice", status="DRAFT", workflow_id=None),
        2: SimpleNamespace(id=2, cr_code="CR-2026-0002", requested_by="alice", status="APPROVING", workflow_id="wf-2"),
    }
    calls: list[tuple[str, int | str]] = []

    async def get_cr(_s, cr_id):
        return crs.get(cr_id)

    async def submit_cr(_s, cr, _channels):
        calls.append(("submit", cr.id))
        cr.status = "SUBMITTED"
        return f"wf-{cr.id}"

    async def cancel_cr(workflow_id):
        calls.append(("cancel", workflow_id))

    async def cancel_draft_cr(_s, cr_id):
        calls.append(("cancel-draft", cr_id))
        return True

    class _Sess:
        async def commit(self):
            pass

    async def _session():
        yield _Sess()

    who: dict = {"user": None}

    def _current():
        if who["user"] is None:
            raise HTTPException(status_code=401, detail="인증이 필요합니다.")
        return who["user"]

    saved = (service.get_cr, service.submit_cr, service.cancel_cr, service.cancel_draft_cr,
             temporal_client.is_connected)
    service.get_cr, service.submit_cr, service.cancel_cr = get_cr, submit_cr, cancel_cr
    service.cancel_draft_cr = cancel_draft_cr
    temporal_client.is_connected = lambda: True
    try:
        app = FastAPI()
        app.include_router(cr_router.router, prefix="/api/v1")
        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_current_user] = _current
        c = TestClient(app)

        def post(path):
            r = c.post(f"/api/v1/changes{path}", json=["IN_APP"] if path.endswith("submit") else None)
            return r.status_code, r.json().get("detail")

        # 비로그인 401
        assert post("/1/submit")[0] == 401 and post("/2/cancel")[0] == 401

        # 타인(bob) — 상신·취소 모두 403, 서비스 호출 없음
        who["user"] = _tu("bob")
        assert post("/1/submit") == (403, OWNER_ONLY)
        assert post("/2/cancel") == (403, OWNER_ONLY)
        assert calls == []

        # 없는 CR 은 권한 판단 전에 404
        assert post("/999/submit")[0] == 404 and post("/999/cancel")[0] == 404

        # 요청자 본인 — 상신 200, 취소 202
        who["user"] = _tu("alice")
        assert post("/1/submit")[0] == 200
        assert post("/2/cancel")[0] == 202
        assert calls == [("submit", 1), ("cancel", "wf-2")]

        # 관리자 — 타인 CR 도 취소 가능
        who["user"] = _tu("root", admin=True)
        assert post("/2/cancel")[0] == 202
        # DRAFT(워크플로우 없음) 취소 — 요청자는 바로 취소(202), 타인은 403
        crs[3] = SimpleNamespace(id=3, cr_code="CR-3", requested_by="alice", status="DRAFT", workflow_id=None)
        who["user"] = _tu("alice")
        assert post("/3/cancel")[0] == 202 and calls[-1] == ("cancel-draft", 3)
        who["user"] = _tu("bob")
        assert post("/3/cancel") == (403, OWNER_ONLY)
    finally:
        (service.get_cr, service.submit_cr, service.cancel_cr, service.cancel_draft_cr,
         temporal_client.is_connected) = saved


if __name__ == "__main__":
    test_cr_requester_guard()
    print("test_cr_requester_guard OK")
