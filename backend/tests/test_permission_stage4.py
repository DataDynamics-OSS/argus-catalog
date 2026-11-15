# SPDX-License-Identifier: Apache-2.0
"""권한 4단계 — 사용자 정의 역할 · 메뉴 보기 전용 · 공용 메뉴 편집 · 거부 감사 · 저장 충돌 (TODO-124, 자기완결형, in-memory SQLite).

확인하는 규칙:
- 사용자 정의 역할은 기반 역할(슈퍼유저 · 사용자)의 권한 행을 복사해 시작하고, 인증 때 기반 역할이 더해지며 권한 표는 그 역할 열로 판정한다.
- 쓰는 사용자 · API 키가 있으면 지울 수 없고, 지우면 그 역할만 허용하던 항목은 관리자 전용으로 남는다.
- 메뉴 보기 전용: 서버 API 차단 메뉴는 쓰기 요청 403(자기 즐겨찾기 · Try-it 제외), 그 메뉴의 편집 기능은 서버 · /me 모두 막힌다.
- 공용 메뉴(표준 · 용어집 · 태그 · 오류 신고)는 메뉴가 막히면 편집 기능이 막힌다.
- 권한 거부는 감사 로그에 1분에 한 번 남는다. 권한 표 저장은 If-Match 가 어긋나면 409.
- 검토 키(standards.map · aiready.judge · changes.rollback · alerts.ack)는 기본 허용형이고, 편집 기능 → 메뉴 표는 프런트와 같다.

실행:
    cd backend
    pytest tests/test_permission_stage4.py
"""

from __future__ import annotations

import pathlib
import re

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.apikeys.models import ArgusApiKey
from app.audit.models import AdminAuditLog
from app.core.auth import TokenUser, get_current_user, get_optional_user
from app.core.database import Base, get_session
from app.permissions import roles as custom_roles
from app.permissions import router as perm
from app.permissions.models import Permission
from app.settings.models import CatalogConfiguration
from app.usermgr import service as usvc
from app.usermgr.models import ArgusRole, ArgusUser
from app.usermgr.schemas import UserAddRequest, UserChangeRoleRequest

REGISTRY = pathlib.Path(__file__).resolve().parents[2] / "frontend/apps/web/lib/permission-registry.ts"

ADMIN = TokenUser(sub="a", username="admin", roles=["argus-admin"])
ALICE = TokenUser(sub="alice", username="alice", roles=["argus-user"])


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[
            Permission.__table__, CatalogConfiguration.__table__, ArgusRole.__table__, ArgusUser.__table__,
            ArgusApiKey.__table__, AdminAuditLog.__table__,
        ])
    make = async_sessionmaker(engine, expire_on_commit=False)
    async with make() as s:
        for rid, name in (("argus-admin", "Admin"), ("argus-superuser", "Superuser"), ("argus-user", "User")):
            s.add(ArgusRole(role_id=rid, name=name))
        await s.commit()
    perm._menu_cache.clear()
    perm._deny_seen.clear()
    custom_roles.invalidate()
    custom_roles.session_factory = make
    yield make
    custom_roles.session_factory = None
    custom_roles.invalidate()
    perm._menu_cache.clear()
    perm._deny_seen.clear()
    await engine.dispose()


def _client(make, user_box: dict, *routers) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(perm.router, prefix="/api/v1")
    for r in routers:
        app.include_router(r)

    async def _session():
        async with make() as db:
            yield db

    def _current():
        if user_box["user"] is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        return user_box["user"]

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _current
    app.dependency_overrides[get_optional_user] = lambda: user_box["user"]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _rows(s, kind: str, key: str) -> set[str]:
    return set((await s.execute(select(Permission.role_id).where(
        Permission.kind == kind, Permission.perm_key == key))).scalars())


# ---------------------------------------------------------------------------
# 사용자 정의 역할
# ---------------------------------------------------------------------------

