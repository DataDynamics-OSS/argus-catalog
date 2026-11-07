# SPDX-License-Identifier: Apache-2.0
"""품질·변경 관리·분류 체계 기능 권한 키가 서버에서 실제로 검사되는지 (자기완결형, in-memory SQLite).

예전에는 ``quality.*`` / ``changes.*`` / ``taxonomies.edit`` 가 권한 관리 화면에만 있고 어디서도
검사하지 않아, 관리자가 막아도 효과가 없었다(IT DQ-T19, CHG-T21, TAX-T13).

- ``quality.rules-manage`` / ``quality.run`` / ``quality.schedule`` / ``changes.create`` /
  ``changes.approve``: open-by-default — 미설정이면 로그인 사용자 모두, 역할을 빼면 403.
- ``taxonomies.edit``: 명시 허용형 — 미설정이면 admin 전용, 역할을 명시 허용하면 그 역할도 가능.

또 백엔드 ``FEATURE_KEYS``/``DEFAULT_DENY_FEATURES`` 와 프런트 ``permission-registry.ts`` 가 일치하는지 본다
(서버는 목록에 없는 key 를 저장하지 않으므로, 어긋나면 화면의 체크박스가 효과 없이 남는다).

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_feature_keys_enforced
"""

from __future__ import annotations

import asyncio
import pathlib
import re

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.catalog.taxonomy_router import router as taxonomy_router
from app.change_mgmt.router import router as change_router
from app.core.auth import TokenUser, get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.permissions import router as perm
from app.permissions.models import Permission
from app.quality.router import router as quality_router

REGISTRY = pathlib.Path(__file__).resolve().parents[2] / "frontend/apps/web/lib/permission-registry.ts"

# 기능 key → 그 key 로 막히는 (메서드, 경로, 본문)
ENDPOINTS: dict[str, list[tuple[str, str, dict | None]]] = {
    "quality.rules-manage": [
        ("POST", "/api/v1/quality/rules", {"dataset_id": 1, "rule_name": "r", "check_type": "NOT_NULL"}),
        ("PUT", "/api/v1/quality/rules/1", {"rule_name": "r"}),
        ("DELETE", "/api/v1/quality/rules/1", None),
    ],
    "quality.run": [
        ("POST", "/api/v1/quality/datasets/1/profile", None),
        ("POST", "/api/v1/quality/datasets/1/check", None),
    ],
    "quality.schedule": [("PUT", "/api/v1/quality/datasets/1/schedule", {"schedule": "daily"})],
    "changes.create": [("POST", "/api/v1/changes", {})],
    "changes.approve": [("POST", "/api/v1/changes/1/decisions", {"decision": "APPROVED"})],
    "taxonomies.edit": [
        ("POST", "/api/v1/catalog/taxonomies", {"name": "t"}),
        ("PUT", "/api/v1/catalog/taxonomies/1", {"name": "t"}),
        ("DELETE", "/api/v1/catalog/taxonomies/1", None),
        ("POST", "/api/v1/catalog/categories", {"taxonomy_id": 1, "name": "c"}),
        ("PUT", "/api/v1/catalog/categories/1", {"name": "c"}),
        ("DELETE", "/api/v1/catalog/categories/1", None),
        ("POST", "/api/v1/catalog/datasets/1/categories/1", None),
        ("DELETE", "/api/v1/catalog/datasets/1/categories/1", None),
    ],
}
OPEN_BY_DEFAULT = [k for k in ENDPOINTS if k != "taxonomies.edit"]


def _tu(name: str, admin: bool = False) -> TokenUser:
    return TokenUser(sub=name, username=name, roles=[settings.auth_keycloak_admin_role] if admin else ["argus-user"])


async def _set_matrix(factory, matrix: dict[str, list[str]]) -> None:
    async with factory() as db:
        await perm._put_matrix(db, "FEATURE", matrix)


def _status(c: TestClient, method: str, path: str, body) -> tuple[int, str]:
    r = c.request(method, path, json=body)
    try:
        detail = r.json().get("detail", "")
    except ValueError:
        detail = ""
    return r.status_code, str(detail)


def _run_http() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(lambda sc: Permission.__table__.create(sc))
    asyncio.run(_init())

    app = FastAPI()
    for r in (quality_router, change_router, taxonomy_router):
        app.include_router(r, prefix="/api/v1")

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
    # 권한을 통과한 뒤 핸들러가 (테이블 없는 DB 에서) 실패하는 것은 상관없다 — 403 여부만 본다
    c = TestClient(app, raise_server_exceptions=False)

    def calls(keys):
        for k in keys:
            for m, p, b in ENDPOINTS[k]:
                yield k, m, p, b

    # 비로그인 → 401
    for k, m, p, b in calls(ENDPOINTS):
        assert _status(c, m, p, b)[0] == 401, (k, m, p)

    # 미설정: open-by-default 키는 일반 사용자 허용, taxonomies.edit 는 admin 전용
    who["user"] = _tu("bob")
    for k, m, p, b in calls(OPEN_BY_DEFAULT):
        assert _status(c, m, p, b)[0] != 403, (k, m, p)
    for k, m, p, b in calls(["taxonomies.edit"]):
        assert _status(c, m, p, b) == (403, "기능 권한이 없습니다: taxonomies.edit"), (m, p)

    # 매트릭스에서 user 역할을 빼면 403, taxonomies.edit 는 user 에 명시 허용하면 통과
    asyncio.run(_set_matrix(factory, {**{k: ["argus-superuser"] for k in OPEN_BY_DEFAULT},
                                      "taxonomies.edit": ["argus-user"]}))
    for k, m, p, b in calls(OPEN_BY_DEFAULT):
        assert _status(c, m, p, b) == (403, f"기능 권한이 없습니다: {k}"), (k, m, p)
    for k, m, p, b in calls(["taxonomies.edit"]):
        assert _status(c, m, p, b)[0] != 403, (m, p)

    # admin 은 매트릭스와 무관하게 허용
    asyncio.run(_set_matrix(factory, {k: [] for k in ENDPOINTS}))
    who["user"] = _tu("root", admin=True)
    for k, m, p, b in calls(ENDPOINTS):
        assert _status(c, m, p, b)[0] != 403, (k, m, p)

    asyncio.run(engine.dispose())


def _registry_matches_server() -> None:
    text = REGISTRY.read_text(encoding="utf-8")
    body = text[text.index("export const FEATURE_REGISTRY"):]
    body = body[:body.index("\n]\n")]
    entries = re.findall(r"\{ key: \"([^\"]+)\"[^\n]*", body)
    keys = set(entries)
    deny = {k for k, line in zip(entries, re.findall(r"\{ key: \"[^\"]+\"[^\n]*", body)) if "defaultDeny: true" in line}
    assert keys == set(perm.FEATURE_KEYS), (keys ^ set(perm.FEATURE_KEYS))
    assert deny == set(perm.DEFAULT_DENY_FEATURES), (deny ^ set(perm.DEFAULT_DENY_FEATURES))
    for k in ENDPOINTS:
        assert k in perm.FEATURE_KEYS, k
    assert "taxonomies.edit" in perm.DEFAULT_DENY_FEATURES


def test_feature_keys_enforced() -> None:
    """pytest 진입점."""
    _registry_matches_server()
    _run_http()


if __name__ == "__main__":
    test_feature_keys_enforced()
    print("test_feature_keys_enforced OK")
