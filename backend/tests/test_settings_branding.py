# SPDX-License-Identifier: Apache-2.0
"""설정 > 사이드바 브랜드 — 제품명 · 회사명 문구와 눌렀을 때 갈 주소 (in-memory SQLite).

검증:
- 조회: 행이 없으면 기본값(Argus Catalog · Data Dynamics Inc · 주소 없음), 일반 사용자도 읽는다
- 저장: 앞뒤 공백 제거, 제품명이 비면 기본값, 회사명 · 주소는 비울 수 있음
- 주소: http(s):// 또는 / 로 시작하는 경로만 — javascript: · // 는 422
- 저장은 관리자 전용
- 로그인 화면용 공개 조회(/branding/public)는 로그인 없이 같은 값 (TODO-146)
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from app.core.auth import TokenUser, get_current_user
from app.core.database import get_session
from app.settings import router as srouter
from tests.test_admin_stage6 import factory  # noqa: F401 — pytest 픽스처

ADMIN = TokenUser(sub="admin", username="admin", roles=["argus-admin"])
USER = TokenUser(sub="u1", username="u1", roles=["argus-user"])


def _app(make, user):
    app = FastAPI()
    app.include_router(srouter.router, prefix="/api/v1")

    async def _session():
        async with make() as db:
            yield db

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: user
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_branding_defaults_and_roundtrip(factory):  # noqa: F811
    async with _client(_app(factory, ADMIN)) as c:
        r = await c.get("/api/v1/settings/branding")
        assert r.status_code == 200
        assert r.json() == {"title": "Argus Catalog", "subtitle": "Data Dynamics Inc", "link_url": ""}

        r = await c.put("/api/v1/settings/branding",
                        json={"title": "  우리 카탈로그 ", "subtitle": "", "link_url": " https://example.com "})
        assert r.status_code == 200
        assert r.json() == {"title": "우리 카탈로그", "subtitle": "", "link_url": "https://example.com"}

    # 일반 사용자도 읽는다(사이드바)
    async with _client(_app(factory, USER)) as c:
        r = await c.get("/api/v1/settings/branding")
        assert r.json() == {"title": "우리 카탈로그", "subtitle": "", "link_url": "https://example.com"}

    async with _client(_app(factory, ADMIN)) as c:
        r = await c.put("/api/v1/settings/branding", json={"title": " ", "subtitle": "팀", "link_url": "/dashboard/apis"})
        assert r.json() == {"title": "Argus Catalog", "subtitle": "팀", "link_url": "/dashboard/apis"}


async def test_branding_rejects_unsafe_url(factory):  # noqa: F811
    async with _client(_app(factory, ADMIN)) as c:
        for bad in ("javascript:alert(1)", "//evil.example", "ftp://x", "dashboard"):
            r = await c.put("/api/v1/settings/branding", json={"title": "A", "subtitle": "", "link_url": bad})
            assert r.status_code == 422, bad


async def test_branding_save_admin_only(factory):  # noqa: F811
    async with _client(_app(factory, USER)) as c:
        r = await c.put("/api/v1/settings/branding", json={"title": "A", "subtitle": "", "link_url": ""})
        assert r.status_code == 403


async def test_branding_public_without_login(factory):  # noqa: F811
    from starlette.requests import Request

    from app.core.auth_gate import is_exempt

    async with _client(_app(factory, ADMIN)) as c:
        await c.put("/api/v1/settings/branding", json={"title": "우리 카탈로그", "subtitle": "", "link_url": ""})
    app = FastAPI()
    app.include_router(srouter.router, prefix="/api/v1")

    async def _session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = _session  # 사용자 없음
    async with _client(app) as c:
        r = await c.get("/api/v1/settings/branding/public")
        assert r.json() == {"title": "우리 카탈로그", "subtitle": "", "link_url": ""}

    def req(method, path):
        return Request({"type": "http", "method": method, "path": path, "query_string": b"", "headers": []})

    assert is_exempt(req("GET", "/api/v1/settings/branding/public"))
    assert not is_exempt(req("GET", "/api/v1/settings/branding"))
    assert not is_exempt(req("PUT", "/api/v1/settings/branding"))
