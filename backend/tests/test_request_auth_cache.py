# SPDX-License-Identifier: Apache-2.0
"""요청 범위 인증 캐시와 순수 ASGI 미들웨어 (TODO-086).

로그인 게이트·비밀번호 변경 게이트·핸들러 의존성이 한 요청에서 토큰을 한 번만 검증하는지, 요청끼리는 섞이지 않는지,
게이트가 바로 돌려주는 응답에도 보안 헤더가 붙는지 본다.
"""

from __future__ import annotations

from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.core import auth
from app.core.auth import TokenUser, create_local_token, get_current_user, get_optional_user
from app.core.auth_gate import ApiAuthGateMiddleware
from app.core.config import settings
from app.core.password_gate import PasswordChangeGateMiddleware
from app.core.security import SecurityHeadersMiddleware


def _token(sub: str, must_change: bool = False) -> str:
    return create_local_token(sub=sub, username=sub, email=f"{sub}@x", first_name="", last_name="",
                              role="argus-user", must_change_password=must_change)


@pytest.fixture
def counted(monkeypatch):
    calls = {"decode": 0, "account": 0, "apikey": 0}
    real_verify = auth._verify_token_local

    def verify(token, expected_type=auth.TOKEN_TYPE_ACCESS):
        calls["decode"] += 1
        return real_verify(token, expected_type)

    async def account(user, payload):
        calls["account"] += 1
        return user

    async def apikey(raw, request):
        calls["apikey"] += 1
        return TokenUser(sub="apikey:9", username="apikey#9", roles=["argus-user"], api_key_id=9)

    monkeypatch.setattr(auth, "_verify_token_local", verify)
    monkeypatch.setattr(auth, "check_account_state", account)
    monkeypatch.setattr(auth, "authenticate_api_key", apikey)
    monkeypatch.setattr(settings, "auth_type", "local")
    return calls


@pytest.fixture
def app():
    a = FastAPI()
    a.add_middleware(PasswordChangeGateMiddleware)
    a.add_middleware(ApiAuthGateMiddleware)
    a.add_middleware(SecurityHeadersMiddleware)

    @a.get("/api/v1/me")
    async def me(user: Annotated[TokenUser, Depends(get_current_user)],
                 opt: Annotated[TokenUser | None, Depends(get_optional_user)]):
        return {"sub": user.sub, "same": opt is user}

    return a


async def _get(app, url, **kw):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(url, **kw)


async def test_token_verified_once_per_request(app, counted):
    r = await _get(app, "/api/v1/me", headers={"Authorization": f"Bearer {_token('alice')}"})
    assert r.status_code == 200 and r.json() == {"sub": "alice", "same": True}
    # 게이트 2개 + 의존성 2개(필수·선택)가 모두 같은 검증 결과를 쓴다
    assert (counted["decode"], counted["account"]) == (1, 1)
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"


async def test_cache_does_not_leak_between_requests(app, counted):
    a = await _get(app, "/api/v1/me", headers={"Authorization": f"Bearer {_token('alice')}"})
    b = await _get(app, "/api/v1/me", headers={"Authorization": f"Bearer {_token('bob')}"})
    assert (a.json()["sub"], b.json()["sub"]) == ("alice", "bob")
    assert (counted["decode"], counted["account"]) == (2, 2)


async def test_api_key_authenticated_once(app, counted):
    r = await _get(app, "/api/v1/me", headers={"X-API-Key": "argus_sk_abc"})
    assert r.status_code == 200 and r.json()["sub"] == "apikey:9"
    assert (counted["apikey"], counted["decode"]) == (1, 0)


async def test_gate_responses_carry_security_headers(app, counted):
    anon = await _get(app, "/api/v1/me")
    assert anon.status_code == 401 and anon.headers["X-Frame-Options"] == "DENY"
    bad = await _get(app, "/api/v1/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert bad.status_code == 401 and bad.headers["X-Content-Type-Options"] == "nosniff"
    must = await _get(app, "/api/v1/me", headers={"Authorization": f"Bearer {_token('carol', must_change=True)}"})
    assert must.status_code == 403 and must.json()["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert must.headers["X-Frame-Options"] == "DENY"


async def test_non_http_scope_passes_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    for mw in (SecurityHeadersMiddleware, ApiAuthGateMiddleware, PasswordChangeGateMiddleware):
        await mw(inner)({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"] * 3