async def test_create_custom_role_copies_base_rows(factory):
    async with factory() as s:
        s.add_all([
            Permission(kind="MENU", perm_key="apis", role_id="argus-superuser"),          # User 에게 막힘
            Permission(kind="FEATURE", perm_key="glossary.edit", role_id="argus-user"),   # User 에게 허용
            Permission(kind="MENU_VIEW", perm_key="datasets", role_id="argus-user"),
        ])
        await s.commit()
    box = {"user": ADMIN}
    async with _client(factory, box) as c:
        r = await c.post("/api/v1/permissions/roles",
                         json={"role_id": "data-steward", "name": "데이터 관리자", "base_role": "argus-user"})
        assert r.status_code == 201, r.text
        assert (await c.post("/api/v1/permissions/roles",
                             json={"role_id": "data-steward", "name": "중복"})).status_code == 409
        assert (await c.post("/api/v1/permissions/roles",
                             json={"role_id": "argus-user", "name": "x"})).status_code == 409
        assert (await c.post("/api/v1/permissions/roles",
                             json={"role_id": "Bad_ID", "name": "x"})).status_code == 422
        assert (await c.post("/api/v1/permissions/roles",
                             json={"role_id": "boss", "name": "x", "base_role": "argus-admin"})).status_code == 422
        listing = (await c.get("/api/v1/permissions/roles")).json()
        assert listing["columns"] == ["argus-superuser", "argus-user", "data-steward"]
        steward = next(x for x in listing["roles"] if x["role_id"] == "data-steward")
        assert steward["base_role"] == "argus-user" and not steward["builtin"]
        box["user"] = ALICE
        assert (await c.get("/api/v1/permissions/roles")).status_code == 403
    async with factory() as s:
        assert await _rows(s, "FEATURE", "glossary.edit") == {"argus-user", "data-steward"}
        assert await _rows(s, "MENU", "apis") == {"argus-superuser"}       # 기반 역할처럼 막힘
        assert await _rows(s, "MENU_VIEW", "datasets") == {"argus-user", "data-steward"}


async def test_custom_role_user_is_judged_by_its_column(factory):
    async with factory() as s:
        s.add_all([
            ArgusRole(role_id="auditor", name="감사"),
            Permission(kind="ROLE", perm_key="auditor", role_id="argus-superuser"),
            Permission(kind="FEATURE", perm_key="audit.view", role_id="auditor"),
            Permission(kind="MENU", perm_key="apis", role_id="argus-superuser"),
        ])
        await s.commit()
    user = TokenUser(sub="b", username="bob", roles=["auditor"])
    assert not user.has_argus_role
    await custom_roles.apply_custom_role(user)
    assert user.custom_role == "auditor" and user.is_superuser and user.has_argus_role and not user.is_admin
    async with factory() as s:
        assert await perm.check_feature_granted(s, user, "audit.view")
        assert not await perm.check_menu(s, user, "apis")       # superuser 는 허용이지만 auditor 열이 없다
        me = await perm.get_my_permissions(user, s)
        assert me["role"] == "auditor" and "audit.view" in me["granted_features"] and "apis" in me["denied_menus"]
    # 기반 역할 정보가 없는 역할은 인정하지 않는다
    stranger = TokenUser(sub="c", username="carl", roles=["no-such-role"])
    await custom_roles.apply_custom_role(stranger)
    assert stranger.custom_role is None and not stranger.has_argus_role


async def test_delete_custom_role_guards_and_sentinel(factory):
    async with factory() as s:
        s.add_all([
            ArgusRole(role_id="ops", name="운영"),
            Permission(kind="ROLE", perm_key="ops", role_id="argus-user"),
            Permission(kind="FEATURE", perm_key="mcp.edit", role_id="ops"),          # ops 만 허용
            Permission(kind="FEATURE", perm_key="apis.edit", role_id="ops"),
            Permission(kind="FEATURE", perm_key="apis.edit", role_id="argus-user"),
        ])
        await s.commit()
        role = (await s.execute(select(ArgusRole).where(ArgusRole.role_id == "ops"))).scalar_one()
        s.add(ArgusUser(username="o", email="o@x", first_name="o", last_name="o", password_hash="x",
                        status="active", role_id=role.id))
        await s.commit()
    box = {"user": ADMIN}
    async with _client(factory, box) as c:
        r = await c.delete("/api/v1/permissions/roles/ops")
        assert r.status_code == 409 and "사용자 1명" in r.json()["detail"]
        assert (await c.delete("/api/v1/permissions/roles/argus-user")).status_code == 409
        assert (await c.delete("/api/v1/permissions/roles/none")).status_code == 404
        async with factory() as s:
            await s.execute(ArgusUser.__table__.delete())
            await s.commit()
        assert (await c.put("/api/v1/permissions/roles/ops", json={"name": "운영팀", "base_role": "argus-superuser"})
                ).json()["base_role"] == "argus-superuser"
        assert (await c.delete("/api/v1/permissions/roles/ops")).status_code == 200
    async with factory() as s:
        assert await _rows(s, "FEATURE", "mcp.edit") == {"__none__"}      # 전체 허용으로 풀리지 않는다
        assert await _rows(s, "FEATURE", "apis.edit") == {"argus-user"}
        assert await _rows(s, "ROLE", "ops") == set()
        assert (await s.execute(select(ArgusRole).where(ArgusRole.role_id == "ops"))).scalar_one_or_none() is None
        actions = set((await s.execute(select(AdminAuditLog.action))).scalars())
        assert {"role.update", "role.delete"} <= actions


