# SPDX-License-Identifier: Apache-2.0
"""관리 기능 3단계(운영) 테스트 (in-memory SQLite, 자기완결형).

검증:
- 설정 비밀값 암호화: 저장은 enc: 암호문, 읽기는 평문, 평문 레거시 값 일괄 암호화(멱등),
  키가 바뀌어 복호화 못 하면 빈 값
- 감사 로그: 설정 변경(바뀐 키만, 비밀값은 값 없이), 사용자 관리(생성·수정·역할·비밀번호·
  비활성·삭제+이관, 전화번호 값 미기록), 권한 매트릭스 전후, 조회 API(관리자 전용·필터),
  기록 실패가 본 작업을 막지 않음, 요청자 IP

실행:
    cd backend
    pytest tests/test_admin_stage3.py
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.audit import router as audit_router
from app.audit import service as audit
from app.audit.models import AdminAuditLog
from app.core import passwords
from app.core.auth import TokenUser, get_current_user, get_optional_user
from app.core.config import settings
from app.core.database import Base, get_session
from app.permissions import router as perm
from app.permissions.models import Permission
from app.settings import service as ssvc
from app.settings.models import CatalogConfiguration
from app.usermgr import ownership
from app.usermgr import router as umr
from app.usermgr.models import ArgusRole, ArgusUser


@pytest.fixture(autouse=True)
def _fast_hash(monkeypatch):
    monkeypatch.setattr(passwords, "ITERATIONS", 1000)
    orig = passwords.hash_password
    monkeypatch.setattr(passwords, "hash_password", lambda p, *, iterations=1000: orig(p, iterations=iterations))
    monkeypatch.setattr(settings, "auth_type", "local")


@pytest.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    tables = [ArgusRole.__table__, ArgusUser.__table__, Permission.__table__, CatalogConfiguration.__table__,
              AdminAuditLog.__table__] + [a.model.__table__ for a in ownership._registry()]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    make = async_sessionmaker(engine, expire_on_commit=False)
    async with make() as s:
        for rid in ("argus-admin", "argus-superuser", "argus-user"):
            s.add(ArgusRole(role_id=rid, name=rid))
        await s.commit()
    monkeypatch.setattr(audit, "audit_session_factory", make)
    perm._menu_cache.clear()
    yield make
    await engine.dispose()


def tu(name: str, role: str = "argus-user", sub: str | None = None) -> TokenUser:
    return TokenUser(sub=sub or name, username=name, roles=[role])


def _app(factory, *routers) -> tuple[FastAPI, dict]:
    app = FastAPI()
    app.add_middleware(audit.ClientIpMiddleware)
    for r in routers:
        app.include_router(r, prefix="/api/v1")

    async def _session():
        async with factory() as db:
            yield db

    who: dict = {"user": None}

    def _current():
        if who["user"] is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        return who["user"]

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _current
    app.dependency_overrides[get_optional_user] = lambda: who["user"]
    return app, who


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _logs(make, category: str | None = None) -> list[AdminAuditLog]:
    async with make() as s:
        stmt = select(AdminAuditLog).order_by(AdminAuditLog.id)
        if category:
            stmt = stmt.where(AdminAuditLog.category == category)
        return list((await s.execute(stmt)).scalars().all())


async def _raw(make, key: str) -> str | None:
    async with make() as s:
        return (await s.execute(
            select(CatalogConfiguration.config_value).where(CatalogConfiguration.config_key == key)
        )).scalar()


# ---------------------------------------------------------------------------
# 설정 비밀값 암호화
# ---------------------------------------------------------------------------

async def test_secret_settings_stored_encrypted(factory):
    async with factory() as s:
        await ssvc.update_config(s, "llm", {"llm_api_key": "sk-live-123", "llm_model": "gpt-x"})
        cfg = await ssvc.get_config_by_category(s, "llm")
    raw = await _raw(factory, "llm_api_key")
    assert raw.startswith("enc:") and "sk-live-123" not in raw
    assert await _raw(factory, "llm_model") == "gpt-x"
    assert cfg["llm_api_key"] == "sk-live-123" and cfg["llm_model"] == "gpt-x"
    # 같은 값을 다시 저장해도 행을 바꾸지 않는다(암호문 재생성 없음)
    async with factory() as s:
        await ssvc.update_config(s, "llm", {"llm_api_key": "sk-live-123"})
    assert await _raw(factory, "llm_api_key") == raw


async def test_seal_legacy_plaintext_secrets(factory):
    async with factory() as s:
        s.add(CatalogConfiguration(category="mail", config_key="mail_smtp_password", config_value="plainpw"))
        s.add(CatalogConfiguration(category="mail", config_key="mail_smtp_host", config_value="smtp.x"))
        s.add(CatalogConfiguration(category="llm", config_key="llm_api_key", config_value=""))
        await s.commit()
        assert (await ssvc.get_config_by_category(s, "mail"))["mail_smtp_password"] == "plainpw"  # 레거시 평문 읽기
        assert await ssvc.seal_legacy_secrets(s) == 1
        assert await ssvc.seal_legacy_secrets(s) == 0  # 멱등
        assert (await ssvc.get_config_by_category(s, "mail"))["mail_smtp_password"] == "plainpw"
    assert (await _raw(factory, "mail_smtp_password")).startswith("enc:")
    assert await _raw(factory, "mail_smtp_host") == "smtp.x"
    assert await _raw(factory, "llm_api_key") == ""


async def test_unseal_with_wrong_key_returns_empty(factory, monkeypatch):
    sealed = ssvc.seal_value("llm_api_key", "sk-1")
    monkeypatch.setenv("ARGUS_SECRET_KEY", "another-secret-key-0123456789abcdef")
    assert ssvc.unseal_value("llm_api_key", sealed) == ""


# ---------------------------------------------------------------------------
# 감사 로그 — 설정
# ---------------------------------------------------------------------------

async def test_settings_change_audited_without_secret_values(factory):
    boss = tu("boss", "argus-admin")
    async with factory() as s:
        await ssvc.update_config(s, "llm", {"llm_api_key": "sk-old", "llm_model": "m1"}, actor=boss)
        await ssvc.update_config(s, "llm", {"llm_api_key": "sk-new", "llm_model": "m1"}, actor=boss)
        await ssvc.update_config(s, "llm", {"llm_api_key": "sk-new", "llm_model": "m1"}, actor=boss)  # 변화 없음
        await ssvc.update_config(s, "llm", {"llm_model": "m2"})  # actor 없음 — 기록 안 함
    logs = await _logs(factory, "settings")
    assert [entry.action for entry in logs] == ["settings.llm", "settings.llm"]
    second = json.loads(logs[1].detail)["changes"]
    assert list(second) == ["llm_api_key"]
    assert second["llm_api_key"] == {"old": "(변경됨)", "new": "(변경됨)"}
    assert all("sk-" not in (entry.detail or "") for entry in logs)
    assert logs[0].actor == "boss" and logs[0].target == "llm"


# ---------------------------------------------------------------------------
# 감사 로그 — 사용자 관리
# ---------------------------------------------------------------------------

async def test_usermgr_actions_audited(factory):
    from app.apis.models import CatalogApi

    app, who = _app(factory, umr.router)
    who["user"] = tu("boss", "argus-admin")
    async with factory() as s:
        rid = (await s.execute(select(ArgusRole.id).where(ArgusRole.role_id == "argus-admin"))).scalar_one()
        s.add(ArgusUser(username="boss", email="boss@ex.com", first_name="B", last_name="O",
                        password_hash="x", status="active", role_id=rid))
        await s.commit()
    async with _client(app) as c:
        r = await c.post("/api/v1/usermgr/users", json={
            "username": "kim", "email": "kim@ex.com", "first_name": "K", "last_name": "M",
            "password": "abcdefg1", "phone_number": "010-1111-2222"}, headers={"X-Forwarded-For": "10.1.2.3"})
        uid = r.json()["id"]
        await c.post("/api/v1/usermgr/users", json={
            "username": "heir", "email": "heir@ex.com", "first_name": "H", "last_name": "R", "password": "abcdefg1"})
        await c.put(f"/api/v1/usermgr/users/{uid}", json={"department": "DP", "phone_number": "010-3333-4444"})
        await c.put(f"/api/v1/usermgr/users/{uid}", json={"department": "DP"})  # 변화 없음
        await c.put(f"/api/v1/usermgr/users/{uid}/role", json={"role": "argus-superuser"})
        await c.put(f"/api/v1/usermgr/users/{uid}/password", json={"password": "newpass12"})
        await c.put(f"/api/v1/usermgr/users/{uid}/deactivate")
        await c.put(f"/api/v1/usermgr/users/{uid}/activate")
        async with factory() as s:
            s.add(CatalogApi(name="a1", urn="urn:a1", created_by="kim"))
            await s.commit()
        assert (await c.delete(f"/api/v1/usermgr/users/{uid}?transfer_to=heir")).status_code == 200

    logs = await _logs(factory, "users")
    assert [entry.action for entry in logs] == [
        "user.create", "user.create", "user.modify", "user.role", "user.password_reset",
        "user.deactivate", "user.activate", "user.delete",
    ]
    assert logs[0].client_ip == "10.1.2.3" and logs[0].target == "kim" and logs[0].actor == "boss"
    modify = json.loads(logs[2].detail)["changes"]
    assert modify["department"] == {"old": None, "new": "DP"}
    assert modify["phone_number"] == {"old": "(변경됨)", "new": "(변경됨)"}
    assert all("010-" not in (entry.detail or "") for entry in logs)
    assert json.loads(logs[3].detail) == {"old": "argus-user", "new": "argus-superuser"}
    deleted = json.loads(logs[-1].detail)
    assert deleted["transfer_to"] == "heir" and deleted["owned_assets"][0]["key"] == "apis"


# ---------------------------------------------------------------------------
# 감사 로그 — 권한
# ---------------------------------------------------------------------------

async def test_permission_matrix_change_audited(factory):
    boss = tu("boss", "argus-admin")
    async with factory() as s:
        # 예전 시드가 남긴 관리 메뉴 행 — 저장 때 정리되지만 권한 변화로 기록하지 않는다
        s.add(Permission(kind="MENU", perm_key="users", role_id="__none__"))
        await s.commit()
        await perm._put_matrix(s, "MENU", {"datasets": ["argus-superuser"], "apis": []}, actor=boss)
        await perm._put_matrix(s, "MENU", {"datasets": ["argus-superuser", "argus-user"], "apis": []}, actor=boss)
        await perm._put_matrix(s, "MENU", {"datasets": ["argus-superuser", "argus-user"], "apis": []}, actor=boss)
    logs = await _logs(factory, "permissions")
    assert len(logs) == 2 and logs[0].action == "permissions.menus"
    first = json.loads(logs[0].detail)["changes"]
    assert first["datasets"] == {"old": "기본", "new": ["argus-superuser"]}
    assert first["apis"] == {"old": "기본", "new": ["__none__"]}
    assert json.loads(logs[1].detail)["changes"] == {"datasets": {"old": ["argus-superuser"], "new": "기본"}}


# ---------------------------------------------------------------------------
# 조회 API · 기록 실패
# ---------------------------------------------------------------------------

async def test_audit_list_api(factory):
    boss = tu("boss", "argus-admin")
    for i in range(3):
        await audit.record(None, boss, "settings", "settings.llm", target="llm", summary=f"s{i}")
    await audit.record(None, tu("other", "argus-admin"), "users", "user.create", target="kim", summary="생성 kim")
    app, who = _app(factory, audit_router.router)
    async with _client(app) as c:
        who["user"] = tu("u1")
        assert (await c.get("/api/v1/admin/audit")).status_code == 403
        who["user"] = boss
        body = (await c.get("/api/v1/admin/audit?page_size=2")).json()
        assert body["total"] == 4 and len(body["items"]) == 2
        assert body["items"][0]["action"] == "user.create"  # 최신순
        assert (await c.get("/api/v1/admin/audit?category=settings")).json()["total"] == 3
        assert (await c.get("/api/v1/admin/audit?actor=other")).json()["total"] == 1
        assert (await c.get("/api/v1/admin/audit?q=kim")).json()["total"] == 1
        assert (await c.get("/api/v1/admin/audit?category=bogus")).status_code == 400


async def test_record_failure_does_not_raise(factory):
    async with factory() as s:
        await s.execute(text("DROP TABLE argus_admin_audit_log"))
        await s.commit()
        await audit.record(s, tu("boss", "argus-admin"), "users", "user.create", target="x")  # 예외 없음
        await ssvc.update_config(s, "llm", {"llm_model": "m9"}, actor=tu("boss", "argus-admin"))
        assert (await ssvc.get_config_by_category(s, "llm"))["llm_model"] == "m9"
