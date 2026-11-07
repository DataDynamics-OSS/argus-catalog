"""통합 테스트 01-auth 후속 수정 회귀 테스트.

- Keycloak "연결 테스트"가 realm discovery 만 보고 성공이라 해서, 틀린 client secret 을 저장하면
  모든 로그인이 깨지던 문제 → 토큰 엔드포인트에 client_credentials 요청으로 secret 까지 확인.
- ``PUT /auth/me/avatar`` 가 아무 문자열이나 저장하던 문제 → preset 목록에 없는 id 는 422.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.auth.router import AVATAR_PRESET_IDS, AvatarUpdateRequest, update_avatar
from app.core.auth import TokenUser
from app.core.config import settings
from app.core.database import Base
from app.settings import router as settings_router
from app.settings.router import AuthConfig, _client_check_result
from app.usermgr.models import ArgusRole, ArgusUser, ArgusUserPreference

# ---------------------------------------------------------------------------
# Keycloak 연결 테스트 — client secret 확인
# ---------------------------------------------------------------------------

_BAD = {"error": "unauthorized_client", "error_description": "Invalid client or Invalid client credentials"}


@pytest.mark.parametrize("status, err, ok", [
    (200, {}, True),                                    # 서비스 계정이 켜진 client — 토큰 발급
    (401, _BAD, False),                                 # 최근 Keycloak: secret 틀림 = unauthorized_client·401
    (401, {"error": "invalid_client"}, False),          # client 없음
    (400, {"error": "invalid_client"}, False),
    (400, {"error": "unauthorized_client",              # 서비스 계정 꺼짐 — secret 은 통과
           "error_description": "Client not enabled to retrieve service account"}, True),
    (500, {}, False),
])
def test_client_check_result(status, err, ok):
    got, msg = _client_check_result(status, err, "argus-client")
    assert got is ok
    if not ok and status == 401:
        assert "client secret 이 올바르지 않습니다" in msg


class _Resp:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class _Client:
    """discovery(GET) 는 성공, token(POST) 은 지정한 응답. 보낸 form 을 기록한다."""

    def __init__(self, token_resp, sent):
        self._token_resp = token_resp
        self._sent = sent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        return _Resp(200, {"issuer": "http://kc/realms/argus"})

    async def post(self, url, data=None):
        self._sent.append((url, data))
        return self._token_resp


def _patch(monkeypatch, token_resp, stored_secret="stored-secret"):
    import httpx
    sent: list = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _Client(token_resp, sent))

    async def fake_unmask(value, category, key):
        return stored_secret if value == settings_router._SECRET_MASK else value
    monkeypatch.setattr(settings_router, "_unmask", fake_unmask)
    return sent


def _cfg(secret: str) -> AuthConfig:
    return AuthConfig(type="keycloak", server_url="http://kc/", realm="argus", client_id="argus-client",
                      client_secret=secret, admin_role="a", superuser_role="s", user_role="u")


async def test_auth_test_wrong_secret_fails(monkeypatch):
    sent = _patch(monkeypatch, _Resp(401, _BAD))
    out = await settings_router.test_auth_connection(None, _cfg("wrong"))
    assert out.success is False
    assert "client secret 이 올바르지 않습니다" in out.message
    url, data = sent[0]
    assert url == "http://kc/realms/argus/protocol/openid-connect/token"
    assert data == {"grant_type": "client_credentials", "client_id": "argus-client", "client_secret": "wrong"}


async def test_auth_test_masked_secret_uses_stored(monkeypatch):
    sent = _patch(monkeypatch, _Resp(200, {"access_token": "x"}))
    out = await settings_router.test_auth_connection(None, _cfg(settings_router._SECRET_MASK))
    assert out.success is True
    assert "client secret 확인됨" in out.message
    assert sent[0][1]["client_secret"] == "stored-secret"


async def test_auth_test_service_account_disabled_is_success(monkeypatch):
    _patch(monkeypatch, _Resp(400, {"error": "unauthorized_client",
                                    "error_description": "Client not enabled to retrieve service account"}))
    out = await settings_router.test_auth_connection(None, _cfg("right"))
    assert out.success is True


# ---------------------------------------------------------------------------
# 아바타 preset 검증
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


async def _me(s) -> TokenUser:
    uid = (await s.execute(select(ArgusUser.id))).scalar_one()
    return TokenUser(sub=str(uid), username="it-auth-user", roles=["argus-user"])


def test_preset_ids_match_frontend():
    import pathlib
    import re
    src = (pathlib.Path(__file__).resolve().parents[2] / "frontend/apps/web/lib/avatar.ts")
    if not src.exists():
        pytest.skip("frontend 소스 없음")
    ids = set(re.findall(r'buildPreset\("([^"]+)"', src.read_text()))
    assert ids == set(AVATAR_PRESET_IDS)


async def test_avatar_unknown_id_rejected(make):
    async with make() as s:
        with pytest.raises(HTTPException) as ei:
            await update_avatar(AvatarUpdateRequest(avatar_preset_id="nonexistent"), await _me(s), s)
        assert ei.value.status_code == 422
        assert "알 수 없는 아바타" in ei.value.detail
        assert (await s.execute(select(ArgusUserPreference))).first() is None


@pytest.mark.parametrize("value, stored", [("adventurer-maria", "adventurer-maria"), (None, None), ("", None)])
async def test_avatar_valid_or_reset(make, value, stored):
    async with make() as s:
        out = await update_avatar(AvatarUpdateRequest(avatar_preset_id=value), await _me(s), s)
        assert out.avatar_preset_id == stored
        pref = (await s.execute(select(ArgusUserPreference))).scalar_one()
        assert pref.avatar_preset_id == stored
