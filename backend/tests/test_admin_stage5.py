# SPDX-License-Identifier: Apache-2.0
"""관리 기능 5단계(남은 리뷰 항목·설정 알려진 이슈) 테스트 (in-memory SQLite + 가짜 외부 서버, 자기완결형).

검증:
- Keycloak 초기화: client 를 redirect "*"·standard flow·서비스 계정 없이 생성, 예전 느슨한 client 는 좁힘,
  secret 은 입력 → 저장값 → 새로 생성(고정값 "argus-client-secret" 사용 안 함), 생성/동기화한 secret 은
  같은 대상이면 설정에 암호화 저장, 다른 대상이면 저장하지 않음, Keycloak 관리자 계정 기본값 없음
- mcp.policy: 소유자·관리자 + 기능 권한 — 매트릭스로 남의 배포까지 넓어지지 않고, 해제한 역할은 자기 배포도 불가
- 버킷 초기화: 연결 실패면 "S3 Connection ok" 를 남기지 않음, 404 면 생성, 403 이면 버킷 단계 오류
- 임베딩 openai/ollama 의 빈 URL → 기본 URL
- LLM 비활성화 시 로드된 provider 해제
- SMTP 비밀번호 없으면 인증 없이 발송, 빈 값 저장은 비밀번호 삭제·마스킹 값은 유지

실행:
    cd backend
    pytest tests/test_admin_stage5.py
"""

from __future__ import annotations

import json

import httpx
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.audit import service as audit
from app.audit.models import AdminAuditLog
from app.core.auth import TokenUser
from app.core.config import settings
from app.core.database import Base
from app.mcp_servers import router as mcp_router
from app.permissions.models import Permission
from app.settings import mail as mailmod
from app.settings import router as srouter
from app.settings import service as ssvc
from app.settings.models import CatalogConfiguration

ADMIN = TokenUser(sub="admin", username="admin", roles=["argus-admin"])


@pytest.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[
            CatalogConfiguration.__table__, AdminAuditLog.__table__, Permission.__table__])
    make = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(srouter, "async_session", make)
    monkeypatch.setattr(audit, "audit_session_factory", make)
    # load_auth_settings 가 바꾸는 전역 설정은 테스트 뒤 원복
    for k in [a for a in vars(type(settings)).get("model_fields", {}) if a.startswith("auth_")] or [
        "auth_type", "auth_keycloak_server_url", "auth_keycloak_realm", "auth_keycloak_client_id",
        "auth_keycloak_client_secret", "auth_keycloak_admin_role", "auth_keycloak_superuser_role",
        "auth_keycloak_user_role",
    ]:
        monkeypatch.setattr(settings, k, getattr(settings, k))
    yield make
    await engine.dispose()


async def _set_cfg(make, category: str, items: dict[str, str]) -> None:
    async with make() as s:
        await ssvc.update_config(s, category, items)


async def _cfg(make, category: str) -> dict[str, str]:
    async with make() as s:
        return await ssvc.get_config_by_category(s, category)


async def _raw(make, key: str) -> str | None:
    async with make() as s:
        return (await s.execute(
            select(CatalogConfiguration.config_value).where(CatalogConfiguration.config_key == key))).scalar()


# ---------------------------------------------------------------------------
# Keycloak 초기화 — 가짜 Keycloak Admin API
# ---------------------------------------------------------------------------

class FakeKeycloak:
    def __init__(self, clients: list[dict] | None = None):
        self.clients = clients or []
        self.calls: list[tuple[str, str, dict | None]] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        path, method = req.url.path, req.method
        body = json.loads(req.content) if req.content and req.headers.get("content-type", "").startswith("application/json") else None
        self.calls.append((method, path, body))
        if path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "t"})
        if path == "/admin/realms/argus" and method == "GET":
            return httpx.Response(200, json={"realm": "argus"})
        if path == "/admin/realms/argus/clients" and method == "GET":
            return httpx.Response(200, json=[c for c in self.clients if c["clientId"] == req.url.params.get("clientId")])
        if path == "/admin/realms/argus/clients" and method == "POST":
            self.clients.append({**body, "id": "c1"})
            return httpx.Response(201, headers={"Location": "http://kc/admin/realms/argus/clients/c1"})
        if path.endswith("/client-secret"):
            return httpx.Response(200, json={"value": self.clients[0].get("secret", "")})
        if path.startswith("/admin/realms/argus/clients/") and method == "PUT":
            self.clients[0].update(body)
            return httpx.Response(204)
        if path.startswith("/admin/realms/argus/roles/"):
            return httpx.Response(200, json={})
        return httpx.Response(404)


