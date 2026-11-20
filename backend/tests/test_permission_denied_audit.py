# SPDX-License-Identifier: Apache-2.0
"""권한 거부 감사 범위 (DEF-159, 자기완결형, in-memory SQLite).

확인하는 규칙:
- 메뉴 · 기능 가드 밖의 403 — 관리자 전용(``require_admin``), 소유자 확인(``assert_owner_or_admin``), 데이터셋 직접 등록
  (``require_dataset_create``), 사용자 관리 · API 키 위임 범위 가드 — 도 같은 형식(``permission.denied``)으로 남는다.
- 관리자 전용 · 소유자 · 위임 범위는 사용자 · 권한 · 경로마다 1분에 한 번 남는다.
- 호출 측이 잡아 다른 길로 처리한 거부는 남지 않고, 감사 실패가 403 응답을 바꾸지 않는다.

실행:
    cd backend
    pytest tests/test_permission_denied_audit.py
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.audit import service as audit_service
from app.audit.models import AdminAuditLog
from app.core.auth import PermissionDenied, TokenUser, assert_owner_or_admin, get_current_user, get_optional_user
from app.core.database import Base, get_session
from app.main import http_exception_handler
from app.permissions import router as perm
from app.permissions.models import Permission
from app.settings.models import CatalogConfiguration
from app.usermgr.models import ArgusRole, ArgusUser

ADMIN = TokenUser(sub="a", username="admin", roles=["argus-admin"])
SUPER = TokenUser(sub="s", username="sam", roles=["argus-superuser"])
ALICE = TokenUser(sub="alice", username="alice", roles=["argus-user"])


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[
            Permission.__table__, CatalogConfiguration.__table__, ArgusRole.__table__, ArgusUser.__table__,
            AdminAuditLog.__table__,
        ])
    make = async_sessionmaker(engine, expire_on_commit=False)
    perm._menu_cache.clear()
    perm._deny_seen.clear()
    audit_service.audit_session_factory = make
    yield make
    audit_service.audit_session_factory = None
    perm._menu_cache.clear()
    perm._deny_seen.clear()
    await engine.dispose()


def _client(make, user_box: dict, *routers) -> httpx.AsyncClient:
    """운영 앱과 같은 HTTP 예외 처리기(:func:`app.main.http_exception_handler`)를 단 시험용 앱."""
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    for r in routers:
        app.include_router(r, prefix="/api/v1")

    async def _session():
        async with make() as db:
            yield db

    def _current():
        if user_box["user"] is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        return user_box["user"]

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _current
    app.dependency_overrides[get_optional_user] = lambda: user_box["user"]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _denials(make) -> list[AdminAuditLog]:
    async with make() as s:
        return list((await s.execute(
            select(AdminAuditLog).where(AdminAuditLog.action == "permission.denied").order_by(AdminAuditLog.id)
        )).scalars())


def _owner_router() -> APIRouter:
    router = APIRouter()

    @router.delete("/things/{thing_id}")
    async def _delete(thing_id: int, current=Depends(get_current_user)):
        assert_owner_or_admin(current, "bob", "데이터셋")  # 동기 가드 — 세션 · 요청 없이 거부
        return {"ok": thing_id}

    @router.post("/things/{thing_id}/fallback")
    async def _fallback(thing_id: int, current=Depends(get_current_user)):
        try:
            assert_owner_or_admin(current, "bob", "데이터셋")
        except PermissionDenied:
            return {"ok": False}  # 호출 측이 잡아 다른 길로 처리 — 거부가 아니다
        return {"ok": True}

    @router.post("/datasets", dependencies=[Depends(perm.require_dataset_create)])
    async def _create():
        return {"ok": True}

    return router


async def test_admin_only_denial_is_audited_per_path(factory):
    box = {"user": ALICE}
    async with _client(factory, box, perm.router) as c:
        assert (await c.get("/api/v1/permissions/menus")).status_code == 403
        assert (await c.get("/api/v1/permissions/menus")).status_code == 403  # 1분 안 같은 경로 — 한 번만
        assert (await c.get("/api/v1/permissions/features")).status_code == 403
        box["user"] = ADMIN
        assert (await c.get("/api/v1/permissions/menus")).status_code == 200
    rows = await _denials(factory)
    assert [(r.actor, r.category, r.target) for r in rows] == [("alice", "permissions", "관리자 전용")] * 2
    details = [json.loads(r.detail) for r in rows]
    assert [d["path"] for d in details] == ["/api/v1/permissions/menus", "/api/v1/permissions/features"]
    assert details[0]["kind"] == "ADMIN" and details[0]["role"] == "argus-user"
    assert rows[0].summary == "권한 거부(관리자 전용): 관리자 전용 — /api/v1/permissions/menus"


async def test_owner_and_dataset_create_denials_are_audited(factory):
    box = {"user": ALICE}
    async with _client(factory, box, _owner_router()) as c:
        r = await c.delete("/api/v1/things/1")
        assert r.status_code == 403 and "소유자 또는 관리자" in r.json()["detail"]
        assert (await c.post("/api/v1/things/1/fallback")).json() == {"ok": False}
        r = await c.post("/api/v1/datasets")
        assert r.status_code == 403 and r.json()["detail"] == perm.DATASET_CREATE_DISABLED_DETAIL
    rows = await _denials(factory)
    assert [(r.target, json.loads(r.detail)["kind"]) for r in rows] == [
        ("데이터셋 소유자", "OWNER"), ("datasets.create", "FEATURE"),
    ]


async def test_user_delegate_escalation_is_audited(factory, monkeypatch):
    from app.usermgr import router as um

    monkeypatch.setattr(um.settings, "auth_type", "local")
    async with factory() as s:
        s.add(Permission(kind="FEATURE", perm_key="users.manage", role_id="argus-superuser"))
        await s.commit()
    body = {"username": "boss", "email": "b@x.io", "first_name": "b", "last_name": "b",
            "password": "Passw0rd!x", "role": "argus-admin"}
    box = {"user": SUPER}
    async with _client(factory, box, um.router) as c:
        r = await c.post("/api/v1/usermgr/users", json=body)
        assert (r.status_code, r.json()["detail"]) == (403, um.ADMIN_TARGET_DETAIL)
        box["user"] = ALICE  # 위임받지 않은 사람의 남의 프로필 수정
        assert (await c.put("/api/v1/usermgr/users/77", json={"phone_number": "010"})).status_code == 403
    rows = await _denials(factory)
    assert [(r.actor, r.target, json.loads(r.detail)["kind"]) for r in rows] == [
        ("sam", "users.manage — argus-admin 계정 생성", "DELEGATE"),
        ("alice", "users.manage", "FEATURE"),
    ]


async def test_api_key_delegate_denial_is_audited(factory):
    from app.apikeys import router as ak

    router = APIRouter()

    @router.post("/keys")
    async def _issue(current=Depends(get_current_user)):
        ak._forbid_admin_key(current, "argus-admin")
        return {"ok": True}

    async with _client(factory, {"user": SUPER}, router) as c:
        assert (await c.post("/api/v1/keys")).status_code == 403
    rows = await _denials(factory)
    assert [(r.target, json.loads(r.detail)["kind"]) for r in rows] == [
        ("settings.api-keys — argus-admin 키", "DELEGATE"),
    ]


async def test_audit_failure_keeps_403(factory, monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("audit down")

    monkeypatch.setattr(perm, "audit_denied", _boom)
    async with _client(factory, {"user": ALICE}, perm.router) as c:
        r = await c.get("/api/v1/permissions/menus")
    assert (r.status_code, r.json()["detail"]) == (403, "관리자 역할이 필요합니다.")
    assert await _denials(factory) == []
