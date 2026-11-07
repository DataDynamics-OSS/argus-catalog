# SPDX-License-Identifier: Apache-2.0
"""관리 기능 6단계 — 설정 값 출처(기본값/설정 파일/화면에서 변경) 테스트 (in-memory SQLite, 자기완결형).

검증:
- 시드 값 그대로면 설정 파일이 정한 키는 config, 아니면 default
- 화면에서 바꾼 값은 db, 설정 파일이 정한 키면 differs_from_file + file_value(비밀값은 값 없이)
- 시드 대상이 아닌 키는 db, AI 어시스턴트 enabled 는 agent.url 로 정해짐
- ARGUS_AUTH_FORCE_LOCAL 이면 auth_type 에 override
- GET /settings/sources 는 관리자 전용

실행:
    cd backend
    pytest tests/test_admin_stage6.py
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.audit import service as audit
from app.audit.models import AdminAuditLog
from app.core import config as cfgmod
from app.core.auth import TokenUser, get_current_user
from app.core.database import Base, get_session
from app.settings import router as srouter
from app.settings import service as ssvc
from app.settings.models import CatalogConfiguration

FILE = {
    "auth": {"type": "keycloak", "keycloak": {"server_url": "http://kc:8080", "client_secret": "file-secret"}},
    "object_storage": {"endpoint": "http://minio:9000"},
    "assistant": {"agent.url": "http://agent:8930"},
}


@pytest.fixture
async def factory(monkeypatch):
    monkeypatch.setattr(cfgmod, "_raw", FILE)
    monkeypatch.delenv("ARGUS_AUTH_FORCE_LOCAL", raising=False)
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all,
                            tables=[CatalogConfiguration.__table__, AdminAuditLog.__table__])
    make = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(audit, "audit_session_factory", make)
    # 지금 설정 파일(FILE) 기준으로 시드
    async with make() as s:
        for cat, items in ssvc._all_defaults(cfgmod.Settings()):
            for key, value, desc in items:
                s.add(CatalogConfiguration(category=cat, config_key=key,
                                           config_value=ssvc.seal_value(key, value), description=desc))
        await s.commit()
    yield make
    await engine.dispose()


async def _sources(make) -> dict:
    async with make() as s:
        return await ssvc.config_sources(s)


async def test_seeded_values_are_config_or_default(factory):
    src = await _sources(factory)
    assert src["auth"]["auth_type"]["source"] == "config"
    assert src["auth"]["auth_keycloak_server_url"]["source"] == "config"
    assert src["auth"]["auth_keycloak_realm"]["source"] == "default"      # 파일에 없음
    assert src["object_storage"]["object_storage_endpoint"]["source"] == "config"
    assert src["object_storage"]["object_storage_bucket"]["source"] == "default"
    assert src["mail"]["mail_smtp_host"]["source"] == "default"
    assert src["assistant"]["assistant_enabled"]["source"] == "config"     # agent.url 로 true
    assert src["auth"]["auth_keycloak_client_secret"]["secret"] is True
    assert not any(i.get("differs_from_file") for cat in src.values() for i in cat.values())


async def test_changed_values_are_db_and_flag_file_drift(factory):
    async with factory() as s:
        await ssvc.update_config(s, "mail", {"mail_smtp_host": "relay.local"})
        await ssvc.update_config(s, "object_storage", {"object_storage_endpoint": "http://other:9000"})
        await ssvc.update_config(s, "auth", {"auth_keycloak_client_secret": "changed-secret",
                                            "auth_keycloak_realm": "prod"})
        s.add(CatalogConfiguration(category="cache", config_key="x_custom", config_value="1"))
        await s.commit()
    src = await _sources(factory)
    mail = src["mail"]["mail_smtp_host"]
    assert mail["source"] == "db" and "differs_from_file" not in mail      # 파일이 정하지 않는 키
    ep = src["object_storage"]["object_storage_endpoint"]
    assert ep == {**ep, "source": "db", "differs_from_file": True, "file_value": "http://minio:9000"}
    sec = src["auth"]["auth_keycloak_client_secret"]
    assert sec["source"] == "db" and sec["differs_from_file"] and "file_value" not in sec
    assert src["auth"]["auth_keycloak_realm"]["source"] == "db"
    assert "differs_from_file" not in src["auth"]["auth_keycloak_realm"]
    assert src["cache"]["x_custom"]["source"] == "db"


async def test_force_local_override(factory, monkeypatch):
    monkeypatch.setenv("ARGUS_AUTH_FORCE_LOCAL", "true")
    src = await _sources(factory)
    assert "ARGUS_AUTH_FORCE_LOCAL" in src["auth"]["auth_type"]["override"]


async def test_sources_endpoint_admin_only(factory):
    app = FastAPI()
    app.include_router(srouter.router, prefix="/api/v1")

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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/v1/settings/sources")).status_code == 401
        who["user"] = TokenUser(sub="u", username="u", roles=["argus-user"])
        assert (await c.get("/api/v1/settings/sources")).status_code == 403
        who["user"] = TokenUser(sub="admin", username="admin", roles=["argus-admin"])
        r = await c.get("/api/v1/settings/sources")
        assert r.status_code == 200 and r.json()["auth"]["auth_type"]["source"] == "config"
        # 비밀값은 어떤 경우에도 값이 응답에 없다
        assert "file-secret" not in r.text


def test_assistant_service_url_prefers_new_key(monkeypatch):
    monkeypatch.setattr(cfgmod, "_raw", {"assistant": {"service.url": "http://ai:8930", "agent.url": "http://old:8930"}})
    assert cfgmod.Settings().assistant_agent_url == "http://ai:8930"
    assert ssvc._file_path("assistant_agent_url") == ("assistant", "service.url")
    # 이전 이름만 있으면 그 값을 쓴다 (한 버전 유지)
    monkeypatch.setattr(cfgmod, "_raw", {"assistant": {"service.url": "", "agent.url": "http://old:8930"}})
    assert cfgmod.Settings().assistant_agent_url == "http://old:8930"
    assert ssvc._file_path("assistant_agent_url") == ("assistant", "agent.url")


async def test_seed_refreshes_description_keeps_value(factory):
    async with factory() as s:
        await ssvc.update_config(s, "assistant", {"assistant_agent_url": "http://mine:8930"})
        row = (await s.execute(select(CatalogConfiguration).where(
            CatalogConfiguration.config_key == "assistant_agent_url"))).scalars().one()
        row.description = "Agent serve URL (old)"
        await s.commit()
    async with factory() as s:
        await ssvc.seed_configuration(s)
    async with factory() as s:
        row = (await s.execute(select(CatalogConfiguration).where(
            CatalogConfiguration.config_key == "assistant_agent_url"))).scalars().one()
        assert row.config_value == "http://mine:8930"          # 값은 유지
        assert row.description.startswith("AI service assistant URL")
