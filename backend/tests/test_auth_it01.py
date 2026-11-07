"""통합 테스트 01-auth 수행 중 발견한 결함 회귀 테스트.

- Keycloak 로그인 실패 시 영문 ``error_description``("Invalid user credentials")을 그대로 화면에
  내보내던 문제 → 로컬 모드와 같은 한국어 401 문구, 클라이언트 설정 오류는 502 로 구분.
- ``PUT /auth/me`` 응답에 ``phone_number``·``must_change_password`` 가 빠져(항상 null/false)
  방금 저장한 전화번호가 응답에 보이지 않던 문제.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.auth import router as auth_router
from app.auth.router import LoginRequest, UpdateProfileRequest, _keycloak_login_error, update_me
from app.core.auth import TokenUser
from app.core.config import settings
from app.core.database import Base
from app.usermgr.models import ArgusRole, ArgusUser, ArgusUserPreference

# ---------------------------------------------------------------------------
# Keycloak 로그인 오류 문구
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("err, status, text", [
    ({"error": "invalid_grant", "error_description": "Invalid user credentials"}, 401,
     "사용자명 또는 비밀번호가 올바르지 않습니다."),
    ({}, 401, "사용자명 또는 비밀번호가 올바르지 않습니다."),
    ({"error": "invalid_grant", "error_description": "Account disabled"}, 403, "비활성화된 계정입니다."),
    ({"error": "invalid_grant", "error_description": "Account is not fully set up"}, 403, "필수 작업"),
    ({"error": "unauthorized_client", "error_description": "Invalid client or Invalid client credentials"}, 502,
     "인증 서버(Keycloak) 설정 오류"),
])
def test_keycloak_login_error_mapping(err, status, text):
    code, detail = _keycloak_login_error(err)
    assert code == status
    assert text in detail
    assert "Invalid" not in detail and "Account" not in detail


class _FakeResp:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        return self._resp


async def test_keycloak_login_failure_is_korean(monkeypatch):
    resp = _FakeResp(401, {"error": "invalid_grant", "error_description": "Invalid user credentials"})
    monkeypatch.setattr(auth_router.httpx, "AsyncClient", lambda *a, **kw: _FakeClient(resp))
    monkeypatch.setattr(settings, "auth_type", "keycloak")
    with pytest.raises(HTTPException) as ei:
        await auth_router._login_keycloak(LoginRequest(username="admin", password="admin"), session=None)
    assert ei.value.status_code == 401
    assert ei.value.detail == "사용자명 또는 비밀번호가 올바르지 않습니다."


# ---------------------------------------------------------------------------
# PUT /auth/me 응답
# ---------------------------------------------------------------------------

@pytest.fixture
async def make(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    tables = [ArgusRole.__table__, ArgusUser.__table__, ArgusUserPreference.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    mk = async_sessionmaker(engine, expire_on_commit=False)
    async with mk() as s:
        s.add(ArgusRole(role_id="argus-user", name="argus-user"))
        await s.commit()
        rid = (await s.execute(select(ArgusRole.id))).scalar_one()
        s.add(ArgusUser(username="it-auth-user", email="it@ex.com", first_name="F", last_name="L",
                        password_hash="x", status="active", role_id=rid, must_change_password=False))
        await s.commit()
    monkeypatch.setattr(settings, "auth_type", "local")
    yield mk
    await engine.dispose()


async def test_update_me_returns_saved_phone_number(make):
    async with make() as s:
        uid = (await s.execute(select(ArgusUser.id))).scalar_one()
        me = TokenUser(sub=str(uid), username="it-auth-user", roles=["argus-user"])
        out = await update_me(UpdateProfileRequest(phone_number="010-1234-5678"), me, s)
    assert out.phone_number == "010-1234-5678"
    assert out.must_change_password is False
