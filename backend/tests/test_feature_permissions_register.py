# SPDX-License-Identifier: Apache-2.0
"""등록·운영 영향 기능 권한이 서버에서 실제로 검사되는지 (자기완결형, in-memory SQLite).

- 등록 계열(open-by-default): ``datasets.create`` ``ai.generate`` ``models.register`` ``oci.register``
  ``agents.register`` ``mcp.register`` ``apis.register`` ``changes.consumers`` — 미설정이면 로그인 사용자
  모두, 매트릭스에서 역할을 빼면 403.
- 명시 허용형: ``mcp.credential`` (배포 자격증명 저장·삭제), ``models.promote`` (Production 승격·해제) —
  미설정이면 관리자만, 역할을 명시 허용하면 그 역할도 가능.

실행:
    cd backend
    pytest tests/test_feature_permissions_register.py
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.agents.router import router as agents_router
from app.ai.router import router as ai_router
from app.apis.router import router as apis_router
from app.catalog.router import router as catalog_router
from app.change_mgmt.router import router as change_router
from app.core.auth import TokenUser, get_current_user
from app.core.config import settings
from app.core.database import Base, get_session
from app.mcp_servers.router import deployments_router, servers_router
from app.models.models import ModelVersion, RegisteredModel
from app.models.router import router as models_router
from app.models.uc_compat import router as uc_router
from app.oci_hub.router import router as oci_router
from app.permissions import router as perm
from app.permissions.models import Permission
from app.settings.models import CatalogConfiguration
from app.settings.service import DATASET_CREATE_BY_USERS_KEY

V1 = "/api/v1"
# 기능 key → 그 key 로 막히는 (메서드, 경로, 본문)
ENDPOINTS: dict[str, list[tuple[str, str, dict | None]]] = {
    "datasets.create": [("POST", f"{V1}/catalog/datasets", {})],
    "ai.generate": [(m, f"{V1}/ai/datasets/1/{k}", {}) for m, k in (
        ("POST", "describe"), ("POST", "summarize"), ("POST", "describe-columns"),
        ("POST", "suggest-tags"), ("POST", "detect-pii"), ("POST", "generate-all"))],
    "models.register": [("POST", f"{V1}/models", {}), ("POST", "/api/2.1/unity-catalog/models", {})],
    "oci.register": [("POST", f"{V1}/oci-models", {})],
    "agents.register": [
        ("POST", f"{V1}/ai-agents", {}), ("POST", f"{V1}/ai-agents/apply", {}),
        ("POST", f"{V1}/ai-agents/import/card", {}), ("POST", f"{V1}/ai-agents/discoveries/1/register", {}),
    ],
    "mcp.register": [
        ("POST", f"{V1}/mcp-servers", {}), ("POST", f"{V1}/mcp-servers/import", {}),
        ("POST", f"{V1}/mcp-deployments", {}),
    ],
    "apis.register": [("POST", f"{V1}/apis", {})],
    "changes.consumers": [("POST", f"{V1}/changes/consumers", {})],
    "mcp.credential": [("PUT", f"{V1}/mcp-deployments/d1/credential", {}),
                       ("DELETE", f"{V1}/mcp-deployments/d1/credential", None)],
}
DEFAULT_DENY = ["mcp.credential"]
OPEN_BY_DEFAULT = [k for k in ENDPOINTS if k not in DEFAULT_DENY]


def _tu(name: str, admin: bool = False, role: str = "argus-user") -> TokenUser:
    return TokenUser(sub=name, username=name, roles=[settings.auth_keycloak_admin_role] if admin else [role])


@pytest.fixture
def env():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[
                Permission.__table__, RegisteredModel.__table__, ModelVersion.__table__,
                CatalogConfiguration.__table__])
        async with factory() as db:
            # 데이터셋 직접 등록은 설정 옵션(기본 꺼짐)이 먼저 걸린다 — 여기서는 켜 두고 매트릭스만 본다
            # (옵션 자체는 tests/test_dataset_create_policy.py)
            db.add(CatalogConfiguration(category="catalog", config_key=DATASET_CREATE_BY_USERS_KEY,
                                        config_value="true"))
            db.add(RegisteredModel(id=1, name="m1", urn="m1.model", created_by="bob", status="active"))
            db.add_all([ModelVersion(id=11, model_id=1, version=1, stage="NONE", status="READY"),
                        ModelVersion(id=12, model_id=1, version=2, stage="PRODUCTION", status="READY")])
            await db.commit()
    asyncio.run(_init())

    app = FastAPI()
    for r in (catalog_router, ai_router, models_router, oci_router, agents_router, servers_router,
              deployments_router, apis_router, change_router):
        app.include_router(r, prefix=V1)
    app.include_router(uc_router)

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
    # 권한을 통과한 뒤 핸들러가 (테이블 없는 DB·빈 본문으로) 실패하는 것은 상관없다 — 이 key 의 403 여부만 본다
    c = TestClient(app, raise_server_exceptions=False)

    def set_matrix(matrix):
        async def _put():
            async with factory() as db:
                await perm._put_matrix(db, "FEATURE", matrix)
        asyncio.run(_put())

    yield c, who, set_matrix
    asyncio.run(engine.dispose())


def _denied(c: TestClient, method: str, path: str, body, key: str) -> bool:
    r = c.request(method, path, json=body)
    try:
        detail = str(r.json().get("detail", ""))
    except (ValueError, AttributeError):
        detail = ""
    return r.status_code == 403 and key in detail


def _calls(keys):
    for k in keys:
        for m, p, b in ENDPOINTS[k]:
            yield k, m, p, b


def test_registry_has_keys():
    for k in ENDPOINTS:
        assert k in perm.FEATURE_KEYS, k
    assert {"mcp.credential", "models.promote"} <= perm.DEFAULT_DENY_FEATURES
    assert "models.promote" in perm.FEATURE_KEYS
    for k in OPEN_BY_DEFAULT:
        assert k not in perm.DEFAULT_DENY_FEATURES, k


def test_register_features_open_by_default_and_lockable(env):
    c, who, set_matrix = env
    who["user"] = _tu("alice")
    for k, m, p, b in _calls(OPEN_BY_DEFAULT):          # 미설정 = 허용
        assert not _denied(c, m, p, b, k), (k, m, p)
    for k, m, p, b in _calls(DEFAULT_DENY):             # 미설정 = 관리자만
        assert _denied(c, m, p, b, k), (k, m, p)

    # user 역할을 빼고(superuser 만), 명시 허용형은 user 에 허용
    set_matrix({**{k: ["argus-superuser"] for k in OPEN_BY_DEFAULT}, **{k: ["argus-user"] for k in DEFAULT_DENY}})
    for k, m, p, b in _calls(OPEN_BY_DEFAULT):
        assert _denied(c, m, p, b, k), (k, m, p)
    for k, m, p, b in _calls(DEFAULT_DENY):
        assert not _denied(c, m, p, b, k), (k, m, p)
    who["user"] = _tu("sam", role="argus-superuser")
    for k, m, p, b in _calls(OPEN_BY_DEFAULT):
        assert not _denied(c, m, p, b, k), (k, m, p)

    # 관리자는 매트릭스와 무관
    set_matrix({k: [] for k in ENDPOINTS})
    who["user"] = _tu("root", admin=True)
    for k, m, p, b in _calls(ENDPOINTS):
        assert not _denied(c, m, p, b, k), (k, m, p)


def test_production_stage_needs_promote_permission(env):
    c, who, set_matrix = env
    stage = lambda v, s: c.put(f"{V1}/models/m1/versions/{v}/stage", json={"stage": s})  # noqa: E731

    who["user"] = _tu("bob")                                          # 모델 생성자
    assert stage(1, "STAGING").status_code == 200                     # 운영과 무관한 변경은 소유자면 가능
    r = stage(1, "PRODUCTION")                                        # 승격 — 미설정이면 관리자만
    assert r.status_code == 403 and "models.promote" in r.json()["detail"]
    r = stage(2, "ARCHIVED")                                          # 운영 버전 내리기도 같은 권한
    assert r.status_code == 403 and "models.promote" in r.json()["detail"]

    set_matrix({"models.promote": ["argus-user"]})
    assert stage(1, "PRODUCTION").status_code == 200
    assert stage(2, "ARCHIVED").status_code == 200

    who["user"] = _tu("eve")                                          # 권한이 있어도 소유자가 아니면 403
    r = stage(1, "NONE")
    assert r.status_code == 403 and "models.promote" not in r.json()["detail"]

    set_matrix({})
    who["user"] = _tu("root", admin=True)
    assert stage(1, "NONE").status_code == 200
