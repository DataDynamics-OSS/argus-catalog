# SPDX-License-Identifier: Apache-2.0
"""API 키 (TODO-056) — 임시 파일 SQLite.

- 발급(평문 한 번), X-API-Key·Bearer 인증, 작업자 표기 apikey#<id>
- 범위(catalog:read/write, external:read, schema-registry:read), 키로 계정·키 관리 API 금지
- 중지·재개·재발급·폐기·만료가 바로 반영(검증 캐시 무효화), 검증 캐시로 DB 조회 생략
- 마지막 사용 시각은 간격마다 한 번만 기록
- 로그인 게이트·비밀번호 변경 게이트 통과, 스키마 레지스트리 Basic 의 비밀번호 자리 키(Keycloak 모드 포함)
- 만료 정책(기본값·최대값), 감사 로그

실행:
    cd backend
    PYTHONPATH=. python -m pytest -q tests/test_api_keys.py
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.core.auth as core_auth
from app.apikeys import service as key_service
from app.apikeys.models import ArgusApiKey
from app.audit import service as audit_service
from app.audit.models import AdminAuditLog
from app.core.config import settings
from app.core.database import Base
from app.usermgr.models import ArgusRole, ArgusUser

_REAL_WRITE = key_service._write_last_used
TOUCHED: list[int] = []      # fixture 가 바꿔 끼운 사용 시각 기록이 받은 키 id


async def _make_factory(path):
    # 파일 DB + NullPool — TestClient(다른 스레드·이벤트 루프)와 asyncio.run 이 같은 DB 를 보게
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
    tables = [ArgusRole.__table__, ArgusUser.__table__, ArgusApiKey.__table__, AdminAuditLog.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        role = ArgusRole(role_id="argus-admin", name="Admin")
        db.add(role)
        await db.flush()
        db.add(ArgusUser(username="admin", email="a@x.io", first_name="a", last_name="b",
                         password_hash="h", role_id=role.id))
        await db.commit()
    return factory


@pytest.fixture
def env(monkeypatch, tmp_path):
    factory = asyncio.run(_make_factory(tmp_path / "keys.db"))
    key_service.reset_caches()
    monkeypatch.setattr(key_service, "session_factory", factory)
    monkeypatch.setattr(core_auth, "account_session_factory", factory)
    # 사용 시각 백그라운드 기록은 기록만 — TestClient 가 요청마다 루프를 닫아 끝나지 않은 DB 작업이 경고를 내므로
    TOUCHED.clear()

    async def _record_touch(key_id, ip):
        TOUCHED.append(key_id)
    monkeypatch.setattr(key_service, "_write_last_used", _record_touch)
    monkeypatch.setattr(audit_service, "audit_session_factory", factory)
    core_auth.invalidate_account_cache()
    monkeypatch.setattr(settings, "auth_type", "local")
    monkeypatch.setattr(settings, "api_keys_enabled", True)
    monkeypatch.setattr(settings, "api_keys_default_expire_days", 365)
    monkeypatch.setattr(settings, "api_keys_max_expire_days", 0)
    monkeypatch.setattr(settings, "schema_registry_enabled", True)
    monkeypatch.setattr(settings, "schema_registry_basic_auth", True)

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from app.apikeys.router import router
    from app.core.auth import get_current_user
    from app.core.auth_gate import ApiAuthGateMiddleware
    from app.core.database import get_session
    from app.core.password_gate import PasswordChangeGateMiddleware
    from app.schema_registry.auth import registry_user

    app = FastAPI()
    app.add_middleware(PasswordChangeGateMiddleware)
    app.add_middleware(ApiAuthGateMiddleware)
    app.include_router(router, prefix="/api/v1")

    @app.get("/api/v1/catalog/things")
    async def things(user=Depends(get_current_user)):
        return {"username": user.username, "sub": user.sub, "role": user.role, "key": user.api_key_id}

    @app.post("/api/v1/catalog/things")
    async def add_thing(user=Depends(get_current_user)):
        return {"username": user.username}

    @app.get("/api/v1/external/avro-schema")
    async def ext(user=Depends(get_current_user)):
        return {"username": user.username}

    @app.get("/api/v1/auth/me")
    async def me(user=Depends(get_current_user)):
        return {"username": user.username, "sub": user.sub}

    @app.put("/api/v1/auth/me")
    async def update_me(user=Depends(get_current_user)):
        return {"username": user.username}

    @app.post("/api/v1/auth/refresh")
    async def refresh(user=Depends(get_current_user)):
        return {"username": user.username}

    @app.get("/api/v1/schema-registry/subjects")
    async def subjects(user=Depends(registry_user)):
        return [user.username]

    async def _session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = _session
    token = core_auth.create_local_token("1", "admin", "a@x.io", "a", "b", "argus-admin", password_hash="h")
    admin = TestClient(app)
    admin.headers["Authorization"] = f"Bearer {token}"
    return admin, TestClient(app), factory


def _issue(admin, **body) -> dict:
    body.setdefault("name", "metadata-sync")
    r = admin.post("/api/v1/api-keys", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _key(raw: str) -> dict:
    return {"X-API-Key": raw}


def _rows(factory, model):
    async def go():
        async with factory() as db:
            return list((await db.execute(select(model))).scalars().all())
    return asyncio.run(go())


def test_issue_and_authenticate(env):
    admin, anon, factory = env
    created = _issue(admin, scopes=["catalog:read"])
    raw = created["api_key"]
    assert raw.startswith("argus_sk_") and created["key_prefix"] == raw[:16]
    assert created["role_id"] == "argus-user" and created["scopes"] == ["catalog:read"]
    assert created["expires_at"] is not None            # 기본 만료 365일

    listed = admin.get("/api/v1/api-keys").json()
    assert [k["id"] for k in listed] == [created["id"]] and "api_key" not in listed[0]
    row = _rows(factory, ArgusApiKey)[0]
    assert row.key_hash == key_service.hash_key(raw) and raw not in (row.key_prefix, row.key_hash)

    for headers in (_key(raw), {"Authorization": f"Bearer {raw}"}):
        r = anon.get("/api/v1/catalog/things", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json() == {"username": f"apikey#{created['id']}", "sub": f"apikey:{created['id']}",
                            "role": "user", "key": created["id"]}

    assert anon.get("/api/v1/catalog/things", headers=_key("argus_sk_nope")).status_code == 401
    assert anon.get("/api/v1/catalog/things", headers=_key("not-a-key")).status_code == 401


def test_scopes(env):
    admin, anon, _ = env
    read = _issue(admin, scopes=["catalog:read"])["api_key"]
    write = _issue(admin, name="w", scopes=["catalog:write"])["api_key"]
    ext = _issue(admin, name="e", scopes=["external:read"])["api_key"]

    assert anon.post("/api/v1/catalog/things", headers=_key(read)).status_code == 403
    assert anon.post("/api/v1/catalog/things", headers=_key(write)).status_code == 200
    assert anon.get("/api/v1/external/avro-schema", headers=_key(ext)).status_code == 200
    r = anon.get("/api/v1/catalog/things", headers=_key(ext))
    assert r.status_code == 403 and "external:read" in r.json()["detail"]

    assert admin.post("/api/v1/api-keys", json={"name": "x", "scopes": ["root"]}).status_code == 422
    assert admin.post("/api/v1/api-keys", json={"name": "x", "scopes": []}).status_code == 422
    scopes = admin.get("/api/v1/api-keys/scopes").json()
    assert {s["code"] for s in scopes} == set(key_service.SCOPES)


def test_key_cannot_manage_keys_or_use_account_api(env):
    admin, anon, _ = env
    raw = _issue(admin, role="argus-admin", scopes=["catalog:write"])["api_key"]
    assert anon.get("/api/v1/api-keys", headers=_key(raw)).status_code == 403
    assert anon.post("/api/v1/api-keys", headers=_key(raw), json={"name": "x"}).status_code == 403
    # 계정 API 는 막지만 읽기 전용 신원 확인 GET /auth/me 는 범위와 무관하게 허용(DEF-085 — ai-service 가 키의 주인을 확인)
    assert anon.put("/api/v1/auth/me", headers=_key(raw)).status_code == 403
    assert anon.post("/api/v1/auth/refresh", headers=_key(raw)).status_code == 403
    created = _issue(admin, name="reader", scopes=["external:read"])
    r = anon.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {created['api_key']}"})
    assert r.status_code == 200 and r.json()["sub"] == f"apikey:{created['id']}"
    # 관리 API 는 관리자 로그인만
    user_key = _issue(admin, name="u")["api_key"]
    assert anon.get("/api/v1/api-keys", headers={"Authorization": f"Bearer {user_key}"}).status_code == 403
    assert anon.get("/api/v1/api-keys").status_code == 401


def test_disable_rotate_revoke_take_effect_immediately(env):
    admin, anon, factory = env
    created = _issue(admin, scopes=["catalog:read"])
    kid, raw = created["id"], created["api_key"]
    assert anon.get("/api/v1/catalog/things", headers=_key(raw)).status_code == 200   # 캐시에 들어감

    assert admin.put(f"/api/v1/api-keys/{kid}", json={"enabled": False}).json()["status"] == "disabled"
    r = anon.get("/api/v1/catalog/things", headers=_key(raw))
    assert r.status_code == 401 and "중지" in r.json()["detail"]
    admin.put(f"/api/v1/api-keys/{kid}", json={"enabled": True})
    assert anon.get("/api/v1/catalog/things", headers=_key(raw)).status_code == 200

    # 범위 변경도 바로
    admin.put(f"/api/v1/api-keys/{kid}", json={"scopes": ["external:read"]})
    assert anon.get("/api/v1/catalog/things", headers=_key(raw)).status_code == 403

    rotated = admin.post(f"/api/v1/api-keys/{kid}/rotate").json()
    new = rotated["api_key"]
    assert new != raw and rotated["id"] == kid and rotated["rotated_at"]
    assert anon.get("/api/v1/external/avro-schema", headers=_key(raw)).status_code == 401
    assert anon.get("/api/v1/external/avro-schema", headers=_key(new)).status_code == 200

    revoked = admin.delete(f"/api/v1/api-keys/{kid}").json()
    assert revoked["status"] == "revoked" and revoked["revoked_by"] == "admin"
    r = anon.get("/api/v1/external/avro-schema", headers=_key(new))
    assert r.status_code == 401 and "폐기" in r.json()["detail"]
    assert admin.put(f"/api/v1/api-keys/{kid}", json={"enabled": True}).status_code == 409
    assert admin.post(f"/api/v1/api-keys/{kid}/rotate").status_code == 409
    assert admin.get("/api/v1/api-keys").json() == []
    assert [k["id"] for k in admin.get("/api/v1/api-keys?include_revoked=true").json()] == [kid]

    actions = [a.action for a in _rows(factory, AdminAuditLog)]
    assert actions == ["apikey.create", "apikey.modify", "apikey.modify", "apikey.modify",
                       "apikey.rotate", "apikey.revoke"]
    assert all(a.category == "settings" and a.actor == "admin" for a in _rows(factory, AdminAuditLog))


def test_expired_key_rejected(env):
    admin, anon, factory = env
    created = _issue(admin)

    async def expire():
        async with factory() as db:
            await db.execute(update(ArgusApiKey).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            await db.commit()
    asyncio.run(expire())
    key_service.invalidate()
    r = anon.get("/api/v1/catalog/things", headers=_key(created["api_key"]))
    assert r.status_code == 401 and "만료" in r.json()["detail"]


def test_expiry_policy(env, monkeypatch):
    admin, _, _ = env
    assert _issue(admin, expires_in_days=0)["expires_at"] is None         # 무기한 허용(최대값 없음)
    monkeypatch.setattr(settings, "api_keys_max_expire_days", 30)
    assert admin.post("/api/v1/api-keys", json={"name": "x", "expires_in_days": 0}).status_code == 422
    assert admin.post("/api/v1/api-keys", json={"name": "x", "expires_in_days": 60}).status_code == 422
    # 만료를 비우면 기본 365 일이 최대 30 일을 넘으므로 30 일로 줄여 발급(DEF-078)
    r = admin.post("/api/v1/api-keys", json={"name": "x"})
    assert r.status_code == 201
    exp = datetime.fromisoformat(r.json()["expires_at"].replace("Z", "+00:00"))
    days = (exp.replace(tzinfo=exp.tzinfo or timezone.utc) - datetime.now(timezone.utc)).days
    assert 29 <= days <= 30
    monkeypatch.setattr(settings, "api_keys_default_expire_days", 0)   # 기본 무기한이어도 최대값으로
    assert admin.post("/api/v1/api-keys", json={"name": "y"}).json()["expires_at"] is not None
    assert admin.post("/api/v1/api-keys", json={"name": "x", "expires_in_days": 30}).status_code == 201


def test_verification_cached_and_last_used_throttled(env, monkeypatch):
    admin, anon, factory = env
    raw = _issue(admin)["api_key"]
    lookups = []
    original = key_service._factory

    def counting():
        lookups.append(1)
        return original()
    monkeypatch.setattr(key_service, "_factory", counting)
    writes = TOUCHED
    for _ in range(5):
        assert anon.get("/api/v1/catalog/things", headers=_key(raw)).status_code == 200
    # 게이트·핸들러가 요청마다 두 번씩 인증하지만 DB 조회는 처음 한 번, 사용 시각 기록도 한 번
    assert len(lookups) == 1 and len(writes) == 1
    # 기록 자체는 직접 확인(fixture 가 백그라운드 기록을 바꿔 끼웠으므로 원래 함수로)
    asyncio.run(_REAL_WRITE(writes[0], "testclient"))
    row = _rows(factory, ArgusApiKey)[0]
    assert row.last_used_at is not None and row.last_used_ip == "testclient"


def test_password_gate_ignores_keys(env):
    admin, anon, _ = env
    raw = _issue(admin)["api_key"]
    # 강제 변경 게이트가 키를 JWT 로 읽지 않는다 — 그대로 통과
    assert anon.get("/api/v1/catalog/things", headers={"Authorization": f"Bearer {raw}"}).status_code == 200


def test_schema_registry_accepts_key_as_basic_password(env, monkeypatch):
    admin, anon, _ = env
    created = _issue(admin, name="nifi", scopes=["schema-registry:read"])
    raw = created["api_key"]
    basic = "Basic " + base64.b64encode(f"nifi:{raw}".encode()).decode()
    r = anon.get("/api/v1/schema-registry/subjects", headers={"Authorization": basic})
    assert r.status_code == 200 and r.json() == [f"apikey#{created['id']}"]
    assert anon.get("/api/v1/schema-registry/subjects", headers=_key(raw)).status_code == 200
    # 이 키로 다른 API 는 못 부름
    assert anon.get("/api/v1/catalog/things", headers=_key(raw)).status_code == 403
    # Keycloak 모드에서도 키는 Basic 으로 받는다(사용자 비밀번호 Basic 은 거부)
    monkeypatch.setattr(settings, "auth_type", "keycloak")
    assert anon.get("/api/v1/schema-registry/subjects", headers={"Authorization": basic}).status_code == 200
    user_basic = "Basic " + base64.b64encode(b"admin:admin").decode()
    assert anon.get("/api/v1/schema-registry/subjects", headers={"Authorization": user_basic}).status_code == 401


def test_keys_disabled(env, monkeypatch):
    admin, anon, _ = env
    raw = _issue(admin)["api_key"]
    monkeypatch.setattr(settings, "api_keys_enabled", False)
    assert anon.get("/api/v1/catalog/things", headers=_key(raw)).status_code == 401