async def test_users_and_keys_accept_custom_roles(factory):
    async with factory() as s:
        s.add_all([ArgusRole(role_id="analyst", name="분석가"), ArgusRole(role_id="legacy", name="옛 역할"),
                   Permission(kind="ROLE", perm_key="analyst", role_id="argus-user")])
        await s.commit()
        req = UserAddRequest(username="ana", email="ana@x.io", first_name="a", last_name="n",
                             password="P@ssw0rd!long", role="analyst", must_change_password=True)
        user = await usvc.add_user(s, req)
        assert user.role == "analyst"
        with pytest.raises(ValueError):
            await usvc.change_role(s, user.id, UserChangeRoleRequest(role="legacy"))   # 기반 역할 정보 없음
        await usvc.change_role(s, user.id, UserChangeRoleRequest(role="argus-superuser"))
    with pytest.raises(ValidationError):
        UserChangeRoleRequest(role="Not Valid")


# ---------------------------------------------------------------------------
# 메뉴 보기 전용 · 공용 메뉴 편집
# ---------------------------------------------------------------------------

def _apis_router() -> APIRouter:
    r = APIRouter(prefix="/api/v1/apis", dependencies=[Depends(perm.require_menu("apis"))])

    @r.get("/list")
    async def _list():
        return {"ok": True}

    @r.post("/x")
    async def _create():
        return {"ok": True}

    @r.post("/x/favorites")
    async def _fav():
        return {"ok": True}

    @r.post("/invoke")
    async def _invoke():
        return {"ok": True}

    return r


async def test_view_only_menu_blocks_writes(factory):
    async with factory() as s:
        s.add(Permission(kind="MENU_VIEW", perm_key="apis", role_id="argus-user"))
        await s.commit()
    box = {"user": ALICE}
    async with _client(factory, box, _apis_router()) as c:
        assert (await c.get("/api/v1/apis/list")).status_code == 200
        r = await c.post("/api/v1/apis/x")
        assert r.status_code == 403 and "보기 전용" in r.json()["detail"]
        assert (await c.post("/api/v1/apis/x/favorites")).status_code == 200
        assert (await c.post("/api/v1/apis/invoke")).status_code == 200
        box["user"] = TokenUser(sub="s", username="sam", roles=["argus-superuser"])
        assert (await c.post("/api/v1/apis/x")).status_code == 200
        box["user"] = ADMIN
        assert (await c.post("/api/v1/apis/x")).status_code == 200
    async with factory() as s:
        assert not await perm.check_feature(s, ALICE, "apis.edit")
        assert await perm.check_feature(s, ALICE, "apis.try-it")             # 조회 기능은 그대로
        me = await perm.get_my_permissions(ALICE, s)
        assert me["readonly_menus"] == ["apis"]
        assert {"apis.edit", "apis.delete", "apis.register"} <= set(me["denied_features"])
        assert "apis.try-it" not in me["denied_features"]


async def test_view_only_on_shared_menu_and_denied_shared_menu(factory):
    async with factory() as s:
        s.add_all([
            Permission(kind="FEATURE", perm_key="glossary.edit", role_id="argus-user"),
            Permission(kind="FEATURE", perm_key="tags.manage", role_id="argus-user"),
            Permission(kind="MENU", perm_key="glossary", role_id="argus-superuser"),       # User 에게 용어집 메뉴 막힘
            Permission(kind="MENU_VIEW", perm_key="tags", role_id="argus-user"),
        ])
        await s.commit()
        assert not await perm.check_feature_granted(s, ALICE, "glossary.edit")
        assert not await perm.check_feature_granted(s, ALICE, "tags.manage")
        me = await perm.get_my_permissions(ALICE, s)
        assert {"glossary.edit", "tags.manage"} <= set(me["denied_features"])
        assert not {"glossary.edit", "tags.manage"} & set(me["granted_features"])
        # 메뉴를 다시 열고 보기 전용을 풀면 편집이 돌아온다
        await s.execute(Permission.__table__.delete().where(Permission.kind.in_(("MENU", "MENU_VIEW"))))
        await s.commit()
        perm._menu_cache.clear()
        assert await perm.check_feature_granted(s, ALICE, "glossary.edit")
        assert await perm.check_feature_granted(s, ALICE, "tags.manage")