@pytest.fixture
def fake_kc(monkeypatch):
    kc = FakeKeycloak()
    orig = httpx.AsyncClient

    class _Client(orig):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(kc.handler)
            super().__init__(*a, **k)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return kc


def _init_req(**kw) -> srouter.KeycloakInitRequest:
    return srouter.KeycloakInitRequest(**{"server_url": "http://kc", "admin_username": "admin",
                                          "admin_password": "pw", **kw})


async def test_keycloak_init_creates_tight_client_and_generates_secret(factory, fake_kc):
    res = await srouter.initialize_keycloak(ADMIN, _init_req())
    created = fake_kc.clients[0]
    assert created["redirectUris"] == [] and created["webOrigins"] == []
    assert created["standardFlowEnabled"] is False and created["serviceAccountsEnabled"] is False
    assert created["directAccessGrantsEnabled"] is True
    secret = created["secret"]
    assert secret and secret != "argus-client-secret" and len(secret) >= 32
    # 새로 만든 secret 은 설정에 암호화 저장
    assert (await _cfg(factory, "auth"))["auth_keycloak_client_secret"] == secret
    assert (await _raw(factory, "auth_keycloak_client_secret")).startswith("enc:")
    assert any(st["step"] == "설정 저장" and st["status"] == "created" for st in res["steps"])
    assert not any(st["status"] == "error" for st in res["steps"])


def test_realm_json_client_matches_init_settings():
    """개발용 realm 가져오기 파일(deploy/config/keycloak/argus-realm.json)의 client 도 자동 초기화와 같은
    최소 구성 — password/refresh grant(Direct Access Grants)만, 리다이렉트 로그인·서비스 계정·redirect URI 없음."""
    from pathlib import Path
    realm = json.loads((Path(__file__).resolve().parents[2] / "deploy/config/keycloak/argus-realm.json")
                       .read_text(encoding="utf-8"))
    client = next(c for c in realm["clients"] if c["clientId"] == "argus-client")
    for key, value in srouter.KEYCLOAK_CLIENT_SETTINGS.items():
        assert client.get(key) == value, key
    assert {r["name"] for r in realm["roles"]["realm"]} == set(srouter.KeycloakInitRequest.model_fields["roles"].default)


async def test_keycloak_init_tightens_existing_loose_client(factory, fake_kc):
    await _set_cfg(factory, "auth", {"auth_keycloak_server_url": "http://kc", "auth_keycloak_realm": "argus",
                                     "auth_keycloak_client_id": "argus-client",
                                     "auth_keycloak_client_secret": "stored-secret"})
    fake_kc.clients.append({"id": "c1", "clientId": "argus-client", "secret": "stored-secret",
                            "redirectUris": ["*"], "webOrigins": ["*"], "standardFlowEnabled": True,
                            "serviceAccountsEnabled": True, "directAccessGrantsEnabled": True})
    res = await srouter.initialize_keycloak(ADMIN, _init_req(client_secret=srouter._SECRET_MASK))
    c = fake_kc.clients[0]
    assert c["redirectUris"] == [] and c["webOrigins"] == [] and c["standardFlowEnabled"] is False
    step = next(st for st in res["steps"] if st["step"] == "Client 'argus-client'")
    assert step["status"] == "created" and "redirectUris" in step["message"]
    # 마스킹 값 → 저장된 secret 사용, 이미 일치 → 설정 저장 단계 없음
    assert next(st for st in res["steps"] if st["step"] == "Client Secret")["status"] == "skip"
    assert not any(st["step"] == "설정 저장" for st in res["steps"])


