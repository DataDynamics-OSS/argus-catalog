# SPDX-License-Identifier: Apache-2.0
"""설정 > 수집 서비스(metadata-sync) — 화면에서 주소·토큰·이력 보관 기간 지정 (in-memory SQLite).

검증:
- 조회: 시드(설정 파일 값), 토큰은 값 대신 설정 여부만
- 저장: 주소 정규화(끝 / 제거), http(s) 아닌 주소 422, 토큰 암호문 저장, 저장 즉시 in-memory settings 반영
- 토큰: 생략하면 유지, 빈 값이면 지움
- 연결 테스트: /v2/status 성공·인증 거부·없는 API·연결 실패, 저장된 토큰 사용
- 관리자 전용

실행:
    cd backend
    pytest tests/test_settings_metadata_sync.py
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from app.core.auth import TokenUser, get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.settings import router as srouter
from app.settings.models import CatalogConfiguration
from tests.test_admin_stage6 import factory  # noqa: F401 — pytest 픽스처

ADMIN = TokenUser(sub="admin", username="admin", roles=["argus-admin"])


def _app(make, user):
    app = FastAPI()
    app.include_router(srouter.router, prefix="/api/v1")

    async def _session():
        async with make() as db:
            yield db

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: user
    return app


async def test_metadata_sync_settings_roundtrip(factory, monkeypatch):  # noqa: F811
    for attr in ("metadata_sync_base_url", "metadata_sync_api_token", "sync_run_retention_days"):
        monkeypatch.setattr(settings, attr, getattr(settings, attr))
    app = _app(factory, ADMIN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/settings/metadata-sync")
        assert r.status_code == 200
        assert r.json() == {"base_url": "http://localhost:4610", "api_token_set": False, "run_retention_days": 90}

        r = await c.put("/api/v1/settings/metadata-sync",
                        json={"base_url": " http://sync:4610/ ", "api_token": "tok-1", "run_retention_days": 30})
        assert r.status_code == 200
        # 저장 즉시 반영(재시작 불필요)
        assert (settings.metadata_sync_base_url, settings.metadata_sync_api_token, settings.sync_run_retention_days) \
            == ("http://sync:4610", "tok-1", 30)
        async with factory() as s:
            stored = (await s.execute(select(CatalogConfiguration.config_value).where(
                CatalogConfiguration.config_key == "metadata_sync_api_token"))).scalar_one()
        assert stored.startswith("enc:") and "tok-1" not in stored

        r = await c.get("/api/v1/settings/metadata-sync")
        assert r.json() == {"base_url": "http://sync:4610", "api_token_set": True, "run_retention_days": 30}
        assert "tok-1" not in r.text

        # 토큰 생략 → 유지, 빈 값 → 지움
        await c.put("/api/v1/settings/metadata-sync", json={"base_url": "http://sync:4610", "run_retention_days": 30})
        assert settings.metadata_sync_api_token == "tok-1"
        await c.put("/api/v1/settings/metadata-sync",
                    json={"base_url": "http://sync:4610", "api_token": "", "run_retention_days": 30})
        assert settings.metadata_sync_api_token == ""

        assert (await c.put("/api/v1/settings/metadata-sync",
                            json={"base_url": "ftp://x", "run_retention_days": 30})).status_code == 422
        assert (await c.put("/api/v1/settings/metadata-sync",
                            json={"base_url": "http://x", "run_retention_days": 0})).status_code == 422

        # 값 출처 패널 — 화면에서 바꾼 값
        src = (await c.get("/api/v1/settings/sources")).json()["metadata_sync"]
        assert src["metadata_sync_base_url"]["source"] == "db"


async def test_metadata_sync_connection_test(factory, monkeypatch):  # noqa: F811
    app = _app(factory, ADMIN)
    seen: list[httpx.Request] = []
    replies = {"mode": "ok"}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if replies["mode"] == "down":
            raise httpx.ConnectError("refused", request=req)
        return {
            "ok": httpx.Response(200, json={"workers": 4, "running": 1, "queued": 0, "version": "0.5.0"}),
            "auth": httpx.Response(401, json={"detail": "no"}),
            "old": httpx.Response(404, json={"detail": "Not Found"}),
        }[replies["mode"]]

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        real = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient",
                            lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}))
        r = (await c.post("/api/v1/settings/metadata-sync/test", json={"base_url": "http://sync:4610/",
                                                                       "api_token": "t"})).json()
        assert r["success"] and "워커 4개" in r["message"]
        assert str(seen[-1].url) == "http://sync:4610/v2/status" and seen[-1].headers["X-API-Token"] == "t"
        replies["mode"] = "auth"
        assert "인증" in (await c.post("/api/v1/settings/metadata-sync/test", json={"base_url": "http://s"})).json()["message"]
        replies["mode"] = "old"
        assert "/v2/status" in (await c.post("/api/v1/settings/metadata-sync/test",
                                             json={"base_url": "http://s"})).json()["message"]
        replies["mode"] = "down"
        r = (await c.post("/api/v1/settings/metadata-sync/test", json={"base_url": "http://s"})).json()
        assert r["success"] is False and "연결할 수 없습니다" in r["message"]


async def test_metadata_sync_settings_admin_only(factory):  # noqa: F811
    app = _app(factory, TokenUser(sub="u", username="u", roles=["argus-user"]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/v1/settings/metadata-sync")).status_code == 403
        assert (await c.put("/api/v1/settings/metadata-sync", json={"base_url": "http://x"})).status_code == 403