async def test_view_only_does_not_close_delegated_admin_menus(factory):
    async with factory() as s:
        s.add_all([
            Permission(kind="FEATURE", perm_key="quality.manage", role_id="argus-user"),
            Permission(kind="FEATURE", perm_key="datasources.sync", role_id="argus-user"),
            Permission(kind="MENU_VIEW", perm_key="datasets", role_id="argus-user"),
            Permission(kind="MENU_VIEW", perm_key="datasources", role_id="argus-user"),
        ])
        await s.commit()
        assert await perm.check_menu(s, ALICE, "quality-runs")
        assert await perm.check_feature_granted(s, ALICE, "datasources.sync")
        assert not await perm.check_feature(s, ALICE, "datasets.edit")
        assert await perm.check_feature(s, ALICE, "datasets.sample-view")


# ---------------------------------------------------------------------------
# 거부 감사 · 저장 충돌
# ---------------------------------------------------------------------------

async def test_denials_are_audited_once_per_window(factory):
    router = APIRouter()

    @router.post("/do", dependencies=[Depends(perm.require_feature_granted("tags.manage"))])
    async def _do():
        return {"ok": True}

    box = {"user": ALICE}
    async with _client(factory, box, router) as c:
        assert (await c.post("/do")).status_code == 403
        assert (await c.post("/do")).status_code == 403
    async with factory() as s:
        rows = (await s.execute(select(AdminAuditLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].category == "permissions" and rows[0].action == "permission.denied"
    assert rows[0].target == "tags.manage" and rows[0].actor == "alice"


async def test_matrix_save_conflict(factory):
    box = {"user": ADMIN}
    async with _client(factory, box) as c:
        r = await c.get("/api/v1/permissions/features")
        etag = r.headers["etag"]
        r1 = await c.put("/api/v1/permissions/features", json={"tags.manage": ["argus-user"]},
                         headers={"If-Match": etag})
        assert r1.status_code == 200 and r1.headers["etag"] != etag
        r2 = await c.put("/api/v1/permissions/features", json={"tags.manage": []}, headers={"If-Match": etag})
        assert r2.status_code == 409
        # If-Match 없이 저장하는 예전 호출은 그대로 받는다
        assert (await c.put("/api/v1/permissions/features", json={})).status_code == 200
        views = await c.get("/api/v1/permissions/menu-views")
        assert views.json() == {} and views.headers["etag"]
        r3 = await c.put("/api/v1/permissions/menu-views",
                         json={"apis": ["argus-user"], "users": ["argus-user"], "tags": []},
                         headers={"If-Match": views.headers["etag"]})
        assert r3.status_code == 200
        assert (await c.get("/api/v1/permissions/menu-views")).json() == {"apis": ["argus-user"]}


# ---------------------------------------------------------------------------
# 키 목록 · 프런트 일치
# ---------------------------------------------------------------------------

def test_review_keys_are_default_allow():
    for k in ("standards.map", "aiready.judge", "changes.rollback", "alerts.ack"):
        assert k in perm.FEATURE_KEYS and k not in perm.DEFAULT_DENY_FEATURES
        assert k in perm.WRITE_FEATURE_MENUS
    assert set(perm.WRITE_FEATURE_MENUS) <= perm.FEATURE_KEYS
    grant_keys = {k for keys in perm.ADMIN_MENU_GRANTS.values() for k in keys}
    assert not grant_keys & set(perm.WRITE_FEATURE_MENUS)
    for key, menu in perm.SHARED_MENU_FEATURES.items():
        assert perm.WRITE_FEATURE_MENUS[key] == menu


def test_write_feature_menus_match_frontend_registry():
    """프런트 FEATURE_REGISTRY 의 menuKey 와 view(조회 기능) 표시가 백엔드 WRITE_FEATURE_MENUS 와 같아야 한다."""
    text = REGISTRY.read_text(encoding="utf-8")
    body = text[text.index("export const FEATURE_REGISTRY"):text.index("...pluginFeatures")]
    grant_keys = {k for keys in perm.ADMIN_MENU_GRANTS.values() for k in keys}
    expected: dict[str, str] = {}
    for line in re.findall(r"\{ key: \"[^\"]+\"[^\n]*", body):
        key = re.search(r'key: "([^"]+)"', line).group(1)
        menu = re.search(r'menuKey: "([^"]+)"', line).group(1)
        if "view: true" in line or key in grant_keys or menu in ("dashboard", "users", "settings"):
            continue
        expected[key] = menu
    assert expected == perm.WRITE_FEATURE_MENUS
