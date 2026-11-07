# SPDX-License-Identifier: Apache-2.0
"""관리 기능 2단계(정합성) 테스트 (in-memory SQLite, 자기완결형).

검증:
- 토큰 즉시 무효화: 비활성화·삭제·비밀번호 재설정 뒤 기존 토큰 401, 강등은 DB 역할로 즉시 반영,
  pwv 없는 예전 토큰 허용, refresh 도 비밀번호 변경 뒤 거부, 로그인 재해시 직후 새 토큰 유효
- Keycloak 모드: 로컬 비활성 계정 토큰 401, JIT upsert 가 비활성 계정을 되살리지 않음,
  생성·역할·비밀번호·동기화 필드 수정 400, 전화번호 수정 허용
- 삭제 시 자산 이관: 소유 자산이 있으면 409, 넘겨받을 사용자 검증, 이관 후 삭제
- 개인정보: 비관리자 조회에서 타인 전화번호 숨김·전화번호 검색 제외, 중복 확인은 관리자 전용

실행:
    cd backend
    pytest tests/test_admin_stage2.py
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.auth.router import LoginRequest, RefreshRequest, _login_local, _refresh_local
from app.core import auth as core_auth
from app.core import passwords
from app.core.auth import TokenUser, create_local_token, get_current_user, get_optional_user
from app.core.config import settings
from app.core.database import Base, get_session
from app.usermgr import ownership
from app.usermgr import router as umr
from app.usermgr import service as usvc
from app.usermgr.models import ArgusRole, ArgusUser


@pytest.fixture(autouse=True)
def _fast_hash(monkeypatch):
    monkeypatch.setattr(passwords, "ITERATIONS", 1000)
    orig = passwords.hash_password
    monkeypatch.setattr(passwords, "hash_password", lambda p, *, iterations=1000: orig(p, iterations=iterations))


@pytest.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    tables = [ArgusRole.__table__, ArgusUser.__table__] + [a.model.__table__ for a in ownership._registry()]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    make = async_sessionmaker(engine, expire_on_commit=False)
    async with make() as s:
        for rid in ("argus-admin", "argus-superuser", "argus-user"):
            s.add(ArgusRole(role_id=rid, name=rid))
        await s.commit()
    monkeypatch.setattr(core_auth, "account_session_factory", make)
    monkeypatch.setattr(settings, "auth_type", "local")
    core_auth.invalidate_account_cache()
    yield make
    core_auth.invalidate_account_cache()
    await engine.dispose()


async def _add(make, username: str, role: str = "argus-user", status: str = "active",
               password: str = "abcdefg1", phone: str | None = None) -> int:
    async with make() as s:
        rid = (await s.execute(select(ArgusRole.id).where(ArgusRole.role_id == role))).scalar_one()
        u = ArgusUser(username=username, email=f"{username}@ex.com", first_name="F", last_name="L",
                      password_hash=passwords.hash_password(password), status=status, role_id=rid,
                      phone_number=phone)
        s.add(u)
        await s.commit()
        return u.id


def _req(token: str) -> Request:
    return Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})


async def _login(make, username: str, password: str = "abcdefg1"):
    async with make() as s:
        return await _login_local(LoginRequest(username=username, password=password), s)


def tu(name: str, role: str = "argus-user", sub: str | None = None) -> TokenUser:
    return TokenUser(sub=sub or name, username=name, roles=[role])


def _app(factory) -> tuple[FastAPI, dict]:
    app = FastAPI()
    app.include_router(umr.router, prefix="/api/v1")

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


# ---------------------------------------------------------------------------
# 토큰 즉시 무효화
# ---------------------------------------------------------------------------

async def test_deactivate_and_delete_revoke_token(factory):
    await _add(factory, "boss", role="argus-admin")
    uid = await _add(factory, "kim")
    tok = (await _login(factory, "kim")).access_token
    assert (await get_current_user(_req(tok))).username == "kim"

    async with factory() as s:
        await usvc.deactivate_user(s, uid)
    core_auth.invalidate_account_cache()
    with pytest.raises(HTTPException) as e:
        await get_current_user(_req(tok))
    assert e.value.status_code == 401
    assert await get_optional_user(_req(tok)) is None

    async with factory() as s:
        await usvc.activate_user(s, uid)
        await usvc.delete_user(s, uid)
    core_auth.invalidate_account_cache()
    with pytest.raises(HTTPException) as e:
        await get_current_user(_req(tok))
    assert e.value.status_code == 401


async def test_demotion_applies_immediately(factory):
    await _add(factory, "boss", role="argus-admin")
    uid = await _add(factory, "lee", role="argus-admin")
    tok = (await _login(factory, "lee")).access_token
    assert (await get_current_user(_req(tok))).is_admin
    async with factory() as s:
        rid = (await s.execute(select(ArgusRole.id).where(ArgusRole.role_id == "argus-user"))).scalar_one()
        (await s.get(ArgusUser, uid)).role_id = rid
        await s.commit()
    core_auth.invalidate_account_cache()
    user = await get_current_user(_req(tok))
    assert not user.is_admin and user.role == "user"


async def test_password_reset_revokes_access_and_refresh(factory):
    uid = await _add(factory, "park")
    pair = await _login(factory, "park")
    assert (await get_current_user(_req(pair.access_token))).username == "park"

    async with factory() as s:
        await usvc.set_password(s, uid, "newpass12", must_change_password=True)
    core_auth.invalidate_account_cache()
    with pytest.raises(HTTPException) as e:
        await get_current_user(_req(pair.access_token))
    assert e.value.status_code == 401 and "비밀번호" in e.value.detail
    async with factory() as s:
        with pytest.raises(HTTPException) as e:
            await _refresh_local(RefreshRequest(refresh_token=pair.refresh_token), s)
    assert e.value.status_code == 401

    # 새 비밀번호로 받은 토큰은 유효
    fresh = await _login(factory, "park", "newpass12")
    assert (await get_current_user(_req(fresh.access_token))).username == "park"


async def test_token_without_pwv_still_accepted(factory):
    uid = await _add(factory, "old")
    legacy = create_local_token(sub=str(uid), username="old", email="", first_name="", last_name="",
                                role="argus-user")
    assert (await get_current_user(_req(legacy))).username == "old"


async def test_login_rehash_token_valid_immediately(factory):
    import hashlib
    uid = await _add(factory, "legacy")
    async with factory() as s:
        (await s.get(ArgusUser, uid)).password_hash = hashlib.sha256(b"oldpass1").hexdigest()
        await s.commit()
    # 재해시 전 지문이 캐시에 있어도 새 토큰이 거부되면 안 된다
    await core_auth._load_account(("local", str(uid)))
    tok = (await _login(factory, "legacy", "oldpass1")).access_token
    assert (await get_current_user(_req(tok))).username == "legacy"


# ---------------------------------------------------------------------------
# Keycloak 모드
# ---------------------------------------------------------------------------

async def test_keycloak_inactive_user_rejected(factory, monkeypatch):
    await _add(factory, "kc", status="inactive")
    monkeypatch.setattr(settings, "auth_type", "keycloak")
    payload = {"preferred_username": "kc", "realm_access": {"roles": ["argus-user"]}}
    with pytest.raises(HTTPException) as e:
        await core_auth.check_account_state(core_auth._build_token_user(payload), payload)
    assert e.value.status_code == 401
    # 로컬 행이 없는 사용자(JIT 전)는 통과
    payload2 = {"preferred_username": "nobody", "realm_access": {"roles": ["argus-user"]}}
    assert (await core_auth.check_account_state(core_auth._build_token_user(payload2), payload2)).username == "nobody"


async def test_jit_upsert_keeps_inactive(factory):
    await _add(factory, "kc2", status="inactive")
    async with factory() as s:
        await usvc.upsert_external_user(
            s, username="kc2", email="kc2@ex.com", first_name="A", last_name="B",
            organization=None, department=None, role_code="argus-user",
        )
        assert (await s.execute(select(ArgusUser.status).where(ArgusUser.username == "kc2"))).scalar() == "inactive"


async def test_keycloak_mode_usermgr_restrictions(factory, monkeypatch):
    await _add(factory, "boss", role="argus-admin")
    uid = await _add(factory, "kc3")
    monkeypatch.setattr(settings, "auth_type", "keycloak")
    app, who = _app(factory)
    who["user"] = tu("boss", "argus-admin")
    async with _client(app) as c:
        r = await c.post("/api/v1/usermgr/users", json={
            "username": "n", "email": "n@ex.com", "first_name": "a", "last_name": "b", "password": "abcdefg1"})
        assert r.status_code == 400
        assert (await c.put(f"/api/v1/usermgr/users/{uid}/role", json={"role": "argus-admin"})).status_code == 400
        assert (await c.put(f"/api/v1/usermgr/users/{uid}/password", json={"password": "abcdefg1"})).status_code == 400
        assert (await c.put(f"/api/v1/usermgr/users/{uid}", json={"first_name": "X"})).status_code == 400
        r = await c.put(f"/api/v1/usermgr/users/{uid}", json={"phone_number": "010-1111-2222"})
        assert r.status_code == 200 and r.json()["phone_number"] == "010-1111-2222"
        # 비활성화는 로컬에서 관리
        assert (await c.put(f"/api/v1/usermgr/users/{uid}/deactivate")).status_code == 200


# ---------------------------------------------------------------------------
# 삭제 시 자산 이관
# ---------------------------------------------------------------------------

async def test_delete_requires_transfer_of_owned_assets(factory):
    from app.apis.models import CatalogApi
    from app.models.models import RegisteredModel

    await _add(factory, "boss", role="argus-admin")
    uid = await _add(factory, "owner1")
    await _add(factory, "heir")
    await _add(factory, "sleepy", status="inactive")
    async with factory() as s:
        s.add(CatalogApi(name="api1", urn="urn:api1", created_by="owner1"))
        s.add(RegisteredModel(name="m1", urn="urn:m1", created_by="someone", owner="owner1"))
        await s.commit()

    app, who = _app(factory)
    who["user"] = tu("boss", "argus-admin")
    async with _client(app) as c:
        r = await c.get(f"/api/v1/usermgr/users/{uid}/owned-assets")
        counts = {i["key"]: i["count"] for i in r.json()["items"]}
        assert r.json()["total"] == 2 and counts["apis"] == 1 and counts["models"] == 1

        r = await c.delete(f"/api/v1/usermgr/users/{uid}")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "owned_assets"
        assert (await c.delete(f"/api/v1/usermgr/users/{uid}?transfer_to=sleepy")).status_code == 400
        assert (await c.delete(f"/api/v1/usermgr/users/{uid}?transfer_to=ghost")).status_code == 400
        assert (await c.delete(f"/api/v1/usermgr/users/{uid}?transfer_to=owner1")).status_code == 400
        # 실패한 시도는 아무것도 바꾸지 않는다
        assert (await c.get(f"/api/v1/usermgr/users/{uid}")).status_code == 200

        assert (await c.delete(f"/api/v1/usermgr/users/{uid}?transfer_to=heir")).status_code == 200

        # 비관리자는 소유 자산 조회 불가
        who["user"] = tu("heir")
        assert (await c.get("/api/v1/usermgr/users/1/owned-assets")).status_code == 403

    async with factory() as s:
        assert (await s.execute(select(CatalogApi.created_by))).scalar() == "heir"
        m = (await s.execute(select(RegisteredModel))).scalars().one()
        assert m.owner == "heir" and m.created_by == "someone"
        assert await s.get(ArgusUser, uid) is None


async def test_delete_without_assets_needs_no_transfer(factory):
    await _add(factory, "boss", role="argus-admin")
    uid = await _add(factory, "plain")
    app, who = _app(factory)
    who["user"] = tu("boss", "argus-admin")
    async with _client(app) as c:
        assert (await c.delete(f"/api/v1/usermgr/users/{uid}")).status_code == 200


# ---------------------------------------------------------------------------
# 개인정보 노출 축소
# ---------------------------------------------------------------------------

async def test_non_admin_sees_no_phone_numbers(factory):
    await _add(factory, "boss", role="argus-admin", phone="010-0000-0000")
    me = await _add(factory, "me", phone="010-1234-5678")
    other = await _add(factory, "other", phone="010-9999-8888")
    app, who = _app(factory)
    async with _client(app) as c:
        who["user"] = tu("me", sub=str(me))
        items = {u["username"]: u for u in (await c.get("/api/v1/usermgr/users?page_size=0")).json()["items"]}
        assert items["other"]["phone_number"] is None and items["boss"]["phone_number"] is None
        assert items["me"]["phone_number"] == "010-1234-5678"
        assert items["other"]["email"] == "other@ex.com"  # 소유자 선택에 필요
        assert (await c.get(f"/api/v1/usermgr/users/{other}")).json()["phone_number"] is None
        # 전화번호로 검색해 번호를 알아낼 수 없다
        assert (await c.get("/api/v1/usermgr/users?search=9999")).json()["total"] == 0
        assert (await c.get("/api/v1/usermgr/check-user?username=other")).status_code == 403

        who["user"] = tu("boss", "argus-admin")
        assert (await c.get(f"/api/v1/usermgr/users/{other}")).json()["phone_number"] == "010-9999-8888"
        assert (await c.get("/api/v1/usermgr/users?search=9999")).json()["total"] == 1
        assert (await c.get("/api/v1/usermgr/check-user?username=other")).json()["username_exists"] is True
