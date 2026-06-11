"""조직 · 시스템 · 토폴로지 API.

사이드바 데이터 소스 메뉴의 ``조직(트리) → 시스템 → 데이터 소스 → 데이터셋`` 계층을 다룬다.
catalog_router 와 동일하게 ``/api/v1/catalog`` prefix 로 마운트된다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import topology_service as svc
from app.catalog.schemas import (
    OrganizationCreate,
    OrganizationResponse,
    OrganizationUpdate,
    DatasourceSystemAssign,
    DatasourceResponse,
    SystemCreate,
    SystemResponse,
    SystemUpdate,
    TopologyResponse,
)
from app.core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog-topology"])


# ---------------------------------------------------------------------------
# 토폴로지 (사이드바 트리)
# ---------------------------------------------------------------------------

@router.get("/topology", response_model=TopologyResponse)
async def get_topology(session: AsyncSession = Depends(get_session)):
    """조직 트리 → 시스템 → 데이터 소스(+데이터셋 수). 미분류 데이터 소스은 unassigned 버킷."""
    return await svc.get_topology(session)


# ---------------------------------------------------------------------------
# 조직 (Organization)
# ---------------------------------------------------------------------------

@router.get("/organizations", response_model=list[OrganizationResponse])
async def list_organizations(session: AsyncSession = Depends(get_session)):
    return await svc.list_organizations(session)


@router.post("/organizations", response_model=OrganizationResponse, status_code=201)
async def create_organization(
    req: OrganizationCreate, session: AsyncSession = Depends(get_session)
):
    try:
        return await svc.create_organization(session, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/organizations/{org_id}", response_model=OrganizationResponse)
async def get_organization(org_id: int, session: AsyncSession = Depends(get_session)):
    org = await svc.get_organization(session, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="조직을(를) 찾을 수 없습니다.")
    return OrganizationResponse.model_validate(org)


@router.put("/organizations/{org_id}", response_model=OrganizationResponse)
async def update_organization(
    org_id: int, req: OrganizationUpdate, session: AsyncSession = Depends(get_session)
):
    try:
        org = await svc.update_organization(session, org_id, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if org is None:
        raise HTTPException(status_code=404, detail="조직을(를) 찾을 수 없습니다.")
    return org


@router.delete("/organizations/{org_id}")
async def delete_organization(org_id: int, session: AsyncSession = Depends(get_session)):
    child_orgs, systems = await svc.count_org_children(session, org_id)
    if child_orgs or systems:
        raise HTTPException(
            status_code=409,
            detail=f"조직을(를) 삭제할 수 없습니다: 하위 조직 {child_orgs}개, 시스템 {systems}개가 연결되어 있습니다.",
        )
    if not await svc.delete_organization(session, org_id):
        raise HTTPException(status_code=404, detail="조직을(를) 찾을 수 없습니다.")
    return {"status": "ok", "message": "Organization deleted"}


# ---------------------------------------------------------------------------
# 시스템 (System)
# ---------------------------------------------------------------------------

@router.get("/systems", response_model=list[SystemResponse])
async def list_systems(
    org_id: int | None = Query(None), session: AsyncSession = Depends(get_session)
):
    return await svc.list_systems(session, org_id=org_id)


@router.post("/systems", response_model=SystemResponse, status_code=201)
async def create_system(req: SystemCreate, session: AsyncSession = Depends(get_session)):
    try:
        return await svc.create_system(session, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/systems/{system_id}", response_model=SystemResponse)
async def get_system(system_id: int, session: AsyncSession = Depends(get_session)):
    system = await svc.get_system(session, system_id)
    if system is None:
        raise HTTPException(status_code=404, detail="시스템을(를) 찾을 수 없습니다.")
    return SystemResponse.model_validate(system)


@router.put("/systems/{system_id}", response_model=SystemResponse)
async def update_system(
    system_id: int, req: SystemUpdate, session: AsyncSession = Depends(get_session)
):
    try:
        system = await svc.update_system(session, system_id, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if system is None:
        raise HTTPException(status_code=404, detail="시스템을(를) 찾을 수 없습니다.")
    return system


@router.delete("/systems/{system_id}")
async def delete_system(
    system_id: int,
    force: bool = Query(False, description="true 면 소속 데이터 소스을 미분류로 두고 삭제"),
    session: AsyncSession = Depends(get_session),
):
    count = await svc.count_system_datasources(session, system_id)
    if count and not force:
        raise HTTPException(
            status_code=409,
            detail=f"시스템을(를) 삭제할 수 없습니다: 데이터 소스 {count}개가 연결되어 있습니다. "
            f"?force=true 를 사용하면 배정을 해제하고 삭제합니다.",
        )
    if not await svc.delete_system(session, system_id):
        raise HTTPException(status_code=404, detail="시스템을(를) 찾을 수 없습니다.")
    return {"status": "ok", "message": "System deleted", "unassigned_datasources": count}


# ---------------------------------------------------------------------------
# 데이터 소스 배정
# ---------------------------------------------------------------------------

@router.put("/datasources/{datasource_id}/system", response_model=DatasourceResponse)
async def assign_datasource_system(
    datasource_id: int,
    req: DatasourceSystemAssign,
    session: AsyncSession = Depends(get_session),
):
    """데이터 소스을 시스템에 배정/이동하거나 해제(system_id=null)한다."""
    try:
        datasource = await svc.assign_datasource_system(session, datasource_id, req.system_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if datasource is None:
        raise HTTPException(status_code=404, detail="데이터 소스을(를) 찾을 수 없습니다.")
    return DatasourceResponse.model_validate(datasource)
