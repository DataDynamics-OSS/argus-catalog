# SPDX-License-Identifier: Apache-2.0
"""권한 5단계 — 4단계 남은 것 정리 (TODO-138, 자기완결형, in-memory SQLite).

확인하는 규칙:
- 데이터셋 ↔ 분류 매핑은 ``taxonomies.map``, 분류체계 · 분류 정의 편집은 ``taxonomies.edit`` 로 따로 판정한다.
- 분류 체계 메뉴가 막힌 역할은 정의 편집(``taxonomies.edit``)을 못 하지만 매핑은 막히지 않는다(데이터셋 화면에서도 쓰므로).
  데이터셋 메뉴가 보기 전용이면 매핑이 막힌다.
- 기동 보정(:func:`seed_default_permissions`)은 ``taxonomies.edit`` 를 받은 역할에 ``taxonomies.map`` 을 한 번만 복사한다.
- 플러그인 기능에 ``menu`` 를 주면 그 메뉴가 보기 전용인 역할은 쓸 수 없다(``view`` · 관리자 전용 · 코어 메뉴는 묶지 않음).

실행:
    cd backend
    pytest tests/test_permission_stage5.py
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.auth import TokenUser
from app.core.database import Base
from app.permissions import roles as custom_roles
from app.permissions import router as perm
from app.permissions.models import Permission
from app.settings.models import CatalogConfiguration

ALICE = TokenUser(sub="alice", username="alice", roles=["argus-user"])


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[Permission.__table__, CatalogConfiguration.__table__])
    make = async_sessionmaker(engine, expire_on_commit=False)
    perm._menu_cache.clear()
    custom_roles.invalidate()
    custom_roles.session_factory = make
    yield make
    custom_roles.session_factory = None
    custom_roles.invalidate()
    perm._menu_cache.clear()
    await engine.dispose()


@pytest.fixture
def plugin_registry():
    """플러그인 등록이 바꾸는 모듈 전역을 테스트 뒤 되돌린다."""
    saved = (set(perm.FEATURE_KEYS), set(perm.DEFAULT_DENY_FEATURES), dict(perm.WRITE_FEATURE_MENUS),
             dict(perm.ADMIN_MENU_GRANTS), set(perm.ADMIN_MENUS))
    yield
    perm.FEATURE_KEYS.clear()
    perm.FEATURE_KEYS.update(saved[0])
    perm.DEFAULT_DENY_FEATURES.clear()
    perm.DEFAULT_DENY_FEATURES.update(saved[1])
    perm.WRITE_FEATURE_MENUS.clear()
    perm.WRITE_FEATURE_MENUS.update(saved[2])
    perm.ADMIN_MENU_GRANTS.clear()
    perm.ADMIN_MENU_GRANTS.update(saved[3])
    perm.ADMIN_MENUS.clear()
    perm.ADMIN_MENUS.update(saved[4])


async def _rows(s, key: str) -> set[str]:
    return set((await s.execute(select(Permission.role_id).where(
        Permission.kind == "FEATURE", Permission.perm_key == key))).scalars())


def test_taxonomy_keys_are_split():
    assert {"taxonomies.edit", "taxonomies.map"} <= perm.DEFAULT_DENY_FEATURES
    assert perm.WRITE_FEATURE_MENUS["taxonomies.map"] == "datasets"
    assert perm.WRITE_FEATURE_MENUS["taxonomies.edit"] == "taxonomies"
    assert perm.SHARED_MENU_FEATURES["taxonomies.edit"] == "taxonomies"
    assert "taxonomies.map" not in perm.SHARED_MENU_FEATURES
    assert perm.SPLIT_FEATURES == {"taxonomies.map": "taxonomies.edit"}


async def test_denied_taxonomies_menu_blocks_definition_edit_not_mapping(factory):
    async with factory() as s:
        s.add_all([
            Permission(kind="FEATURE", perm_key="taxonomies.edit", role_id="argus-user"),
            Permission(kind="FEATURE", perm_key="taxonomies.map", role_id="argus-user"),
            Permission(kind="MENU", perm_key="taxonomies", role_id="argus-superuser"),     # User 에게 분류 체계 메뉴 막힘
        ])
        await s.commit()
        assert not await perm.check_feature_granted(s, ALICE, "taxonomies.edit")
        assert await perm.check_feature_granted(s, ALICE, "taxonomies.map")
        me = await perm.get_my_permissions(ALICE, s)
        assert "taxonomies.edit" in me["denied_features"] and "taxonomies.map" in me["granted_features"]


async def test_view_only_datasets_blocks_mapping(factory):
    async with factory() as s:
        s.add_all([
            Permission(kind="FEATURE", perm_key="taxonomies.edit", role_id="argus-user"),
            Permission(kind="FEATURE", perm_key="taxonomies.map", role_id="argus-user"),
            Permission(kind="MENU_VIEW", perm_key="datasets", role_id="argus-user"),
        ])
        await s.commit()
        assert not await perm.check_feature_granted(s, ALICE, "taxonomies.map")
        assert await perm.check_feature_granted(s, ALICE, "taxonomies.edit")


async def test_seed_copies_split_grants_once(factory):
    async with factory() as s:
        s.add_all([
            Permission(kind="FEATURE", perm_key="taxonomies.edit", role_id="argus-user"),
            Permission(kind="FEATURE", perm_key="taxonomies.edit", role_id="argus-superuser"),
        ])
        await s.commit()
        await perm.seed_default_permissions(s)
        assert await _rows(s, "taxonomies.map") == {"argus-user", "argus-superuser"}
        assert await perm.check_feature_granted(s, ALICE, "taxonomies.map")
        # 다시 돌려도 중복 복사하지 않는다
        await perm.seed_default_permissions(s)
        assert len((await s.execute(select(Permission).where(Permission.perm_key == "taxonomies.map"))).all()) == 2
        # 관리자가 매핑을 아무에게도 허용하지 않게 저장하면(센티널) 다시 복사하지 않는다
        await perm._put_matrix(s, "FEATURE", {"taxonomies.map": []})
        await perm.seed_default_permissions(s)
        assert await _rows(s, "taxonomies.map") == {"__none__"}
        perm._menu_cache.clear()
        assert not await perm.check_feature_granted(s, ALICE, "taxonomies.map")


async def test_seed_without_old_grants_leaves_admin_only(factory):
    async with factory() as s:
        await perm.seed_default_permissions(s)
        assert await _rows(s, "taxonomies.map") == set()
        assert not await perm.check_feature_granted(s, ALICE, "taxonomies.map")


async def test_plugin_feature_menu_binding(factory, plugin_registry):
    perm.register_plugin_permissions("acme", features=[
        {"key": "acme.export", "menu": "plugin.acme"},                          # 편집 기능 → 묶임
        {"key": "acme.read", "menu": "plugin.acme", "view": True},             # 조회 기능 → 안 묶임
        {"key": "acme.admin-op", "menu": "plugin.acme-admin"},                 # 관리자 전용 메뉴 → 안 묶임
        {"key": "acme.core", "menu": "datasets"},                              # 코어 메뉴 → 안 묶임
        "acme.plain",
    ], menus=[{"key": "plugin.acme-admin", "admin_only": True}])
    assert perm.WRITE_FEATURE_MENUS.get("acme.export") == "plugin.acme"
    for k in ("acme.read", "acme.admin-op", "acme.core", "acme.plain"):
        assert k in perm.FEATURE_KEYS and k not in perm.WRITE_FEATURE_MENUS, k
    async with factory() as s:
        assert await perm.check_feature(s, ALICE, "acme.export")
        s.add(Permission(kind="MENU_VIEW", perm_key="plugin.acme", role_id="argus-user"))
        await s.commit()
        perm._menu_cache.clear()
        assert not await perm.check_feature(s, ALICE, "acme.export")
        assert await perm.check_feature(s, ALICE, "acme.read")
        me = await perm.get_my_permissions(ALICE, s)
        assert "acme.export" in me["denied_features"] and "plugin.acme" in me["readonly_menus"]