async def test_keycloak_init_other_target_does_not_overwrite_saved_secret(factory, fake_kc):
    await _set_cfg(factory, "auth", {"auth_keycloak_server_url": "http://other", "auth_keycloak_realm": "prod",
                                     "auth_keycloak_client_id": "argus-client",
                                     "auth_keycloak_client_secret": "prod-secret"})
    res = await srouter.initialize_keycloak(ADMIN, _init_req(client_secret="typed-secret"))
    assert fake_kc.clients[0]["secret"] == "typed-secret"
    assert (await _cfg(factory, "auth"))["auth_keycloak_client_secret"] == "prod-secret"
    assert next(st for st in res["steps"] if st["step"] == "설정 저장")["status"] == "skip"


def test_keycloak_init_requires_admin_credentials():
    with pytest.raises(ValidationError):
        srouter.KeycloakInitRequest(server_url="http://kc")
    with pytest.raises(ValidationError):
        srouter.KeycloakInitRequest(server_url="http://kc", admin_username="admin", admin_password="")


# ---------------------------------------------------------------------------
# mcp.policy
# ---------------------------------------------------------------------------

@pytest.fixture
def deployment(monkeypatch):
    async def created_by(_s, name):
        return "owner" if name == "dep" else None

    async def exists(_s, name):
        return name == "dep"

    monkeypatch.setattr(mcp_router.service, "get_deployment_created_by", created_by)
    monkeypatch.setattr(mcp_router.service, "deployment_exists", exists)


async def _allow(make, roles: list[str]) -> None:
    async with make() as s:
        for r in roles:
            s.add(Permission(kind="FEATURE", perm_key="mcp.policy", role_id=r))
        await s.commit()


async def _guard(make, user: TokenUser, name: str = "dep") -> int:
    async with make() as s:
        try:
            await mcp_router._guard_policy(user, s, name)
            return 200
        except HTTPException as e:
            return e.status_code


async def test_mcp_policy_matrix_never_widens_beyond_owner(factory, deployment):
    owner_su = TokenUser(sub="owner", username="owner", roles=["argus-superuser"])
    other_su = TokenUser(sub="x", username="other", roles=["argus-superuser"])
    owner_user = TokenUser(sub="owner", username="owner", roles=["argus-user"])
    # 미설정: 소유자·관리자
    assert await _guard(factory, owner_user) == 200
    assert await _guard(factory, other_su) == 403
    assert await _guard(factory, ADMIN) == 200
    assert await _guard(factory, ADMIN, "nope") == 404
    # User 만 해제(Superuser 허용) — 예전에는 다른 Superuser 도 남의 배포 정책을 바꿀 수 있었다
    await _allow(factory, ["argus-superuser"])
    assert await _guard(factory, other_su) == 403
    assert await _guard(factory, owner_su) == 200
    # 해제된 역할은 자기 배포여도 불가
    assert await _guard(factory, owner_user) == 403
    assert await _guard(factory, ADMIN) == 200


# ---------------------------------------------------------------------------
# 버킷 초기화
# ---------------------------------------------------------------------------

class _FakeS3:
    def __init__(self, head_exc: Exception | None):
        self.head_exc = head_exc
        self.created: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def head_bucket(self, Bucket):  # noqa: N803 (boto3 인자 이름)
        if self.head_exc:
            raise self.head_exc

    async def create_bucket(self, Bucket):  # noqa: N803
        self.created.append(Bucket)


def _patch_s3(monkeypatch, head_exc):
    fake = _FakeS3(head_exc)

    class _Session:
        def client(self, *a, **k):
            return fake

    monkeypatch.setattr(srouter.aioboto3, "Session", _Session)
    return fake


async def _init_bucket():
    req = srouter.ObjectStorageTestRequest(endpoint="http://s3", access_key="a", secret_key="s", bucket="b1")
    return {st["step"]: st for st in (await srouter.initialize_object_storage(ADMIN, req))["steps"]}


