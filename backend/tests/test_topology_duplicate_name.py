# SPDX-License-Identifier: Apache-2.0
"""최상위 조직 · 미분류 시스템도 같은 이름을 거부한다 (DEF-111), 토폴로지에 조직 설명 (TODO-104)."""

from __future__ import annotations

import asyncio

import pytest

from app.catalog import topology_service as svc
from app.catalog.schemas import OrganizationCreate, OrganizationUpdate, SystemCreate, SystemUpdate
from tests.test_datasource_stage2 import _make_session


async def _run() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        root = await svc.create_organization(db, OrganizationCreate(name="재무", description="재무 본부"))
        # 최상위 같은 이름 — 예전에는 NULL 상위라 유일 제약이 안 걸려 저장됐다
        with pytest.raises(svc.DuplicateNameError):
            await svc.create_organization(db, OrganizationCreate(name="재무"))
        other = await svc.create_organization(db, OrganizationCreate(name="기술"))
        with pytest.raises(svc.DuplicateNameError):
            await svc.update_organization(db, other.id, OrganizationUpdate(name="재무"))
        # 다른 상위 아래는 같은 이름 허용, 그곳을 최상위로 옮기면 충돌
        child = await svc.create_organization(db, OrganizationCreate(name="재무", parent_id=other.id))
        with pytest.raises(svc.DuplicateNameError):
            await svc.update_organization(db, child.id, OrganizationUpdate(parent_id=None))
        # 자기 자신은 충돌 아님(설명만 바꿔도 통과)
        await svc.update_organization(db, root.id, OrganizationUpdate(name="재무", description="바뀐 설명"))

        await svc.create_system(db, SystemCreate(name="결제"))
        with pytest.raises(svc.DuplicateNameError):
            await svc.create_system(db, SystemCreate(name="결제"))
        s2 = await svc.create_system(db, SystemCreate(name="결제", org_id=root.id))
        with pytest.raises(svc.DuplicateNameError):
            await svc.update_system(db, s2.id, SystemUpdate(org_id=None))
        await svc.update_system(db, s2.id, SystemUpdate(name="결제", owner="홍길동"))

        topo = await svc.get_topology(db)
        by_name = {o.name: o for o in topo.organizations}
        assert by_name["재무"].description == "바뀐 설명"


def test_topology_duplicate_name() -> None:
    asyncio.run(_run())
