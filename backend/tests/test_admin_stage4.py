# SPDX-License-Identifier: Apache-2.0
"""관리 기능 4단계(사용성) 테스트 (in-memory SQLite, 자기완결형).

검증:
- 로그인 잠금: 비밀번호 5회 연속 실패 → 잠금(423), 잠금 중에는 맞는 비밀번호도 423,
  잠금 시간이 지나면 풀림, 성공하면 실패 횟수 초기화·마지막 로그인 기록,
  관리자 잠금 해제·비밀번호 재설정이 잠금을 푼다, 잠금 해제는 관리자 전용·감사 기록
- 사용자 목록 서버 정렬: 전체 결과 기준(페이지를 넘어), 이름·역할·마지막 로그인(기록 없음은 맨 뒤),
  허용되지 않은 정렬 열 400, 응답에 last_login_at·locked_until
- SSO(JIT) 로그인도 마지막 로그인을 기록

실행:
    cd backend
    pytest tests/test_admin_stage4.py
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.audit import service as audit
from app.audit.models import AdminAuditLog
from app.auth.router import LoginRequest, _login_local
from app.core import auth as core_auth
from app.core import passwords
from app.core.auth import TokenUser, get_current_user, get_optional_user
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
    monkeypatch.setattr(settings, "auth_type", "local")


@pytest.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    tables = [ArgusRole.__table__, ArgusUser.__table__, AdminAuditLog.__table__] + [
        a.model.__table__ for a in ownership._registry()
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    make = async_sessionmaker(engine, expire_on_commit=False)
    async with make() as s:
        for rid in ("argus-admin", "argus-superuser", "argus-user"):
            s.add(ArgusRole(role_id=rid, name=rid))
        await s.commit()
    monkeypatch.setattr(core_auth, "account_session_factory", make)
    monkeypatch.setattr(audit, "audit_session_factory", make)
    core_auth.invalidate_account_cache()
    yield make
    core_auth.invalidate_account_cache()
    await engine.dispose()


async def _add(make, username: str, role: str = "argus-user", password: str = "abcdefg1",
               first: str = "F", last: str = "L", **extra) -> int:
    async with make() as s:
        rid = (await s.execute(select(ArgusRole.id).where(ArgusRole.role_id == role))).scalar_one()
        u = ArgusUser(username=username, email=f"{username}@it.data-dynamics.io", first_name=first, last_name=last,
                      password_hash=passwords.hash_password(password), status="active", role_id=rid, **extra)
        s.add(u)
        await s.commit()
        return u.id


async def _user(make, username: str) -> ArgusUser:
    async with make() as s:
        return (await s.execute(select(ArgusUser).where(ArgusUser.username == username))).scalar_one()


async def _login(make, username: str, password: str = "abcdefg1"):
    async with make() as s:
        return await _login_local(LoginRequest(username=username, password=password), s)


async def _fail(make, username: str, password: str = "wrongpw1") -> int:
    with pytest.raises(HTTPException) as e:
        await _login(make, username, password)
    return e.value.status_code


def tu(name: str, role: str = "argus-user") -> TokenUser:
    return TokenUser(sub=name, username=name, roles=[role])


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
# 로그인 잠금
# ---------------------------------------------------------------------------

async def test_lock_after_repeated_failures(factory):
    await _add(factory, "kim")
    for _ in range(usvc.LOGIN_MAX_FAILURES - 1):
        assert await _fail(factory, "kim") == 401
    assert (await _user(factory, "kim")).failed_login_count == usvc.LOGIN_MAX_FAILURES - 1
    # 5번째 실패로 잠김 (응답은 여전히 401 — 이번 시도는 비밀번호 오류)
    assert await _fail(factory, "kim") == 401
    u = await _user(factory, "kim")
    assert usvc.active_lock(u) is not None
    # 잠금 중에는 맞는 비밀번호도 423
    with pytest.raises(HTTPException) as e:
        await _login(factory, "kim")
    assert e.value.status_code == 423
    assert "잠겼습니다" in e.value.detail


async def test_success_resets_counter_and_records_last_login(factory):
    await _add(factory, "lee")
    assert (await _user(factory, "lee")).last_login_at is None
    for _ in range(3):
        await _fail(factory, "lee")
    await _login(factory, "lee")
    u = await _user(factory, "lee")
    assert u.failed_login_count == 0
    assert u.locked_until is None
    assert u.last_login_at is not None
    # 초기화됐으므로 다시 4번 틀려도 잠기지 않는다
    for _ in range(usvc.LOGIN_MAX_FAILURES - 1):
        await _fail(factory, "lee")
    await _login(factory, "lee")


async def test_lock_expires(factory):
    await _add(factory, "park", locked_until=usvc.utcnow() - timedelta(seconds=1))
    await _login(factory, "park")  # 만료된 잠금은 무시
    await _add(factory, "choi", locked_until=usvc.utcnow() + timedelta(minutes=3))
    with pytest.raises(HTTPException) as e:
        await _login(factory, "choi")
    assert e.value.status_code == 423
    assert "약 3분" in e.value.detail


async def test_unlock_endpoint_admin_only_and_audited(factory):
    await _add(factory, "admin", role="argus-admin")
    uid = await _add(factory, "jung", locked_until=usvc.utcnow() + timedelta(minutes=10), failed_login_count=2)
    app, who = _app(factory)
    async with _client(app) as c:
        who["user"] = tu("jung")
        assert (await c.put(f"/api/v1/usermgr/users/{uid}/unlock")).status_code == 403
        who["user"] = tu("admin", "argus-admin")
        listed = (await c.get("/api/v1/usermgr/users", params={"search": "jung"})).json()["items"][0]
        assert listed["locked_until"] is not None
        r = await c.put(f"/api/v1/usermgr/users/{uid}/unlock")
        assert r.status_code == 200
        assert r.json()["locked_until"] is None
        assert (await c.put("/api/v1/usermgr/users/99999/unlock")).status_code == 404
    u = await _user(factory, "jung")
    assert u.locked_until is None and u.failed_login_count == 0
    await _login(factory, "jung")
    async with factory() as s:
        logs = (await s.execute(select(AdminAuditLog).where(AdminAuditLog.action == "user.unlock"))).scalars().all()
    assert [(log.actor, log.target) for log in logs] == [("admin", "jung")]


async def test_password_reset_clears_lock(factory):
    await _add(factory, "admin", role="argus-admin")
    uid = await _add(factory, "yoon", locked_until=usvc.utcnow() + timedelta(minutes=10))
    app, who = _app(factory)
    who["user"] = tu("admin", "argus-admin")
    async with _client(app) as c:
        r = await c.put(f"/api/v1/usermgr/users/{uid}/password", json={"password": "newpass12"})
        assert r.status_code == 200, r.text
    await _login(factory, "yoon", "newpass12")


# ---------------------------------------------------------------------------
# 서버 정렬·마지막 로그인
# ---------------------------------------------------------------------------

async def test_server_sort_across_pages(factory):
    await _add(factory, "admin", role="argus-admin", first="관리", last="가")
    now = usvc.utcnow()
    await _add(factory, "u1", first="철수", last="한", last_login_at=now - timedelta(days=2))
    await _add(factory, "u2", first="영희", last="김", role="argus-superuser")
    await _add(factory, "u3", first="민수", last="박", last_login_at=now - timedelta(hours=1))
    app, who = _app(factory)
    who["user"] = tu("admin", "argus-admin")

    async def names(**params) -> list[str]:
        r = await c.get("/api/v1/usermgr/users", params={"page_size": 2, **params})
        assert r.status_code == 200, r.text
        return [i["username"] for i in r.json()["items"]]

    async with _client(app) as c:
        # 성·이름 순 — 1페이지만이 아니라 전체 결과 기준
        assert await names(sort="name", order="asc") == ["admin", "u2"]
        assert await names(sort="name", order="asc", page=2) == ["u3", "u1"]
        assert await names(sort="name", order="desc") == ["u1", "u3"]
        # 역할 코드 순 (argus-admin < argus-superuser < argus-user), 동률은 id 순
        assert await names(sort="role", order="asc") == ["admin", "u2"]
        # 마지막 로그인 — 기록 없음은 방향과 무관하게 맨 뒤 (동률은 정렬 방향대로 id 순)
        assert await names(sort="last_login_at", order="desc", page_size=10) == ["u3", "u1", "u2", "admin"]
        assert await names(sort="last_login_at", order="asc", page_size=10) == ["u1", "u3", "admin", "u2"]
        # 허용되지 않은 열·방향
        assert (await c.get("/api/v1/usermgr/users", params={"sort": "password_hash"})).status_code == 400
        assert (await c.get("/api/v1/usermgr/users", params={"sort": "name", "order": "up"})).status_code == 422
        item = (await c.get("/api/v1/usermgr/users", params={"search": "u3"})).json()["items"][0]
        assert item["last_login_at"] is not None and item["locked_until"] is None


async def test_jit_records_last_login(factory):
    async with factory() as s:
        await usvc.upsert_external_user(s, username="sso1", email="sso1@it.data-dynamics.io", first_name="A",
                                        last_name="B", organization=None, department=None, role_code="argus-user")
    first = (await _user(factory, "sso1")).last_login_at
    assert first is not None
    async with factory() as s:
        await usvc.upsert_external_user(s, username="sso1", email="sso1@it.data-dynamics.io", first_name="A",
                                        last_name="B", organization=None, department=None, role_code="argus-user")
    assert usvc.as_aware((await _user(factory, "sso1")).last_login_at) >= usvc.as_aware(first)
