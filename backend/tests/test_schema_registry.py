# SPDX-License-Identifier: Apache-2.0
"""Confluent 호환 스키마 레지스트리 (TODO-052) — in-memory SQLite.

- 최신 버전 등록·캐시 적중, id·버전 조회가 바뀌지 않음
- Avro 입력(필드·설명·Avro 편집본)이 바뀐 커밋 뒤 캐시 무효화와 새 버전, 같은 본문이면 같은 id
- Avro 와 무관한 변경(pii_type)은 캐시를 지우지 않음
- POST /subjects/{subject} 조회, Confluent 오류 형식, 쓰기 거부
- Basic 인증: 성공 자격 기억(비밀번호 재검증 생략), 비밀번호 변경 시 무효, 실패 횟수, Keycloak 모드 거부
- 인증 게이트 예외 경로

실행:
    cd backend
    PYTHONPATH=. python -m pytest -q tests/test_schema_registry.py
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
import app.core.auth as core_auth
import app.external.cache as cache_mod
import app.schema_registry.changes  # noqa: F401 — 세션 이벤트 등록
from app.core.config import settings
from app.core.passwords import hash_password
from app.schema_registry import auth as sr_auth
from app.schema_registry import service as sr_service
from app.schema_registry.models import SchemaRegistryVersion
from app.usermgr.models import ArgusRole, ArgusUser

URN = "pg-abc.shop.orders.dataset"
SUBJECT_PATH = f"/api/v1/schema-registry/subjects/{URN}"


def _tables():
    names = [cm.Dataset.__table__, cm.DatasetSchema.__table__, cm.DatasetProperty.__table__,
             cm.DatasetUrnAlias.__table__, SchemaRegistryVersion.__table__,
             ArgusUser.__table__, ArgusRole.__table__]
    pending, seen, out = list(names), set(), []
    while pending:
        t = pending.pop()
        if t.name in seen:
            continue
        seen.add(t.name)
        out.append(t)
        pending.extend(fk.column.table for fk in t.foreign_keys)
    return out


async def _make_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: cm.Base.metadata.create_all(c, tables=_tables()))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        ds = cm.Datasource(datasource_id="pg-abc", name="pg", type="postgresql")
        db.add(ds)
        await db.flush()
        d = cm.Dataset(urn=URN, name="orders", qualified_name="pg-abc.shop.orders", datasource_id=ds.id,
                       description="주문")
        db.add(d)
        await db.flush()
        db.add_all([
            cm.DatasetSchema(dataset_id=d.id, field_path="order_id", field_type="NUMBER",
                             native_type="bigint", nullable="false", ordinal=0),
            cm.DatasetSchema(dataset_id=d.id, field_path="amount", field_type="NUMBER",
                             native_type="decimal(12,2)", nullable="true", ordinal=1),
        ])
        role = ArgusRole(role_id="argus-user", name="User")
        db.add(role)
        await db.flush()
        db.add(ArgusUser(username="svc-flow", email="svc@x.io", first_name="f", last_name="l",
                         password_hash=hash_password("secret", iterations=1000), role_id=role.id))
        await db.commit()
    return factory


@pytest.fixture
def env(monkeypatch):
    factory = asyncio.run(_make_factory())
    monkeypatch.setattr(cache_mod, "_cache", cache_mod.MetadataCache(max_size=100, ttl_seconds=60))
    sr_service.reset_caches()
    sr_auth.reset_cache()
    monkeypatch.setattr(sr_auth, "session_factory", factory)
    monkeypatch.setattr(core_auth, "account_session_factory", factory)
    core_auth.invalidate_account_cache()
    monkeypatch.setattr(settings, "auth_type", "local")
    monkeypatch.setattr(settings, "cache_enabled", True)
    monkeypatch.setattr(settings, "schema_registry_enabled", True)
    monkeypatch.setattr(settings, "schema_registry_basic_auth", True)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.database import get_session
    from app.schema_registry.router import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = _session
    client = TestClient(app)
    client.headers["Authorization"] = _basic("svc-flow", "secret")
    return client, factory


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def _run(factory, fn):
    async def go():
        async with factory() as db:
            await fn(db)
            await db.commit()
    asyncio.run(go())


def _dataset_id(factory) -> int:
    async def go():
        async with factory() as db:
            return (await db.execute(select(cm.Dataset.id).where(cm.Dataset.urn == URN))).scalar_one()
    return asyncio.run(go())


def _set_field(factory, path: str, **values):
    async def fn(db):
        row = (await db.execute(select(cm.DatasetSchema).where(cm.DatasetSchema.field_path == path))).scalar_one()
        for k, v in values.items():
            setattr(row, k, v)
    _run(factory, fn)


# ---------------------------------------------------------------------------
# 조회·버전
# ---------------------------------------------------------------------------

def test_latest_registers_and_caches(env):
    c, _ = env
    r = c.get(f"{SUBJECT_PATH}/versions/latest")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.schemaregistry.v1+json")
    assert r.headers["x-cache-hit"] == "false"
    body = r.json()
    assert body["subject"] == URN and body["version"] == 1
    schema = json.loads(body["schema"])
    assert schema["type"] == "record" and [f["name"] for f in schema["fields"]] == ["order_id", "amount"]

    r2 = c.get(f"{SUBJECT_PATH}/versions/latest")
    assert r2.headers["x-cache-hit"] == "true"
    assert r2.json() == body

    r3 = c.get(f"/api/v1/schema-registry/schemas/ids/{body['id']}")
    assert r3.status_code == 200 and r3.json() == {"schema": body["schema"]}
    assert "immutable" in r3.headers["cache-control"]
    assert c.get(f"/api/v1/schema-registry/schemas/ids/{body['id']}").headers["x-cache-hit"] == "true"
    assert c.get(f"/api/v1/schema-registry/schemas/ids/{body['id']}/subjects").json() == [URN]
    assert c.get(f"/api/v1/schema-registry/schemas/ids/{body['id']}/versions").json() == [
        {"subject": URN, "version": 1}]
    assert c.get(f"{SUBJECT_PATH}/versions").json() == [1]
    assert c.get(f"{SUBJECT_PATH}/versions/1/schema").text == body["schema"]
    assert URN in c.get("/api/v1/schema-registry/subjects").json()


def test_field_change_invalidates_and_adds_version(env):
    c, factory = env
    v1 = c.get(f"{SUBJECT_PATH}/versions/latest").json()

    _set_field(factory, "amount", field_type="STRING", native_type="varchar(20)")
    r = c.get(f"{SUBJECT_PATH}/versions/latest")
    assert r.headers["x-cache-hit"] == "false"          # 커밋 이벤트가 캐시를 지웠다
    v2 = r.json()
    assert v2["version"] == 2 and v2["id"] != v1["id"]
    assert '"string"' in v2["schema"]

    # 예전 id·버전은 그대로
    assert c.get(f"/api/v1/schema-registry/schemas/ids/{v1['id']}").json()["schema"] == v1["schema"]
    assert c.get(f"{SUBJECT_PATH}/versions/1").json()["id"] == v1["id"]
    assert c.get(f"{SUBJECT_PATH}/versions").json() == [1, 2]

    # 되돌리면 같은 본문 → 같은 id(버전 1)
    _set_field(factory, "amount", field_type="NUMBER", native_type="decimal(12,2)")
    back = c.get(f"{SUBJECT_PATH}/versions/latest").json()
    assert back["id"] == v1["id"] and back["version"] == 1


def test_description_change_invalidates_but_pii_type_does_not(env):
    c, factory = env
    c.get(f"{SUBJECT_PATH}/versions/latest")

    _set_field(factory, "amount", pii_type="NAME")       # Avro 와 무관
    assert c.get(f"{SUBJECT_PATH}/versions/latest").headers["x-cache-hit"] == "true"

    async def describe(db):
        d = (await db.execute(select(cm.Dataset).where(cm.Dataset.urn == URN))).scalar_one()
        d.description = "주문 원장"
    _run(factory, describe)
    r = c.get(f"{SUBJECT_PATH}/versions/latest")
    assert r.headers["x-cache-hit"] == "false"
    assert json.loads(r.json()["schema"])["doc"] == "주문 원장"


def test_avro_override_set_and_cleared(env):
    c, factory = env
    auto = c.get(f"{SUBJECT_PATH}/versions/latest").json()
    dataset_id = _dataset_id(factory)
    override = {"type": "record", "name": "orders", "namespace": "shop",
                "fields": [{"name": "order_id", "type": "long"}]}

    from app.catalog.service import update_dataset_properties

    async def set_override(db):
        await update_dataset_properties(db, dataset_id, [{"key": "argus.avro_schema", "value": json.dumps(override)}])
    _run(factory, set_override)
    edited = c.get(f"{SUBJECT_PATH}/versions/latest").json()
    assert json.loads(edited["schema"]) == override and edited["version"] == 2

    async def clear_override(db):   # 빈 값 = 삭제 — 대량 DELETE 경로(mark_changed)
        await update_dataset_properties(db, dataset_id, [{"key": "argus.avro_schema", "value": ""}])
    _run(factory, clear_override)
    again = c.get(f"{SUBJECT_PATH}/versions/latest").json()
    assert again["id"] == auto["id"]


def test_lookup_by_schema_and_errors(env):
    c, _ = env
    v1 = c.get(f"{SUBJECT_PATH}/versions/latest").json()
    pretty = json.dumps(json.loads(v1["schema"]), indent=2)   # 공백이 달라도 같은 스키마
    r = c.post(SUBJECT_PATH, content=json.dumps({"schema": pretty}),
               headers={"Content-Type": "application/vnd.schemaregistry.v1+json"})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == v1["id"] and r.json()["version"] == 1

    other = json.dumps({"type": "record", "name": "x", "fields": []})
    r = c.post(SUBJECT_PATH, json={"schema": other})
    assert r.status_code == 404 and r.json()["error_code"] == 40403
    assert c.post(SUBJECT_PATH, json={"schema": "{not json"}).json()["error_code"] == 42201

    r = c.get("/api/v1/schema-registry/subjects/nope.dataset/versions/latest")
    assert r.status_code == 404 and r.json()["error_code"] == 40401
    assert c.get(f"{SUBJECT_PATH}/versions/abc").json()["error_code"] == 42202
    assert c.get(f"{SUBJECT_PATH}/versions/0").json()["error_code"] == 42202
    assert c.get(f"{SUBJECT_PATH}/versions/9").json()["error_code"] == 40402
    assert c.get("/api/v1/schema-registry/schemas/ids/999").json()["error_code"] == 40403
    assert c.post(f"{SUBJECT_PATH}/versions", json={"schema": other}).status_code == 405
    assert c.delete(SUBJECT_PATH).status_code == 405
    assert c.get("/api/v1/schema-registry/config").json() == {"compatibilityLevel": "NONE"}
    assert c.get("/api/v1/schema-registry/schemas/types").json() == ["AVRO"]


# ---------------------------------------------------------------------------
# 인증
# ---------------------------------------------------------------------------

def test_basic_auth_remembers_verified_credentials(env, monkeypatch):
    c, _ = env
    calls = {"n": 0}
    real = sr_auth.verify_password_async

    async def counting(password, stored):
        calls["n"] += 1
        return await real(password, stored)

    monkeypatch.setattr(sr_auth, "verify_password_async", counting)
    for _ in range(5):
        assert c.get("/api/v1/schema-registry/subjects").status_code == 200
    assert calls["n"] == 1          # 처음 한 번만 비밀번호를 검증


def test_basic_auth_rejects_after_password_change(env):
    c, factory = env
    assert c.get("/api/v1/schema-registry/subjects").status_code == 200

    async def change(db):
        u = (await db.execute(select(ArgusUser).where(ArgusUser.username == "svc-flow"))).scalar_one()
        u.password_hash = hash_password("new-secret", iterations=1000)
    _run(factory, change)
    core_auth.invalidate_account_cache()   # 비밀번호 변경 API 가 하는 일

    assert c.get("/api/v1/schema-registry/subjects").status_code == 401
    r = c.get("/api/v1/schema-registry/subjects", headers={"Authorization": _basic("svc-flow", "new-secret")})
    assert r.status_code == 200


def test_basic_auth_failures_counted_and_errors(env):
    c, factory = env
    bad = {"Authorization": _basic("svc-flow", "wrong")}
    r = c.get("/api/v1/schema-registry/subjects", headers=bad)
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")

    async def failures():
        async with factory() as db:
            return (await db.execute(
                select(ArgusUser.failed_login_count).where(ArgusUser.username == "svc-flow"))).scalar_one()
    assert asyncio.run(failures()) == 1

    assert c.get("/api/v1/schema-registry/subjects", headers={"Authorization": ""}).status_code == 401
    assert c.get("/api/v1/schema-registry/subjects", headers={"Authorization": "Basic !!!"}).status_code == 401


def test_basic_auth_disabled_in_keycloak_mode(env, monkeypatch):
    c, _ = env
    monkeypatch.setattr(settings, "auth_type", "keycloak")
    assert c.get("/api/v1/schema-registry/subjects").status_code == 401


def test_gate_exempts_registry_paths():
    from starlette.requests import Request

    from app.core import auth_gate

    def req(path):
        return Request({"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": []})

    assert auth_gate.is_exempt(req("/api/v1/schema-registry"))
    assert auth_gate.is_exempt(req("/api/v1/schema-registry/subjects/x/versions/latest"))
    assert not auth_gate.is_exempt(req("/api/v1/schema-registry-x"))
