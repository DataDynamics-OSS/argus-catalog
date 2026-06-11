# SPDX-License-Identifier: Apache-2.0
"""조직(Organization) · 시스템(System) · 토폴로지 서비스.

사이드바 데이터 소스 메뉴를 ``조직(트리) → 시스템 → 데이터 소스 → 데이터셋`` 계층으로
구조화하기 위한 CRUD 와 트리 조립 로직.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Dataset, Datasource, Organization, System
from app.catalog.schemas import (
    OrganizationCreate,
    OrganizationResponse,
    OrganizationUpdate,
    SystemCreate,
    SystemResponse,
    SystemUpdate,
    TopologyDatasource,
    TopologyOrganization,
    TopologyResponse,
    TopologySystem,
    TopologyUnassigned,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """이름을 URL 친화적 slug 로 변환 (영숫자/한글 외 → '-')."""
    s = re.sub(r"[^0-9a-zA-Z가-힣]+", "-", name.strip().lower()).strip("-")
    return s or "item"


async def _unique_code(session: AsyncSession, model, name: str) -> str:
    """``model.code`` 가 유일하도록 slug 를 만들고, 충돌 시 ``-N`` 접미사를 붙인다."""
    base = _slugify(name)
    code = base
    n = 1
    while await session.scalar(select(model.id).where(model.code == code)):
        n += 1
        code = f"{base}-{n}"
    return code


# ---------------------------------------------------------------------------
# 조직 (Organization)
# ---------------------------------------------------------------------------

async def list_organizations(session: AsyncSession) -> list[OrganizationResponse]:
    rows = (
        await session.execute(
            select(Organization).order_by(Organization.sort_order, Organization.name)
        )
    ).scalars().all()
    return [OrganizationResponse.model_validate(o) for o in rows]


async def get_organization(session: AsyncSession, org_id: int) -> Organization | None:
    return await session.get(Organization, org_id)


async def create_organization(
    session: AsyncSession, req: OrganizationCreate
) -> OrganizationResponse:
    if req.parent_id is not None and await session.get(Organization, req.parent_id) is None:
        raise ValueError(f"parent organization not found: {req.parent_id}")
    org = Organization(
        code=await _unique_code(session, Organization, req.name),
        name=req.name,
        parent_id=req.parent_id,
        description=req.description,
        sort_order=req.sort_order,
    )
    session.add(org)
    await session.commit()
    await session.refresh(org)
    logger.info("조직 생성: %s (id=%d, parent=%s)", org.name, org.id, org.parent_id)
    return OrganizationResponse.model_validate(org)


async def _descendant_org_ids(session: AsyncSession, org_id: int) -> set[int]:
    """주어진 조직의 모든 후손 id (자기 자신 제외). 순환 방지용."""
    descendants: set[int] = set()
    frontier = {org_id}
    while frontier:
        rows = (
            await session.execute(
                select(Organization.id).where(Organization.parent_id.in_(frontier))
            )
        ).scalars().all()
        nxt = {r for r in rows if r not in descendants}
        descendants |= nxt
        frontier = nxt
    return descendants


async def update_organization(
    session: AsyncSession, org_id: int, req: OrganizationUpdate
) -> OrganizationResponse | None:
    org = await session.get(Organization, org_id)
    if org is None:
        return None

    data = req.model_dump(exclude_unset=True)

    if "parent_id" in data:
        new_parent = data["parent_id"]
        if new_parent is not None:
            if new_parent == org_id:
                raise ValueError("organization cannot be its own parent")
            if await session.get(Organization, new_parent) is None:
                raise ValueError(f"parent organization not found: {new_parent}")
            if new_parent in await _descendant_org_ids(session, org_id):
                raise ValueError("cannot move organization under its own descendant (cycle)")

    for key, value in data.items():
        setattr(org, key, value)
    await session.commit()
    await session.refresh(org)
    logger.info("조직 수정: id=%d, 필드=%s", org.id, list(data.keys()))
    return OrganizationResponse.model_validate(org)


async def count_org_children(session: AsyncSession, org_id: int) -> tuple[int, int]:
    """(직속 하위 조직 수, 직속 시스템 수) — 삭제 가드용."""
    child_orgs = await session.scalar(
        select(func.count(Organization.id)).where(Organization.parent_id == org_id)
    )
    systems = await session.scalar(
        select(func.count(System.id)).where(System.org_id == org_id)
    )
    return int(child_orgs or 0), int(systems or 0)


async def delete_organization(session: AsyncSession, org_id: int) -> bool:
    org = await session.get(Organization, org_id)
    if org is None:
        return False
    await session.delete(org)
    await session.commit()
    logger.info("조직 삭제: id=%d", org_id)
    return True


# ---------------------------------------------------------------------------
# 시스템 (System)
# ---------------------------------------------------------------------------

async def list_systems(
    session: AsyncSession, org_id: int | None = None
) -> list[SystemResponse]:
    stmt = select(System).order_by(System.sort_order, System.name)
    if org_id is not None:
        stmt = stmt.where(System.org_id == org_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [SystemResponse.model_validate(s) for s in rows]


async def get_system(session: AsyncSession, system_id: int) -> System | None:
    return await session.get(System, system_id)


async def create_system(session: AsyncSession, req: SystemCreate) -> SystemResponse:
    if req.org_id is not None and await session.get(Organization, req.org_id) is None:
        raise ValueError(f"organization not found: {req.org_id}")
    system = System(
        code=await _unique_code(session, System, req.name),
        name=req.name,
        org_id=req.org_id,
        summary=req.summary,
        description=req.description,
        owner=req.owner,
        status=req.status,
        sort_order=req.sort_order,
    )
    session.add(system)
    await session.commit()
    await session.refresh(system)
    logger.info("시스템 생성: %s (id=%d, org=%d)", system.name, system.id, system.org_id)
    return SystemResponse.model_validate(system)


async def update_system(
    session: AsyncSession, system_id: int, req: SystemUpdate
) -> SystemResponse | None:
    system = await session.get(System, system_id)
    if system is None:
        return None
    data = req.model_dump(exclude_unset=True)
    if data.get("org_id") is not None and await session.get(Organization, data["org_id"]) is None:
        raise ValueError(f"organization not found: {data['org_id']}")
    for key, value in data.items():
        setattr(system, key, value)
    await session.commit()
    await session.refresh(system)
    logger.info("시스템 수정: id=%d, 필드=%s", system.id, list(data.keys()))
    return SystemResponse.model_validate(system)


async def count_system_datasources(session: AsyncSession, system_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count(Datasource.id)).where(Datasource.system_id == system_id)
        ) or 0
    )


async def delete_system(session: AsyncSession, system_id: int) -> bool:
    """시스템 삭제. 소속 데이터 소스은 FK SET NULL 로 미분류로 떨어진다."""
    system = await session.get(System, system_id)
    if system is None:
        return False
    await session.delete(system)
    await session.commit()
    logger.info("시스템 삭제: id=%d", system_id)
    return True


# ---------------------------------------------------------------------------
# 데이터 소스 배정
# ---------------------------------------------------------------------------

async def assign_datasource_system(
    session: AsyncSession, datasource_id: int, system_id: int | None
) -> Datasource | None:
    """데이터 소스을 시스템에 배정/이동하거나 해제(system_id=None)한다."""
    datasource = await session.get(Datasource, datasource_id)
    if datasource is None:
        return None
    if system_id is not None and await session.get(System, system_id) is None:
        raise ValueError(f"system not found: {system_id}")
    datasource.system_id = system_id
    await session.commit()
    await session.refresh(datasource)
    logger.info("데이터 소스 %d 를 시스템=%s 에 배정", datasource_id, system_id)
    return datasource


# ---------------------------------------------------------------------------
# 토폴로지 (사이드바 트리)
# ---------------------------------------------------------------------------

async def get_topology(session: AsyncSession) -> TopologyResponse:
    """조직 트리 → 시스템 → 데이터 소스(+데이터셋 수)을 한 번에 조립한다.

    각 엔티티를 1쿼리씩 읽어 메모리에서 조립한다 (N+1 회피). system_id 가 NULL 인
    데이터 소스은 'unassigned' 버킷으로 모은다.
    """
    orgs = (
        await session.execute(
            select(Organization).order_by(Organization.sort_order, Organization.name)
        )
    ).scalars().all()
    systems = (
        await session.execute(
            select(System).order_by(System.sort_order, System.name)
        )
    ).scalars().all()
    datasources = (
        await session.execute(select(Datasource).order_by(Datasource.name))
    ).scalars().all()
    # 데이터셋 수 (datasource_id 별 집계) 1쿼리
    counts_rows = (
        await session.execute(
            select(Dataset.datasource_id, func.count(Dataset.id)).group_by(Dataset.datasource_id)
        )
    ).all()
    ds_count = {pid: cnt for pid, cnt in counts_rows}

    # 데이터 소스 → 시스템별 그룹핑 (+ 미분류)
    datasources_by_system: dict[int, list[TopologyDatasource]] = {}
    unassigned: list[TopologyDatasource] = []
    for p in datasources:
        node = TopologyDatasource(
            id=p.id, name=p.name, type=p.type,
            origin=p.origin, dataset_count=int(ds_count.get(p.id, 0)),
        )
        if p.system_id is None:
            unassigned.append(node)
        else:
            datasources_by_system.setdefault(p.system_id, []).append(node)

    # 시스템 → 조직별 그룹핑 (org_id=NULL 은 미분류 시스템으로 분리)
    systems_by_org: dict[int, list[TopologySystem]] = {}
    unassigned_systems: list[TopologySystem] = []
    for s in systems:
        node = TopologySystem(
            id=s.id, name=s.name, status=s.status, owner=s.owner, org_id=s.org_id,
            summary=s.summary, description=s.description,
            datasources=datasources_by_system.get(s.id, []),
        )
        if s.org_id is None:
            unassigned_systems.append(node)
        else:
            systems_by_org.setdefault(s.org_id, []).append(node)

    # 조직 트리 조립 (parent_id 기준)
    children_by_parent: dict[int | None, list[Organization]] = {}
    for o in orgs:
        children_by_parent.setdefault(o.parent_id, []).append(o)

    def build(org: Organization) -> TopologyOrganization:
        return TopologyOrganization(
            id=org.id, name=org.name, parent_id=org.parent_id,
            children=[build(c) for c in children_by_parent.get(org.id, [])],
            systems=systems_by_org.get(org.id, []),
        )

    roots = [build(o) for o in children_by_parent.get(None, [])]
    return TopologyResponse(
        organizations=roots,
        unassigned=TopologyUnassigned(datasources=unassigned, systems=unassigned_systems),
    )
