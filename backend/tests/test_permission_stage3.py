# SPDX-License-Identifier: Apache-2.0
"""권한 3단계 — 관리 기능 위임 · 플러그인 권한 (TODO-123, 자기완결형, in-memory SQLite).

확인하는 규칙:
- 위임 키(users.manage · audit.view · settings.*)는 명시 허용형 — 미설정이면 관리자만, 역할에 허용하면 위임된다.
- 설정 메뉴는 settings.* 중 하나라도 받으면 열리고, API 는 받은 항목만 통과한다. 인증 · CORS 는 관리자 고정.
- 사용자 관리 메뉴는 users.manage 로 열린다. 권한 관리 메뉴는 늘 관리자 전용.
- 위임받은 사람은 관리자 계정을 바꾸거나 관리자를 만들 수 없고, 관리자 역할 API 키를 다룰 수 없다(권한 상승 방지).
- 플러그인은 기능 키 · 관리자 전용 메뉴 · API 메뉴 가드를 선언할 수 있다.

실행:
    cd backend
    pytest tests/test_permission_stage3.py
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.auth import TokenUser, get_current_user
from app.core.database import Base, get_session
from app.permissions import router as perm
from app.permissions.models import Permission
from app.settings.models import CatalogConfiguration
from tests.test_admin_stage6 import factory as settings_factory  # noqa: F401 — pytest 픽스처

ADMIN = TokenUser(sub="a", username="admin", roles=["argus-admin"])
SUPER = TokenUser(sub="s", username="sam", roles=["argus-superuser"])
ALICE = TokenUser(sub="alice", username="alice", roles=["argus-user"])


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[Permission.__table__, CatalogConfiguration.__table__])
    make = async_sessionmaker(engine, expire_on_commit=False)
    perm._menu_cache.clear()
    yield make
    perm._menu_cache.clear()
    await engine.dispose()


async def _grant(s, key: str, role: str = "argus-superuser") -> None:
    s.add(Permission(kind="FEATURE", perm_key=key, role_id=role))
    await s.commit()
    perm._menu_cache.clear()


# ---------------------------------------------------------------------------
# 키 목록 · 메뉴
# ---------------------------------------------------------------------------

DELEGATE_KEYS = ["users.manage", "audit.view", *perm.SETTINGS_DELEGATE_FEATURES]


def test_delegate_keys_are_default_deny():
    assert len(perm.SETTINGS_DELEGATE_FEATURES) == 10
    for k in DELEGATE_KEYS:
        assert k in perm.FEATURE_KEYS and k in perm.DEFAULT_DENY_FEATURES, k
    assert perm.ADMIN_MENU_GRANTS["users"] == ("users.manage",)
    assert perm.ADMIN_MENU_GRANTS["settings"] == perm.SETTINGS_DELEGATE_FEATURES
    assert "permissions" not in perm.ADMIN_MENU_GRANTS


async def test_admin_menus_open_by_delegation(factory):
    async with factory() as s:
        me = await perm.get_my_permissions(SUPER, s)
        assert {"users", "settings", "permissions"} <= set(me["denied_menus"])
        await _grant(s, "settings.mail")
        me = await perm.get_my_permissions(SUPER, s)
        assert "settings" not in me["denied_menus"]
        assert {"users", "permissions"} <= set(me["denied_menus"])
        await _grant(s, "users.manage")
        me = await perm.get_my_permissions(SUPER, s)
        assert "users" not in me["denied_menus"] and "permissions" in me["denied_menus"]
        assert {"settings", "users"} <= set((await perm.get_my_permissions(ALICE, s))["denied_menus"])
        # 관리 메뉴는 매트릭스에 저장되지 않는다
        await perm._put_matrix(s, "MENU", {"settings": ["argus-user"]})
        assert "settings" in (await perm.get_my_permissions(ALICE, s))["denied_menus"]


# ---------------------------------------------------------------------------
# 설정 API — 항목별 위임
# ---------------------------------------------------------------------------

def _settings_app(make, user):
    from app.settings import router as srouter

    app = FastAPI()
    app.include_router(srouter.router, prefix="/api/v1")

    async def _session():
        async with make() as db:
            yield db

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: user
    return app


async def test_settings_api_follows_delegation(settings_factory):  # noqa: F811
    perm._menu_cache.clear()
    async with settings_factory() as s:
        await _grant(s, "settings.mail")
    app = _settings_app(settings_factory, SUPER)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/v1/settings/mail")).status_code == 200
        for path in ("/api/v1/settings/llm", "/api/v1/settings/notify", "/api/v1/settings/auth",
                     "/api/v1/settings/cors", "/api/v1/settings/catalog", "/api/v1/settings/change"):
            assert (await c.get(path)).status_code == 403, path
        sources = (await c.get("/api/v1/settings/sources")).json()
        assert set(sources) <= {"mail"}
    app = _settings_app(settings_factory, ALICE)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/v1/settings/mail")).status_code == 403
        assert (await c.get("/api/v1/settings/sources")).status_code == 403


# ---------------------------------------------------------------------------
# 라우트에 걸린 위임 키
# ---------------------------------------------------------------------------

def _route_strings(method: str, path: str) -> set[str]:
    from app.main import app

    def walk(dep) -> set[str]:
        out: set[str] = set()
        for cell in getattr(dep.call, "__closure__", None) or ():
            try:
                v = cell.cell_contents
            except ValueError:
                continue
            if isinstance(v, str):
                out.add(v)
            elif isinstance(v, tuple):
                out |= {x for x in v if isinstance(x, str)}
        for sub in dep.dependencies:
            out |= walk(sub)
        return out

    for r in app.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", ()):
            return walk(r.dependant)
    raise AssertionError(f"route not found: {method} {path}")


@pytest.mark.parametrize(("method", "path", "key"), [
    ("GET", "/api/v1/admin/audit", "audit.view"),
    ("POST", "/api/v1/api-keys", "settings.api-keys"),
    ("POST", "/api/v1/usermgr/users", "users.manage"),
    ("PUT", "/api/v1/usermgr/users/{user_id}/password", "users.manage"),
    ("DELETE", "/api/v1/external/cache", "settings.cache"),
    ("PUT", "/api/v1/settings/llm", "settings.llm"),
    ("PUT", "/api/v1/settings/object-storage", "settings.object-storage"),
])
def test_delegate_keys_on_routes(method, path, key):
    assert key in _route_strings(method, path)


def test_role_change_stays_admin_only():
    from app.usermgr import router as um

    keys = _route_strings("PUT", "/api/v1/usermgr/users/{user_id}/role")
    assert "users.manage" not in keys
    assert "AdminUser" in str(um.change_role.__annotations__.get("admin")) or "require_admin" in str(
        um.change_role.__annotations__.get("admin"))


# ---------------------------------------------------------------------------
# 권한 상승 방지
# ---------------------------------------------------------------------------

async def test_user_delegate_cannot_touch_admin_accounts(monkeypatch):
    from app.usermgr import router as um

    async def _get_user(_s, user_id):
        return SimpleNamespace(id=user_id, username=f"u{user_id}", role="argus-admin" if user_id == 1 else "argus-user")

    monkeypatch.setattr(um.service, "get_user", _get_user)
    with pytest.raises(HTTPException) as e:
        await um._forbid_admin_target(None, SUPER, 1, "비밀번호 재설정")
    assert (e.value.status_code, e.value.detail) == (403, um.ADMIN_TARGET_DETAIL)
    await um._forbid_admin_target(None, SUPER, 2, "비밀번호 재설정")
    await um._forbid_admin_target(None, ADMIN, 1, "비밀번호 재설정")

    monkeypatch.setattr(um.settings, "auth_type", "local")
    from app.usermgr.schemas import UserAddRequest

    req = UserAddRequest.model_validate({"username": "boss", "email": "b@x.io", "first_name": "b", "last_name": "b",
                                         "password": "Passw0rd!x", "role": "argus-admin"})
    with pytest.raises(HTTPException) as e:
        await um.add_user(req, SUPER, None)
    assert e.value.status_code == 403


def test_api_key_delegate_cannot_handle_admin_keys():
    from app.apikeys import router as ak

    with pytest.raises(HTTPException) as e:
        ak._forbid_admin_key(SUPER, "argus-admin")
    assert e.value.status_code == 403
    with pytest.raises(HTTPException):
        ak._forbid_admin_key(SUPER, "argus-user", "argus-admin")
    ak._forbid_admin_key(SUPER, "argus-superuser", None)
    ak._forbid_admin_key(ADMIN, "argus-admin")


async def test_user_delegate_sees_phone_and_edits_others(factory):
    from app.usermgr import router as um

    async with factory() as s:
        assert await um._can_manage_users(s, SUPER) is False
        await _grant(s, "users.manage")
        assert await um._can_manage_users(s, SUPER) is True
        assert await um._can_manage_users(s, ALICE) is False
        assert await um._can_manage_users(s, ADMIN) is True


# ---------------------------------------------------------------------------
# 플러그인
# ---------------------------------------------------------------------------

@pytest.fixture
def restore_perm_tables():
    saved = (set(perm.FEATURE_KEYS), set(perm.DEFAULT_DENY_FEATURES), set(perm.ADMIN_MENUS), dict(perm.ADMIN_MENU_GRANTS))
    yield
    perm.FEATURE_KEYS.clear(); perm.FEATURE_KEYS.update(saved[0])  # noqa: E702
    perm.DEFAULT_DENY_FEATURES.clear(); perm.DEFAULT_DENY_FEATURES.update(saved[1])  # noqa: E702
    perm.ADMIN_MENUS.clear(); perm.ADMIN_MENUS.update(saved[2])  # noqa: E702
    perm.ADMIN_MENU_GRANTS.clear(); perm.ADMIN_MENU_GRANTS.update(saved[3])  # noqa: E702


async def test_plugin_declares_features_and_admin_menu(factory, restore_perm_tables):
    from app.plugins import _register_permissions

    plugin = SimpleNamespace(
        router=None,
        features=[{"key": "acme.billing-admin", "default_deny": True}, "acme.billing-view",
                  {"key": "datasets.edit", "default_deny": True}],  # 코어 키는 무시(의미를 바꾸지 않음)
        menus=[{"key": "plugin.acme-billing", "admin_only": True, "granted_by": ["acme.billing-admin"]},
               {"key": "acme-bad", "admin_only": True}],
        menu_key="plugin.acme-billing",
    )
    deps = _register_permissions("acme", plugin)
    assert len(deps) == 1
    assert {"acme.billing-admin", "acme.billing-view"} <= perm.FEATURE_KEYS
    assert "acme.billing-admin" in perm.DEFAULT_DENY_FEATURES
    assert "acme.billing-view" not in perm.DEFAULT_DENY_FEATURES
    assert "datasets.edit" not in perm.DEFAULT_DENY_FEATURES
    assert "plugin.acme-billing" in perm.ADMIN_MENUS and "acme-bad" not in perm.ADMIN_MENUS

    async with factory() as s:
        assert await perm.check_menu(s, SUPER, "plugin.acme-billing") is False
        assert "plugin.acme-billing" in (await perm.get_my_permissions(SUPER, s))["denied_menus"]
        await _grant(s, "acme.billing-admin")
        assert await perm.check_menu(s, SUPER, "plugin.acme-billing") is True
        assert "plugin.acme-billing" not in (await perm.get_my_permissions(SUPER, s))["denied_menus"]
        assert await perm.check_menu(s, ALICE, "plugin.acme-billing") is False
        assert await perm.check_menu(s, ADMIN, "plugin.acme-billing") is True
        # 플러그인 기능 키는 저장할 수 있다
        await perm._put_matrix(s, "FEATURE", {"acme.billing-view": ["argus-superuser"]})
        assert (await perm._get_matrix(s, "FEATURE"))["acme.billing-view"] == ["argus-superuser"]


def test_plugin_without_permissions_adds_nothing(restore_perm_tables):
    from app.plugins import _register_permissions

    before = set(perm.FEATURE_KEYS)
    assert _register_permissions("plain", SimpleNamespace(router=None)) == []
    assert perm.FEATURE_KEYS == before