async def test_bucket_init_connection_failure_not_reported_ok(factory, monkeypatch):
    _patch_s3(monkeypatch, EndpointConnectionError(endpoint_url="http://s3"))
    steps = await _init_bucket()
    assert steps["S3 Connection"]["status"] == "error"
    assert "Bucket 'b1'" not in steps


async def test_bucket_init_creates_missing_bucket(factory, monkeypatch):
    fake = _patch_s3(monkeypatch, ClientError({"Error": {"Code": "404"}}, "HeadBucket"))
    steps = await _init_bucket()
    assert steps["S3 Connection"]["status"] == "ok"
    assert steps["Bucket 'b1'"]["status"] == "created" and fake.created == ["b1"]


async def test_bucket_init_forbidden_is_bucket_error(factory, monkeypatch):
    fake = _patch_s3(monkeypatch, ClientError({"Error": {"Code": "403"}}, "HeadBucket"))
    steps = await _init_bucket()
    assert steps["S3 Connection"]["status"] == "ok"
    assert steps["Bucket 'b1'"]["status"] == "error" and fake.created == []


async def test_bucket_init_existing_bucket_skipped(factory, monkeypatch):
    _patch_s3(monkeypatch, None)
    steps = await _init_bucket()
    assert steps["S3 Connection"]["status"] == "ok" and steps["Bucket 'b1'"]["status"] == "skip"


# ---------------------------------------------------------------------------
# 임베딩 빈 URL · LLM 비활성화
# ---------------------------------------------------------------------------

def test_embedding_empty_url_uses_default():
    from app.embedding.registry import create_provider
    p = create_provider({"embedding_provider": "ollama", "embedding_api_url": "", "embedding_model": "m"})
    assert p._base_url == "http://localhost:11434"
    p = create_provider({"embedding_provider": "ollama", "embedding_api_url": "http://e:1/", "embedding_model": "m"})
    assert p._base_url == "http://e:1"


async def test_llm_disable_releases_provider(factory, monkeypatch):
    from app.ai import registry

    class _P:
        closed = False

        async def close(self):
            _P.closed = True

    monkeypatch.setattr(registry, "_current_provider", _P())
    await _set_cfg(factory, "llm", {"llm_enabled": "false"})
    async with factory() as s:
        await ssvc.load_llm_settings(s)
    assert await registry.get_provider() is None and _P.closed


# ---------------------------------------------------------------------------
# SMTP 무인증
# ---------------------------------------------------------------------------

async def test_smtp_without_password_sends_unauthenticated(monkeypatch):
    import aiosmtplib
    sent: list[dict] = []

    async def fake_send(msg, **kw):
        sent.append(kw)

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    cfg = mailmod.EmailConfig(enabled=True, smtp_host="relay", smtp_port=25, use_tls=False,
                              from_email="noreply@it.data-dynamics.io", smtp_password="")
    assert await mailmod.send_mail(cfg, "s", "b", to=["a@it.data-dynamics.io"], raise_errors=True)
    assert sent[-1]["username"] is None and sent[-1]["password"] is None
    cfg.smtp_password = "pw"
    await mailmod.send_mail(cfg, "s", "b", to=["a@it.data-dynamics.io"])
    assert sent[-1]["username"] == "noreply@it.data-dynamics.io" and sent[-1]["password"] == "pw"


async def test_mail_password_mask_keeps_empty_clears(factory):
    base = mailmod.EmailConfig(enabled=True, from_email="noreply@it.data-dynamics.io", smtp_password="pw1")
    async with factory() as s:
        await srouter.update_mail_config(ADMIN, base, s)
        assert (await _cfg(factory, "mail"))["mail_smtp_password"] == "pw1"
        await srouter.update_mail_config(ADMIN, base.model_copy(update={"smtp_password": srouter._SECRET_MASK}), s)
        assert (await _cfg(factory, "mail"))["mail_smtp_password"] == "pw1"
        await srouter.update_mail_config(ADMIN, base.model_copy(update={"smtp_password": ""}), s)
        assert (await _cfg(factory, "mail"))["mail_smtp_password"] == ""
