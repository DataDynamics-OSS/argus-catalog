# SPDX-License-Identifier: Apache-2.0
"""데이터셋 직접 등록 정책 — 설정 옵션 ``catalog_dataset_create_by_users`` (자기완결형, in-memory SQLite).

- 기본(옵션 꺼짐): 일반 사용자의 POST /catalog/datasets 는 403(한국어 사유), ``/permissions/me`` 의
  ``denied_features`` 에 ``datasets.create`` 가 들어간다(화면의 '데이터셋 추가' 버튼이 숨는다).
- 옵션을 켜면 일반 사용자도 등록 가능 — 그때도 기능 권한 ``datasets.create`` 매트릭스는 계속 적용.
- 관리자는 옵션·매트릭스와 무관하게 항상 가능.
- 동기화 계정(외부 metadata-sync 는 같은 POST 로 데이터셋을 만든다): ``datasources.sync`` 가 명시 허용된
  역할은 옵션이 꺼져 있어도 가능 — 동기화로 데이터셋이 생기는 경로를 막지 않는다.

실행:
    cd backend
    pytest tests/test_dataset_create_policy.py
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
from app.audit.models import AdminAuditLog
from app.catalog.router import router as catalog_router
from app.core.auth import TokenUser, get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.permissions import router as perm
from app.permissions.models import Permission
from app.settings import service as ssvc
from app.settings.models import CatalogConfiguration
from app.settings.router import router as settings_router

_TABLES = [
    "catalog_organizations", "catalog_systems", "catalog_datasources",
    "catalog_catalogs", "catalog_catalog_connections",
    "catalog_datasets", "catalog_dataset_properties", "catalog_dataset_schemas",
    "catalog_schema_snapshots", "catalog_tags", "catalog_dataset_tags", "catalog_owners",
    "catalog_glossary_terms", "catalog_dataset_glossary_terms",
]
V1 = "/api/v1"


def _tu(name: str, admin: bool = False, role: str = "argus-user") -> TokenUser:
    return TokenUser(sub=name, username=name, roles=[settings.auth_keycloak_admin_role] if admin else [role])


@pytest.fixture
def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init() -> int:
        async with engine.begin() as conn:
            for t in _TABLES:
                await conn.run_sync(lambda sc, t=t: cm.Base.metadata.tables[t].create(sc, checkfirst=True))
            for model in (Permission, CatalogConfiguration, AdminAuditLog):
                await conn.run_sync(lambda sc, m=model: m.__table__.create(sc, checkfirst=True))
        async with factory() as db:
            src = cm.Datasource(datasource_id="ds-dev", name="dev src", type="mysql", origin="DEV")
            db.add(src)
            await db.commit()
            return src.id
    src_id = asyncio.run(_init())

    app = FastAPI()
    for r in (catalog_router, perm.router, settings_router):
        app.include_router(r, prefix=V1)

    async def _session():
        async with factory() as db:
            yield db

    who: dict = {"user": None}

    def _current():
        if who["user"] is None:
            raise HTTPException(status_code=401, detail="인증이 필요합니다.")
        return who["user"]

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _current
    c = TestClient(app)

    seq = {"n": 0}

    def create():
        seq["n"] += 1
        return c.post(f"{V1}/catalog/datasets", json={"name": f"db.t{seq['n']}", "datasource_id": src_id})

    def set_matrix(matrix):
        async def _put():
            async with factory() as db:
                await perm._put_matrix(db, "FEATURE", matrix)
        asyncio.run(_put())

    def set_option(on: bool):
        prev = who["user"]
        who["user"] = _tu("root", admin=True)
        r = c.put(f"{V1}/settings/catalog", json={"dataset_create_by_users": on})
        assert r.status_code == 200, r.text
        who["user"] = prev

    def denied_features() -> list[str]:
        r = c.get(f"{V1}/permissions/me")
        assert r.status_code == 200, r.text
        return r.json()["denied_features"]

    yield c, who, create, set_matrix, set_option, denied_features, factory
    asyncio.run(engine.dispose())


def test_default_denies_regular_users(env):
    c, who, create, _m, _o, denied_features, _f = env
    for role in ("argus-user", "argus-superuser"):
        who["user"] = _tu("alice", role=role)
        r = create()
        assert r.status_code == 403, r.text
        assert "데이터셋 직접 등록이 허용되어 있지 않습니다" in r.json()["detail"]
        assert "datasets.create" in denied_features()

    # 설정 조회 — 행이 없으면 꺼짐, 관리자 전용
    who["user"] = _tu("root", admin=True)
    assert c.get(f"{V1}/settings/catalog").json() == {"dataset_create_by_users": False}
    who["user"] = _tu("alice")
    assert c.get(f"{V1}/settings/catalog").status_code == 403
    assert c.put(f"{V1}/settings/catalog", json={"dataset_create_by_users": True}).status_code == 403


def test_option_on_allows_regular_users_with_feature_matrix(env):
    c, who, create, set_matrix, set_option, denied_features, _f = env
    set_option(True)
    who["user"] = _tu("root", admin=True)
    assert c.get(f"{V1}/settings/catalog").json() == {"dataset_create_by_users": True}

    who["user"] = _tu("alice")
    assert "datasets.create" not in denied_features()
    r = create()
    assert r.status_code == 200, r.text
    assert r.json()["created_by"] == "alice"

    # 옵션이 켜져 있어도 기능 권한 매트릭스는 그대로 적용된다
    set_matrix({"datasets.create": ["argus-superuser"]})
    r = create()
    assert r.status_code == 403 and "datasets.create" in r.json()["detail"]
    assert "datasets.create" in denied_features()
    who["user"] = _tu("sam", role="argus-superuser")
    assert create().status_code == 200
    assert "datasets.create" not in denied_features()

    # 다시 끄면 막힌다
    set_option(False)
    r = create()
    assert r.status_code == 403 and "직접 등록이 허용되어 있지 않습니다" in r.json()["detail"]


def test_admin_always_allowed(env):
    c, who, create, set_matrix, set_option, denied_features, _f = env
    who["user"] = _tu("root", admin=True)
    assert create().status_code == 200                       # 옵션 꺼짐(기본)
    set_matrix({"datasets.create": []})                      # 매트릭스로 전 역할을 막아도
    assert create().status_code == 200
    assert denied_features() == []


def test_sync_account_not_blocked(env):
    """외부 metadata-sync 는 계정 토큰으로 POST /catalog/datasets 를 호출한다 — 동기화 권한이 있으면 통과."""
    c, who, create, set_matrix, _o, denied_features, _f = env
    who["user"] = _tu("sync-bot")
    assert create().status_code == 403                       # 동기화 권한 없는 일반 계정은 옵션 꺼짐에 막힘

    set_matrix({"datasources.sync": ["argus-user"]})          # 명시 허용형 — 동기화 계정 역할에 허용
    r = create()
    assert r.status_code == 200, r.text
    assert r.json()["created_by"] == "sync-bot"
    assert "datasets.create" not in denied_features()


def test_option_is_seeded_and_audited(env):
    *_rest, set_option, _d, factory = env
    defaults = dict((cat, items) for cat, items in ssvc._all_defaults())
    assert (ssvc.DATASET_CREATE_BY_USERS_KEY, "false") in [(k, v) for k, v, _ in defaults["catalog"]]

    set_option(True)

    async def _check():
        async with factory() as db:
            row = (await db.execute(select(CatalogConfiguration).where(
                CatalogConfiguration.config_key == ssvc.DATASET_CREATE_BY_USERS_KEY))).scalars().one()
            assert row.category == "catalog" and row.config_value == "true"
            logs = (await db.execute(select(AdminAuditLog))).scalars().all()
            assert any(log.action == "settings.catalog" for log in logs)
    asyncio.run(_check())
